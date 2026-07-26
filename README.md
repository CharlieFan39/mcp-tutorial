# MCP 从零搭建教学项目（MCP Tutorial）

本项目系统性地教授如何**从零开始搭建一个 MCP（Model Context Protocol）系统**——
即为 AI 大模型提供工具调用、资源访问能力的"模型控制面"服务端。

**适合谁学**：具备基础编程能力（会 Python 基本语法、大致了解 HTTP 和 JSON）、
想搞懂"AI 是怎么调用外部工具的"的开发者。不需要任何 AI/LLM 开发经验。

课程范本参考了本 workspace 中真实项目 `UnifiedModel` 的 MCP 契约
（`UnifiedModel/api/mcp/tools.schema.json`），并将其拆解为 6 个循序渐进的课程，
每课都是**可独立运行的完整代码**。

## 30 秒理解 MCP：一个餐厅比喻

整套教程用同一个比喻贯穿（第二章详细展开），先混个眼熟：

```
 LLM 客户端 = 顾客          （点菜的人）
 传输层     = 服务员        （只传话，不做菜 → Lesson 1/4）
 协议层     = 前台领班      （验单、分发、兜底 → Lesson 1/2）
 工具 Tools = 炒菜档口      （执行操作，可能有副作用 → Lesson 2）
 资源 Resources = 自助餐台  （只读取用，无副作用 → Lesson 3）
 认证授权   = 保安+会员等级 （你是谁？能点什么菜？→ Lesson 5）
 配置与契约 = 营业执照+菜单 （承诺提供什么，白纸黑字 → Lesson 6）
```

## 环境要求

- Python 3.8+（仅使用标准库，**无需安装任何第三方依赖**）
- Windows 下请使用 `py` 命令运行（如 `py server.py`）

## 项目结构

```
mcp-tutorial/
├── README.md                        # 本文件：学习路径总览
├── docs/                            # 核心概念文档（先读文档再动手）
│   ├── 01-mcp-protocol-basics.md    # MCP 协议基础：JSON-RPC、生命周期、传输层
│   ├── 02-architecture.md           # MCP 服务器架构设计：三层架构与餐厅比喻 ★必读
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

## 怎么学效果最好（建议流程）

每一课都按同一个节奏走，大约 30~60 分钟一课：

1. **读**：读该课 README 的"学习目标"和"核心概念"（10 分钟）；
2. **跑**：先原样运行 `server.py`，用手动粘贴报文或测试客户端跑通（10 分钟）；
3. **改**：故意改坏一处（比如删掉 `flush()`、把日志打到 stdout），观察出什么错，
   再改回来——**理解错误比理解正确更深刻**（10 分钟）；
4. **答**：做课末思考题，答不上来就回头翻对应文档章节；
5. **练**：有余力做扩展练习（Lesson 6 有毕业练习）。

## 学习路径

### 阶段一：理解概念（docs/）

| 文档 | 内容 | 你将学到 |
|------|------|----------|
| [01-mcp-protocol-basics.md](docs/01-mcp-protocol-basics.md) | 协议基础 | JSON-RPC 2.0 报文格式、initialize 握手、三种传输层 |
| [02-architecture.md](docs/02-architecture.md) | 架构设计 ★ | 三层架构餐厅比喻、一条消息的完整旅程、分发器模式 |
| [03-auth-and-security.md](docs/03-auth-and-security.md) | 安全模型 | Bearer Token、作用域、写操作"双钥匙"开关 |

### 阶段二：动手实现（lessons/）

| 课程 | 主题 | 新增能力 | 对应 MCP 方法 |
|------|------|----------|---------------|
| Lesson 1 | 最小服务器 | stdio 传输、JSON-RPC 解析、握手生命周期 | `initialize`、`notifications/initialized`、`ping` |
| Lesson 2 | 工具系统 | 工具注册表、输入校验、工具执行 | `tools/list`、`tools/call` |
| Lesson 3 | 资源系统 | 静态资源、URI 模板、资源读取 | `resources/list`、`resources/templates/list`、`resources/read` |
| Lesson 4 | HTTP 传输 | streamable-http 端点、会话管理 | （同上，换传输层） |
| Lesson 5 | 认证授权 | API Key 校验、作用域、写工具保护 | （安全横切层） |
| Lesson 6 | 完整整合 | 配置驱动、契约 schema、日志级别 | 全部方法 + `logging/setLevel` |

每课目录下都有独立的 `README.md`（讲原理 + 逐步指导）和 `server.py`（完整可运行代码，
注释密度按教学标准编写，建议对照 README 逐段阅读）。

**课程之间的依赖关系**：1 → 2 → 3 可顺序学；4 依赖 2；5 依赖 4；6 整合全部。
时间紧张时的最小路径：1 → 2 → 4 → 5（跳过资源和整合，仍能覆盖核心主干）。

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
   好比 AI 世界的 USB-C：双方各实现一次协议，任意组合即插即用。
2. **报文格式**：全部基于 JSON-RPC 2.0（`jsonrpc`/`id`/`method`/`params`）——
   本质就是"用 JSON 写的函数调用"，`id` 是取餐号。
3. **三种传输**：`stdio`（子进程标准输入输出）、`streamable-http`（单端点 POST）、
   `http+sse`（旧版双端点，已被 streamable-http 取代）。
4. **生命周期**：`initialize`（能力协商）→ `notifications/initialized`（客户端就绪通知）→ 正常请求。
   记忆口诀："问菜单 → 落座 → 点菜"。
5. **安全铁律**：写操作类工具必须默认关闭（`enabled_by_default: false`），
   且需要显式开关（`requires_explicit_write_enable: true`）——像银行金库的双钥匙，
   参见 UnifiedModel 的 `entity_write` / `entity_expire` 工具设计。

## 常见疑问（FAQ）

**Q：MCP 和普通的 REST API 有什么区别？**
A：REST API 是给程序员看文档再写代码调用的；MCP 的工具描述（description + inputSchema）
是给 LLM 直接读的，LLM 自己决定何时调用、怎么传参——所以"描述写得清楚"本身就是接口设计。

**Q：为什么教程用 Python 标准库而不用官方 SDK？**
A：SDK 会把协议细节封装掉，恰恰是这些细节（握手、路由、错误码）才是理解 MCP 的关键。
学完本教程后再用 SDK，你会知道每个 API 背后发生了什么。

**Q：一定要按顺序学吗？**
A：概念文档（docs/01、02）+ Lesson 1 是地基，必须先学；其余见上文依赖关系图。

## 与真实项目对照

学完本教程后，建议阅读真实实现印证所学：

| 教程内容 | UnifiedModel 对应文件 |
|----------|----------------------|
| 协议层（Lesson 1/2/3） | `UnifiedModel/cmd/umodel-mcp/protocol.go` |
| HTTP 传输（Lesson 4） | `UnifiedModel/cmd/umodel-mcp/http.go` |
| 入口与装配（Lesson 6） | `UnifiedModel/cmd/umodel-mcp/main.go` |
| 契约 schema（Lesson 6） | `UnifiedModel/api/mcp/tools.schema.json` |
