# Lesson 1：最小 MCP 服务器（stdio + 生命周期）

> 用餐厅比喻：这一课我们先把"店"开起来——只有一个服务员（stdio 传输）
> 和一个前台领班（协议分发器），后厨还是空的（没有任何工具/资源）。
> 但顾客已经可以进门、问菜单、落座、打招呼了。

## 学习目标

- 实现 stdio 传输：从 stdin 逐行读 JSON-RPC 请求，向 stdout 逐行写响应
- 实现三个生命周期方法：`initialize`、`notifications/initialized`、`ping`
- 理解通知与请求的区别（有无 `id`）
- 理解"日志必须走 stderr"的原因

## 开始前：stdio 传输到底是怎么回事？

很多初学者卡在这里，先说清楚。当 Claude Desktop 这类客户端配置了一个本地 MCP 服务器，
它做的事是：

1. 把你的 `server.py` **作为子进程启动**（相当于替你在命令行敲了 `py server.py`）；
2. 拿到这个子进程的三根"管道"：stdin（进水管）、stdout（出水管）、stderr（排污管）；
3. 想发请求 → 往子进程的 stdin 写一行 JSON；
4. 等响应 → 从子进程的 stdout 读一行 JSON；
5. stderr 里的内容当作日志显示，**不参与协议**。

所以"一行 = 一条消息"是 stdio 传输的根本约定，
而 stdout 就是唯一的协议通道——这解释了本课两条铁律：
**响应写完必须 `flush()`**（否则卡在缓冲区里，客户端干等），
**日志绝不能用普通 `print()`**（会污染协议通道）。

## 代码逐段讲解（对照 server.py 阅读）

### 第 1 段：错误码常量与报文工具函数

JSON-RPC 响应只有两种形态，先写两个构造函数，之后所有课程都复用：

```python
def ok_response(msg_id, result):
    # 成功响应：id 必须原样带回（取餐号对上才能取餐）
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}

def error_response(msg_id, code, message):
    # 失败响应：code 用标准错误码（见 docs/01 的表格），message 给人看
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
```

注意 `result` 和 `error` **二选一，永不同时出现**——这是 JSON-RPC 规范要求。

### 第 2 段：日志函数

```python
def log(msg: str) -> None:
    """日志一律走 stderr —— stdout 是协议通道，绝不能污染。"""
    print(f"[lesson01] {msg}", file=sys.stderr, flush=True)
```

就一行，但它是本课最重要的工程习惯。以后你写任何 stdio 服务都要这样。

### 第 3 段：协议层——分发器骨架

```python
class MinimalMCPServer:
    def __init__(self):
        self.initialized = False       # 生命周期状态：顾客落座了吗
        self._handlers = {             # 路由表：method 名 → 处理函数
            "initialize": self._on_initialize,
            "ping": self._on_ping,
        }
```

为什么用字典而不是 if-else？因为后面五课会不断加方法
（`tools/list`、`resources/read`……），路由表让"加方法"变成"加一行"。

`handle_message` 是核心，处理顺序有讲究，一步都不能少：

```python
def handle_message(self, msg):
    # ① 先验格式：不是合法 JSON-RPC 2.0 → -32600
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return error_response(..., INVALID_REQUEST, ...)

    # ② 再分请求/通知：没有 id 的是通知 → 处理但返回 None（永不回包）
    if msg_id is None:
        self._on_notification(method, params)
        return None

    # ③ 生命周期门禁：没握手完成，只放行 initialize 和 ping
    if not self.initialized and method not in ("initialize", "ping"):
        return error_response(msg_id, INVALID_REQUEST, "server not initialized")

    # ④ 查路由表：找不到 → -32601（注意：是报错，不是崩溃！）
    handler = self._handlers.get(method)
    if handler is None:
        return error_response(msg_id, METHOD_NOT_FOUND, ...)

    # ⑤ 执行 + 兜底：业务异常统一转 -32603，保证服务器永不因一条消息挂掉
    try:
        return ok_response(msg_id, handler(params))
    except Exception as exc:
        return error_response(msg_id, INTERNAL_ERROR, str(exc))
```

