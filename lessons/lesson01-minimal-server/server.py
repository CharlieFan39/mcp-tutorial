# -*- coding: utf-8 -*-
"""Lesson 1：最小 MCP 服务器（stdio 传输 + 生命周期）。

只做三件事：
  1. stdio 传输：stdin 逐行读 JSON-RPC，stdout 逐行写响应；
  2. 生命周期：initialize / notifications/initialized / ping；
  3. 协议兜底：解析错误、未知方法、未初始化拒绝。

运行：py server.py     （然后手动粘贴 JSON-RPC 报文，见 README.md）
"""
import json
import sys

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "tutorial-mcp-lesson01", "version": "0.1.0"}

# JSON-RPC 2.0 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(msg: str) -> None:
    """日志一律走 stderr —— stdout 是协议通道，绝不能污染。"""
    print(f"[lesson01] {msg}", file=sys.stderr, flush=True)


def ok_response(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class MinimalMCPServer:
    """协议层：方法分发 + 生命周期状态机。"""

    def __init__(self):
        self.initialized = False  # 收到 notifications/initialized 后置真
        # method -> handler 注册表，避免巨型 if-else
        self._handlers = {
            "initialize": self._on_initialize,
            "ping": self._on_ping,
        }

    # ---- 分发器 -------------------------------------------------------

    def handle_message(self, msg: dict):
        """处理一条报文。请求返回响应 dict；通知返回 None。"""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return error_response(msg.get("id") if isinstance(msg, dict) else None,
                                  INVALID_REQUEST, "not a valid JSON-RPC 2.0 request")
        method = msg["method"]
        params = msg.get("params") or {}
        msg_id = msg.get("id")

        # 无 id => 通知：只处理，永不回包（哪怕出错）
        if msg_id is None:
            self._on_notification(method, params)
            return None

        # 生命周期门禁：初始化完成前只允许 initialize / ping
        if not self.initialized and method not in ("initialize", "ping"):
            return error_response(msg_id, INVALID_REQUEST, "server not initialized")

        handler = self._handlers.get(method)
        if handler is None:
            return error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        try:
            return ok_response(msg_id, handler(params))
        except Exception as exc:  # 兜底：业务异常统一转 internal error
            log(f"handler error: {exc}")
            return error_response(msg_id, INTERNAL_ERROR, str(exc))

    # ---- 生命周期 -----------------------------------------------------

    def _on_initialize(self, params: dict) -> dict:
        client = params.get("clientInfo", {})
        log(f"initialize from client={client.get('name')} "
            f"protocol={params.get('protocolVersion')}")
        return {
            "protocolVersion": PROTOCOL_VERSION,
            # 本课还没有任何能力；Lesson 2/3 会填充 tools/resources
            "capabilities": {},
            "serverInfo": SERVER_INFO,
        }

    def _on_ping(self, params: dict) -> dict:
        return {}  # 规范要求 ping 返回空对象

    def _on_notification(self, method: str, params: dict) -> None:
        if method == "notifications/initialized":
            self.initialized = True
            log("client initialized, entering normal phase")
        else:
            log(f"ignore unknown notification: {method}")


def stdio_main(server: MinimalMCPServer) -> None:
    """传输层：stdin 每行一条请求，stdout 每行一条响应。"""
    log("stdio server started, waiting for requests ...")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = error_response(None, PARSE_ERROR, f"parse error: {exc}")
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue
        resp = server.handle_message(msg)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)
    log("stdin closed, exiting")


if __name__ == "__main__":
    stdio_main(MinimalMCPServer())
