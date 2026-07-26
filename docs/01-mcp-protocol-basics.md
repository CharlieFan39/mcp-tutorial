# 第一章：MCP 协议基础

## 1.1 MCP 是什么

MCP（Model Context Protocol）是一个开放协议，定义了 **LLM 客户端**（如 Claude Desktop、
IDE 智能助手）与**上下文提供方服务器**之间的标准通信方式。服务器向客户端暴露三类能力：

| 能力 | 说明 | 类比 |
|------|------|------|
| **Tools（工具）** | 模型可主动调用的函数（可能有副作用） | POST 接口 |
| **Resources（资源）** | 模型可读取的数据（无副作用） | GET 接口 |
| **Prompts（提示）** | 预置的提示词模板 | 代码片段模板 |

一句话：**MCP 之于 AI 应用，如同 USB-C 之于外设**——一次接入，处处可用。

换个角度理解：在 MCP 出现之前，每个 AI 应用接每个外部系统都要写一套专用胶水代码，
10 个应用 × 10 个系统 = 100 套对接；有了 MCP，双方各自实现一次协议，10 + 10 = 20 套就够了。

## 1.2 报文格式：JSON-RPC 2.0

不要被名字吓到：JSON-RPC 就是“用 JSON 写的函数调用”——
`method` 是函数名，`params` 是入参，`id` 是取餐号（凭它把响应对回请求）。
MCP 的所有消息都是 JSON-RPC 2.0 报文，共三种：

### 请求（Request）—— 必须有 `id`，期待响应

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
```

### 响应（Response）—— `id` 与请求一致，`result` 与 `error` 二选一

```json
{"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
```

### 通知（Notification）—— 没有 `id`，不需要响应

```json
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

区分请求与通知只看一点：**有没有 `id`**。
请求像“点餐拿取餐号”（一定要等到菜），通知像“跟服务员说声我到了”（说完就走，不等回复）。
服务器对通知**永不回包**，哪怕处理出错也不回——这是新手最容易踩的协议红线。

### 标准错误码

| code | 含义 | 何时使用 |
|------|------|----------|
| -32700 | Parse error | JSON 解析失败 |
| -32600 | Invalid Request | 缺少 jsonrpc/method 字段 |
| -32601 | Method not found | 未实现的 method |
| -32602 | Invalid params | 参数校验失败 |
| -32603 | Internal error | 服务器内部异常 |

## 1.3 生命周期：三步握手

用餐厅比喻（第二章会详细展开）：`initialize` 是“进门问你们能做什么菜”，
服务器回“我们有这些能力”；`notifications/initialized` 是“好，我落座了”；
之后才能正式点菜。顺序不能乱：没落座就点菜会被拒。

```
客户端                                服务器
  │                                     │
  │── initialize（协议版本+客户端能力）──►│
  │◄─ result（协议版本+服务器能力+服务器信息）─│
  │                                     │
  │── notifications/initialized ───────►│   (通知，无响应)
  │                                     │
  │── tools/list、tools/call ... ──────►│   正常业务阶段
```

`initialize` 请求示例：

```json
{
  "jsonrpc": "2.0", "id": 0, "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "my-client", "version": "1.0.0"}
  }
}
```

`initialize` 响应示例（服务器声明自己支持哪些能力）：

```json
{
  "jsonrpc": "2.0", "id": 0,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "tools": {"listChanged": false},
      "resources": {"subscribe": false, "listChanged": false},
      "logging": {}
    },
    "serverInfo": {"name": "tutorial-mcp", "version": "0.1.0"}
  }
}
```

要点：
- **能力协商**：客户端只会调用服务器在 `capabilities` 中声明过的方法族。
- **版本协商**：若服务器不支持客户端请求的版本，应返回自己支持的最新版本。
- 在收到 `initialized` 通知之前，服务器只应响应 `initialize` 和 `ping`。

## 1.4 三种传输层

参考 `UnifiedModel/api/mcp/tools.schema.json` 中的 `transports` 字段：

```json
"transports": ["stdio", "streamable-http", "http+sse"]
```

### stdio（第 1~3、5 课使用）

- 客户端把服务器作为**子进程**启动；
- 请求写入服务器的 **stdin**（每行一条 JSON），响应从 **stdout** 读出（每行一条）；
- **stderr 用于日志**（绝不能把日志打到 stdout，否则会污染协议流！）；
- 适合本地集成（如 Claude Desktop 配置本地命令）。

### streamable-http（第 4、6 课使用）

- 单一端点（惯例是 `/mcp`），所有 JSON-RPC 消息 POST 到该端点；
- 响应可以是普通 JSON，也可升级为 SSE 流（用于服务器推送）；
- 用 `Mcp-Session-Id` 响应头维护会话；
- 适合远程部署、多客户端共享。

### http+sse（旧版，仅作了解）

- 双端点：GET `/sse` 建立事件流 + POST `/messages` 发送消息；
- 已被 streamable-http 取代，新项目不建议实现。

## 1.5 方法速查表

以 UnifiedModel 契约中的 `mcp_methods` 为准：

| 方法 | 类型 | 作用 | 教程课次 |
|------|------|------|----------|
| `initialize` | 请求 | 握手与能力协商 | Lesson 1 |
| `notifications/initialized` | 通知 | 客户端就绪 | Lesson 1 |
| `ping` | 请求 | 心跳保活（返回空对象） | Lesson 1 |
| `tools/list` | 请求 | 列出可用工具 | Lesson 2 |
| `tools/call` | 请求 | 调用工具 | Lesson 2 |
| `resources/list` | 请求 | 列出静态资源 | Lesson 3 |
| `resources/templates/list` | 请求 | 列出 URI 模板 | Lesson 3 |
| `resources/read` | 请求 | 读取资源内容 | Lesson 3 |
| `prompts/list` / `prompts/get` | 请求 | 提示模板 | Lesson 6 |
| `logging/setLevel` | 请求 | 动态调整日志级别 | Lesson 6 |
| `completion/complete` | 请求 | 参数自动补全 | （进阶，本教程不实现） |

下一章：[02-architecture.md](02-architecture.md) —— 如何把这些方法组织成清晰的分层架构。
