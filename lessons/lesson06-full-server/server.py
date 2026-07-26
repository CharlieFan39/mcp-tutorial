# -*- coding: utf-8 -*-
"""Lesson 6：配置驱动的完整 MCP 服务器（整合 Lesson 1~5 全部能力）。

能力清单（对照同目录 tools.schema.json 契约）：
  - 传输：stdio / streamable-http（config + 命令行切换）；
  - 方法：initialize、initialized、ping、tools/*、resources/*、prompts/*、logging/setLevel；
  - 安全：Bearer 认证、scope 授权、写操作双重开关、审计日志；
  - 工程：config.json 配置、--selfcheck 契约自检。

运行：
  py server.py --selfcheck            # 校验代码注册与契约一致
  py server.py                        # stdio 模式
  py server.py --http --port 8848     # HTTP 模式
  py server.py --http --enable-write  # 覆盖配置，开启写工具
"""
import argparse
import hmac
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = "2025-03-26"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_SESSIONS = 64
LOG_LEVELS = ["debug", "info", "warning", "error"]

_current_log_level = "info"


def log(msg: str, level: str = "info") -> None:
    if LOG_LEVELS.index(level) >= LOG_LEVELS.index(_current_log_level):
        print(f"[{level}] {msg}", file=sys.stderr, flush=True)


def audit(event: str, **kv) -> None:
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
# 迷你 JSON Schema 校验器（同 Lesson 2）
# ---------------------------------------------------------------------------

def validate_schema(value: Any, schema: dict, path: str = "$") -> None:
    expected = schema.get("type")
    if expected:
        py_types = {"object": dict, "array": list, "string": str,
                    "boolean": bool, "integer": int, "number": (int, float)}[expected]
        if expected in ("integer", "number") and isinstance(value, bool):
            raise InvalidParamsError(f"{path}: expected {expected}, got boolean")
        if not isinstance(value, py_types):
            raise InvalidParamsError(f"{path}: expected {expected}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidParamsError(f"{path}: value {value!r} not in enum {schema['enum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                raise InvalidParamsError(f"{path}: missing required property '{req}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                validate_schema(value[key], sub, f"{path}.{key}")


# ---------------------------------------------------------------------------
# 安全层（同 Lesson 5）
# ---------------------------------------------------------------------------

@dataclass
class Principal:
    name: str
    scopes: List[str]


# stdio 模式的本地调用者：进程边界即信任边界，授予全部作用域
LOCAL_PRINCIPAL = Principal("local", ["read", "write", "admin"])


class Authenticator:
    def __init__(self, token_entries: List[dict]):
        self._tokens = {e["token"]: Principal(e["principal"], e["scopes"])
                        for e in token_entries}

    def authenticate(self, auth_header: Optional[str]) -> Optional[Principal]:
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):]
        for known, principal in self._tokens.items():
            if hmac.compare_digest(token, known):
                return principal
        return None


# ---------------------------------------------------------------------------
# 能力层：工具
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]
    required_scope: str = "read"
    enabled_by_default: bool = True
    requires_explicit_write_enable: bool = False


class ToolRegistry:
    def __init__(self, write_enabled: bool, disabled: List[str]):
        self.write_enabled = write_enabled
        self._disabled = set(disabled)
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def all_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def _visible(self, tool: Tool) -> bool:
        if tool.name in self._disabled:
            return False
        if tool.requires_explicit_write_enable and not self.write_enabled:
            return False
        return True

    def list_tools(self) -> List[dict]:
        return [{"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema}
                for t in self._tools.values() if self._visible(t)]

    def call(self, name: str, arguments: dict, principal: Principal) -> dict:
        tool = self._tools.get(name)
        if tool is None or not self._visible(tool):
            raise InvalidParamsError(f"unknown tool: {name}")
        if tool.required_scope not in principal.scopes:
            audit("tool deny", principal=principal.name, tool=name,
                  reason="insufficient-scope")
            raise InvalidParamsError(
                f"insufficient scope: tool '{name}' requires '{tool.required_scope}'")
        validate_schema(arguments, tool.input_schema, path=f"arguments({name})")
        try:
            output = tool.handler(arguments)
        except ToolExecutionError as exc:
            audit("tool call", principal=principal.name, tool=name, ok=False)
            return {"content": [{"type": "text", "text": f"tool failed: {exc}"}],
                    "isError": True}
        audit("tool call", principal=principal.name, tool=name, ok=True)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False}


