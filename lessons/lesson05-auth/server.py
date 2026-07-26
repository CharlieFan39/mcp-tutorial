# -*- coding: utf-8 -*-
"""Lesson 5：认证授权与写操作保护。

在 Lesson 4 的 HTTP 传输之上叠加安全层：
  - Bearer Token 认证 -> Principal(name, scopes)；
  - 工具声明 required_scope，tools/call 时比对；
  - 写操作双重开关：--enable-write（部署侧） + write scope（调用侧）；
  - 未开写开关时，写工具从 tools/list 中隐藏；
  - 审计日志（token 原文绝不入日志）。

运行：
  py server.py --http --port 8848                 # 写功能关闭
  py server.py --http --port 8848 --enable-write  # 写功能开启
"""
import argparse
import hmac
import json
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "tutorial-mcp-lesson05", "version": "0.5.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_SESSIONS = 64


def log(msg: str) -> None:
    print(f"[lesson05] {msg}", file=sys.stderr, flush=True)


def audit(event: str, **kv) -> None:
    """审计日志：谁/何时/何事/成败。注意：绝不记录 token 原文。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    detail = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"{ts} [AUDIT] {event:<10} {detail}", file=sys.stderr, flush=True)


def ok_response(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class InvalidParamsError(Exception):
    pass


class ToolExecutionError(Exception):
    pass


# ---------------------------------------------------------------------------
# 安全层：认证（你是谁）
# ---------------------------------------------------------------------------

@dataclass
class Principal:
    name: str
    scopes: List[str]


# 演示 token 表——仅教学用！生产必须放数据库/IdP，并支持过期与轮换。
DEMO_TOKENS = {
    "demo-token-123": Principal("analyst", ["read"]),
    "demo-token-456": Principal("operator", ["read", "write"]),
    "demo-token-789": Principal("admin", ["read", "write", "admin"]),
}


class Authenticator:
    def __init__(self, token_table: Dict[str, Principal]):
        self._tokens = token_table

    def authenticate(self, auth_header: Optional[str]) -> Optional[Principal]:
        """认证失败一律返回 None，调用方回 401；
        不区分'token 不存在'与'格式不对'，避免给探测者提供信息。"""
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):]
        # 恒定时间比较，防时序侧信道（对演示表逐一比较）
        for known, principal in self._tokens.items():
            if hmac.compare_digest(token, known):
                return principal
        return None


# ---------------------------------------------------------------------------
# 能力层：带 scope 与写开关的工具注册表
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]
    required_scope: str = "read"
    # 对照 tools.schema.json 的 requires_explicit_write_enable
    requires_explicit_write_enable: bool = False


class ToolRegistry:
    def __init__(self, write_enabled: bool):
        self.write_enabled = write_enabled  # 部署侧开关（--enable-write）
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def _visible(self, tool: Tool) -> bool:
        """写开关未开时，写工具直接隐藏——LLM 看不到就不会尝试调用。"""
        return self.write_enabled or not tool.requires_explicit_write_enable

    def list_tools(self) -> List[dict]:
        return [{"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema}
                for t in self._tools.values() if self._visible(t)]

    def call(self, name: str, arguments: dict, principal: Principal) -> dict:
        tool = self._tools.get(name)
        if tool is None or not self._visible(tool):
            # 隐藏的工具与不存在的工具返回同样的错误，不泄露其存在性
            raise InvalidParamsError(f"unknown tool: {name}")
        # 授权：调用侧作用域检查
        if tool.required_scope not in principal.scopes:
            audit("tool deny", principal=principal.name, tool=name,
                  reason="insufficient-scope")
            raise InvalidParamsError(
                f"insufficient scope: tool '{name}' requires '{tool.required_scope}'")
        for req in tool.input_schema.get("required", []):
            if req not in arguments:
                raise InvalidParamsError(f"missing required argument: {req}")
        try:
            output = tool.handler(arguments)
        except ToolExecutionError as exc:
            audit("tool call", principal=principal.name, tool=name, ok=False)
            return {"content": [{"type": "text", "text": f"tool failed: {exc}"}],
                    "isError": True}
        audit("tool call", principal=principal.name, tool=name, ok=True)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False}


# 演示用内存"实体库"，模拟 UnifiedModel 的实体存储
FAKE_ENTITY_STORE: Dict[str, dict] = {}


def build_registry(write_enabled: bool) -> ToolRegistry:
    reg = ToolRegistry(write_enabled)
    reg.register(Tool(
        name="query_metrics",
        description="查询服务指标（只读演示工具）。",
        input_schema={"type": "object", "required": ["service"],
                      "properties": {"service": {"type": "string"}}},
        handler=lambda args: {"service": args["service"], "latency_p99_ms": 42.0},
        required_scope="read",
    ))
    reg.register(Tool(
        name="entity_write",
        description="写入或更新一个实体（写操作，需显式启用写功能且持有 write 作用域）。",
        input_schema={"type": "object", "required": ["entity_id", "data"],
                      "properties": {"entity_id": {"type": "string"},
                                     "data": {"type": "object"}}},
        handler=lambda args: (FAKE_ENTITY_STORE.__setitem__(args["entity_id"], args["data"])
                              or {"written": args["entity_id"]}),
        required_scope="write",
        requires_explicit_write_enable=True,
    ))
    reg.register(Tool(
        name="entity_expire",
        description="使一个实体过期删除（写操作，需显式启用写功能且持有 write 作用域）。",
        input_schema={"type": "object", "required": ["entity_id"],
                      "properties": {"entity_id": {"type": "string"}}},
        handler=lambda args: {"expired": args["entity_id"],
                              "existed": FAKE_ENTITY_STORE.pop(args["entity_id"], None) is not None},
        required_scope="write",
        requires_explicit_write_enable=True,
    ))
    return reg


# ---------------------------------------------------------------------------
# 协议层：handle_message 增加 principal 参数（安全上下文随请求传递）
# ---------------------------------------------------------------------------

class MCPServer:
    def __init__(self, tools: ToolRegistry):
        self.initialized = False
        self.tools = tools

    def handle_message(self, msg: dict, principal: Principal):
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
        try:
            if method == "initialize":
                return ok_response(msg_id, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO})
            if method == "ping":
                return ok_response(msg_id, {})
            if method == "tools/list":
                return ok_response(msg_id, {"tools": self.tools.list_tools()})
            if method == "tools/call":
                name = params.get("name")
                if not name:
                    raise InvalidParamsError("params.name is required")
                return ok_response(msg_id, self.tools.call(
                    name, params.get("arguments") or {}, principal))
            return error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        except InvalidParamsError as exc:
            return error_response(msg_id, INVALID_PARAMS, str(exc))
        except Exception as exc:
            log(f"handler error: {exc}")
            return error_response(msg_id, INTERNAL_ERROR, str(exc))


# ---------------------------------------------------------------------------
# 传输层：HTTP（在 Lesson 4 基础上加认证拦截）
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self, tools: ToolRegistry):
        self._tools = tools
        self._sessions: "OrderedDict[str, MCPServer]" = OrderedDict()

    def create(self) -> str:
        if len(self._sessions) >= MAX_SESSIONS:
            self._sessions.popitem(last=False)
        sid = uuid.uuid4().hex
        self._sessions[sid] = MCPServer(self._tools)
        return sid

    def get(self, sid: str):
        return self._sessions.get(sid)

    def delete(self, sid: str) -> bool:
        return self._sessions.pop(sid, None) is not None


def make_handler(sessions: SessionManager, authn: Authenticator):
    class McpHttpHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log(f"http {self.address_string()} {fmt % args}")

        def _send_json(self, status: int, payload, extra_headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":  # 健康检查免认证（不含敏感信息）
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            # ---- 认证拦截：进入 JSON-RPC 层之前完成 ----
            principal = authn.authenticate(self.headers.get("Authorization"))
            if principal is None:
                audit("auth fail", remote=self.address_string())
                # 401 统一措辞，不泄露失败细节
                self._send_json(401, {"error": "unauthorized"},
                                {"WWW-Authenticate": "Bearer"})
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
                sid = sessions.create()
                extra["Mcp-Session-Id"] = sid
                audit("auth ok", principal=principal.name,
                      scopes=",".join(principal.scopes))
            server = sessions.get(sid or "")
            if server is None:
                self._send_json(400, error_response(
                    msg.get("id"), INVALID_REQUEST,
                    "missing or unknown Mcp-Session-Id (send initialize first)"))
                return

            resp = server.handle_message(msg, principal)
            if resp is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json(200, resp, extra)

        def do_DELETE(self):
            if self.path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            if authn.authenticate(self.headers.get("Authorization")) is None:
                self._send_json(401, {"error": "unauthorized"})
                return
            if sessions.delete(self.headers.get("Mcp-Session-Id", "")):
                self._send_json(200, {"terminated": True})
            else:
                self._send_json(404, {"error": "session not found"})

    return McpHttpHandler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lesson 5 MCP server with auth")
    parser.add_argument("--http", action="store_true",
                        help="use streamable-http transport (本课必须)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument("--enable-write", action="store_true",
                        help="显式开启写操作工具（部署侧开关）")
    args = parser.parse_args()

    if not args.http:
        parser.error("Lesson 5 演示 HTTP 认证，请加 --http 启动")

    registry = build_registry(write_enabled=args.enable_write)
    log(f"write tools {'ENABLED' if args.enable_write else 'disabled (hidden)'}")
    httpd = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(SessionManager(registry), Authenticator(DEMO_TOKENS)))
    log(f"listening on http://{args.host}:{args.port}/mcp")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
