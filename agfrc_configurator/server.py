#!/usr/bin/env python3
"""AGFRC V3 Servo Configurator - Local Web Server

Serves the configurator web app on localhost:8090.
The browser talks directly to the USB HID dongle via Web HID API.
No backend HID logic needed.
"""

import http.server
import socketserver
import os
import sys
import ssl
import threading

PORT = 8092
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def log_message(self, format, *args):
        pass  # quiet

def main():
    os.chdir(WEB_DIR)

    # Try HTTPS first (Web HID requires secure context, but localhost is exempt)
    # localhost is treated as a secure context by Chrome, so HTTP is fine.
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        print(f"AGFRC V3 Servo Configurator")
        print(f"Server running at http://localhost:{PORT}")
        print(f"Open Chrome or Edge and navigate to the URL above.")
        print(f"Press Ctrl+C to stop.")
        print()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")

if __name__ == '__main__':
    main()