顺序背后的逻辑：**越便宜的检查越先做**（格式 → 类型 → 状态 → 路由 → 执行），
任何一步失败就立刻返回，不浪费后面的工夫。

### 第 4 段：initialize 返回什么

```python
def _on_initialize(self, params):
    return {
        "protocolVersion": PROTOCOL_VERSION,   # 我说的是哪版协议
        "capabilities": {},                    # 我有哪些能力（本课后厨是空的！）
        "serverInfo": SERVER_INFO,             # 我叫什么、什么版本
    }
```

`capabilities` 是**能力协商**的关键：客户端只会调用你声明过的方法族。
本课声明为空 `{}`，所以规矩的客户端连 `tools/list` 都不会发。
Lesson 2 会在这里加上 `"tools": {...}`，Lesson 3 加 `"resources": {...}`——
你可以把它想成"菜单上有什么档口，开业时就要挂出来"。

### 第 5 段：传输层——主循环

```python
def stdio_main(server):
    for line in sys.stdin:            # 阻塞式逐行读：没消息就等着
        line = line.strip()
        if not line:
            continue                  # 空行容错：跳过
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            # JSON 都坏了，没法知道 id，只能用 id=null 回 -32700
            print(json.dumps(error_response(None, PARSE_ERROR, ...)), flush=True)
            continue
        resp = server.handle_message(msg)
        if resp is not None:          # None = 刚才是通知，不回包
            print(json.dumps(resp, ensure_ascii=False), flush=True)
```

留意传输层多么"傻"：它只认识"行"和"JSON"，
完全不知道什么是 initialize、什么是工具——这就是分层的纪律性。

## 运行与验证

方式一：手动交互（在 PowerShell 中运行后，逐行粘贴下面的 JSON）：

```powershell
py server.py
```

```json
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"manual","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":1,"method":"ping"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

预期结果逐条对照：

| 发送 | 预期 | 为什么 |
|------|------|--------|
| 第 1 条 initialize | 返回能力声明（capabilities 为空） | 握手第一步 |
| 第 2 条 initialized | **无任何输出** | 它是通知，通知不回包 |
| 第 3 条 ping | 返回 `{"jsonrpc":"2.0","id":1,"result":{}}` | 规范要求 ping 回空对象 |
| 第 4 条 tools/list | 返回 -32601 | 本课路由表里还没有它 |

方式二：自动化客户端：

```powershell
py ..\..\client\test_client.py --stdio server.py
```

## 动手实验（强烈建议做，比读十遍都有用）

1. **打破铁律 1**：把 `log()` 里的 `sys.stderr` 改成 `sys.stdout`，再跑测试客户端。
   观察：客户端在启动阶段就会报 JSON 解析错误——因为日志行混进了协议流。改回来。
2. **打破铁律 2**：删掉主循环 `print(...)` 里的 `flush=True`，再跑手动交互。
   观察：在某些环境下响应会延迟出现甚至不出现（缓冲未刷）。改回来。
3. **违反生命周期**：跳过前两条报文，直接发 `ping` 和 `tools/list`。
   观察：ping 通过（门禁白名单），tools/list 被拒（"server not initialized"）。

## 思考题

1. 为什么通知出错也不能回包？
   提示：客户端没在等这条响应；你回了，它就会试图把这条响应配对给某个还在等待的请求 id，
   造成"张冠李戴"。
2. `initialize` 和 `notifications/initialized` 为什么要分成两步，而不是握手一次完成？
   提示：客户端收到 initialize 响应后可能还要做自己的准备工作（注册能力、初始化 UI），
   `initialized` 通知是它明确说"我这边好了"——双向确认才算握手完成。

✅ 学完本课你已经拥有一个"能开门迎客的空餐厅"。
下一课 [lesson02-tools](../lesson02-tools/README.md) 我们给后厨开第一个炒菜档口。
