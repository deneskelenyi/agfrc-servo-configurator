# AGFRC V3 Servo Configurator — WebHID edition

Standalone Chrome/Edge app. Talks to the AGFRC V3 dongle **directly from the
browser** via WebHID — no Python backend, no WebSocket server, no
dependencies. The only server is a tiny static file server, needed because
WebHID requires a secure context (`http://localhost` qualifies).

## Run

```
python3 server.py
```

then open http://localhost:8094/ in Chrome or Edge, click **Connect**, and
pick the "USBBootloaderV1.4" dongle in Chrome's device picker.

## Notes

- Chrome/Edge only (WebHID). Firefox/Safari do not implement it.
- Do not use this at the same time as the Python backend (`agfrc_configurator/`) —
  only one client should use the dongle at a time.
- Protocol is identical to the verified `agfrc_configurator/agfrc.py`
  implementation; report ID 0x04, 63 data bytes, vendor page 0xFF00.
- Firmware upload is implemented but the erase/flash path is untested —
  only flash a `.sfw` that matches the exact servo model.
- Verified live in Chrome against a B53DHS (read, write, and a full
  parameter-page restore). Encode/decode is byte-exact against the
  B44BLS2 and B53DHS reference frames.
- After writing a B53DHS the servo answers reads with zeros for up to
  ~20 s while it commits flash — Disconnect/Connect and Read again;
  this is normal, not a failure.
