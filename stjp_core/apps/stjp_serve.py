"""
Tiny HTTP server for the STJP graph viewer.

Serves the current directory and opens stjp_graph.html in the default browser.
Use this so the page can fetch() events.jsonl without running into file://
CORS restrictions.

Usage:
    python stjp_serve.py            # serves on http://127.0.0.1:8765
    python stjp_serve.py 9000       # custom port
"""
import http.server
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
# This script lives in stjp_core/apps/, but the HTML viewer and the
# events_*.jsonl it fetches live in stjp_core/ — serve that directory.
ROOT = Path(__file__).resolve().parent.parent


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # mute per-request logs
    def end_headers(self):
        # Prevent the browser from caching any jsonl or json files
        if self.path.endswith(".jsonl") or self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve():
    import os
    os.chdir(ROOT)
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), QuietHandler) as srv:
        url_replay = f"http://127.0.0.1:{PORT}/stjp_comparison.html"
        url_live   = f"http://127.0.0.1:{PORT}/stjp_comparison.html?live=1"
        print(f"Serving {ROOT} on http://127.0.0.1:{PORT}")
        print(f"  replay : {url_replay}")
        print(f"  live   : {url_live}")
        print("  (Ctrl+C to stop)")
        # Open the live URL automatically; user can switch in the same tab
        threading.Thread(target=lambda: (time.sleep(0.5), webbrowser.open(url_live)),
                         daemon=True).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    serve()
