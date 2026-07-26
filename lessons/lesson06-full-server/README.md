# Lesson 6：完整整合 —— 配置驱动的 MCP 服务器

> 前置：完成 Lesson 1~5。本课把所有能力整合为一个**配置驱动**的完整服务器，
> 并引入**契约 schema** 的工程实践。

## 学习目标

- 用 `config.json` 集中管理：传输、端口、认证、工具开关、日志级别
- 整合全部方法族：生命周期 + tools + resources + prompts + `logging/setLevel`
- 理解契约文件（`tools.schema.json`）的作用：让 API 承诺可被 CI 校验
- 启动时执行**自检**：注册的工具/资源与契约声明一致，否则拒绝启动

## 本课文件

| 文件 | 作用 |
|------|------|
| `server.py` | 完整服务器（约 600 行，整合前五课全部能力） |
| `config.json` | 运行配置（传输/认证/写开关/日志级别） |
| `tools.schema.json` | 教学版契约：声明本服务器承诺提供的工具与资源 |

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
- 命令行参数（`--http`/`--port`/`--enable-write`）**覆盖**配置文件——运维惯例：
  配置文件是基线，命令行是临时覆盖；
- 演示 token 放在配置里是教学简化，生产应放环境变量或密钥管理服务。

## 契约 schema 的工程价值

对照真实的 `UnifiedModel/api/mcp/tools.schema.json`：契约文件用 JSON Schema 的
`enum`/`const` 把"服务器必须提供哪些工具、哪些资源、哪些方法"固化下来。价值：

1. **防漂移**：代码改了工具名，忘了改文档？启动自检直接失败；
2. **可测试**：CI 中拿契约跑一遍 `tools/list`，输出必须匹配（本课 `--selfcheck` 演示）；
3. **可协作**：客户端团队看契约即可开发，不必读服务器源码。

## prompts 与 logging/setLevel

- `prompts/list` / `prompts/get`：提示模板带参数（`{service}`），`get` 时渲染成
  完整的消息列表——这是把领域知识（如"如何做根因分析"）打包给 LLM 的正规方式。
- `logging/setLevel`：客户端可动态调日志级别，需要 `admin` 作用域（授权横切到管理方法的示例）。

## 运行与验证

```powershell
# 启动自检（不启动服务，只校验代码与契约一致）
py server.py --selfcheck

# stdio 模式（认证自动关闭）
py server.py

# HTTP 模式（读 config.json 的认证配置）
py server.py --http --port 8848

# 完整端到端测试
py ..\..\client\test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-123 --full
```

## 毕业练习

1. 新增一个工具 `entity_get`（只读，read scope），并更新 `tools.schema.json`，
   跑通 `--selfcheck`；
2. 把会话管理改成带 TTL 的过期淘汰；
3. 给 `tools/call` 加一个每 principal 的简单限流（如 10 次/分钟）；
4. 对照阅读 `UnifiedModel/cmd/umodel-mcp/protocol.go`，找出教学版缺少的
   生产级细节（如 TOON 输出格式、分页游标）。
