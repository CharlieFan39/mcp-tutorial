# 第二章：MCP 服务器架构设计

> 读完本章你将明白：一个 MCP 服务器的代码应该怎么"切"成几块，每块负责什么，
> 以及为什么这样切之后代码更好写、更好改、更好测。

## 2.1 先建立直觉：把 MCP 服务器想象成一家餐厅

在看抽象的"分层架构"之前，先用一家餐厅来理解整个系统。
这个比喻会贯穿全部 6 课，请务必记住它：

```
        LLM 客户端（顾客）
              │  点菜 / 上菜
              ▼
┌─────────────────────────────────┐
│  传输层 = 服务员                 │   只负责"传话"：把点单送进去、把菜端出来。
│  （stdio / HTTP）                │   服务员不需要会做菜，也不关心菜谱。
├─────────────────────────────────┤
│  协议层 = 前台领班               │   检查点单写得对不对（JSON-RPC 格式）、
│  （JSON-RPC 分发器）             │   看点的是哪个档口的菜（method 路由）、
│                                  │   客人还没落座就点菜要拦住（生命周期门禁）。
├─────────────────────────────────┤
│  能力层 = 后厨各档口             │   真正干活的地方：
│  （工具/资源/提示 注册表）        │   · 炒菜档口 = 工具（Tools，执行操作）
│                                  │   · 自助餐台 = 资源（Resources，只读取用）
│                                  │   · 今日推荐菜单 = 提示（Prompts，模板）
└─────────────────────────────────┘
     横切关注点 = 餐厅保安 + 监控摄像头（认证授权 + 审计日志）
```

对应到代码世界：

| 餐厅角色 | 架构层 | 职责一句话 | 真实项目对应文件 |
|----------|--------|-----------|------------------|
| 服务员 | 传输层 Transport | 收字节、发字节，**不理解内容** | `UnifiedModel/cmd/umodel-mcp/http.go` |
| 前台领班 | 协议层 Protocol | 验格式、找档口、兜底错误 | `UnifiedModel/cmd/umodel-mcp/protocol.go` |
| 后厨档口 | 能力层 Capabilities | 真正执行业务逻辑 | 同上（工具实现部分） |
| 保安+摄像头 | 横切层 | 认证授权、日志（每层都会经过） | Lesson 5 实现 |

**为什么要这样切？** 想象一下不分层的后果：服务员既要传话又要炒菜——
换一个上菜方式（stdio 换成 HTTP）就得把炒菜的代码全部重写一遍。
分层之后，"换服务员不用换厨师"：Lesson 4 会亲手验证，
同一套协议层+能力层代码，不改一行就能同时跑在 stdio 和 HTTP 上。

## 2.2 一条消息的完整旅程

理解分层最好的方式，是跟着一条真实消息走一遍。
假设 LLM 想调用 `calc` 工具计算 2+3：

```
① 客户端发出请求（顾客点菜）
   {"jsonrpc":"2.0","id":7,"method":"tools/call",
    "params":{"name":"calc","arguments":{"op":"add","a":2,"b":3}}}
        │
        ▼
② 传输层收到一行字节（服务员收到点单纸条）
   - stdio 模式：从 stdin 读到这一行
   - HTTP 模式：从 POST /mcp 的请求体读到
   - 它只做一件事：json.loads() 解析成 dict，交给协议层
        │
        ▼
③ 协议层分发（前台领班看单）
   - 检查格式：有没有 "jsonrpc":"2.0"？有没有 method？ ✔
   - 看 id：id=7，是请求（要回话）；如果没有 id 就是通知（不回话）
   - 生命周期：客户端已完成 initialize 握手了吗？ ✔
   - 查路由表：method="tools/call" → 交给工具注册表处理
        │
        ▼
④ 能力层执行（后厨炒菜）
   - 查菜单：注册表里有叫 "calc" 的工具吗？ ✔
   - 验食材：arguments 符合该工具声明的 JSON Schema 吗？ ✔
   - 开火：调用 handler({"op":"add","a":2,"b":3}) → 得到 5
   - 摆盘：包装成 MCP 标准格式 {"content":[{"type":"text","text":"5"}],"isError":false}
        │
        ▼
⑤ 协议层封装响应（领班核对单号）
   {"jsonrpc":"2.0","id":7,"result":{...④的结果...}}
        │
        ▼
⑥ 传输层发回（服务员上菜）
   - stdio：写到 stdout 并 flush
   - HTTP：作为 200 响应的 body 返回
```

任何一步出问题，都由**离问题最近的层**负责报错：
- ② JSON 都解析不了 → 传输层直接回 `-32700 Parse error`；
- ③ method 没人认识 → 协议层回 `-32601 Method not found`；
- ④ 参数不合规 → 能力层抛异常，协议层统一转成 `-32602 Invalid params`。

