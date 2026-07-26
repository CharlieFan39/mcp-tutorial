# Lesson 4：HTTP 传输层（streamable-http）

> 前置：完成 Lesson 2/3。餐厅比喻：厨师和菜单一个都不换，只把"店内点餐的服务员"
> 换成"接电话订单的客服"——本课证明**分层架构的价值**：协议层与能力层零改动，只换传输层。

## 学习目标

- 实现 streamable-http 传输：单端点 `POST /mcp`
- 会话管理：`initialize` 后签发 `Mcp-Session-Id`，`DELETE /mcp` 终止会话
- 同一份 `server.py` 通过 `--http` 参数在两种传输间切换
- 理解每个会话独立的协议状态（每个 session 一个 `MCPServer` 实例）

## 开始前：为什么 stdio 不够用了？

stdio 模式下客户端和服务器是"一对一包场"：一个客户端独占一个子进程。
但如果想把 MCP 服务器部署到公司服务器上让全组共用呢？
stdio 做不到——你不可能让每个同事都 SSH 上来起进程。
这就需要 HTTP：**一个服务器，多个客户端，各自独立会话**。

## 核心概念

### 端点约定

| 请求 | 作用 | 类比 |
|------|------|------|
| `POST /mcp`（body = JSON-RPC 请求） | 发请求，响应 body = JSON-RPC 响应 | 打电话点单 |
| `POST /mcp`（body = 通知） | 返回 `202 Accepted`，空 body | 打电话说声"我到了"，对方"嗯"一声挂了 |
| `DELETE /mcp` + `Mcp-Session-Id` 头 | 终止会话 | 结账走人 |
| `GET /healthz` | 健康检查（非 MCP 规范，运维惯例） | 路过看看店开没开门 |

留意通知的 `202 Accepted`：HTTP 必须有响应（这是 HTTP 的规矩），
但 JSON-RPC 通知又不能有回包（这是 JSON-RPC 的规矩）——
`202 + 空 body` 是两者的调和：HTTP 层面答复了"收到"，JSON-RPC 层面什么都没说。

### 会话流程：桌号牌机制

HTTP 是无状态的——服务器天然不记得"上一个请求是谁发的"。
但 MCP 有生命周期状态（`initialized` 了没有），怎么办？答案是**会话 ID**，
就像餐厅给你发桌号牌：

```
客户端                            服务器
  │── POST /mcp {initialize} ────►│  新建 session，生成 UUID
  │◄─ 200 + Mcp-Session-Id: abc ──│  响应头里发"桌号牌"
  │── POST /mcp + Session-Id ────►│  凭桌号牌找到"这一桌"的 MCPServer 实例
  │── DELETE /mcp + Session-Id ──►│  结账，清理会话
```

三个设计要点，每个都有"为什么"：

1. **每个会话一个独立的 `MCPServer` 实例**。
   为什么？两个客户端 A、B 同时连进来：A 握手完成、B 还没握手。
   如果共用一个实例，A 的 `initialized=True` 会让 B 未握手就能调工具——状态串味了。
   一桌一个服务档案，互不干扰。

2. **无会话头且不是 `initialize` → 400**。
   没有桌号牌又不是来办新卡的，前台没法服务。

3. **会话表要考虑淘汰**（教学版：上限 64 个 + 满了踢最旧；生产版：TTL 过期）。
   为什么？客户端可能异常退出不发 DELETE，会话对象会无限堆积——内存泄漏。

### stdio 与 http 复用同一协议层

本课 `server.py` 的结构，注意箭头方向——两种传输都指向**同一个** MCPServer：

```
        MCPServer（协议层，与 Lesson 2 完全相同，一行未改）
           ▲                      ▲
     StdioTransport         HttpTransport
     （15 行主循环）      （SessionManager + HTTP Handler）
           ▲                      ▲
      py server.py         py server.py --http
```

读代码时可以做个实验：把 Lesson 2 和本课的 `MCPServer` 类放一起 diff——
除了工具清单不同，分发逻辑一字不差。这就是"换服务员不用换厨师"。

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

## 动手实验

1. **观察桌号牌**：用 `Invoke-WebRequest`（而不是 `Invoke-RestMethod`）发 initialize，
   查看响应的 `Headers["Mcp-Session-Id"]`——这就是你的桌号牌。
2. **不带牌点菜**：不带 `Mcp-Session-Id` 头直接 POST 一条 `tools/list`，
   确认收到 400 和 "missing or unknown Mcp-Session-Id"。
3. **两桌互不干扰**：开两个 PowerShell 窗口各自跑一遍测试客户端，
   确认各自拿到不同的 Session-Id、各自正常工作。

## 思考题

1. 为什么会话状态不能放在全局单例里？
   （提示：回看"设计要点 1"——两个客户端同时握手会怎样？）
2. 真正的 streamable-http 还支持把响应升级为 SSE 流（服务器主动推送进度/日志），
   本课未实现。哪些场景需要它？
   （长时间工具执行的进度通知、resources 订阅变更——"菜要炒 10 分钟，
   服务员隔两分钟来报一次进度"。）

✅ 下一课 [lesson05-auth](../lesson05-auth/README.md)：
店开到大马路上了，得请保安了——认证授权与写操作保护。
