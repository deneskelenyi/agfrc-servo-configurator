#!/usr/bin/env python3
"""AGFRC V3 servo programmer dongle - verified protocol module (stdlib only).

Transport: plain hidraw interrupt OUT/IN, 64-byte reports.
  TX: [0x04, cmd, 0x00, 0x00, payload...]
  RX: [0x04, 0x01, status, payload...]   (status 0xfa = no servo / link error)

Commands:
  0x91 [0x20]        connect handshake (RX byte5 == 0x01 = dongle OK)
  0x8a [0x04]        poll for servo (RX byte2 == 0x00 = servo present)
  0xcd [0x2b]        read parameters (block = rx[5:27], name = rx[39:46])
  0xcb [0x18]+block  write parameters (RX echoes frame = success)

Supported servo models (identified by poll ID bytes, see MODELS):
  B44BLS2  22-byte block layout (offsets OFF_*)
  B53DHS   27-byte page-1 layout + paged name read/write (offsets B53_OFF_*)
  Unknown IDs fall back to the B44BLS2 layout.

CLI:
  python3 agfrc.py read
  python3 agfrc.py set <param> <value>
  python3 agfrc.py raw <22-byte-hex>
"""

import glob
import os
import select
import sys
import time

VID = 0x0471
PID = 0x13AA
REPORT_SIZE = 64
REPORT_ID = 0x04

CMD_CONNECT = 0x91
CMD_POLL = 0x8A
CMD_READ = 0xCD
CMD_WRITE = 0xCB

# Bootloader commands (from the decompiled Windows app; UNVERIFIED against
# real hardware — the same source's read/write IDs did not match the dongle).
CMD_ENTER_BOOTLOAD = 0x83
CMD_BOOTLOAD_DATA = 0x81
CMD_RESET = 0x80
BOOTLOAD_SEQ = bytes([0xFF, 0x55, 0xAA])
RESET_SEQ = bytes([0x01, 0x02, 0x03, 0x04, 0x05])

BLOCK_LEN = 22

# Parameter block offsets
OFF_DAMPING = 1
OFF_SENSITIVITY = 2
OFF_PWM = 6
OFF_TRAVEL_R = 8
OFF_TRAVEL_L = 9
OFF_SOFT_START = 10
OFF_OLP1_DUTY = 12
OFF_OLP2_DUTY = 13
OFF_OLP3_DUTY = 14
OFF_OLP1_TIME = 15
OFF_OLP2_TIME = 16
OFF_OLP3_TIME = 17
OFF_FLAGS = 20
FLAG_INVERSION = 0x01
FLAG_LOSEPPM_NEUTRAL = 0x08

PAGE1_LEN = 27  # B53DHS page-1 data (rx[5:32] of the 0xcd response)

MODEL_B44BLS2 = "b44bls2"
MODEL_B53DHS = "b53dhs"

# Servo poll ID bytes (rx[3:9] of the 0x8a response) -> (model name, layout).
# Unknown IDs fall back to the B44BLS2 layout (the only model the tool
# originally supported).
MODELS = {
    (0x01, 0x01, 0x33, 0x32, 0x01, 0x02): ("B44BLS2", MODEL_B44BLS2),
    (0x01, 0x01, 0x03, 0x01, 0x01, 0x01): ("B53DHS", MODEL_B53DHS),
}
DEFAULT_MODEL = ("unknown", MODEL_B44BLS2)

# B53DHS page-1 offsets (verified against the Windows app + usbmon)
B53_OFF_SOFT_START = 2
B53_OFF_TRAVEL_R = 4
B53_OFF_TRAVEL_L = 5
B53_OFF_NEUTRAL = 6  # stored as value + 128
B53_OFF_DAMPING_HI = 10  # big-endian 16-bit at [10..11]
B53_OFF_SENSITIVITY = 12
B53_OFF_PWM = 17  # mirrored at [18] and [19] on write (servo keeps 3 copies)

# The only parameters the B53DHS accepts — the official app never writes
# OLP/LosePPM/Inversion/radio modes on this model.
B53DHS_SETTABLE = (
    "travel_r", "travel_l", "neutral", "soft_start",
    "damping", "sensitivity", "pwm",
)


