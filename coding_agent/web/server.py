"""A small localhost-only HTTP server for the coding-agent workbench."""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from coding_agent.web.manager import RunManager, WorkbenchError


MAX_REQUEST_BYTES = 64 * 1024
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
RUN_ACTION_PATTERN = re.compile(
    r"^/api/runs/(?P<run_id>[a-f0-9]{32})/(?P<action>approval|cancel)$"
)

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        manager: RunManager,
        *,
        token: str | None = None,
        static_root: Path | None = None,
    ) -> None:
        self.manager = manager
        self.token = token or secrets.token_urlsafe(32)
        self.static_root = static_root or Path(__file__).resolve().parent / "static"
        super().__init__(server_address, WorkbenchRequestHandler)


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server: WorkbenchHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._valid_host():
            self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "Invalid host"})
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/status":
            if not self._valid_token():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid token"})
                return
            try:
                query = parse_qs(parsed.query)
                after = _optional_int(query.get("after", [None])[0], "after")
                wait_s = _optional_float(query.get("wait", ["0"])[0], "wait")
                snapshot = self.server.manager.snapshot(
                    after_revision=after,
                    wait_s=min(wait_s or 0.0, 20.0),
                )
            except WorkbenchError as error:
                self._send_workbench_error(error)
                return
            except ValueError as error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "INVALID_QUERY", "message": str(error)}},
                )
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "本地服务发生内部错误",
                        }
                    },
                )
                return
            self._send_json(HTTPStatus.OK, snapshot)
            return
        if path in STATIC_FILES:
            self._send_static(path)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._valid_host():
            self.close_connection = True
            self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "Invalid host"})
            return
        if not self._valid_origin():
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid origin"})
            return
        if not self._valid_token():
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid token"})
            return

        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            body = self._read_json_body()
            if path == "/api/runs":
                _require_exact_fields(body, {"task"})
                run_id = self.server.manager.start(body.get("task"))
                self._send_json(HTTPStatus.ACCEPTED, {"run_id": run_id})
                return

            match = RUN_ACTION_PATTERN.fullmatch(path)
            if match is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            run_id = match.group("run_id")
            action = match.group("action")
            if action == "approval":
                _require_exact_fields(body, {"approval_id", "approved"})
                self.server.manager.approve(
                    run_id,
                    body.get("approval_id"),
                    body.get("approved"),
                )
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            _require_exact_fields(body, set())
            self.server.manager.cancel(run_id)
            self._send_json(HTTPStatus.OK, {"ok": True})
        except WorkbenchError as error:
            self._send_workbench_error(error)
        except RequestError as error:
            # Some validation failures happen before the request body is consumed.
            # Closing the connection prevents leftover bytes from being parsed as
            # another HTTP request.
            self.close_connection = True
            self._send_json(
                error.status,
                {"error": {"code": error.code, "message": str(error)}},
            )
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "本地服务发生内部错误",
                    }
                },
            )

    def log_message(self, format: str, *arguments: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "UNSUPPORTED_TRANSFER_ENCODING",
                "不支持分块请求体",
            )
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.partition(";")[0].strip().casefold()
        if media_type != "application/json":
            raise RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "UNSUPPORTED_MEDIA_TYPE",
                "请求必须使用 application/json",
            )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_LENGTH",
                "Content-Length 无效",
            ) from error
        if length < 0:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_LENGTH",
                "Content-Length 无效",
            )
        if length > MAX_REQUEST_BYTES:
            raise RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "REQUEST_TOO_LARGE",
                "请求内容过大",
            )
        raw_body = self.rfile.read(length)
        try:
            decoded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "请求不是有效 JSON",
            ) from error
        if not isinstance(decoded, dict):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "JSON 顶层必须是对象",
            )
        return decoded

    def _send_static(self, path: str) -> None:
        file_name, content_type = STATIC_FILES[path]
        try:
            content = (self.server.static_root / file_name).read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if file_name == "index.html":
            content = content.replace(
                b"__WORKBENCH_TOKEN__",
                self.server.token.encode("ascii"),
            )
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self._write_body(content)

    def _send_workbench_error(self, error: WorkbenchError) -> None:
        self._send_json(
            error.status,
            {"error": {"code": error.code, "message": str(error)}},
        )

    def _send_json(self, status: int, payload: Any) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self._write_body(content)

    def _write_body(self, content: bytes) -> None:
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Closing or refreshing the page normally aborts an outstanding
            # long-poll request. It is not a server failure and should not
            # print a traceback into the user's terminal.
            self.close_connection = True

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _valid_token(self) -> bool:
        supplied = self.headers.get("X-Workbench-Token", "")
        return secrets.compare_digest(supplied, self.server.token)

    def _valid_host(self) -> bool:
        host_header = self.headers.get("Host", "")
        hostname = host_header.rsplit(":", 1)[0].strip("[]").casefold()
        return hostname in LOCAL_HOSTS

    def _valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            hostname = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "http" or hostname not in LOCAL_HOSTS:
            return False
        expected_port = self.server.server_address[1]
        return port == expected_port


class RequestError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def create_server(
    manager: RunManager,
    *,
    port: int = 0,
    token: str | None = None,
    static_root: Path | None = None,
) -> WorkbenchHTTPServer:
    if not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return WorkbenchHTTPServer(
        ("127.0.0.1", port),
        manager,
        token=token,
        static_root=static_root,
    )


def serve_workbench(
    workspace: Path,
    env_file: Path,
    *,
    port: int = 8_765,
    open_browser: bool = True,
) -> None:
    manager = RunManager(workspace, env_file)
    server = create_server(manager, port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Local workbench: {url}")
    print(f"Workspace: {manager.snapshot()['workspace']}")
    print("Press Ctrl+C to stop. API keys are never sent to the browser.")

    if open_browser:
        opener = threading.Timer(0.35, lambda: webbrowser.open(url))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nWorkbench stopped.")
    finally:
        try:
            manager.close()
        finally:
            server.server_close()


def _require_exact_fields(body: Mapping[str, Any], fields: set[str]) -> None:
    present = set(body)
    if present != fields:
        expected = ", ".join(sorted(fields)) or "no fields"
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            "INVALID_FIELDS",
            f"请求字段必须为: {expected}",
        )


def _optional_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < -1:
        raise ValueError(f"{name} must be at least -1")
    return parsed


def _optional_float(value: str | None, name: str) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed
