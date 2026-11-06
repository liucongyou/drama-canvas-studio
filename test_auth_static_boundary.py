"""Account-auth boundary: static entry is public; tenant data is not."""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class AuthStaticBoundaryTests(unittest.TestCase):
    def test_login_entry_loads_without_session_but_api_and_uploads_do_not(self):
        with tempfile.TemporaryDirectory(prefix="auth-boundary-") as temp:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = os.environ.copy()
            env.update({
                "PYTHONIOENCODING": "utf-8",
                "CANVAS_MULTI_TENANT": "1",
                "CANVAS_AUTH_MODE": "accounts",
                "CANVAS_API_HOST": "127.0.0.1",
                "CANVAS_FRONTEND_ORIGIN": "http://127.0.0.1:8891",
                "CANVAS_ALLOWED_ORIGINS": "http://127.0.0.1:8891",
                "CANVAS_COOKIE_SECURE": "0",
                "CANVAS_TENANT_DATA_ROOT": temp,
                "CANVAS_DIST_DIR": str(ROOT / "web-vimax" / "dist"),
            })
            process = subprocess.Popen(
                [
                    str(ROOT / ".venv" / "Scripts" / "python.exe"),
                    "-u",
                    "-c",
                    (
                        "import server; "
                        f"server.PORT={port}; "
                        f"server.CanvasHTTPServer(('127.0.0.1',{port}),server.Handler)"
                        ".serve_forever()"
                    ),
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                for _ in range(80):
                    try:
                        with socket.create_connection(
                            ("127.0.0.1", port),
                            timeout=0.2,
                        ):
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail(process.stderr.read().decode("utf-8", "replace"))
                        time.sleep(0.1)
                else:
                    self.fail("isolated server did not start")

                def get(path: str):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        port,
                        timeout=10,
                    )
                    connection.request("GET", path)
                    response = connection.getresponse()
                    body = response.read()
                    headers = dict(response.getheaders())
                    connection.close()
                    return response.status, headers, body

                home = get("/")
                self.assertEqual(home[0], 200)
                self.assertIn(b'id="root"', home[2])

                api = get("/api/projects")
                self.assertEqual(api[0], 401)
                self.assertIn(b"ACCOUNT_AUTH_REQUIRED", api[2])

                upload = get("/uploads/missing.png")
                self.assertEqual(upload[0], 401)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    unittest.main()
