# AGFRC V3 Servo Configurator - Handoff

## STATUS (2026-07-20)

The USB protocol is CRACKED and verified live against the real hardware.
A working native configurator exists in this directory:

- `agfrc.py` -- protocol module + CLI (stdlib only, no dependencies)
  - `python3 agfrc.py read` -- print all parameters + raw block
  - `python3 agfrc.py set <param> <value>` -- change one param (read-modify-write)
  - `python3 agfrc.py raw <22-byte-hex>` -- write a full raw block
- `backend.py` -- WebSocket backend on ws://localhost:8093
  (run with `venv/bin/python backend.py`; venv has websocket-server)
- `backend.html` -- single-page UI, served by `server.py` on port 8092
  (open http://localhost:8092/backend.html in any browser)

Verified against: AGFRC V3 dongle + B44BLS2 servo, read/write/restore all OK.
B53DHS read/write mapped against the Windows app + usbmon (see below);
per-model support implemented in all four components (agfrc.py branches on
the model detected at connect() time, both UIs adapt their field set).

## IMPORTANT: THE OLD PROTOCOL ANALYSIS WAS WRONG

The earlier reverse-engineering from the EXE (feature reports, commands
0x9A/0xAC/0xAE/0x83..., config offsets, SET_REPORT/GET_REPORT) does NOT match
the real wire protocol. GET_REPORT stalls by design and is never used.
Everything below has been verified live on hardware -- use only this.

## VERIFIED PROTOCOL

### Hardware / transport

- Dongle: VID 0x0471, PID 0x13AA, product "USBBootloaderV1.4".
- Appears as /dev/hidrawN. Find the node by scanning
  /sys/class/hidraw/hidraw*/device/uevent for '00000471' and '000013AA'.
  A udev rule already gives it mode 0666.
- Plain hidraw interrupt OUT/IN, all frames exactly 64 bytes:
  `os.open(node, O_RDWR|O_NONBLOCK)`, `os.write()` 64-byte reports,
  `select()` + `os.read()` 64-byte reports. No pyusb, no hidapi,
  no feature reports.
- Only ONE process may use the hidraw node at a time (two readers steal
  each other's frames). Never run clients in parallel.

### Frame format

- TX: byte0=0x04 (report ID), byte1=cmd, byte2=0x00, byte3=0x00,
  payload starts at byte4.
- RX: byte0=0x04, byte1=0x01, byte2=status, payload starts at byte3.
- RX byte2 == 0xfa means "no servo / link error".
- Always drain pending RX (non-blocking reads until empty) before each
  transaction, then write TX, then read one frame with ~1s timeout.

### Commands

1. Connect: cmd 0x91 payload [0x20]
   -> RX `04 01 00 00 20 01 ...` (RX byte5 == 0x01 = dongle OK)
2. Poll for servo: cmd 0x8a payload [0x04], every ~300ms, up to ~5s.
   Servo present when RX byte2 == 0x00; RX bytes 3..8 = servo ID
   (e.g. `01 01 33 32 01 02`, "32" ASCII = 32-bit MCU).
   If it stays 0xfa: "servo not detected -- reseat servo / replug dongle".
3. Read parameters: cmd 0xcd payload [0x2b]
   -> RX `04 01 00 00 2b <data...>`
   - 22-byte parameter block = rx[5:27]
   - servo name = rx[39:46] (7 ASCII chars, strip non-printables)
   - rx[~30] is a read counter, ignore it
4. Write parameters: cmd 0xcb payload [0x18] + full 22-byte block
   -> RX echoes the same frame = success. Writes are PERMANENT.
   NOTE: right after a write the servo answers one read with zeros;
   wait ~100ms (agfrc.py retries automatically).

### Parameter block map (offsets 0-21)

- [0]  unknown constant (0x3e on B44BLS2) -- preserve
- [1]  Damping Factor (raw 0-255)
- [2]  Sensitivity (raw 0-255)
- [3]  unknown 0x48, [4] unknown 0xf0, [5] unknown 0x2f -- preserve
- [6]  PWM Power: percent = raw/255*100
- [7]  unknown 0x64 -- preserve
- [8]  Travel Range R (raw 0-255)
- [9]  Travel Range L (raw 0-255)
- [10] Soft Start Level (raw)
- [11] unknown 0xdc -- preserve
- [12..14] Overload Level 1/2/3 duty: percent = raw/255*100
- [15..17] Overload Level 1/2/3 time: seconds = raw/13
- [18] unknown 0xdc, [19] unknown 0x0a -- preserve
- [20] flags: bit0 (0x01) = Inversion; bit3 (0x08) = Lose-PPM
       "Go Neutral Position" (clear = "Keep Position"); other bits preserve
- [21] unknown 0x90 -- preserve

When changing one parameter, always send the full 22-byte block with the
unknown bytes preserved from the last read (read-modify-write).

### Known-good B44BLS2 state (restore with `agfrc.py raw` if needed)

    3e 96 1e 48 f0 2f eb 64 d2 d2 08 dc ca 73 52 34 68 9c dc 0a 62 90

(damping=150, sensitivity=30, pwm=92.2%, travel R/L=210/210, soft start 8,
OLP 4.0s/79.2% 8.0s/45.1% 12.0s/32.2%, LosePPM=Keep, Inversion off)

### B53DHS observation (2026-07-20, read-only)

Superseded — the layout has since been verified against the Windows app and
usbmon captures; see the "B53DHS" section below. Kept for reference:

    3b d0 0b f6 e1 e1 80 03 00 50 01 f4 20 00 00 f0 0c f0 f0 f0 1e 1e

- Same protocol, stable across reads.
- Poll ID bytes differ: `01 01 03 01 01 01` (B44BLS2: `01 01 33 32 01 02`).
- The byte layout DOES differ per model — decoding with the B44BLS2 map
  gives nonsense (travel_r = 0).

### B53DHS (verified 2026-07-20 against the Windows app + usbmon captures)

- Poll ID bytes (rx[3:9] of the 0x8a response): `01 01 03 01 01 01`.
  `identify_model()` in agfrc.py maps ID bytes -> model; unknown IDs fall
  back to the B44BLS2 layout.
- Read: same cmd 0xcd [0x2b], response `04 01 00 00 2b <43 data bytes>`.
  The first 27 data bytes (rx[5:32]) are "page 1".
- Paged read: tx[1]=0xcd, tx[2]=0x00, tx[3]=addr, tx[4]=len. The name page
  is addr=0x3b len=0x24 -> response `04 01 00 3b 24 <13 bytes>` =
  5 prefix bytes + 6 ASCII name bytes at rx[10:16] + 2 suffix bytes
  (captured: `23 e3 00 00 00 42 35 33 44 48 53 2a 2a` = prefix, "B53DHS",
  `2a 2a`; confirmed 13 bytes on hardware 2026-07-20 — an earlier version
  of this doc said 12, which dropped the second `2a` suffix byte).
  NOTE: this needs tx[3] set — use `_transact_raw(cmd, b2, b3,
  payload)`, not `_transact()`. If the paged read fails or the name is not
  ASCII, fall back to the model name from the ID table.
- Page-1 decode map (idx = offset into the 27 data bytes):
  - [4] Travel R, [5] Travel L (raw byte = displayed degrees)
  - [6] Neutral, stored as value + 128 (0x7c -> -4)
  - [2] Soft Start level (raw)
  - [10..11] Damping, big-endian 16-bit (0x019f = 415)
  - [12] Sensitivity (raw)
  - [17] PWM Power: pct = raw*100/255; encode round(pct*255/100).
    On write also set [18]=[19]=[17] — the servo keeps three copies.
  - All other bytes (0,1,3,7,8,9,13,14,15,16,20..26) are unknown/preserved:
    never modify on write, always carry over from the last read.
- Write (3 frames, exactly as the official app):
  1. page-1:  tx = cb 00 00 3b + 27 page-1 bytes (data at tx[5:])
  2. name page (only if the name changed): tx = cb 00 3b 24 + 13 bytes —
     preserve the 5 prefix and 2 suffix bytes from the last name-page read,
     replace only the 6 ASCII name bytes (zero-padded)
  3. finalize: tx = 9a 16 67 14 14 03 (rest zero)
  Each write frame is confirmed with rx[1]=0x01, rx[2]=0x00; rx[2]=0x02 or
  0xfa means the servo bus is down. Re-read to verify afterwards.
  VERIFIED on hardware 2026-07-20 (rescue restore of the reference frame,
  flash byte-exact). IMPORTANT: after the 0x9a finalize the B53DHS answers
  data reads with zeros for up to ~20s while committing flash (B44BLS2:
  ~150ms). Recovery if it does not come back: HID disconnect/connect +
  re-handshake (or servo replug). A write-verify that reads zeros during
  this window does NOT mean the write failed — keep retrying ~15-20s
  before falling back to disconnect/connect + Read.
- NOT programmable on this model (the official app's Write never sends
  them): OLP (all 6 time/duty values), Lose PPM, Inversion, OTP, OCP and
  the FUTABA/SANWA radio modes. Evidence: changing those spinners in the
  official app produces byte-identical write frames in usbmon captures.
  Both UIs hide these fields for the B53DHS with a note.

### Bootloader (partially verified 2026-07-20)

- cmd 0x83 payload [ff 55 aa]: enter bootloader. VERIFIED: after one send
  the servo stops answering 0x8a polls (returns 0xfa) and 0xcd reads
  return zeros. The dongle replies with an echo `04 01 00 00 ff 55 aa ...`.
- cmd 0x80 payload [01 02 03 04 05]: reset. VERIFIED: servo reboots into
  normal mode, all parameters intact (read back the known-good block).
- Per the decompiled Windows app: a SECOND 0x83 erases the flash, then
  the .sfw file is streamed verbatim in 22-byte 0x81 blocks, 0xFF-padded,
  one response per block. NOT verified -- the only .sfw on hand is for an
  A50BHL, and flashing it into the B44BLS2 would brick it.
- Implemented end-to-end: agfrc.py `upload_firmware()`, backend.py
  "fw_upload" command (base64, with fw_progress messages), and a
  Firmware Update section + parameter guide in backend.html.

### HID report descriptor (for WebHID)

Vendor page 0xFF00. Report ID 0x04 = 63 data bytes, IN + OUT + FEATURE.
Wire byte0 (report ID) is stripped by WebHID: sendReport(0x04, data) where
data[0]=cmd, data[3:]=payload; inputreport data[0]=0x01, data[1]=status,
data[2:]=payload (i.e. all python rx[] indices shift down by 1).

## STILL UNKNOWN / TODO

- Firmware erase + flash-write path (0x83 second send, 0x81 blocks) --
  code written, untested (needs a .sfw matching the connected servo).
- .svo/.svc config file AES encryption -- config save/load uses JSON instead.
- Meaning of the unknown/preserved block bytes.
