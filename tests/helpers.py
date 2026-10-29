import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        routes = {
            "/ok": (200, "<title>Hello</title><p>fine</p>", {}),
            "/gone": (404, "nope", {}),
            "/410": (410, "", {}),
            "/old": (301, "", {"Location": "/ok"}),
            "/temp": (302, "", {"Location": "/ok"}),
            "/deep/page": (301, "", {"Location": "/"}),
            "/soft": (200, "<html><title>Page Not Found</title></html>", {}),
            "/parked": (200, "<html><title>x</title><body>This domain is for sale! buy this domain</body></html>", {}),
            "/forbidden": (403, "", {}),
            "/": (200, "<title>Home</title>", {}),
        }
        if p in routes:
            code, body, headers = routes[p]
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())
        elif p.startswith("/cdx"):
            self._json([["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                        ["k", "20190304000000", "x", "text/html", "200", "d", "1"]] if "gone" in p or "soft" in p else [])
        elif p.startswith("/avail"):
            self._json({"archived_snapshots": {}})
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, obj):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())


def serve():
    srv = HTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"