## 2.3 各层设计详解

### 传输层：只当"传话筒"

**stdio 模式**（本地场景，如 Claude Desktop 拉起本地进程）：

```python
for line in sys.stdin:            # 一行 = 一条消息，这是 stdio 传输的约定
    msg = json.loads(line)        # 字节 → dict，传输层的全部"理解"仅此而已
    resp = server.handle_message(msg)   # 剩下的事全部交给协议层
    if resp is not None:                # None 表示这是通知，不用回
        print(json.dumps(resp), flush=True)   # flush 必须有！否则响应可能卡在缓冲区
```

⚠️ **stdio 模式的头号大坑**：stdout 是协议通道，**日志必须走 stderr**。
只要有一句 `print("debug...")` 混进 stdout，客户端就会把它当协议报文解析，直接报错。
这也是为什么所有课程的 `log()` 函数都写死 `file=sys.stderr`。

**HTTP 模式**（远程场景，多客户端共享一个服务器）：
单一端点 `POST /mcp`；`initialize` 成功后服务器签发 `Mcp-Session-Id` 响应头，
客户端后续请求都带上这个头——就像餐厅给你一个桌号牌，
服务员凭桌号知道你是哪桌的（详见 Lesson 4）。

### 协议层：一个字典搞定路由

协议层的核心是一个**分发器（dispatcher）**。新手最容易写成一长串 if-else，
更好的做法是"路由表"——一个 `method 名 → 处理函数` 的字典：

```python
class MCPServer:
    def __init__(self):
        self.initialized = False          # 生命周期状态：客人落座了吗
        self._handlers = {                # 路由表：点哪道菜找哪个档口
            "initialize": self._on_initialize,
            "ping":       lambda p: {},   # ping 就是回个空对象，一行搞定
            "tools/list": self._on_tools_list,
            "tools/call": self._on_tools_call,
        }

    def handle_message(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")

        if msg_id is None:                # 没有 id = 通知：处理但绝不回包
            self._handle_notification(method, msg.get("params") or {})
            return None

        handler = self._handlers.get(method)
        if handler is None:               # 菜单上没有这道菜
            return error_response(msg_id, -32601, f"Method not found: {method}")
        try:
            return ok_response(msg_id, handler(msg.get("params") or {}))
        except InvalidParams as e:        # 业务代码只管抛异常……
            return error_response(msg_id, -32602, str(e))
        except Exception as e:            # ……分发器统一兜底转成 JSON-RPC error
            return error_response(msg_id, -32603, str(e))
```

这段不到 25 行的代码浓缩了协议层的三条铁律：

1. **通知永不回包**——哪怕处理时出错也不回。这是 JSON-RPC 的硬性规定：
   客户端没在等回话，你硬回一条，反而会被误认为是其它请求的响应，把消息流搞乱；
2. **路由表代替 if-else**——加新方法 = 往字典里加一行，不用改分发逻辑；
3. **异常统一兜底**——业务代码（能力层）只管抛异常，转换成 JSON-RPC 错误码
   这件事在分发器里只做一次，保证任何异常都不会让服务器崩溃。

### 能力层①：工具注册表（ToolRegistry）

工具 = **元数据 + 处理函数**。先看数据结构，
每个字段都对照真实契约 `UnifiedModel/api/mcp/tools.schema.json` 的必填项：

```python
@dataclass
class Tool:
    name: str                    # 唯一名（菜名），如 query_metrics
    description: str             # 给 LLM 看的菜单描述——写得越清楚，LLM 点菜越准
    input_schema: dict           # JSON Schema：这道菜需要哪些"食材"（参数）
    handler: Callable            # 真正"炒菜"的函数
    enabled_by_default: bool = True               # 这道菜默认上架吗
    requires_explicit_write_enable: bool = False  # 危险菜品：需要店长解锁（写开关）
    required_scope: str = "read"                  # 顾客需要什么会员等级才能点（Lesson 5）
```

`tools/call` 的执行是一条固定流水线，六步缺一不可：

```
① 查找工具     注册表里有这个 name 吗？没有 → -32602
② 检查开关     工具被禁用/写开关未开？→ 当作不存在处理（不泄露其存在）
③ 检查权限     调用者的 scope 够吗？不够 → 拒绝并留审计日志（Lesson 5）
④ 校验输入     arguments 过一遍 input_schema，字段错 → -32602 + 指明哪个字段
⑤ 执行         调用 handler(arguments)
⑥ 包装结果     成功/业务失败都包成 MCP 标准 content 格式
```

第⑥步的"包装"有个新手必须理解的关键区分——**两种失败走两条路**：

