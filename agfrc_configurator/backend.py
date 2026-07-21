#!/usr/bin/env python3
"""AGFRC V3 Servo Configurator - WebSocket backend.

Bridges the browser UI (backend.html) to the dongle via the verified
hidraw protocol in agfrc.py. Runs on ws://localhost:8093.

JSON messages from the browser:
  {"cmd": "connect"}                  -> {"ok": true, "params": {...}}
  {"cmd": "read"}                     -> {"ok": true, "params": {...}}
  {"cmd": "write", "params": {...}}   -> {"ok": true, "params": {...}}
  {"cmd": "fw_upload", "data": b64}   -> progress: {"cmd": "fw_progress", ...}
                                       done: {"ok": true, "boot_version": ...}
  {"cmd": "disconnect"}               -> {"ok": true}
Errors: {"ok": false, "error": "..."}

Usage: venv/bin/python backend.py
"""

import base64
import binascii
import json
import threading
import traceback

from websocket_server import WebsocketServer

from agfrc import AgfrcDevice, AgfrcError, MODEL_B53DHS, params_to_block, params_to_page1

PORT = 8093


class Backend:
    def __init__(self):
        self.dev = None
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            if self.dev is None:
                self.dev = AgfrcDevice()
            self.dev.connect()
            return {"ok": True, "params": self.dev.read_params()}

    def disconnect(self):
        with self.lock:
            if self.dev is not None:
                self.dev.close()
                self.dev = None
            return {"ok": True}

    def read(self):
        with self.lock:
            self._require()
            return {"ok": True, "params": self.dev.read_params()}

    def write(self, params):
        with self.lock:
            self._require()
            current = self.dev.read_params()
            if self.dev.model == MODEL_B53DHS:
                page1 = params_to_page1(bytes.fromhex(current["block"]), params)
                name = params.get("name", params.get("servo_name"))
                self.dev.write_params(page1, name=name)
            else:
                block = params_to_block(bytes.fromhex(current["block"]), params)
                if not self.dev.write_params(block):
                    raise AgfrcError("write was not acknowledged by the servo")
            return {"ok": True, "params": self.dev.read_params()}

    def fw_upload(self, name, data_b64, progress_cb=None):
        try:
            fw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise AgfrcError("corrupt firmware data received from the browser")
        if not fw or len(fw) > 256 * 1024:
            raise AgfrcError(f"implausible .sfw size: {len(fw)} bytes")
        with self.lock:
            self._require()
            boot_ver = self.dev.upload_firmware(fw, progress_cb)
        return {"ok": True, "boot_version": boot_ver, "bytes": len(fw)}

    def _require(self):
        if self.dev is None or self.dev.fd is None:
            raise AgfrcError("not connected — press Connect first")


backend = Backend()


def on_message(client, server, message):
    cmd = None
    try:
        msg = json.loads(message)
        cmd = msg.get("cmd")
        if cmd == "connect":
            result = backend.connect()
        elif cmd == "read":
            result = backend.read()
        elif cmd == "write":
            result = backend.write(msg.get("params", {}))
        elif cmd == "fw_upload":
            def progress(sent, total):
                server.send_message(client, json.dumps(
                    {"ok": True, "cmd": "fw_progress", "sent": sent, "total": total}))
            result = backend.fw_upload(msg.get("name", ""), msg.get("data", ""), progress)
        elif cmd == "disconnect":
            result = backend.disconnect()
        else:
            result = {"ok": False, "error": f"unknown command: {cmd}"}
        result["cmd"] = cmd
    except (AgfrcError, OSError, ValueError) as e:
        result = {"ok": False, "error": str(e), "cmd": cmd}
    except Exception:
        # Never let an unexpected error kill the socket; report it cleanly.
        traceback.print_exc()
        result = {"ok": False, "error": f"internal error: {traceback.format_exc(limit=1).strip().splitlines()[-1]}", "cmd": cmd}
    server.send_message(client, json.dumps(result))


def on_connect(client, server):
    print(f"client connected: {client['id']}")


def on_disconnect(client, server):
    print(f"client disconnected: {client['id']}")


def main():
    server = WebsocketServer(host="127.0.0.1", port=PORT)
    server.set_fn_message_received(on_message)
    server.set_fn_new_client(on_connect)
    server.set_fn_client_left(on_disconnect)
    print(f"AGFRC V3 Servo Configurator backend")
    print(f"WebSocket server on ws://localhost:{PORT}")
    print(f"Open http://localhost:8092/backend.html in any browser")
    server.run_forever()


if __name__ == "__main__":
    main()
