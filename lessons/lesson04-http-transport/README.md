# Lesson 4：HTTP 传输层（streamable-http）

> 前置：完成 Lesson 2/3。本课证明**分层架构的价值**：协议层与能力层零改动，只换传输层。

## 学习目标

- 实现 streamable-http 传输：单端点 `POST /mcp`
- 会话管理：`initialize` 后签发 `Mcp-Session-Id`，`DELETE /mcp` 终止会话
- 同一份 `server.py` 通过 `--http` 参数在两种传输间切换
- 理解每个会话独立的协议状态（每个 session 一个 `MCPServer` 实例）

## 核心概念

### 端点约定

| 请求 | 作用 |
|------|------|
| `POST /mcp`（body = JSON-RPC） | 发送请求/通知；响应 body = JSON-RPC 响应 |
| `POST /mcp`（通知） | 返回 `202 Accepted`，空 body |
| `DELETE /mcp` + `Mcp-Session-Id` 头 | 终止会话 |
| `GET /healthz` | 健康检查（非 MCP 规范，运维惯例） |

### 会话流程

```
客户端                            服务器
  │── POST /mcp {initialize} ────►│  新建 session，生成 UUID
  │◄─ 200 + Mcp-Session-Id: abc ──│  响应头携带会话 ID
  │── POST /mcp + Session-Id ────►│  查表找到该会话的 MCPServer 实例
  │── DELETE /mcp + Session-Id ──►│  清理会话
```

要点：
- **每个会话一个独立的 `MCPServer` 实例**——生命周期状态（`initialized`）互不干扰；
- 无会话头且不是 `initialize` 请求 → `400 Bad Request`；
- 会话表要考虑淘汰（教学版用简单的上限 + 最旧淘汰，生产用 TTL）。

### stdio 与 http 复用同一协议层

本课 `server.py` 的结构：

```
MCPServer（协议层，Lesson 2/3 的合并版）
   ▲              ▲
StdioTransport   HttpTransport   ← 只有这里不同
```

## 运行与验证

```powershell
# HTTP 模式启动
py server.py --http --port 8847

# 另开一个终端，用测试客户端验证
py ..\..\client\test_client.py --http http://127.0.0.1:8847/mcp

# 或者直接用 PowerShell 发一条 initialize：
Invoke-RestMethod -Uri http://127.0.0.1:8847/mcp -Method Post -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"ps","version":"0"}}}'

# stdio 模式依旧可用（验证传输层可插拔）
py server.py
```

## 思考题

1. 为什么会话状态不能放在全局单例里？（提示：两个客户端同时握手会怎样）
2. 真正的 streamable-http 还支持把响应升级为 SSE 流（服务器主动推送进度/日志），
   本课未实现。哪些场景需要它？（长时间工具执行的进度通知、resources 订阅变更）
