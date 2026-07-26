# Lesson 1：最小 MCP 服务器（stdio + 生命周期）

## 学习目标

- 实现 stdio 传输：从 stdin 逐行读 JSON-RPC 请求，向 stdout 逐行写响应
- 实现三个生命周期方法：`initialize`、`notifications/initialized`、`ping`
- 理解通知与请求的区别（有无 `id`）
- 理解"日志必须走 stderr"的原因

## 逐步指导

### 第 1 步：报文工具函数

先写两个构造响应的帮助函数——之后所有课程都会复用这个模式：

```python
def ok_response(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}

def error_response(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
```

### 第 2 步：实现 initialize

服务器收到 `initialize` 后返回三样东西：协议版本、能力声明、服务器信息。
本课能力为空（还没有工具/资源），后续课程逐步填充。

### 第 3 步：生命周期状态机

用一个布尔位 `self.initialized` 记录状态：收到 `notifications/initialized` 通知后置真；
在此之前，除 `initialize`/`ping` 外的请求一律拒绝。

### 第 4 步：主循环

```
for 每行 stdin:
    解析 JSON（失败 → -32700）
    分发（通知不回包；未知方法 → -32601）
    响应写 stdout 并 flush
```

## 运行与验证

方式一：手动交互（在 PowerShell 中运行后，逐行粘贴下面的 JSON）：

```powershell
py server.py
```

```json
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"manual","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":1,"method":"ping"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

预期：第 1 条返回能力声明；第 2 条无输出（通知）；第 3 条返回 `{}`；
第 4 条返回 -32601（本课还没实现工具）。

方式二：自动化客户端：

```powershell
py ..\..\client\test_client.py --stdio server.py
```

## 思考题

1. 为什么通知出错也不能回包？（提示：客户端没在等响应，回包会被误认为是其它请求的响应）
2. 若把 `log()` 里的 `sys.stderr` 改成 `sys.stdout` 会发生什么？动手试试。