FAKE_ENTITY_STORE: Dict[str, dict] = {}


def _tool_query_metrics(args: dict) -> dict:
    import random
    random.seed(hash((args["service"], args.get("metric", "latency_p99"))))
    now = int(time.time())
    return {"service": args["service"],
            "metric": args.get("metric", "latency_p99"),
            "points": [{"ts": now - 60 * i, "value": round(random.uniform(10, 500), 2)}
                       for i in range(args.get("last_minutes", 5))]}


def _tool_query_explain(args: dict) -> dict:
    parts = [p.strip() for p in args["query"].split("|")]
    return {"query": args["query"],
            "stages": [{"stage": i, "operation": p.split(" ")[0], "raw": p}
                       for i, p in enumerate(parts)],
            "estimated_cost": "low" if len(parts) <= 2 else "medium"}


def _tool_entity_write(args: dict) -> dict:
    FAKE_ENTITY_STORE[args["entity_id"]] = args["data"]
    return {"written": args["entity_id"], "total_entities": len(FAKE_ENTITY_STORE)}


def _tool_entity_expire(args: dict) -> dict:
    existed = FAKE_ENTITY_STORE.pop(args["entity_id"], None) is not None
    return {"expired": args["entity_id"], "existed": existed}


def build_tools(write_enabled: bool, disabled: List[str]) -> ToolRegistry:
    reg = ToolRegistry(write_enabled, disabled)
    reg.register(Tool(
        name="query_metrics",
        description="查询指定服务的时序指标数据（只读）。",
        input_schema={"type": "object", "required": ["service"],
                      "properties": {"service": {"type": "string"},
                                     "metric": {"type": "string"},
                                     "last_minutes": {"type": "integer"}}},
        handler=_tool_query_metrics))
    reg.register(Tool(
        name="query_explain",
        description="解释管道式查询语句的执行阶段与开销，不真正执行（只读）。",
        input_schema={"type": "object", "required": ["query"],
                      "properties": {"query": {"type": "string"}}},
        handler=_tool_query_explain))
    reg.register(Tool(
        name="entity_write",
        description="写入或更新实体（写操作：需启用写功能且持有 write 作用域）。",
        input_schema={"type": "object", "required": ["entity_id", "data"],
                      "properties": {"entity_id": {"type": "string"},
                                     "data": {"type": "object"}}},
        handler=_tool_entity_write,
        required_scope="write", enabled_by_default=False,
        requires_explicit_write_enable=True))
    reg.register(Tool(
        name="entity_expire",
        description="使实体过期删除（写操作：需启用写功能且持有 write 作用域）。",
        input_schema={"type": "object", "required": ["entity_id"],
                      "properties": {"entity_id": {"type": "string"}}},
        handler=_tool_entity_expire,
        required_scope="write", enabled_by_default=False,
        requires_explicit_write_enable=True))
    return reg


# ---------------------------------------------------------------------------
# 能力层：资源（同 Lesson 3）
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str
    provider: Callable[[], str]


@dataclass
class ResourceTemplate:
    uri_template: str
    name: str
    description: str
    mime_type: str
    provider: Callable[[Dict[str, str]], str]


def template_to_regex(uri_template: str) -> "re.Pattern":
    pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(uri_template))
    return re.compile(f"^{pattern}$")