def identify_model(id_bytes):
    """Map servo poll ID bytes to (model name, layout)."""
    return MODELS.get(tuple(id_bytes), DEFAULT_MODEL)


class AgfrcError(Exception):
    pass


def find_hidraw_node():
    """Locate the hidraw node for VID 0x0471 / PID 0x13AA via sysfs."""
    for uevent in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            with open(uevent) as f:
                text = f.read()
        except OSError:
            continue
        if "00000471" in text and "000013AA" in text:
            return "/dev/" + os.path.basename(os.path.dirname(os.path.dirname(uevent)))
    raise AgfrcError(
        "AGFRC V3 dongle not found (VID=0x0471 PID=0x13AA). Is it plugged in?"
    )


def _pct(raw):
    return round(raw / 255 * 100, 1)


def _pct_raw(pct):
    return max(0, min(255, round(float(pct) / 100 * 255)))


def _secs(raw):
    return round(raw / 13, 1)


def _secs_raw(secs):
    return max(0, min(255, round(float(secs) * 13)))


def decode_block(block):
    """Decode a 22-byte parameter block into a dict of named parameters."""
    if len(block) != BLOCK_LEN:
        raise AgfrcError(f"parameter block must be {BLOCK_LEN} bytes")
    flags = block[OFF_FLAGS]
    return {
        "damping": block[OFF_DAMPING],
        "sensitivity": block[OFF_SENSITIVITY],
        "pwm": _pct(block[OFF_PWM]),
        "travel_r": block[OFF_TRAVEL_R],
        "travel_l": block[OFF_TRAVEL_L],
        "soft_start": block[OFF_SOFT_START],
        "olp1_pct": _pct(block[OFF_OLP1_DUTY]),
        "olp2_pct": _pct(block[OFF_OLP2_DUTY]),
        "olp3_pct": _pct(block[OFF_OLP3_DUTY]),
        "olp1_time": _secs(block[OFF_OLP1_TIME]),
        "olp2_time": _secs(block[OFF_OLP2_TIME]),
        "olp3_time": _secs(block[OFF_OLP3_TIME]),
        "inversion": bool(flags & FLAG_INVERSION),
        "loseppm": "neutral" if (flags & FLAG_LOSEPPM_NEUTRAL) else "keep",
    }


def set_param(block, name, value):
    """Return a new block with one named parameter changed."""
    block = bytearray(block)
    if name == "damping":
        block[OFF_DAMPING] = _u8(value)
    elif name == "sensitivity":
        block[OFF_SENSITIVITY] = _u8(value)
    elif name == "pwm":
        block[OFF_PWM] = _pct_raw(value)
    elif name == "travel_r":
        block[OFF_TRAVEL_R] = _u8(value)
    elif name == "travel_l":
        block[OFF_TRAVEL_L] = _u8(value)
    elif name == "soft_start":
        block[OFF_SOFT_START] = _u8(value)
    elif name == "olp1_pct":
        block[OFF_OLP1_DUTY] = _pct_raw(value)
    elif name == "olp2_pct":
        block[OFF_OLP2_DUTY] = _pct_raw(value)
    elif name == "olp3_pct":
        block[OFF_OLP3_DUTY] = _pct_raw(value)
    elif name == "olp1_time":
        block[OFF_OLP1_TIME] = _secs_raw(value)
    elif name == "olp2_time":
        block[OFF_OLP2_TIME] = _secs_raw(value)
    elif name == "olp3_time":
        block[OFF_OLP3_TIME] = _secs_raw(value)
    elif name == "inversion":
        v = str(value).strip().lower()
        if v in ("on", "1", "true", "yes"):
            block[OFF_FLAGS] |= FLAG_INVERSION
        elif v in ("off", "0", "false", "no"):
            block[OFF_FLAGS] &= ~FLAG_INVERSION
        else:
            raise AgfrcError("inversion must be on/off")
    elif name == "loseppm":
        v = str(value).strip().lower()
        if v in ("neutral", "go_neutral", "go neutral position"):
            block[OFF_FLAGS] |= FLAG_LOSEPPM_NEUTRAL
        elif v in ("keep", "keep_position", "keep position"):
            block[OFF_FLAGS] &= ~FLAG_LOSEPPM_NEUTRAL
        else:
            raise AgfrcError("loseppm must be keep/neutral")
    else:
        raise AgfrcError(f"unknown parameter: {name}")
    return bytes(block)


