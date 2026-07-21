#!/usr/bin/env python3
"""Static server for the WebHID AGFRC configurator.

WebHID requires a secure context: http://localhost counts, so this tiny
server is all that's needed. No backend logic — the page talks to the
dongle directly from Chrome/Edge via WebHID.

Usage: python3 server.py   ->  open http://localhost:8094/
"""

import http.server
import socketserver

PORT = 8094


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving the WebHID configurator on http://localhost:{PORT}/")
        print("Open it in Chrome or Edge — no backend needed.")
        httpd.serve_forever()