class ResourceRegistry:
    def __init__(self):
        self._static: Dict[str, Resource] = {}
        self._templates: List[ResourceTemplate] = []

    def register(self, res: Resource) -> None:
        self._static[res.uri] = res

    def register_template(self, tpl: ResourceTemplate) -> None:
        self._templates.append(tpl)

    def all_uris(self) -> List[str]:
        return list(self._static) + [t.uri_template for t in self._templates]

    def list_resources(self) -> List[dict]:
        return [{"uri": r.uri, "name": r.name, "description": r.description,
                 "mimeType": r.mime_type} for r in self._static.values()]

    def list_templates(self) -> List[dict]:
        return [{"uriTemplate": t.uri_template, "name": t.name,
                 "description": t.description, "mimeType": t.mime_type}
                for t in self._templates]

    def read(self, uri: str) -> dict:
        res = self._static.get(uri)
        if res is not None:
            return {"contents": [{"uri": uri, "mimeType": res.mime_type,
                                  "text": res.provider()}]}
        for tpl in self._templates:
            m = template_to_regex(tpl.uri_template).match(uri)
            if m:
                return {"contents": [{"uri": uri, "mimeType": tpl.mime_type,
                                      "text": tpl.provider(m.groupdict())}]}
        raise InvalidParamsError(f"unknown resource uri: {uri}")


FAKE_WORKSPACES = {
    "demo": {"entities": 128, "datasets": ["logs", "metrics", "traces"]},
    "prod": {"entities": 4096, "datasets": ["logs", "metrics", "traces", "events"]},
}


def _res_overview(params: Dict[str, str]) -> str:
    ws = params["workspace"]
    info = FAKE_WORKSPACES.get(ws)
    if info is None:
        raise InvalidParamsError(f"workspace not found: {ws}")
    return (f"# Workspace: {ws}\n\n- entities: {info['entities']}\n"
            f"- datasets: {', '.join(info['datasets'])}\n")


def _res_schema_index(params: Dict[str, str]) -> str:
    ws = params["workspace"]
    info = FAKE_WORKSPACES.get(ws)
    if info is None:
        raise InvalidParamsError(f"workspace not found: {ws}")
    return "\n".join([f"# Schema Index of {ws}", ""] +
                     [f"- dataset `{d}`: fields = [timestamp, service, value]"
                      for d in info["datasets"]]) + "\n"


def build_resources() -> ResourceRegistry:
    reg = ResourceRegistry()
    reg.register(Resource(
        uri="tutorial://guide/getting-started", name="getting-started",
        description="服务器使用入门指南", mime_type="text/markdown",
        provider=lambda: "# Getting Started\n\n先读 overview，再看 schema-index。\n"))
    reg.register_template(ResourceTemplate(
        uri_template="tutorial://workspace/{workspace}/overview",
        name="workspace-overview", description="工作区概览",
        mime_type="text/markdown", provider=_res_overview))
    reg.register_template(ResourceTemplate(
        uri_template="tutorial://workspace/{workspace}/schema-index",
        name="workspace-schema-index", description="工作区数据结构索引",
        mime_type="text/markdown", provider=_res_schema_index))
    return reg


# ---------------------------------------------------------------------------
# 能力层：提示模板（prompts/list + prompts/get）
# ---------------------------------------------------------------------------

PROMPTS = {
    "incident-triage": {
        "description": "对指定服务做故障初步分诊的提示模板",
        "arguments": [{"name": "service", "description": "服务名", "required": True}],
        "template": ("你是 SRE 专家。请对服务 {service} 做故障分诊：\n"
                     "1. 先用 query_metrics 查看 latency_p99 与 error_rate；\n"
                     "2. 读取 tutorial://workspace/demo/overview 了解拓扑；\n"
                     "3. 给出三个最可能的根因假设及验证步骤。"),
    },
    "query-writing": {
        "description": "指导编写管道式查询语句的提示模板",
        "arguments": [{"name": "goal", "description": "查询目标描述", "required": True}],
        "template": ("请为以下目标编写管道式查询，并先用 query_explain 验证：\n目标：{goal}"),
    },
}


