# Lesson 5：认证授权与写操作保护

> 前置：完成 Lesson 4。本课在 HTTP 传输上叠加安全层。先读 [docs/03-auth-and-security.md](../../docs/03-auth-and-security.md)。

## 学习目标

- Bearer Token 认证：`Authorization: Bearer <token>` → 解析出 Principal（身份 + 作用域）
- 作用域授权：工具声明 `required_scope`，调用时比对
- **写操作双重开关**（对照 UnifiedModel 的 `entity_write` 设计）：
  1. 部署侧：`--enable-write` 启动参数
  2. 调用侧：token 需含 `write` 作用域
- 审计日志：谁、何时、调了什么、成败与拒绝原因（token 绝不入日志）

## 演示用 Token 表（仅教学！生产必须放数据库/IdP 并支持轮换）

| token | 身份 | 作用域 |
|-------|------|--------|
| `demo-token-123` | analyst | read |
| `demo-token-456` | operator | read, write |
| `demo-token-789` | admin | read, write, admin |

## 演示工具

| 工具 | required_scope | 需要写开关 | 对应真实工具 |
|------|----------------|-----------|--------------|
| `query_metrics` | read | 否 | `query_spl_execute` |
| `entity_write` | write | **是** | `entity_write` |
| `entity_expire` | write | **是** | `entity_expire` |

## 运行与验证

```powershell
# 场景 A：默认启动（写功能关闭）
py server.py --http --port 8848
```

另开终端验证：

```powershell
# 1. 无 token → 401
Invoke-WebRequest -Uri http://127.0.0.1:8848/mcp -Method Post -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}'

# 2. read token：tools/list 里看不到 entity_write（写开关未开，能力被隐藏）
py ..\..\client\test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-123
```

```powershell
# 场景 B：开启写功能
py server.py --http --port 8848 --enable-write

# 3. read token 调 entity_write → 拒绝（insufficient scope），审计日志留痕
py ..\..\client\test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-123 --call "entity_write" --args '{\"entity_id\":\"e1\",\"data\":{\"k\":\"v\"}}'

# 4. write token 调 entity_write → 成功
py ..\..\client\test_client.py --http http://127.0.0.1:8848/mcp --token demo-token-456 --call "entity_write" --args '{\"entity_id\":\"e1\",\"data\":{\"k\":\"v\"}}'
```

观察服务器 stderr 中的 `[AUDIT]` 行，确认四种事件都有记录：
认证成功 / 认证失败 / 工具放行 / 工具拒绝。

## 思考题

1. 为什么"未开写开关时把写工具从 tools/list 中隐藏"比"列出来但调用时拒绝"更好？
2. 401 响应为什么不能区分"token 不存在"和"token 已过期"？（防枚举探测）
3. stdio 模式为什么可以不做 Bearer 认证？信任边界在哪里？
