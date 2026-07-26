# -*- coding: utf-8 -*-
"""Lesson 2：工具系统（tools/list + tools/call）。

在 Lesson 1 基础上新增：
  - Tool 数据结构与 ToolRegistry 注册表；
  - 迷你 JSON Schema 校验器（type/required/enum/properties/minimum/maxLength）；
  - 协议错误 vs 工具业务错误（isError）的区分。

运行：py server.py
"""
import json
import sys
import time
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "tutorial-mcp-lesson02", "version": "0.2.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(msg: str) -> None:
    print(f"[lesson02] {msg}", file=sys.stderr, flush=True)


def ok_response(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class InvalidParamsError(Exception):
    """参数校验失败 -> 分发器统一转 -32602。"""


class ToolExecutionError(Exception):
    """工具业务失败 -> 转成 isError:true 的 result，而不是 JSON-RPC error。"""


# ---------------------------------------------------------------------------
# 迷你 JSON Schema 校验器（教学版，覆盖最常用关键字）
# ---------------------------------------------------------------------------

def validate_schema(value: Any, schema: dict, path: str = "$") -> None:
    """校验失败抛 InvalidParamsError，消息中带字段路径，便于 LLM 自行纠正。"""
    expected = schema.get("type")
    if expected:
        py_types = {
            "object": dict, "array": list, "string": str,
            "boolean": bool, "integer": int, "number": (int, float),
        }[expected]
        # bool 是 int 的子类，需要排除
        if expected in ("integer", "number") and isinstance(value, bool):
            raise InvalidParamsError(f"{path}: expected {expected}, got boolean")
        if not isinstance(value, py_types):
            raise InvalidParamsError(f"{path}: expected {expected}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise InvalidParamsError(f"{path}: value {value!r} not in enum {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise InvalidParamsError(f"{path}: {value} < minimum {schema['minimum']}")

    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        raise InvalidParamsError(f"{path}: string longer than {schema['maxLength']}")

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                raise InvalidParamsError(f"{path}: missing required property '{req}'")
        for key, sub_schema in schema.get("properties", {}).items():
            if key in value:
                validate_schema(value[key], sub_schema, f"{path}.{key}")


# ---------------------------------------------------------------------------
# 能力层：工具注册表
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]
    enabled_by_default: bool = True


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")  # 启动即失败，尽早暴露
        self._tools[tool.name] = tool
        log(f"tool registered: {tool.name}")

    def list_tools(self) -> List[dict]:
        """tools/list 的载荷：只暴露元数据，不暴露 handler。"""
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in self._tools.values() if t.enabled_by_default
        ]

    def call(self, name: str, arguments: dict) -> dict:
        """tools/call 流水线：查找 -> 校验 -> 执行 -> 包装。"""
        tool = self._tools.get(name)
        if tool is None or not tool.enabled_by_default:
            raise InvalidParamsError(f"unknown tool: {name}")
        validate_schema(arguments, tool.input_schema, path=f"arguments({name})")
        try:
            output = tool.handler(arguments)
        except ToolExecutionError as exc:
            # 业务失败：包装为 isError，让 LLM 能看到原因并自行纠正
            return {"content": [{"type": "text", "text": f"tool failed: {exc}"}],
                    "isError": True}
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False}


# ---------------------------------------------------------------------------
# 示例工具实现（模拟 UnifiedModel 的查询场景）
# ---------------------------------------------------------------------------

def tool_query_metrics(args: dict) -> dict:
    """模拟指标查询：真实场景中这里会请求 Prometheus/SLS 等后端。"""
    random.seed(hash((args["service"], args["metric"])))  # 让结果可复现，便于教学验证
    now = int(time.time())
    points = [{"ts": now - 60 * i, "value": round(random.uniform(10, 500), 2)}
              for i in range(args.get("last_minutes", 5))]
    return {"service": args["service"], "metric": args["metric"], "points": points}


def tool_query_explain(args: dict) -> dict:
    """模拟查询解释：拆解 '<dataset> | where <cond>' 形式的查询语句。"""
    query = args["query"]
    parts = [p.strip() for p in query.split("|")]
    return {
        "query": query,
        "stages": [{"stage": i, "operation": p.split(" ")[0], "raw": p}
                   for i, p in enumerate(parts)],
        "estimated_cost": "low" if len(parts) <= 2 else "medium",
    }


def tool_calc(args: dict):
    a, b, op = args["a"], args["b"], args["op"]
    if op == "div" and b == 0:
        raise ToolExecutionError("division by zero")  # 业务错误示例
    return {"add": a + b, "sub": a - b, "mul": a * b,
            "div": a / b if b else None}[op]


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        name="query_metrics",
        description="查询指定服务的时序指标数据。当用户询问某服务的延迟、QPS、错误率等"
                    "监控数据时使用。返回按分钟采样的数据点列表。",
        input_schema={
            "type": "object",
            "required": ["service", "metric"],
            "properties": {
                "service": {"type": "string", "description": "服务名，如 checkout", "maxLength": 64},
                "metric": {"type": "string", "description": "指标名，如 latency_p99"},
                "last_minutes": {"type": "integer", "description": "取最近 N 分钟，默认 5", "minimum": 1},
            },
        },
        handler=tool_query_metrics,
    ))
    reg.register(Tool(
        name="query_explain",
        description="解释一条管道式查询语句的执行阶段与预估开销，但不真正执行。"
                    "当用户想了解查询会做什么、或调试查询语法时使用。",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "description": "管道式查询语句"}},
        },
        handler=tool_query_explain,
    ))
    reg.register(Tool(
        name="calc",
        description="四则运算计算器（教学演示用：展示 enum 校验与业务错误处理）。",
        input_schema={
            "type": "object",
            "required": ["op", "a", "b"],
            "properties": {
                "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
        },
        handler=tool_calc,
    ))
    return reg


# ---------------------------------------------------------------------------
# 协议层（在 Lesson 1 之上新增 tools/* 两个 handler）
# ---------------------------------------------------------------------------

class MCPServer:
    def __init__(self, tools: ToolRegistry):
        self.initialized = False
        self.tools = tools
        self._handlers = {
            "initialize": self._on_initialize,
            "ping": lambda p: {},
            "tools/list": self._on_tools_list,
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
                log("client initialized")
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
        return {
            "protocolVersion": PROTOCOL_VERSION,
            # 本课起声明 tools 能力，客户端由此得知可以调用 tools/*
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }

    def _on_tools_list(self, params: dict) -> dict:
        return {"tools": self.tools.list_tools()}

    def _on_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            raise InvalidParamsError("params.name is required")
        return self.tools.call(name, params.get("arguments") or {})


def stdio_main(server: MCPServer) -> None:
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


if __name__ == "__main__":
    stdio_main(MCPServer(build_registry()))
