# Lesson 6：完整整合 —— 配置驱动的 MCP 服务器

> 前置：完成 Lesson 1~5。餐厅比喻：厨房、菜单、保安都齐了，这一课办两件"开业大事"——
> 把规章制度写成**配置文件**（营业方式可调整），把对外承诺写成**契约 schema**（白纸黑字可查验），
> 然后整店合并、正式营业。

## 学习目标

- 用 `config.json` 集中管理：传输、端口、认证、工具开关、日志级别
- 整合全部方法族：生命周期 + tools + resources + prompts + `logging/setLevel`
- 理解契约文件（`tools.schema.json`）的作用：让 API 承诺可被 CI 校验
- 启动时执行**自检**：注册的工具/资源与契约声明一致，否则拒绝启动

## 本课文件

| 文件 | 作用 | 类比 |
|------|------|------|
| `server.py` | 完整服务器（整合前五课全部能力） | 整家餐厅 |
| `config.json` | 运行配置（传输/认证/写开关/日志级别） | 营业方式（堂食/外卖、会员制度） |
| `tools.schema.json` | 教学版契约：声明本服务器承诺提供的工具与资源 | 挂在门口的公示菜单 |

## 配置文件讲解（config.json）

```json
{
  "server": {"name": "...", "version": "..."},
  "transport": {"default": "stdio", "http": {"host": "...", "port": 8848}},
  "auth": {"enabled": true, "tokens": [{"token": "...", "principal": "...", "scopes": [...]}]},
  "tools": {"write_enabled": false, "disabled": []},
  "logging": {"level": "info"}
}
```

要点：
- **命令行参数覆盖配置文件**（`--http`/`--port`/`--enable-write`）。
  这是运维惯例：配置文件是"日常营业方式"（基线），命令行是"今天临时调整"（覆盖）。
  代码里就一行：`write_enabled = args.enable_write or cfg["tools"]["write_enabled"]`；
- 演示 token 放在配置里是教学简化，生产应放环境变量或密钥管理服务；
- `disabled` 列表可以精确下架某个工具而不动代码——"今天这道菜卖完了"。

## 契约 schema 的工程价值

对照真实的 `UnifiedModel/api/mcp/tools.schema.json`：契约文件用 JSON Schema 的
`enum`/`const` 把"服务器必须提供哪些工具、哪些资源、哪些方法"固化下来。

为什么需要它？想一个常见事故：开发者把工具 `query_metrics` 改名成 `metrics_query`，
代码正常跑，但所有依赖旧名字的客户端全部瘫痪——而且没有任何报错提醒你。
契约就是防这个的：

1. **防漂移**：代码改了工具名，忘了改契约？`--selfcheck` 启动自检直接失败；
2. **可测试**：CI 中拿契约跑一遍 `tools/list`，输出必须匹配；
3. **可协作**：客户端团队看契约即可开发，不必读服务器源码。

本课 `selfcheck()` 的核心就是"两边取集合、找差集"：

```python
declared = {t["name"] for t in contract["tools"]}     # 契约里承诺的（公示菜单）
actual   = {t.name    for t in tools.all_tools()}     # 代码里注册的（后厨实际）
# 只在契约不在代码 → "declared but not registered"（菜单有、后厨做不出来）
# 只在代码不在契约 → "registered but missing from contract"（后厨偷偷加菜没公示）
```

## prompts 与 logging/setLevel

- `prompts/list` / `prompts/get`：提示模板带参数（`{service}`），`get` 时渲染成
  完整的消息列表——这是把领域知识（如"如何做根因分析"）打包给 LLM 的正规方式。
  类比"主厨推荐套餐"：不是一道菜，而是一套点菜攻略。
- `logging/setLevel`：客户端可动态调日志级别，需要 `admin` 作用域——
  这是"授权横切到非工具方法"的示例：不只 tools/call 要查权限，管理操作同样要查。

## 运行与验证

```powershell
# 启动自检（不启动服务，只校验代码与契约一致）
py server.py --selfcheck

# stdio 模式（认证自动关闭——本地信任边界，见 docs/03）
py server.py

# HTTP 模式（读 config.json 的认证配置）
py server.py --http --port 8848

# 完整端到端测试
py ..\..\client\test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-123 --full
```

预期：`--selfcheck` 输出每个工具/资源的 `OK` 与最终 `selfcheck PASSED`；
`--full` 测试 11 项巡检全部通过（含一条"预期失败"的负面用例）。

## 毕业练习（附引导提示）

按顺序做，难度递增。每题先自己想 15 分钟再看提示。

1. **新增工具 `entity_get`**（只读，read scope），并更新 `tools.schema.json`，跑通 `--selfcheck`。
   引导：三处要动——`build_tools()` 里注册（仿照 `query_metrics`）、
   契约的 `enum` 列表、契约的 `contract_instance.tools` 数组。
   故意先只改代码不改契约，观察 selfcheck 怎么报错——体会契约的价值。

2. **把会话管理改成带 TTL 的过期淘汰**。
   引导：给 `SessionManager` 的每个会话记一个 `last_active` 时间戳；
   `get()` 时刷新时间戳；`create()` 时顺手清理超过 30 分钟未活跃的会话。
   验证：把 TTL 临时调成 5 秒，创建会话、等 6 秒、再用旧 Session-Id 请求，应收到 400。

3. **给 `tools/call` 加每 principal 的简单限流**（如 10 次/分钟）。
   引导：用 `{principal.name: [时间戳列表]}` 字典，每次调用前清掉 60 秒外的记录，
   超过 10 条就拒绝。想一想：限流被触发时应该返回 JSON-RPC error 还是 `isError: true`？
   （建议 error——这不是"这次尝试失败"，而是"现在不该调"，重试同样会失败。）

4. **对照阅读真实实现**：打开 `UnifiedModel/cmd/umodel-mcp/protocol.go`，
   找出教学版缺少的生产级细节，至少列出三条。
   参考方向：TOON 输出格式（省 token 的表格序列化）、分页游标（工具结果太大怎么办）、
   更完整的错误分类。这一步是从"学会"到"会用"的桥梁。

## 全教程总结：你现在拥有的知识地图

```
API 接口      ✔ JSON-RPC 报文、initialize 握手、HTTP 端点与会话（L1/L4）
工具集成      ✔ 注册表、Schema 校验、双路错误处理、description 提示工程（L2）
服务注册      ✔ 资源/提示注册表、URI 模板、配置驱动装配、契约自检（L3/L6）
认证授权      ✔ Bearer 认证、作用域、双重开关、审计日志（L5）
```

学完后的进阶路线：读 MCP 官方规范原文 → 用官方 SDK 重写本教程的 Lesson 6 →
给自己的实际业务写一个真实的 MCP 服务器。
