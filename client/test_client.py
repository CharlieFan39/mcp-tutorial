# -*- coding: utf-8 -*-
"""MCP 通用测试客户端（教学配套工具，仅标准库）。

支持两种传输：
  stdio：把服务器作为子进程拉起，通过 stdin/stdout 通信；
  http ：向 streamable-http 端点 POST，自动携带 Mcp-Session-Id 与 Bearer token。

用法：
  py test_client.py --stdio <server.py 路径>
  py test_client.py --http http://127.0.0.1:8848/mcp [--token demo-token-123]
  py test_client.py --http ... --full                      # 跑完整方法巡检
  py test_client.py --http ... --call calc --args "{...}"  # 调用单个工具
"""
import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "tutorial-test-client", "version": "1.0.0"}


def pretty(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 两种传输的客户端实现
# ---------------------------------------------------------------------------

class StdioClient:
    """把服务器作为子进程拉起，逐行读写。"""

    def __init__(self, server_path: str):
        self.proc = subprocess.Popen(
            [sys.executable, server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=sys.stderr,  # 服务器日志直通到当前终端，便于观察
            text=True, encoding="utf-8")
        self._next_id = 0

    def request(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout")
        return json.loads(line)

    def notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


class HttpClient:
    """POST 到 streamable-http 端点，自动管理 Mcp-Session-Id。"""

    def __init__(self, endpoint: str, token: str = None):
        self.endpoint = endpoint
        self.token = token
        self.session_id = None
        self._next_id = 0

    def _post(self, msg: dict):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.endpoint, method="POST", headers=headers,
            data=json.dumps(msg, ensure_ascii=False).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid  # initialize 响应头里携带
                body = resp.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from None

    def request(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        return self._post(msg)

    def notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._post(msg)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 测试流程
# ---------------------------------------------------------------------------

def step(title: str, resp) -> bool:
    """打印一步结果，返回是否成功（error 且非预期时判失败）。"""
    print(f"\n=== {title} ===")
    if resp is None:
        print("(notification, no response)")
        return True
    print(pretty(resp))
    return "error" not in resp


def run_basic(client) -> int:
    """基础巡检：握手 + ping + 尽力探测 tools/resources/prompts。"""
    failures = 0

    resp = client.request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": CLIENT_INFO})
    if not step("initialize", resp):
        print("!! initialize failed, abort")
        return 1
    capabilities = resp.get("result", {}).get("capabilities", {})

    client.notify("notifications/initialized")
    print("\n=== notifications/initialized ===\n(sent)")

    if not step("ping", client.request("ping")):
        failures += 1

    # 按服务器声明的能力探测对应方法族（能力协商的意义所在）
    if "tools" in capabilities:
        resp = client.request("tools/list")
        if not step("tools/list", resp):
            failures += 1
        else:
            names = [t["name"] for t in resp["result"]["tools"]]
            print(f"--> visible tools: {names}")

    if "resources" in capabilities:
        if not step("resources/list", client.request("resources/list")):
            failures += 1
        if not step("resources/templates/list",
                    client.request("resources/templates/list")):
            failures += 1

    if "prompts" in capabilities:
        if not step("prompts/list", client.request("prompts/list")):
            failures += 1

    return failures


def run_full(client) -> int:
    """完整巡检（针对 lesson06）：在基础巡检之上补充读资源、取提示、调工具。"""
    failures = run_basic(client)

    if not step("resources/read overview(demo)",
                client.request("resources/read",
                               {"uri": "tutorial://workspace/demo/overview"})):
        failures += 1

    if not step("prompts/get incident-triage",
                client.request("prompts/get",
                               {"name": "incident-triage",
                                "arguments": {"service": "checkout"}})):
        failures += 1

    if not step("tools/call query_metrics",
                client.request("tools/call",
                               {"name": "query_metrics",
                                "arguments": {"service": "checkout"}})):
        failures += 1

    # 负面用例：未知工具应返回 -32602（预期失败，得到 error 才算通过）
    resp = client.request("tools/call", {"name": "no_such_tool", "arguments": {}})
    print("\n=== tools/call no_such_tool (expect error) ===")
    print(pretty(resp))
    if "error" not in resp:
        print("!! expected an error but got result")
        failures += 1

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP tutorial test client")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdio", metavar="SERVER_PY", help="以子进程方式测试 stdio 服务器")
    group.add_argument("--http", metavar="ENDPOINT", help="测试 streamable-http 端点")
    parser.add_argument("--token", help="Bearer token（http 模式）")
    parser.add_argument("--full", action="store_true", help="跑 lesson06 完整巡检")
    parser.add_argument("--call", metavar="TOOL", help="额外调用一个指定工具")
    parser.add_argument("--args", default="{}", help="--call 的参数（JSON 字符串）")
    args = parser.parse_args()

    client = StdioClient(args.stdio) if args.stdio else HttpClient(args.http, args.token)
    try:
        failures = run_full(client) if args.full else run_basic(client)

        if args.call:
            resp = client.request("tools/call",
                                  {"name": args.call,
                                   "arguments": json.loads(args.args)})
            if not step(f"tools/call {args.call}", resp):
                failures += 1
    finally:
        client.close()

    print(f"\n{'=' * 40}\nRESULT: {'ALL PASSED' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
