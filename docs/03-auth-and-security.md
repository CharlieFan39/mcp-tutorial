# 第三章：认证授权与安全模型

> 继续餐厅比喻：认证 = 门口保安验身份（“你是谁？”），
> 授权 = 会员等级制度（“你能点哪些菜？”），
> 审计日志 = 监控摄像头（“谁在什么时候做了什么？”）。
> 三者职责不同，缺一不可。

## 3.1 威胁模型：MCP 服务器面临什么风险

| 风险 | 场景 | 对策 |
|------|------|------|
| 未授权访问 | HTTP 模式下任何人都能连 | Bearer Token / API Key 认证 |
| 越权操作 | 只读用户调用写工具 | 作用域（scope）授权 |
| 误操作破坏数据 | LLM 幻觉调用 `entity_write` | 写工具默认禁用 + 显式开关 |
| 注入攻击 | 工具参数拼进查询/命令 | JSON Schema 校验 + 参数化执行 |
| 凭据泄露 | Token 写死在代码/日志里 | 环境变量注入 + 日志脱敏 |

## 3.2 认证（Authentication）：你是谁

### stdio 模式

stdio 服务器由客户端本地拉起，进程边界即信任边界，一般**不需要**额外认证；
凭据（如访问下游数据库的 key）通过**环境变量**传入。

怎么理解“进程边界即信任边界”？能在你电脑上把这个进程启动起来的人，
本来就已经控制了你的电脑——再要求他出示 token 没有意义，
就像在自家厨房做饭不需要向自己出示身份证。

### HTTP 模式：Bearer Token

```
POST /mcp HTTP/1.1
Authorization: Bearer <token>
```

服务器端校验流程（Lesson 5 实现）：

```python
def authenticate(self, auth_header: str) -> Principal | None:
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer "):]
    entry = self._token_table.get(token)      # 生产中应查数据库/IdP 并用恒定时间比较
    if entry is None or entry.expired():
        return None
    return Principal(name=entry.name, scopes=entry.scopes)
```

认证失败返回 **HTTP 401**（注意：这是传输层错误，不进入 JSON-RPC 层）。

生产环境的进阶方向（本教程不实现，但要知道）：
- OAuth 2.1 授权码流程（MCP 规范推荐的远程服务器认证方式）；
- mTLS 双向证书；
- Token 轮换与过期。

## 3.3 授权（Authorization）：你能干什么

### 作用域模型

教程采用极简的三级作用域：

| scope | 能做什么 |
|-------|----------|
| `read` | 调用只读工具、读取资源 |
| `write` | 额外允许调用写工具 |
| `admin` | 额外允许 `logging/setLevel` 等管理操作 |

每个工具声明 `required_scope`，`tools/call` 时比对调用者的 scopes：

```python
if tool.required_scope not in principal.scopes:
    return error_response(msg_id, -32602,
        f"insufficient scope: tool requires '{tool.required_scope}'")
```

### 写操作双重开关（UnifiedModel 的重要设计）

可以把它理解成银行金库的“双钥匙”：开门需要银行经理的钥匙（部署时的配置开关）
**和**客户本人的钥匙（调用时的 write 作用域）同时插入，缺一都打不开。

观察 `tools.schema.json` 里的字段设计：

```json
{
  "name": "entity_write",
  "enabled_by_default": false,
  "requires_explicit_write_enable": true
}
```

写操作要真正可用，必须**同时**满足两个条件：

1. **部署时**：管理员在配置中显式开启（`--enable-write` 或配置文件 `"write_enabled": true`）；
2. **调用时**：调用者持有 `write` 作用域。

这可以防止"配置了万能 token 但忘了关写功能"或"开了写功能但 token 权限过大"
任一单点失误造成数据破坏。**未开启时，写工具甚至不出现在 `tools/list` 里**
——对 LLM 隐藏它看不该见的能力，是最便宜的防线。

## 3.4 输入校验

所有工具参数必须经过 `input_schema`（JSON Schema）校验后才能进入业务逻辑。
教程实现一个覆盖常用关键字的迷你校验器（`type` / `required` / `enum` /
`properties` / `minimum` / `maxLength`），生产中可换成 jsonschema 库。

校验失败 → 返回 `-32602 Invalid params`，并说明具体哪个字段不合法
（清晰的错误消息能让 LLM 自动修正参数后重试）。

## 3.5 审计日志

安全事件必须留痕（Lesson 5/6 实现）：

```
2026-07-26 10:00:01 [AUDIT] auth ok    principal=analyst scopes=[read]
2026-07-26 10:00:05 [AUDIT] tool call  principal=analyst tool=query_metrics ok=true
2026-07-26 10:00:09 [AUDIT] tool deny  principal=analyst tool=entity_write reason=insufficient-scope
```

记录原则：
- 记录**谁、何时、调了什么工具、成功与否、拒绝原因**；
- **绝不记录** token 原文、密码、完整参数中的敏感值（必要时脱敏为 `sk-***`）。

## 3.6 安全检查清单

- [ ] HTTP 模式下所有端点强制认证，401 不泄露"token 不存在"还是"已过期"
- [ ] 工具按最小权限声明 `required_scope`
- [ ] 写工具默认禁用，且未启用时不出现在 tools/list
- [ ] 所有输入过 JSON Schema 校验
- [ ] 凭据只从环境变量/配置文件读取，不硬编码
- [ ] 审计日志完整且不含敏感信息
- [ ] HTTP 生产部署置于 TLS 之后（反向代理即可）

至此概念篇结束。进入 [lessons/lesson01-minimal-server](../lessons/lesson01-minimal-server/README.md) 开始动手。
