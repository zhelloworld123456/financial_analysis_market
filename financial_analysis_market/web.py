from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .engine import AnalysisEngine
from .errors import DataSourceError, FinancialAnalysisError, InvalidStockQuery


LOGGER = logging.getLogger(__name__)
STATIC_PATH = Path(__file__).with_name("static")
INDEX_PATH = STATIC_PATH / "index.html"
STATIC_FILES = {
    "/static/app.css": (STATIC_PATH / "app.css", "text/css; charset=utf-8"),
    "/static/app.js": (STATIC_PATH / "app.js", "application/javascript; charset=utf-8"),
}


class AnalysisHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: AnalysisEngine) -> None:
        super().__init__(address, AnalysisHandler)
        self.engine = engine


class AnalysisHandler(BaseHTTPRequestHandler):
    server: AnalysisHTTPServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send_bytes(200, INDEX_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/api/capabilities":
            self._send_json(200, self.server.engine.capabilities())
        elif path == "/api/chat/session":
            session_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            try:
                self._send_json(200, self.server.engine.chat_snapshot(session_id))
            except ValueError as exc:
                self._send_json(404, {"error": str(exc)})
        elif path in STATIC_FILES:
            file_path, content_type = STATIC_FILES[path]
            self._send_bytes(200, file_path.read_bytes(), content_type)
        else:
            self._send_json(404, {"error": "页面不存在"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/api/analyze", "/api/screen", "/api/chat/stream"):
            self._send_json(404, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > (16384 if path == "/api/chat/stream" else 4096):
                raise ValueError("请求体为空或过大")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            if path == "/api/chat/stream":
                message = payload.get("message")
                query = payload.get("query", "")
                session_id = payload.get("session_id")
                if not isinstance(message, str) or not message.strip() or len(message) > 2000:
                    raise ValueError("message 必须是 1～2000 字的非空字符串")
                if not isinstance(query, str) or len(query) > 40:
                    raise ValueError("query 必须是最多 40 字的股票名称或代码")
                if session_id is not None and not isinstance(session_id, str):
                    raise ValueError("session_id 必须是字符串")
                self._send_ndjson(
                    self.server.engine.chat(message.strip(), query.strip(), session_id or None)
                )
                return
            if path == "/api/analyze":
                if not isinstance(payload.get("query"), str):
                    raise ValueError("query 必须是股票名称或代码字符串")
                use_ai = payload.get("use_ai", True)
                if not isinstance(use_ai, bool):
                    raise ValueError("use_ai 必须是布尔值")
                result = self.server.engine.analyze(payload["query"], use_ai=use_ai)
            else:
                limit = payload.get("limit", 20)
                if not isinstance(limit, int) or isinstance(limit, bool):
                    raise ValueError("limit 必须是 5～50 的整数")
                result = self.server.engine.screen(limit)
            self._send_json(200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"请求格式错误：{exc}"})
        except InvalidStockQuery as exc:
            self._send_json(400, {"error": str(exc)})
        except DataSourceError as exc:
            self._send_json(502, {"error": str(exc)})
        except FinancialAnalysisError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            LOGGER.exception("分析请求失败")
            self._send_json(500, {"error": "服务内部错误，请查看终端日志"})

    def _send_ndjson(self, events: Any) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        try:
            for event in events:
                body = json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOGGER.exception("对话事件流中断")
            try:
                body = json.dumps({"type": "error", "error": "对话事件流中断，请查看终端日志"}, ensure_ascii=False).encode("utf-8") + b"\n"
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), message % args)


def serve(engine: AnalysisEngine, host: str = "127.0.0.1", port: int = 8000) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = AnalysisHTTPServer((host, port), engine)
    print(f"Financial Analysis Market 已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
