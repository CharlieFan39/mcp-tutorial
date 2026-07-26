# -*- coding: utf-8 -*-
"""Lesson 3：资源系统（resources/list + resources/templates/list + resources/read）。

在 Lesson 2 基础上新增：
  - Resource / ResourceTemplate 数据结构与 ResourceRegistry；
  - URI 模板匹配：{param} -> 正则命名组；
  - initialize 声明 resources 能力。

运行：py server.py
"""
import json
import re
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "tutorial-mcp-lesson03", "version": "0.3.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(msg: str) -> None:
    print(f"[lesson03] {msg}", file=sys.stderr, flush=True)


def ok_response(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class InvalidParamsError(Exception):
    pass


# ---------------------------------------------------------------------------
# 能力层：资源注册表
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    """静态资源：URI 固定，内容由 provider 函数惰性生成。"""
    uri: str
    name: str
    description: str
    mime_type: str
    provider: Callable[[], str]          # 无参：URI 即唯一定位


@dataclass
class ResourceTemplate:
    """模板资源：URI 含 {param} 占位符，读取时提取参数传给 provider。"""
    uri_template: str                    # 如 tutorial://workspace/{workspace}/overview
    name: str
    description: str
    mime_type: str
    provider: Callable[[Dict[str, str]], str]  # 入参为 URI 中提取的参数


def template_to_regex(uri_template: str) -> "re.Pattern":
    """把 {param} 转成命名组：tutorial://ws/{id}/x -> tutorial://ws/(?P<id>[^/]+)/x"""
    pattern = re.escape(uri_template)
    pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", pattern)
    return re.compile(f"^{pattern}$")


class ResourceRegistry:
    def __init__(self):
        self._static: Dict[str, Resource] = {}
        self._templates: List[ResourceTemplate] = []

    def register(self, res: Resource) -> None:
        self._static[res.uri] = res
        log(f"resource registered: {res.uri}")

    def register_template(self, tpl: ResourceTemplate) -> None:
        self._templates.append(tpl)
        log(f"resource template registered: {tpl.uri_template}")

    def list_resources(self) -> List[dict]:
        return [{"uri": r.uri, "name": r.name, "description": r.description,
                 "mimeType": r.mime_type} for r in self._static.values()]

    def list_templates(self) -> List[dict]:
        return [{"uriTemplate": t.uri_template, "name": t.name,
                 "description": t.description, "mimeType": t.mime_type}
                for t in self._templates]

    def read(self, uri: str) -> dict:
        """先精确匹配静态资源，再逐个尝试模板。"""
        res = self._static.get(uri)
        if res is not None:
            return self._wrap(uri, res.mime_type, res.provider())
        for tpl in self._templates:
            m = template_to_regex(tpl.uri_template).match(uri)
            if m:
                return self._wrap(uri, tpl.mime_type, tpl.provider(m.groupdict()))
        raise InvalidParamsError(f"unknown resource uri: {uri}")

    @staticmethod
    def _wrap(uri: str, mime_type: str, text: str) -> dict:
        return {"contents": [{"uri": uri, "mimeType": mime_type, "text": text}]}


# ---------------------------------------------------------------------------
# 示例资源（模拟 UnifiedModel 的 overview / schema-index）
# ---------------------------------------------------------------------------

FAKE_WORKSPACES = {
    "demo": {"entities": 128, "datasets": ["logs", "metrics", "traces"]},
    "prod": {"entities": 4096, "datasets": ["logs", "metrics", "traces", "events"]},
}


def provide_getting_started() -> str:
    return ("# Getting Started\n\n"
            "本服务器提供工作区数据的只读视图。\n"
            "先读 overview 了解工作区，再用 schema-index 查看数据结构。\n")


def provide_overview(params: Dict[str, str]) -> str:
    ws = params["workspace"]
    info = FAKE_WORKSPACES.get(ws)
    if info is None:
        # 资源不存在也走 InvalidParamsError -> -32602
        raise InvalidParamsError(f"workspace not found: {ws}")
    return (f"# Workspace: {ws}\n\n"
            f"- entities: {info['entities']}\n"
            f"- datasets: {', '.join(info['datasets'])}\n")


def provide_schema_index(params: Dict[str, str]) -> str:
    ws = params["workspace"]
    info = FAKE_WORKSPACES.get(ws)
    if info is None:
        raise InvalidParamsError(f"workspace not found: {ws}")
    lines = [f"# Schema Index of {ws}", ""]
    lines += [f"- dataset `{d}`: fields = [timestamp, service, value]"
              for d in info["datasets"]]
    return "\n".join(lines) + "\n"


def build_resources() -> ResourceRegistry:
    reg = ResourceRegistry()
    reg.register(Resource(
        uri="tutorial://guide/getting-started",
        name="getting-started",
        description="服务器使用入门指南",
        mime_type="text/markdown",
        provider=provide_getting_started,
    ))
    # 对照 umodel://workspace/{workspace}/overview
    reg.register_template(ResourceTemplate(
        uri_template="tutorial://workspace/{workspace}/overview",
        name="workspace-overview",
        description="指定工作区的概览（实体数、数据集列表）",
        mime_type="text/markdown",
        provider=provide_overview,
    ))
    reg.register_template(ResourceTemplate(
        uri_template="tutorial://workspace/{workspace}/schema-index",
        name="workspace-schema-index",
        description="指定工作区的数据结构索引",
        mime_type="text/markdown",
        provider=provide_schema_index,
    ))
    return reg


# ---------------------------------------------------------------------------
# 协议层（新增 resources/* 三个 handler；工具部分从简仅留 calc）
# ---------------------------------------------------------------------------

class MCPServer:
    def __init__(self, resources: ResourceRegistry):
        self.initialized = False
        self.resources = resources
        self._handlers = {
            "initialize": self._on_initialize,
            "ping": lambda p: {},
            "resources/list": lambda p: {"resources": self.resources.list_resources()},
            "resources/templates/list":
                lambda p: {"resourceTemplates": self.resources.list_templates()},
            "resources/read": self._on_resources_read,
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
            # 声明 resources 能力（subscribe/listChanged 是进阶特性，本教程不实现）
            "capabilities": {
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": SERVER_INFO,
        }

    def _on_resources_read(self, params: dict) -> dict:
        uri = params.get("uri")
        if not uri:
            raise InvalidParamsError("params.uri is required")
        return self.resources.read(uri)


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
    stdio_main(MCPServer(build_resources()))