def _u8(value):
    v = int(value)
    if not 0 <= v <= 255:
        raise AgfrcError(f"value out of range 0-255: {value}")
    return v


def params_to_block(old_block, params):
    """Apply a dict of named params (from the UI/backend) to a block."""
    block = bytes(old_block)
    for name, value in params.items():
        block = set_param(block, name, value)
    return block


def decode_page1_b53dhs(page1):
    """Decode a 27-byte B53DHS page-1 block into a dict of named parameters."""
    if len(page1) != PAGE1_LEN:
        raise AgfrcError(f"page-1 block must be {PAGE1_LEN} bytes")
    return {
        "travel_r": page1[B53_OFF_TRAVEL_R],
        "travel_l": page1[B53_OFF_TRAVEL_L],
        "neutral": page1[B53_OFF_NEUTRAL] - 128,
        "soft_start": page1[B53_OFF_SOFT_START],
        "damping": (page1[B53_OFF_DAMPING_HI] << 8) | page1[B53_OFF_DAMPING_HI + 1],
        "sensitivity": page1[B53_OFF_SENSITIVITY],
        "pwm": _pct(page1[B53_OFF_PWM]),
    }


def set_param_b53dhs(page1, name, value):
    """Return a new B53DHS page-1 block with one named parameter changed.

    All other bytes are unknown/preserved and are carried over untouched.
    """
    page1 = bytearray(page1)
    if name == "travel_r":
        page1[B53_OFF_TRAVEL_R] = _u8(value)
    elif name == "travel_l":
        page1[B53_OFF_TRAVEL_L] = _u8(value)
    elif name == "neutral":
        page1[B53_OFF_NEUTRAL] = _u8(int(value) + 128)
    elif name == "soft_start":
        page1[B53_OFF_SOFT_START] = _u8(value)
    elif name == "damping":
        v = int(value)
        if not 0 <= v <= 0xFFFF:
            raise AgfrcError(f"value out of range 0-65535: {value}")
        page1[B53_OFF_DAMPING_HI] = v >> 8
        page1[B53_OFF_DAMPING_HI + 1] = v & 0xFF
    elif name == "sensitivity":
        page1[B53_OFF_SENSITIVITY] = _u8(value)
    elif name == "pwm":
        raw = _pct_raw(value)
        # The servo keeps three copies of the PWM byte.
        page1[B53_OFF_PWM] = page1[B53_OFF_PWM + 1] = page1[B53_OFF_PWM + 2] = raw
    else:
        raise AgfrcError(f"parameter not supported on B53DHS: {name}")
    return bytes(page1)


def params_to_page1(old_page1, params):
    """Apply a dict of named params (from the UI/backend) to a B53DHS page-1.

    Parameters the B53DHS does not support (OLP, loseppm, inversion, ...)
    are ignored, like the official app does.
    """
    page1 = bytes(old_page1)
    for name, value in params.items():
        if name in B53DHS_SETTABLE:
            page1 = set_param_b53dhs(page1, name, value)
    return page1


