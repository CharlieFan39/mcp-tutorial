# -*- coding: utf-8 -*-
"""Lesson 4：HTTP 传输层（streamable-http）。

演示分层架构的价值：MCPServer（协议层）与 Lesson 2 完全一致，
本课只新增 HttpTransport；stdio 与 http 由命令行参数切换。

运行：
  py server.py                      # stdio 模式
  py server.py --http --port 8847   # HTTP 模式，端点 POST /mcp
"""
import argparse
import json
import sys
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "tutorial-mcp-lesson04", "version": "0.4.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_SESSIONS = 64  # 会话表上限，超出淘汰最旧（生产应使用 TTL）


def log(msg: str) -> None:
    print(f"[lesson04] {msg}", file=sys.stderr, flush=True)


def ok_response(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class InvalidParamsError(Exception):
    pass


class ToolExecutionError(Exception):
    pass


# ---------------------------------------------------------------------------
# 能力层：极简工具注册表（同 Lesson 2 模式，工具从简）
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def list_tools(self) -> List[dict]:
        return [{"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema} for t in self._tools.values()]

    def call(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise InvalidParamsError(f"unknown tool: {name}")
        for req in tool.input_schema.get("required", []):
            if req not in arguments:
                raise InvalidParamsError(f"missing required argument: {req}")
        try:
            output = tool.handler(arguments)
        except ToolExecutionError as exc:
            return {"content": [{"type": "text", "text": f"tool failed: {exc}"}],
                    "isError": True}
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False}


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        name="echo",
        description="原样返回输入文本（用于验证传输层连通性）。",
        input_schema={"type": "object", "required": ["text"],
                      "properties": {"text": {"type": "string"}}},
        handler=lambda args: args["text"],
    ))
    reg.register(Tool(
        name="server_time",
        description="返回服务器当前时间（UTC 秒）。",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: {"epoch": __import__("time").time()},
    ))
    return reg


# ---------------------------------------------------------------------------
# 协议层（与 Lesson 2 相同——本课的重点是它零改动即可换传输）
# ---------------------------------------------------------------------------

class MCPServer:
    def __init__(self, tools: ToolRegistry):
        self.initialized = False
        self.tools = tools
        self._handlers = {
            "initialize": self._on_initialize,
            "ping": lambda p: {},
            "tools/list": lambda p: {"tools": self.tools.list_tools()},
            "tools/call": self._on_tools_call,
        }

    def handle_message(self, msg: dict):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return error_response(msg.get("id") if isinstance(msg, dict) else None,
                                  INVALID_REQUEST, "not a valid JSON-RPC 2.0 request")
        method, params, msg_id = msg["method"], msg.get("params") or {}, msg.get("id")
        if msg_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            return None
        if not self.initialized and method not in ("initialize", "ping"):
            return error_response(msg_id, INVALID_REQUEST, "server not initialized")
        handler = self._handlers.get(method)
        if handler is None:
            return error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        try:
            return ok_response(msg_id, handler(params))
        except InvalidParamsError as exc:
            return error_response(msg_id, INVALID_PARAMS, str(exc))
        except Exception as exc:
            log(f"handler error: {exc}")
            return error_response(msg_id, INTERNAL_ERROR, str(exc))

    def _on_initialize(self, params: dict) -> dict:
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO}

    def _on_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            raise InvalidParamsError("params.name is required")
        return self.tools.call(name, params.get("arguments") or {})


# ---------------------------------------------------------------------------
# 传输层 A：stdio（与 Lesson 1 相同）
# ---------------------------------------------------------------------------

def stdio_main(tools: ToolRegistry) -> None:
    server = MCPServer(tools)
    log("stdio server started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps(error_response(None, PARSE_ERROR, f"parse error: {exc}")),
                  flush=True)
            continue
        resp = server.handle_message(msg)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------------------
# 传输层 B：streamable-http（本课新增）
# ---------------------------------------------------------------------------

class SessionManager:
    """会话表：session_id -> 独立的 MCPServer 实例（各自的生命周期状态互不干扰）。"""

    def __init__(self, tools: ToolRegistry):
        self._tools = tools
        self._sessions: "OrderedDict[str, MCPServer]" = OrderedDict()

    def create(self) -> str:
        if len(self._sessions) >= MAX_SESSIONS:
            evicted, _ = self._sessions.popitem(last=False)  # 淘汰最旧
            log(f"session evicted: {evicted}")
        sid = uuid.uuid4().hex
        self._sessions[sid] = MCPServer(self._tools)
        log(f"session created: {sid}")
        return sid

    def get(self, sid: str):
        return self._sessions.get(sid)

    def delete(self, sid: str) -> bool:
        return self._sessions.pop(sid, None) is not None


def make_handler(sessions: SessionManager):
    class McpHttpHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 重定向默认访问日志到 stderr
            log(f"http {self.address_string()} {fmt % args}")

        def _send_json(self, status: int, payload, extra_headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok", "server": SERVER_INFO["name"]})
            else:
                self._send_json(404, {"error": "not found"})

        def do_DELETE(self):
            if self.path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            sid = self.headers.get("Mcp-Session-Id", "")
            if sessions.delete(sid):
                self._send_json(200, {"terminated": True})
            else:
                self._send_json(404, {"error": "session not found"})

        def do_POST(self):
            if self.path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                msg = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, error_response(None, PARSE_ERROR, f"parse error: {exc}"))
                return

            sid = self.headers.get("Mcp-Session-Id")
            extra = {}
            if msg.get("method") == "initialize":
                # initialize 开启新会话，会话 ID 通过响应头下发
                sid = sessions.create()
                extra["Mcp-Session-Id"] = sid
            server = sessions.get(sid or "")
            if server is None:
                self._send_json(400, error_response(
                    msg.get("id"), INVALID_REQUEST,
                    "missing or unknown Mcp-Session-Id (send initialize first)"))
                return

            resp = server.handle_message(msg)
            if resp is None:
                # 通知：202 Accepted，空 body
                self.send_response(202)
                self.send_header("Content-Length", "0")
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
            else:
                self._send_json(200, resp, extra)

    return McpHttpHandler


def http_main(tools: ToolRegistry, host: str, port: int) -> None:
    sessions = SessionManager(tools)
    httpd = ThreadingHTTPServer((host, port), make_handler(sessions))
    log(f"streamable-http server listening on http://{host}:{port}/mcp")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lesson 4 MCP server")
    parser.add_argument("--http", action="store_true", help="use streamable-http transport")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8847)
    args = parser.parse_args()

    registry = build_registry()
    if args.http:
        http_main(registry, args.host, args.port)
    else:
        stdio_main(registry)