| 失败类型 | 例子 | 返回方式 | 为什么 |
|----------|------|----------|--------|
| 协议错误 | 工具不存在、缺参数 | JSON-RPC `error`（-32602） | 说明**客户端代码写错了**，该修代码 |
| 业务错误 | 除零、查询超时 | `result` 里 `isError: true` | 说明**这次尝试失败了**，LLM 看到错误文本能自己换参数重试 |

```json
// 业务失败的正确姿势：它是"合法的结果"，不是"协议出错"
{"content": [{"type": "text", "text": "tool failed: division by zero"}],
 "isError": true}
```

一句话记忆：**"点菜单写错了"回 error，"菜炒糊了"回 isError**。

### 能力层②：资源注册表（ResourceRegistry）

资源 = 自助餐台：只读、无副作用，顾客（客户端/用户）自己挑选取用。
两类资源的区别：

1. **静态资源**：URI 固定，像"每天都有的例汤"——
   `tutorial://guide/getting-started`，直接列在 `resources/list`；
2. **模板资源**：URI 带占位符，像"任选口味的现做面条"——
   `tutorial://workspace/{workspace}/overview`，
   列在 `resources/templates/list`，读取时从 URI 中提取参数。

模板匹配的实现只需一步正则转换（Lesson 3 详解）：

```python
# "tutorial://ws/{id}/x"  →  正则 "tutorial://ws/(?P<id>[^/]+)/x"
# 之后 m.groupdict() 就能拿到 {"id": "..."} 传给内容生成函数
pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(uri_template))
```

`resources/read` 的查找顺序：**先精确匹配静态资源，再逐个尝试模板**——
就像先看今日例汤，没有再看现做档口。

## 2.4 模块文件划分建议

Lesson 6 的完整服务器按此划分（单文件课程用"类"划分代替"文件"划分，思路一致）：

```
server.py        # 入口：解析参数、加载配置、装配、启动（≈ 餐厅开业流程）
config.json      # 配置：传输、端口、认证、工具开关（≈ 营业执照+菜单开关）
transport 部分   # StdioTransport / HttpTransport（服务员）
protocol 部分    # MCPServer 分发器 + 生命周期（前台领班）
tools 部分       # ToolRegistry + 各工具实现（炒菜档口）
resources 部分   # ResourceRegistry（自助餐台）
auth 部分        # Authenticator（保安，Lesson 5 引入）
```

## 2.5 设计检查清单（附"为什么"与自查方法）

每一项都给出了违反后果和验证方法，写完自己的服务器后逐项过一遍：

- [ ] **stdout 只输出协议报文，日志全部走 stderr**（stdio 模式）
  为什么：一行日志混入 stdout 就会污染协议流，客户端解析报错。
  自查：全局搜索 `print(`，确认每一处要么是协议响应、要么带 `file=sys.stderr`。

- [ ] **通知（无 id）不回包**
  为什么：客户端没在等待，多余的回包会被误配给其它请求。
  自查：向服务器发 `{"jsonrpc":"2.0","method":"notifications/initialized"}`，
  确认 stdout 没有任何输出。

- [ ] **未知 method 返回 -32601 而不是崩溃**
  为什么：客户端能力各异，发来没实现的方法很正常，崩溃 = 服务不可用。
  自查：发一条 `{"jsonrpc":"2.0","id":1,"method":"foo/bar"}`，应收到 -32601。

- [ ] **工具业务错误用 `isError: true`，协议错误才用 JSON-RPC error**
  为什么：`isError` 让 LLM 能读到失败原因并自我纠正；error 则中断这轮调用。
  自查：用 `calc` 除以零，确认返回的是 result（含 isError）而不是 error。

- [ ] **写操作工具默认禁用，需配置显式开启**
  为什么：防止 LLM 幻觉调用造成数据破坏（详见第三章 3.3）。
  自查：默认启动后调 `tools/list`，确认写工具不在列表里。

- [ ] **initialize 之前拒绝业务请求**
  为什么：握手没完成，能力还没协商，此时的业务请求属于协议违规。
  自查：跳过 initialize 直接发 `tools/list`，应收到 "server not initialized"。

## 2.6 本章小结（30 秒回顾）

- 三层架构 = 服务员（传输）+ 前台领班（协议）+ 后厨档口（能力），
  外加保安和摄像头（认证与审计）；
- 分层的最大收益：**换传输层不用动业务代码**；
- 协议层三铁律：通知不回包、路由表分发、异常统一兜底；
- 工具调用六步流水线：查找 → 开关 → 权限 → 校验 → 执行 → 包装；
- 两种失败两条路：点菜单写错了回 error，菜炒糊了回 isError。

下一章：[03-auth-and-security.md](03-auth-and-security.md) —— 给餐厅配上保安与摄像头。