class AgfrcDevice:
    def __init__(self, node=None):
        self.node = node
        self.fd = None
        self.model_name = "unknown"
        self.model = MODEL_B44BLS2
        self._name = None       # last B53DHS name read
        self._name_page = None  # last B53DHS name page (13 bytes, for prefix/suffix)

    # -- low-level ------------------------------------------------------

    def _open(self):
        if self.fd is None:
            if self.node is None:
                self.node = find_hidraw_node()
            self.fd = os.open(self.node, os.O_RDWR | os.O_NONBLOCK)

    def _drain(self):
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            if not os.read(self.fd, REPORT_SIZE):
                return

    def _transact(self, cmd, payload=b"", timeout=1.0):
        """Drain RX, write one TX frame (tx[2]=tx[3]=0), read one RX frame."""
        return self._transact_raw(cmd, 0x00, 0x00, payload, timeout)

    def _transact_raw(self, cmd, b2, b3, payload=b"", timeout=1.0):
        """Drain RX, write one TX frame with explicit tx[2]/tx[3], read one RX.

        Needed for the B53DHS paged read/write frames, which carry an
        address in tx[3].
        """
        self._drain()
        tx = bytearray(REPORT_SIZE)
        tx[0] = REPORT_ID
        tx[1] = cmd
        tx[2] = b2
        tx[3] = b3
        tx[4 : 4 + len(payload)] = payload
        os.write(self.fd, bytes(tx))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgfrcError(f"timeout waiting for response to cmd 0x{cmd:02x}")
            r, _, _ = select.select([self.fd], [], [], remaining)
            if r:
                data = os.read(self.fd, REPORT_SIZE)
                if data:
                    return data

    # -- high-level -----------------------------------------------------

    def connect(self, poll_timeout=5.0, poll_interval=0.3):
        """Handshake (0x91) then poll for the servo (0x8a)."""
        self._open()
        rx = self._transact(CMD_CONNECT, bytes([0x20]))
        if len(rx) < 6 or rx[0] != REPORT_ID or rx[1] != 0x01 or rx[5] != 0x01:
            raise AgfrcError(f"dongle handshake failed: {rx.hex(' ')}")
        deadline = time.monotonic() + poll_timeout
        while True:
            rx = self._transact(CMD_POLL, bytes([0x04]))
            if len(rx) > 2 and rx[2] == 0x00:
                id_bytes = rx[3:9]  # servo ID bytes
                self.model_name, self.model = identify_model(id_bytes)
                return id_bytes
            if time.monotonic() >= deadline:
                raise AgfrcError(
                    "servo not detected — reseat servo / replug dongle"
                )
            time.sleep(poll_interval)

    def read_params(self, retries=3):
        """Read the parameter block; return named params + raw block + name.

        Branches on the model detected at connect() time. The servo briefly
        answers with zeros right after a write, so an all-zero block is
        retried a few times.
        """
        if self.model == MODEL_B53DHS:
            return self._read_b53dhs(retries)
        last = None
        for attempt in range(retries):
            rx = self._transact(CMD_READ, bytes([0x2B]))
            if len(rx) < 46 or rx[2] == 0xFA:
                raise AgfrcError(
                    "servo not detected — reseat servo / replug dongle"
                )
            block = bytes(rx[5:27])
            name = "".join(chr(b) for b in rx[39:46] if 32 <= b < 127).strip()
            if any(block) or attempt == retries - 1:
                result = decode_block(block)
                result["servo_name"] = name
                result["block"] = block.hex()
                result["model"] = self.model_name
                result["layout"] = self.model
                return result
            last = block
            time.sleep(0.2)
        return last  # unreachable

    # -- B53DHS ---------------------------------------------------------

    def _read_b53dhs(self, retries=3):
        """Read the 27-byte B53DHS page-1 block + paged name."""
        last = None
        for attempt in range(retries):
            rx = self._transact(CMD_READ, bytes([0x2B]))
            if len(rx) < 32 or rx[2] == 0xFA:
                raise AgfrcError(
                    "servo not detected — reseat servo / replug dongle"
                )
            page1 = bytes(rx[5:32])
            if any(page1) or attempt == retries - 1:
                result = decode_page1_b53dhs(page1)
                result["servo_name"] = self._read_name_b53dhs()
                result["block"] = page1.hex()
                result["model"] = self.model_name
                result["layout"] = self.model
                return result
            last = page1
            time.sleep(0.2)
        return last  # unreachable

    def _read_name_b53dhs(self):
        """Paged read of the B53DHS name page (addr 0x3b, len 0x24).

        Response: 04 01 00 3b 24 <13 bytes> — 5 prefix bytes, 6 ASCII name
        bytes at rx[10:16], 2 suffix bytes. The raw page is cached so a
        later name write can preserve prefix/suffix. Falls back to the
        model name from the ID table on any failure.
        """
        try:
            rx = self._transact_raw(CMD_READ, 0x00, 0x3B, bytes([0x24]))
            if len(rx) < 18 or rx[2] != 0x00 or rx[3] != 0x3B:
                raise AgfrcError("bad name-page response")
            page = bytes(rx[5:18])
            name = "".join(chr(b) for b in page[5:11] if 32 <= b < 127).strip()
            if not name:
                raise AgfrcError("no ASCII name in name page")
            self._name_page = page
            self._name = name
            return name
        except AgfrcError:
            self._name = self.model_name
            return self.model_name

    def _write_b53dhs(self, page1, name=None):
        """Write the B53DHS 3-frame sequence (page-1, name page, finalize)."""
        if len(page1) != PAGE1_LEN:
            raise AgfrcError(f"page-1 block must be {PAGE1_LEN} bytes")
        # 1. page-1 write: tx = cb 00 00 3b + 27 page-1 bytes
        rx = self._transact(CMD_WRITE, bytes([0x3B]) + bytes(page1))
        self._check_write_ack(rx)
        # 2. name page, only if the name is being changed
        if name is not None:
            name = str(name).strip()
            if name and name != self._name:
                if self._name_page is None:
                    raise AgfrcError("no name page cached — read the servo first")
                page = bytearray(self._name_page)
                raw = name.encode("ascii", "replace")[:6]
                page[5:11] = raw + b"\x00" * (6 - len(raw))
                rx = self._transact_raw(CMD_WRITE, 0x00, 0x3B, bytes([0x24]) + bytes(page))
                self._check_write_ack(rx)
                self._name_page = bytes(page)
                self._name = name
        # 3. finalize: tx = 9a 16 67 14 14 03
        rx = self._transact_raw(0x9A, 0x16, 0x67, bytes([0x14, 0x14, 0x03]))
        self._check_write_ack(rx)
        # Servo needs a moment to commit the write before it answers reads.
        time.sleep(0.15)
        return True

    @staticmethod
    def _check_write_ack(rx):
        """Each B53DHS write frame is confirmed with rx[1]=0x01, rx[2]=0x00;
        rx[2]=0x02 or 0xfa means the servo bus is down."""
        if len(rx) < 3 or rx[1] != 0x01 or rx[2] != 0x00:
            raise AgfrcError(
                "write not acknowledged (servo bus down?) — "
                "reseat servo / replug dongle"
            )

    def write_params(self, block, name=None):
        """Write the parameter block. Returns True on success.

        B44BLS2: full 22-byte block. B53DHS: 27-byte page-1 block, plus an
        optional new servo name (name page is only sent when it changes).
        """
        if self.model == MODEL_B53DHS:
            return self._write_b53dhs(block, name)
        if len(block) != BLOCK_LEN:
            raise AgfrcError(f"parameter block must be {BLOCK_LEN} bytes")
        rx = self._transact(CMD_WRITE, bytes([0x18]) + bytes(block))
        if len(rx) < 4 or rx[2] == 0xFA:
            raise AgfrcError(
                "servo not detected — reseat servo / replug dongle"
            )
        # Servo needs a moment to commit the write before it answers reads.
        time.sleep(0.15)
        # Success = dongle echoes the write frame back (payload matches).
        return len(rx) >= 27 and rx[4] == 0x18 and bytes(rx[5:27]) == bytes(block)

    def upload_firmware(self, fw_bytes, progress_cb=None, block_pause=0.005):
        """Upload a .sfw image to the servo via its bootloader.

        Sequence (from the decompiled Windows app):
          1. enter bootloader (0x83 + FF 55 AA), read boot version
          2. enter bootloader again -> erases flash
          3. stream the file in 22-byte blocks (0x81), pad tail with 0xFF
          4. reset (0x80 + 01 02 03 04 05)

        WARNING: erases the servo flash in step 2. Only call with a .sfw
        that matches the exact servo model, or the servo will be bricked.
        progress_cb(sent_blocks, total_blocks) is called after each block.
        """
        self._open()
        # 1. Enter bootloader, read boot version
        rx = self._transact(CMD_ENTER_BOOTLOAD, BOOTLOAD_SEQ, timeout=2.0)
        if len(rx) < 4 or rx[2] == 0xFA:
            raise AgfrcError(
                "servo did not enter bootloader — reseat servo / replug dongle"
            )
        boot_ver = "".join(chr(b) for b in rx[3:15] if 32 <= b < 127).strip()

        # 2. Second enter = erase flash (point of no return)
        rx = self._transact(CMD_ENTER_BOOTLOAD, BOOTLOAD_SEQ, timeout=5.0)
        if len(rx) < 4 or rx[2] == 0xFA:
            raise AgfrcError("flash erase was not acknowledged by the servo")

        # 3. Stream firmware in 22-byte blocks, 0xFF-padded
        total = (len(fw_bytes) + BLOCK_LEN - 1) // BLOCK_LEN
        for i in range(total):
            chunk = fw_bytes[i * BLOCK_LEN : (i + 1) * BLOCK_LEN]
            chunk = chunk + b"\xff" * (BLOCK_LEN - len(chunk))
            rx = self._transact(CMD_BOOTLOAD_DATA, chunk, timeout=2.0)
            if len(rx) < 4 or rx[2] == 0xFA:
                raise AgfrcError(
                    f"write error at block {i + 1}/{total} — "
                    "do not power off; retry the upload"
                )
            if progress_cb:
                progress_cb(i + 1, total)
            time.sleep(block_pause)

        # 4. Reset the servo (reply optional — it reboots)
        try:
            self._transact(CMD_RESET, RESET_SEQ, timeout=0.5)
        except AgfrcError:
            pass
        return boot_ver

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# -- CLI ----------------------------------------------------------------

