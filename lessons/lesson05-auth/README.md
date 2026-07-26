# Lesson 5：认证授权与写操作保护

> 前置：完成 Lesson 4。餐厅比喻：店开到大马路上（HTTP），什么人都可能进来，
> 得配三样东西——门口保安（认证）、会员等级（授权）、监控摄像头（审计日志）。
> 先读 [docs/03-auth-and-security.md](../../docs/03-auth-and-security.md)。

## 学习目标

- Bearer Token 认证：`Authorization: Bearer <token>` → 解析出 Principal（身份 + 作用域）
- 作用域授权：工具声明 `required_scope`，调用时比对
- **写操作双重开关**（对照 UnifiedModel 的 `entity_write` 设计）：
  1. 部署侧：`--enable-write` 启动参数（银行经理的钥匙）
  2. 调用侧：token 需含 `write` 作用域（客户本人的钥匙）
- 审计日志：谁、何时、调了什么、成败与拒绝原因（token 绝不入日志）

## 核心概念

### 认证发生在哪一层？（时序很重要）

认证是在**进入 JSON-RPC 层之前**、由 HTTP 传输层完成的拦截：

```
HTTP 请求进来
  │
  ├─① 保安查证：Authorization 头 → Authenticator.authenticate()
  │    验不过 → 直接 HTTP 401，请求根本进不了协议层
  │    （注意：401 是 HTTP 状态码，不是 JSON-RPC error——两个体系）
  │
  ├─② 查桌号牌：Mcp-Session-Id → 找到会话
  │
  └─③ 正常协议处理：handle_message(msg, principal)
       此时 principal（身份+等级）作为参数随请求传递，
       后续每一步（如 tools/call 的权限检查）都能用它
```

代码里的三个安全细节，逐个看懂：

```python
# 细节 1：恒定时间比较（hmac.compare_digest 而不是 ==）
# 普通 == 比较遇到第一个不同字符就返回，攻击者可通过响应时间差逐位猜 token。
for known, principal in self._tokens.items():
    if hmac.compare_digest(token, known):
        return principal

# 细节 2：401 统一措辞，不说"token 不存在"还是"已过期"
# 区分开就等于告诉攻击者"你猜的 token 格式对了/曾经存在过"——白送情报。
self._send_json(401, {"error": "unauthorized"})

# 细节 3：审计日志只记身份名，绝不记 token 原文
audit("auth ok", principal=principal.name, scopes=...)   # ✔
# audit("auth ok", token=token)                          # ✘ 泄露凭据！
```

### 授权：工具流水线从四步扩到六步

Lesson 2 的流水线是"查找 → 校验 → 执行 → 包装"，本课插入两步：

```
① 查找工具
② 检查开关   ← 新增：写开关未开的工具，当作"不存在"处理
③ 检查权限   ← 新增：required_scope 不在调用者 scopes 里 → 拒绝 + 审计
④ 校验输入
⑤ 执行
⑥ 包装结果
```

②的实现有个精妙之处——**隐藏而不是拒绝**：

```python
def _visible(self, tool):
    # 写开关未开 → 写工具从 tools/list 里直接消失
    return self.write_enabled or not tool.requires_explicit_write_enable

def call(self, name, arguments, principal):
    tool = self._tools.get(name)
    if tool is None or not self._visible(tool):
        # 隐藏的工具与不存在的工具返回同样的错误——不泄露其存在性
        raise InvalidParamsError(f"unknown tool: {name}")
```

为什么隐藏比"列出来但调用时拒绝"更好？两个原因：
- **对 LLM**：看不到就不会尝试调用，省去无意义的失败重试；
- **对攻击者**：探测不到服务器有哪些"隐藏能力"。

### 双重开关的完整逻辑表

以 `entity_write` 为例（`requires_explicit_write_enable=true`，`required_scope="write"`）：

| 部署侧 --enable-write | 调用者 scope | 结果 |
|----|----|----|
| ✘ 未开 | 无论是谁 | 工具不在 tools/list，调用返回 "unknown tool" |
| ✔ 开 | 只有 read | tools/list 可见，调用被拒 "insufficient scope" + 审计留痕 |
| ✔ 开 | 含 write | 调用成功 + 审计留痕 |

两把钥匙缺一不可——单独配错任何一边都不会造成数据破坏。

## 演示用 Token 表（仅教学！生产必须放数据库/IdP 并支持轮换）

| token | 身份 | 作用域 | 角色类比 |
|-------|------|--------|----------|
| `demo-token-123` | analyst | read | 普通会员：只能看 |
| `demo-token-456` | operator | read, write | 金卡会员：能点"危险菜品" |
| `demo-token-789` | admin | read, write, admin | 店长：还能调监控 |

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
# 1. 无 token → 401（连协议层都进不去）
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

验证时对照服务器 stderr 中的 `[AUDIT]` 行，四种事件都应有记录：

```
[AUDIT] auth fail   remote=...                                    ← 场景 1
[AUDIT] auth ok     principal=analyst scopes=read                 ← 场景 2
[AUDIT] tool deny   principal=analyst tool=entity_write reason=insufficient-scope  ← 场景 3
[AUDIT] tool call   principal=operator tool=entity_write ok=True  ← 场景 4
```

## 动手实验

1. **体验双重开关**：场景 A（未开写开关）下用 operator token（有 write scope）
   调 `entity_write`，确认照样返回 "unknown tool"——一把钥匙开不了金库。
2. **加一个 scope**：新增 `stats` 作用域和一个 `usage_stats` 工具
   （required_scope="stats"），给 admin token 加上该 scope，验证只有 admin 能调。
3. **验证日志不泄密**：全文搜索服务器输出，确认任何一行都不含 `demo-token` 字样。

## 思考题

1. 为什么"未开写开关时把写工具从 tools/list 中隐藏"比"列出来但调用时拒绝"更好？
   （回看"隐藏而不是拒绝"一节的两个原因。）
2. 401 响应为什么不能区分"token 不存在"和"token 已过期"？
   （防枚举探测——细节 2。）
3. stdio 模式为什么可以不做 Bearer 认证？信任边界在哪里？
   （docs/03 的"自家厨房"比喻：能启动进程的人已经控制了机器。）

✅ 下一课 [lesson06-full-server](../lesson06-full-server/README.md)：
保安到位，最后一步——把整家店的规章制度写成"营业执照"（配置 + 契约），正式开业。