class PromptRegistry:
    def list_prompts(self) -> List[dict]:
        return [{"name": name, "description": p["description"], "arguments": p["arguments"]}
                for name, p in PROMPTS.items()]

    def get(self, name: str, arguments: Dict[str, str]) -> dict:
        p = PROMPTS.get(name)
        if p is None:
            raise InvalidParamsError(f"unknown prompt: {name}")
        for arg in p["arguments"]:
            if arg["required"] and arg["name"] not in arguments:
                raise InvalidParamsError(f"missing prompt argument: {arg['name']}")
        text = p["template"].format(**arguments)
        return {"description": p["description"],
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": text}}]}


# ---------------------------------------------------------------------------
# 协议层：完整分发器
# ---------------------------------------------------------------------------

class MCPServer:
    def __init__(self, cfg: dict, tools: ToolRegistry,
                 resources: ResourceRegistry, prompts: PromptRegistry):
        self.initialized = False
        self.cfg = cfg
        self.tools = tools
        self.resources = resources
        self.prompts = prompts

    def handle_message(self, msg: dict, principal: Principal):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return error_response(msg.get("id") if isinstance(msg, dict) else None,
                                  INVALID_REQUEST, "not a valid JSON-RPC 2.0 request")
        method, params, msg_id = msg["method"], msg.get("params") or {}, msg.get("id")
        if msg_id is None:
            if method == "notifications/initialized":
                self.initialized = True
                log("client initialized", "debug")
            return None
        if not self.initialized and method not in ("initialize", "ping"):
            return error_response(msg_id, INVALID_REQUEST, "server not initialized")
        try:
            result = self._dispatch(method, params, principal)
            if result is _NOT_FOUND:
                return error_response(msg_id, METHOD_NOT_FOUND,
                                      f"Method not found: {method}")
            return ok_response(msg_id, result)
        except InvalidParamsError as exc:
            return error_response(msg_id, INVALID_PARAMS, str(exc))
        except Exception as exc:
            log(f"handler error: {exc}", "error")
            return error_response(msg_id, INTERNAL_ERROR, str(exc))

    def _dispatch(self, method: str, params: dict, principal: Principal):
        global _current_log_level
        if method == "initialize":
            return {"protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                        "logging": {}},
                    "serverInfo": self.cfg["server"]}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools.list_tools()}
        if method == "tools/call":
            name = params.get("name")
            if not name:
                raise InvalidParamsError("params.name is required")
            return self.tools.call(name, params.get("arguments") or {}, principal)
        if method == "resources/list":
            return {"resources": self.resources.list_resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": self.resources.list_templates()}
        if method == "resources/read":
            uri = params.get("uri")
            if not uri:
                raise InvalidParamsError("params.uri is required")
            return self.resources.read(uri)
        if method == "prompts/list":
            return {"prompts": self.prompts.list_prompts()}
        if method == "prompts/get":
            name = params.get("name")
            if not name:
                raise InvalidParamsError("params.name is required")
            return self.prompts.get(name, params.get("arguments") or {})
        if method == "logging/setLevel":
            # 管理操作：授权横切到非工具方法的示例
            if "admin" not in principal.scopes:
                raise InvalidParamsError("insufficient scope: requires 'admin'")
            level = params.get("level")
            if level not in LOG_LEVELS:
                raise InvalidParamsError(f"invalid level: {level}, expect one of {LOG_LEVELS}")
            _current_log_level = level
            audit("set level", principal=principal.name, level=level)
            return {}
        return _NOT_FOUND


_NOT_FOUND = object()  # 分发器哨兵：区分"方法不存在"与"合法的 None 结果"


# ---------------------------------------------------------------------------
# 契约自检：代码注册表 vs tools.schema.json 的 contract_instance
# ---------------------------------------------------------------------------

def selfcheck(tools: ToolRegistry, resources: ResourceRegistry) -> bool:
    with open(os.path.join(BASE_DIR, "tools.schema.json"), encoding="utf-8") as f:
        contract = json.load(f)["contract_instance"]

    ok = True
    declared_tools = {t["name"]: t for t in contract["tools"]}
    actual_tools = {t.name: t for t in tools.all_tools()}
    for name in declared_tools.keys() | actual_tools.keys():
        if name not in actual_tools:
            print(f"  FAIL tool '{name}' declared in contract but not registered")
            ok = False
        elif name not in declared_tools:
            print(f"  FAIL tool '{name}' registered but missing from contract")
            ok = False
        else:
            d, a = declared_tools[name], actual_tools[name]
            if d["enabled_by_default"] != a.enabled_by_default or \
               d.get("requires_explicit_write_enable", False) != a.requires_explicit_write_enable:
                print(f"  FAIL tool '{name}' flags mismatch between contract and code")
                ok = False
            else:
                print(f"  OK   tool '{name}'")

    declared_uris = {r["uri_template"] for r in contract["resources"]}
    actual_uris = set(resources.all_uris())
    for uri in declared_uris | actual_uris:
        if uri not in actual_uris:
            print(f"  FAIL resource '{uri}' declared but not registered")
            ok = False
        elif uri not in declared_uris:
            print(f"  FAIL resource '{uri}' registered but missing from contract")
            ok = False
        else:
            print(f"  OK   resource '{uri}'")

    print(f"selfcheck {'PASSED' if ok else 'FAILED'}")
    return ok


# ---------------------------------------------------------------------------
# 传输层
# ---------------------------------------------------------------------------

def stdio_main(server: MCPServer) -> None:
    log("stdio server started (auth disabled: local trust boundary)")
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
        resp = server.handle_message(msg, LOCAL_PRINCIPAL)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


class SessionManager:
    def __init__(self, factory: Callable[[], MCPServer]):
        self._factory = factory
        self._sessions: "OrderedDict[str, MCPServer]" = OrderedDict()

    def create(self) -> str:
        if len(self._sessions) >= MAX_SESSIONS:
            self._sessions.popitem(last=False)
        sid = uuid.uuid4().hex
        self._sessions[sid] = self._factory()
        return sid

    def get(self, sid: str):
        return self._sessions.get(sid)

    def delete(self, sid: str) -> bool:
        return self._sessions.pop(sid, None) is not None


def make_handler(sessions: SessionManager, authn: Optional[Authenticator]):
    class McpHttpHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log(f"http {self.address_string()} {fmt % args}", "debug")

        def _send_json(self, status: int, payload, extra_headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> Optional[Principal]:
            if authn is None:  # 配置里 auth.enabled=false
                return LOCAL_PRINCIPAL
            return authn.authenticate(self.headers.get("Authorization"))

        def do_GET(self):
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            principal = self._auth()
            if principal is None:
                audit("auth fail", remote=self.address_string())
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
            if self._auth() is None:
                self._send_json(401, {"error": "unauthorized"})
                return
            if sessions.delete(self.headers.get("Mcp-Session-Id", "")):
                self._send_json(200, {"terminated": True})
            else:
                self._send_json(404, {"error": "session not found"})

    return McpHttpHandler


# ---------------------------------------------------------------------------
# 入口：加载配置 -> 装配 -> 启动
# ---------------------------------------------------------------------------

def main() -> int:
    global _current_log_level
    parser = argparse.ArgumentParser(description="Lesson 6 full MCP server")
    parser.add_argument("--config", default=os.path.join(BASE_DIR, "config.json"))
    parser.add_argument("--http", action="store_true", help="覆盖配置：使用 HTTP 传输")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--enable-write", action="store_true", help="覆盖配置：开启写工具")
    parser.add_argument("--selfcheck", action="store_true", help="只做契约自检，不启动服务")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    _current_log_level = cfg.get("logging", {}).get("level", "info")

    # 命令行覆盖配置文件（配置是基线，命令行是临时覆盖）
    write_enabled = args.enable_write or cfg["tools"].get("write_enabled", False)
    tools = build_tools(write_enabled, cfg["tools"].get("disabled", []))
    resources = build_resources()
    prompts = PromptRegistry()

    if args.selfcheck:
        return 0 if selfcheck(tools, resources) else 1

    log(f"write tools {'ENABLED' if write_enabled else 'disabled (hidden)'}")

    if args.http or cfg["transport"]["default"] == "streamable-http":
        host = args.host or cfg["transport"]["http"]["host"]
        port = args.port or cfg["transport"]["http"]["port"]
        authn = Authenticator(cfg["auth"]["tokens"]) if cfg["auth"]["enabled"] else None
        factory = lambda: MCPServer(cfg, tools, resources, prompts)
        httpd = ThreadingHTTPServer((host, port),
                                    make_handler(SessionManager(factory), authn))
        log(f"listening on http://{host}:{port}/mcp "
            f"(auth {'on' if authn else 'off'})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log("shutting down")
    else:
        stdio_main(MCPServer(cfg, tools, resources, prompts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
