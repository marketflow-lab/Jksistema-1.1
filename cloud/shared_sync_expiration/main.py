"""Private Cloud Run endpoint invoked by Cloud Scheduler."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from google.cloud import firestore

from .expiration import expire


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def respond(self, status, payload):
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/healthz":
            self.respond(200, {"status": "ready"})
        else:
            self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/expire":
            self.respond(404, {"error": "not_found"})
            return
        try:
            result = expire(self.server.firestore)
        except Exception as error:
            print(json.dumps({"event": "shared_sync_expiration_error", "type": type(error).__name__}))
            self.respond(503, {"error": "expiration_incomplete"})
            return
        print(json.dumps({"event": "shared_sync_expiration", **result}))
        self.respond(503 if result.get("failed") else 200, result)


def main():
    project = os.environ["JK_FIRESTORE_PROJECT_ID"]
    with HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler) as server:
        server.firestore = firestore.Client(project=project, database="(default)")
        server.serve_forever()


if __name__ == "__main__":
    main()
