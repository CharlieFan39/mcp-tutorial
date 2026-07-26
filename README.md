# MCP 从零搭建教学项目（MCP Tutorial）

本项目系统性地教授如何**从零开始搭建一个 MCP（Model Context Protocol）系统**——
即为 AI 大模型提供工具调用、资源访问能力的"模型控制面"服务端。

课程范本参考了本 workspace 中真实项目 `UnifiedModel` 的 MCP 契约
（`UnifiedModel/api/mcp/tools.schema.json`），并将其拆解为 6 个循序渐进的课程，
每课都是**可独立运行的完整代码**。

## 环境要求

- Python 3.8+（仅使用标准库，**无需安装任何第三方依赖**）
- Windows 下请使用 `py` 命令运行（如 `py server.py`）

## 项目结构

```
mcp-tutorial/
├── README.md                        # 本文件：学习路径总览
├── docs/                            # 核心概念文档（先读文档再动手）
│   ├── 01-mcp-protocol-basics.md    # MCP 协议基础：JSON-RPC、生命周期、传输层
│   ├── 02-architecture.md           # MCP 服务器架构设计：分层与模块划分
│   └── 03-auth-and-security.md      # 认证授权与安全模型
├── lessons/                         # 六个渐进式课程（每课可独立运行）
│   ├── lesson01-minimal-server/     # 第1课：最小 stdio 服务器与握手生命周期
│   ├── lesson02-tools/              # 第2课：工具注册表与 tools/list、tools/call
│   ├── lesson03-resources/          # 第3课：资源系统 resources/*、URI 模板
│   ├── lesson04-http-transport/     # 第4课：HTTP 传输层（streamable-http）
│   ├── lesson05-auth/               # 第5课：认证授权、作用域、写操作保护
│   └── lesson06-full-server/        # 第6课：配置驱动的完整整合服务器 + 契约校验
└── client/
    └── test_client.py               # 通用测试客户端（stdio / http 双模式）
```

## 学习路径

### 阶段一：理解概念（docs/）

| 文档 | 内容 | 你将学到 |
|------|------|----------|
| [01-mcp-protocol-basics.md](docs/01-mcp-protocol-basics.md) | 协议基础 | JSON-RPC 2.0 报文格式、initialize 握手、三种传输层 |
| [02-architecture.md](docs/02-architecture.md) | 架构设计 | 传输层/协议层/能力层三层架构，如何划分模块 |
| [03-auth-and-security.md](docs/03-auth-and-security.md) | 安全模型 | API Key、Bearer Token、作用域、写操作双重开关 |

### 阶段二：动手实现（lessons/）

| 课程 | 主题 | 新增能力 | 对应 MCP 方法 |
|------|------|----------|---------------|
| Lesson 1 | 最小服务器 | stdio 传输、JSON-RPC 解析、握手生命周期 | `initialize`、`notifications/initialized`、`ping` |
| Lesson 2 | 工具系统 | 工具注册表、输入校验、工具执行 | `tools/list`、`tools/call` |
| Lesson 3 | 资源系统 | 静态资源、URI 模板、资源读取 | `resources/list`、`resources/templates/list`、`resources/read` |
| Lesson 4 | HTTP 传输 | streamable-http 端点、会话管理 | （同上，换传输层） |
| Lesson 5 | 认证授权 | API Key 校验、作用域、写工具保护 | （安全横切层） |
| Lesson 6 | 完整整合 | 配置驱动、契约 schema、日志级别 | 全部方法 + `logging/setLevel` |

每课目录下都有独立的 `README.md`（讲原理 + 逐步指导）和 `server.py`（完整可运行代码）。

### 阶段三：验证（client/）

```powershell
# 用测试客户端跑通任意一课（stdio 模式）
py client/test_client.py --stdio lessons/lesson01-minimal-server/server.py

# 跑通完整服务器（先启动 lesson06 的 HTTP 模式，再用 http 模式测试）
py lessons/lesson06-full-server/server.py --http --port 8848
py client/test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-123
```

## 核心知识点速查

1. **MCP 是什么**：Anthropic 提出的开放协议，让 LLM 客户端（Claude、IDE 等）以统一方式
   发现并调用外部工具（tools）、读取外部数据（resources）、获取提示模板（prompts）。
2. **报文格式**：全部基于 JSON-RPC 2.0（`jsonrpc`/`id`/`method`/`params`）。
3. **三种传输**：`stdio`（子进程标准输入输出）、`streamable-http`（单端点 POST）、
   `http+sse`（旧版双端点，已被 streamable-http 取代）。
4. **生命周期**：`initialize`（能力协商）→ `notifications/initialized`（客户端就绪通知）→ 正常请求。
5. **安全铁律**：写操作类工具必须默认关闭（`enabled_by_default: false`），
   且需要显式开关（`requires_explicit_write_enable: true`）——参见 UnifiedModel 的
   `entity_write` / `entity_expire` 工具设计。

## 与真实项目对照

学完本教程后，建议阅读真实实现印证所学：

| 教程内容 | UnifiedModel 对应文件 |
|----------|----------------------|
| 协议层（Lesson 1/2/3） | `UnifiedModel/cmd/umodel-mcp/protocol.go` |
| HTTP 传输（Lesson 4） | `UnifiedModel/cmd/umodel-mcp/http.go` |
| 入口与装配（Lesson 6） | `UnifiedModel/cmd/umodel-mcp/main.go` |
| 契约 schema（Lesson 6） | `UnifiedModel/api/mcp/tools.schema.json` |