SET_PARAMS = (
    "damping, sensitivity, pwm, travel_r, travel_l, soft_start, "
    "olp1_pct, olp2_pct, olp3_pct, olp1_time, olp2_time, olp3_time, "
    "inversion (on/off), loseppm (keep/neutral)   [B44BLS2]\n"
    "damping, sensitivity, pwm, travel_r, travel_l, soft_start, "
    "neutral, name   [B53DHS]"
)


def _print_params(p):
    print(f"Model:            {p.get('model', 'unknown')}")
    print(f"Servo:            {p['servo_name']}")
    print(f"Damping Factor:   {p['damping']}")
    print(f"Sensitivity:      {p['sensitivity']}")
    print(f"PWM Power:        {p['pwm']}%")
    print(f"Travel Range R:   {p['travel_r']}")
    print(f"Travel Range L:   {p['travel_l']}")
    print(f"Soft Start:       Level {p['soft_start']}")
    if p.get("layout") == MODEL_B53DHS:
        print(f"Neutral:          {p['neutral']}")
        print("Overload/Lose PPM/Inversion: not programmable on this model "
              "(ignored by official app)")
    else:
        print(f"Overload L1:      {p['olp1_time']}s @ {p['olp1_pct']}%")
        print(f"Overload L2:      {p['olp2_time']}s @ {p['olp2_pct']}%")
        print(f"Overload L3:      {p['olp3_time']}s @ {p['olp3_pct']}%")
        print(f"Lose PPM:         {'Go Neutral Position' if p['loseppm'] == 'neutral' else 'Keep Position'}")
        print(f"Inversion:        {'on' if p['inversion'] else 'off'}")
    print(f"Raw block:        {p['block']}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print("settable params:", SET_PARAMS)
        return 2
    cmd = argv[1]
    try:
        with AgfrcDevice() as dev:
            dev.connect()
            print(f"connected: {dev.model_name} ({dev.model})", file=sys.stderr)
            if cmd == "read":
                _print_params(dev.read_params())
            elif cmd == "set":
                if len(argv) != 4:
                    print("usage: agfrc.py set <param> <value>")
                    print("params:", SET_PARAMS)
                    return 2
                name, value = argv[2], argv[3]
                p = dev.read_params()
                if p.get("layout") == MODEL_B53DHS:
                    page1 = bytes.fromhex(p["block"])
                    new_name = None
                    if name in ("name", "servo_name"):
                        new_name = value
                    else:
                        page1 = set_param_b53dhs(page1, name, value)
                    dev.write_params(page1, name=new_name)
                else:
                    block = set_param(bytes.fromhex(p["block"]), name, value)
                    dev.write_params(block)
                after = dev.read_params()
                print(f"set {name} = {value}")
                _print_params(after)
            elif cmd == "raw":
                if len(argv) != 3:
                    print("usage: agfrc.py raw <block-hex> "
                          "(22 bytes B44BLS2 / 27 bytes B53DHS)")
                    return 2
                block = bytes.fromhex(argv[2].replace(" ", ""))
                dev.write_params(block)
                _print_params(dev.read_params())
            else:
                print(f"unknown command: {cmd}")
                return 2
    except AgfrcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
