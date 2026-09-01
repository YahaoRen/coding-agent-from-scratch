"""HTTP contract tests for the localhost browser workbench."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from coding_agent.web.manager import WorkbenchError
from coding_agent.web.server import (
    MAX_REQUEST_BYTES,
    WorkbenchRequestHandler,
    create_server,
)


RUN_ID = "a" * 32
APPROVAL_ID = "b" * 32
TEST_TOKEN = "local-test-workbench-token"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeRunManager:
    """Record HTTP-to-manager calls without starting an agent or model."""

    def __init__(self) -> None:
        self.snapshot_calls: list[dict[str, Any]] = []
        self.start_calls: list[Any] = []
        self.approval_calls: list[tuple[Any, Any, Any]] = []
        self.cancel_calls: list[Any] = []
        self.snapshot_error: Exception | None = None
        self.start_error: Exception | None = None
        self.approval_error: Exception | None = None
        self.cancel_error: Exception | None = None

    def snapshot(
        self,
        *,
        after_revision: int | None = None,
        wait_s: float = 0.0,
    ) -> dict[str, Any]:
        self.snapshot_calls.append(
            {"after_revision": after_revision, "wait_s": wait_s}
        )
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return {
            "configuration": {
                "ready": True,
                "model": "offline-test-model",
                "message": None,
            },
            "workspace": "temporary-workspace",
            "run": None,
        }

    def start(self, task: Any) -> str:
        self.start_calls.append(task)
        if self.start_error is not None:
            raise self.start_error
        return RUN_ID

    def approve(self, run_id: Any, approval_id: Any, approved: Any) -> None:
        self.approval_calls.append((run_id, approval_id, approved))
        if self.approval_error is not None:
            raise self.approval_error

    def cancel(self, run_id: Any) -> None:
        self.cancel_calls.append(run_id)
        if self.cancel_error is not None:
            raise self.cancel_error


class WorkbenchHTTPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temporary_directory.name)
        (self.static_root / "index.html").write_text(
            "<!doctype html><meta name='workbench-token' "
            "content='__WORKBENCH_TOKEN__'><link rel='stylesheet' href='/app.css'>"
            "<script src='/app.js' defer></script>",
            encoding="utf-8",
        )
        (self.static_root / "app.css").write_text(
            "body { color: #172033; }\n",
            encoding="utf-8",
        )
        (self.static_root / "app.js").write_text(
            "document.documentElement.dataset.ready = 'true';\n",
            encoding="utf-8",
        )
        # This file exists under the static root but is deliberately not allowlisted.
        (self.static_root / "private.txt").write_text(
            "must not be served",
            encoding="utf-8",
        )

        self.manager = FakeRunManager()
        self.server = create_server(
            self.manager,  # type: ignore[arg-type]
            port=0,
            token=TEST_TOKEN,
            static_root=self.static_root,
        )
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        if body is not None:
            if raw_body is not None:
                raise ValueError("body and raw_body are mutually exclusive")
            raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=5,
        )
        try:
            connection.request(
                method,
                path,
                body=raw_body,
                headers=request_headers,
            )
            response = connection.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def api_headers(
        self,
        *,
        token: str = TEST_TOKEN,
        origin: str | None = None,
    ) -> dict[str, str]:
        headers = {"X-Workbench-Token": token}
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def assert_json_error(
        self,
        content: bytes,
        code: str | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(content)
        self.assertIn("error", payload)
        if code is not None:
            self.assertEqual(payload["error"]["code"], code)
        return payload

    def test_static_assets_are_served_with_security_headers(self) -> None:
        expected_types = {
            "/": "text/html; charset=utf-8",
            "/index.html": "text/html; charset=utf-8",
            "/app.css": "text/css; charset=utf-8",
            "/app.js?v=1": "text/javascript; charset=utf-8",
        }

        for path, expected_type in expected_types.items():
            with self.subTest(path=path):
                status, headers, content = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], expected_type)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertEqual(int(headers["Content-Length"]), len(content))

        _, _, index = self.request("GET", "/")
        self.assertIn(TEST_TOKEN.encode("ascii"), index)
        self.assertNotIn(b"__WORKBENCH_TOKEN__", index)

    def test_production_page_is_self_contained_and_uses_safe_text_rendering(self) -> None:
        static_root = PROJECT_ROOT / "coding_agent" / "web" / "static"
        html = (static_root / "index.html").read_text(encoding="utf-8")
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        self.assertIn("__WORKBENCH_TOKEN__", html)
        self.assertIn('href="/app.css"', html)
        self.assertIn('src="/app.js"', html)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertIn("textContent", javascript)
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("outerHTML", javascript)
        self.assertNotIn("insertAdjacentHTML", javascript)

    def test_favicon_is_an_empty_successful_response(self) -> None:
        status, headers, content = self.request("GET", "/favicon.ico")

        self.assertEqual(status, 204)
        self.assertEqual(content, b"")
        self.assertEqual(headers["Content-Length"], "0")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_browser_disconnect_does_not_escape_as_server_error(self) -> None:
        class DisconnectedWriter:
            def write(self, content: bytes) -> None:
                raise ConnectionAbortedError("browser closed")

        handler = object.__new__(WorkbenchRequestHandler)
        handler.wfile = DisconnectedWriter()  # type: ignore[assignment]
        handler.close_connection = False

        handler._write_body(b"response")

        self.assertTrue(handler.close_connection)

    def test_static_allowlist_blocks_missing_files_and_directory_traversal(self) -> None:
        blocked_paths = (
            "/private.txt",
            "/missing.js",
            "/../private.txt",
            "/%2e%2e/private.txt",
            "/%2e%2e%2fprivate.txt",
        )

        for path in blocked_paths:
            with self.subTest(path=path):
                status, headers, content = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertEqual(
                    headers["Content-Type"],
                    "application/json; charset=utf-8",
                )
                self.assertEqual(json.loads(content), {"error": "Not found"})

    def test_invalid_host_is_rejected_before_static_or_manager_access(self) -> None:
        status, _, content = self.request(
            "GET",
            "/api/status",
            headers={
                "Host": "attacker.example",
                "X-Workbench-Token": TEST_TOKEN,
            },
        )

        self.assertEqual(status, 421)
        self.assertEqual(json.loads(content), {"error": "Invalid host"})
        self.assertEqual(self.manager.snapshot_calls, [])

    def test_status_requires_token_and_forwards_bounded_query(self) -> None:
        for token in (None, "wrong-token"):
            with self.subTest(token=token):
                headers = {} if token is None else {"X-Workbench-Token": token}
                status, _, content = self.request(
                    "GET",
                    "/api/status",
                    headers=headers,
                )
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(content), {"error": "Invalid token"})

        status, headers, content = self.request(
            "GET",
            "/api/status?after=7&wait=999",
            headers=self.api_headers(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.manager.snapshot_calls, [{"after_revision": 7, "wait_s": 20.0}])
        self.assertNotIn(TEST_TOKEN, content.decode("utf-8"))

    def test_invalid_status_query_returns_structured_error(self) -> None:
        for path in (
            "/api/status?after=not-an-integer",
            "/api/status?wait=nan",
        ):
            with self.subTest(path=path):
                status, _, content = self.request(
                    "GET",
                    path,
                    headers=self.api_headers(),
                )
                self.assertEqual(status, 400)
                self.assert_json_error(content, "INVALID_QUERY")
        self.assertEqual(self.manager.snapshot_calls, [])

    def test_start_requires_matching_origin_when_origin_is_present(self) -> None:
        invalid_origins = (
            "https://127.0.0.1:%d" % self.port,
            "http://attacker.example:%d" % self.port,
            "http://localhost:%d" % (self.port + 1),
            "http://localhost:not-a-port",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                status, _, content = self.request(
                    "POST",
                    "/api/runs",
                    body={"task": "Fix the test"},
                    headers=self.api_headers(origin=origin),
                )
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(content), {"error": "Invalid origin"})

        self.assertEqual(self.manager.start_calls, [])

        status, _, content = self.request(
            "POST",
            "/api/runs",
            body={"task": "Fix the test"},
            headers=self.api_headers(origin=f"http://localhost:{self.port}"),
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(content), {"run_id": RUN_ID})
        self.assertEqual(self.manager.start_calls, ["Fix the test"])

    def test_post_requires_token_before_manager_access(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/runs",
            body={"task": "Fix the test"},
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
        )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(content), {"error": "Invalid token"})
        self.assertEqual(self.manager.start_calls, [])

    def test_start_returns_run_id_and_maps_manager_errors(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/runs",
            body={"task": "Inspect the project"},
            headers=self.api_headers(),
        )

        self.assertEqual(status, 202)
        self.assertEqual(json.loads(content), {"run_id": RUN_ID})
        self.assertEqual(self.manager.start_calls, ["Inspect the project"])

        self.manager.start_error = WorkbenchError(
            409,
            "RUN_ACTIVE",
            "已有任务正在运行",
        )
        status, _, content = self.request(
            "POST",
            "/api/runs",
            body={"task": "Start another task"},
            headers=self.api_headers(),
        )
        self.assertEqual(status, 409)
        self.assert_json_error(content, "RUN_ACTIVE")

    def test_approval_endpoint_forwards_exact_decision(self) -> None:
        status, _, content = self.request(
            "POST",
            f"/api/runs/{RUN_ID}/approval",
            body={"approval_id": APPROVAL_ID, "approved": False},
            headers=self.api_headers(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {"ok": True})
        self.assertEqual(
            self.manager.approval_calls,
            [(RUN_ID, APPROVAL_ID, False)],
        )

    def test_cancel_endpoint_requires_an_empty_object(self) -> None:
        status, _, content = self.request(
            "POST",
            f"/api/runs/{RUN_ID}/cancel",
            body={},
            headers=self.api_headers(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {"ok": True})
        self.assertEqual(self.manager.cancel_calls, [RUN_ID])

        status, _, content = self.request(
            "POST",
            f"/api/runs/{RUN_ID}/cancel",
            body={"unexpected": True},
            headers=self.api_headers(),
        )
        self.assertEqual(status, 400)
        self.assert_json_error(content, "INVALID_FIELDS")
        self.assertEqual(self.manager.cancel_calls, [RUN_ID])

    def test_invalid_action_path_does_not_reach_manager(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/runs/not-a-valid-run-id/cancel",
            body={},
            headers=self.api_headers(),
        )

        self.assertEqual(status, 404)
        self.assertEqual(json.loads(content), {"error": "Not found"})
        self.assertEqual(self.manager.cancel_calls, [])

    def test_malformed_and_non_object_json_are_rejected(self) -> None:
        invalid_bodies = (b"{not json", b"[]")
        for raw_body in invalid_bodies:
            with self.subTest(raw_body=raw_body):
                status, _, content = self.request(
                    "POST",
                    "/api/runs",
                    raw_body=raw_body,
                    headers={
                        **self.api_headers(),
                        "Content-Type": "application/json; charset=utf-8",
                    },
                )
                self.assertEqual(status, 400)
                self.assert_json_error(content, "INVALID_JSON")

        self.assertEqual(self.manager.start_calls, [])

    def test_non_json_content_type_is_rejected(self) -> None:
        for content_type in ("text/plain", "application/jsonp"):
            with self.subTest(content_type=content_type):
                status, _, content = self.request(
                    "POST",
                    "/api/runs",
                    raw_body=b"",
                    headers={
                        **self.api_headers(),
                        "Content-Type": content_type,
                    },
                )
                self.assertEqual(status, 415)
                self.assert_json_error(content, "UNSUPPORTED_MEDIA_TYPE")
        self.assertEqual(self.manager.start_calls, [])

    def test_chunked_request_body_is_rejected(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/runs",
            raw_body=b"{}",
            headers={
                **self.api_headers(),
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
        )

        self.assertEqual(status, 400)
        self.assert_json_error(content, "UNSUPPORTED_TRANSFER_ENCODING")
        self.assertEqual(self.manager.start_calls, [])

    def test_request_larger_than_limit_is_rejected_without_reading_it(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/runs",
            headers={
                **self.api_headers(),
                "Content-Type": "application/json",
                "Content-Length": str(MAX_REQUEST_BYTES + 1),
            },
        )

        self.assertEqual(status, 413)
        self.assert_json_error(content, "REQUEST_TOO_LARGE")
        self.assertEqual(self.manager.start_calls, [])

    def test_unexpected_manager_error_returns_generic_json(self) -> None:
        self.manager.start_error = RuntimeError("private implementation detail")

        status, _, content = self.request(
            "POST",
            "/api/runs",
            body={"task": "Trigger failure"},
            headers=self.api_headers(),
        )

        self.assertEqual(status, 500)
        payload = self.assert_json_error(content, "INTERNAL_ERROR")
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private implementation detail", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_unexpected_status_error_returns_generic_json(self) -> None:
        self.manager.snapshot_error = RuntimeError("private status detail")

        status, _, content = self.request(
            "GET",
            "/api/status",
            headers=self.api_headers(),
        )

        self.assertEqual(status, 500)
        payload = self.assert_json_error(content, "INTERNAL_ERROR")
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private status detail", rendered)
        self.assertNotIn("Traceback", rendered)


if __name__ == "__main__":
    unittest.main()
