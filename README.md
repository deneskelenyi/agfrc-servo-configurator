# AGFRC V3 Servo Configurator

An independent, open-source configurator for the **AGFRC V3 USB servo-programmer
dongle** and its programmable servos. The USB/HID protocol was reverse-engineered
from the official Windows application and verified live against real hardware
(see [`PROTOCOL.md`](PROTOCOL.md) for the full protocol documentation).

Two front-ends are included — pick whichever fits:

| | Python backend + web UI | WebHID standalone app |
|---|---|---|
| Directory | `agfrc_configurator/` | `webhid_app/` |
| Requirements | Python 3 + one venv package (`websocket-server`) | Chrome/Edge only, zero dependencies |
| How it talks to the dongle | Linux `hidraw` | Browser WebHID (works on Windows/Linux/macOS) |
| Best for | Linux desktop, scripting/CLI | Any machine with Chrome — nothing to install |

## Supported servos

| Model | Status | Programmable via this tool |
|---|---|---|
| **B44BLS2** | Full support, verified on hardware | Travel R/L, neutral, damping, sensitivity, PWM power, soft start, overload protection (3 levels), Lose-PPM, inversion |
| **B53DHS** | Read/write verified on hardware | Travel R/L, neutral, damping, sensitivity, PWM power, soft start, servo name |

Notes on the B53DHS:

- Auto-detected by its ID bytes; the UI hides unsupported controls automatically.
- Overload protection / Lose-PPM / inversion / radio modes are **not programmable
  on this model — even the official app never writes them** (proven by diffing
  USB captures while changing them; the write frames are byte-identical).
- After a write the servo answers reads with zeros for up to ~20 s while it
  commits flash. Disconnect/Connect and read again — this is normal.
- Other models with the same dongle will likely identify as unknown and fall
  back to the B44BLS2 layout — read first and compare with the official app
  before writing.

## Quickstart

### WebHID app (recommended, no installation)

```
cd webhid_app
python3 server.py        # tiny static server; WebHID needs localhost or https
```

Open http://localhost:8094/ in Chrome/Edge, click **Connect**, pick the
"USBBootloaderV1.4" dongle. The static server binds to all interfaces, so the
app also works from another machine on your LAN (Chrome on Windows talking to a
dongle plugged into that machine).

### Python backend + web UI (Linux)

```
cd agfrc_configurator
python3 -m venv venv && venv/bin/pip install websocket-server
venv/bin/python backend.py &     # WebSocket backend on ws://localhost:8093
venv/bin/python server.py        # static UI on http://localhost:8092/
```

Open http://localhost:8092/ (redirects to `backend.html`).

### CLI (Linux, stdlib only)

```
python3 agfrc.py read                  # print all parameters + raw block
python3 agfrc.py set damping 160       # read-modify-write one parameter
python3 agfrc.py raw <hex>             # write a full raw block
```

## Firmware upload

Both UIs can upload a `.sfw` firmware image through the servo's bootloader.
The bootloader handshake is verified; the erase/flash path is implemented but
untested. **Flashing a `.sfw` that does not match the exact servo model will
brick the servo.** No firmware files are included in this repository.

## Safety notes

- Only one client may use the dongle at a time (backend, WebHID app, official
  app, or CLI) — close the others first.
- Unknown/preserved bytes in a model's parameter page are carried over
  untouched on write; the tools never synthesize them.
- Writing parameters is read-modify-write against the live servo. If in doubt,
  save the raw block first (`agfrc.py read` prints it).

## Disclaimer

AGFRC is a trademark of its owner. This is an independent, unofficial project
created for interoperability; it is not affiliated with or endorsed by AGFRC.
No AGFRC software, firmware, or decompiled material is distributed here —
the protocol was documented from observed USB traffic. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
