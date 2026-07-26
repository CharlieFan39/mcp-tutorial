# 第二章：MCP 服务器架构设计

## 2.1 三层架构

一个可维护的 MCP 服务器应当分为三层（真实项目 `UnifiedModel/cmd/umodel-mcp/` 也是这样划分的：
`main.go` 装配、`http.go` 传输、`protocol.go` 协议与能力）：

```
┌─────────────────────────────────────────────────┐
│                  传输层 Transport                │
│   stdio（stdin/stdout 逐行读写）                 │
│   streamable-http（POST /mcp + 会话头）          │
│   职责：收字节、发字节，不理解业务                │
├─────────────────────────────────────────────────┤
│                  协议层 Protocol                 │
│   JSON-RPC 解析/封装、方法路由、错误码           │
│   生命周期状态机（未初始化→已初始化）             │
│   职责：把 method 分发给能力层，统一错误处理      │
├─────────────────────────────────────────────────┤
│                  能力层 Capabilities             │
│   ToolRegistry（工具注册表 + 输入校验 + 执行）    │
│   ResourceRegistry（资源注册表 + URI 模板匹配）   │
│   PromptRegistry（提示模板）                     │
│   职责：真正干活的业务代码                       │
└─────────────────────────────────────────────────┘
         横切关注点：认证授权、日志、配置
```

**分层的核心收益**：传输层可插拔。同一套协议层+能力层代码，
既能跑在 stdio 上（本地），也能跑在 HTTP 上（远程）——Lesson 4 会亲手验证这一点。

## 2.2 各层设计要点

### 传输层

- **stdio**：主循环 `for line in stdin`，每行一个 JSON；响应写 stdout 后必须 `flush()`；
  日志一律走 stderr。
- **http**：单端点 `POST /mcp`；`initialize` 成功后签发 `Mcp-Session-Id` 响应头，
  后续请求带该头识别会话；`DELETE /mcp` 终止会话。

### 协议层

协议层的核心是一个**分发器（dispatcher）**，教程中的实现模式：

```python
class MCPServer:
    def handle_message(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:            # 通知：处理但不回包
            self._handle_notification(method, msg.get("params") or {})
            return None
        handler = self._handlers.get(method)
        if handler is None:
            return error_response(msg_id, -32601, f"Method not found: {method}")
        try:
            return ok_response(msg_id, handler(msg.get("params") or {}))
        except InvalidParams as e:
            return error_response(msg_id, -32602, str(e))
        except Exception as e:
            return error_response(msg_id, -32603, str(e))
```

要点：
1. **通知永不回包**（哪怕出错），这是 JSON-RPC 的硬性规定；
2. 每个 method 一个 handler 函数，注册进字典，避免巨型 if-else；
3. 异常统一在分发器兜底转成 JSON-RPC error，业务代码只管抛异常。

### 能力层：工具注册表

工具的元数据结构（对照 `tools.schema.json` 中每个 tool 的必填字段）：

```python
@dataclass
class Tool:
    name: str                              # 唯一名，如 query_spl_execute
    description: str                       # 给 LLM 看的说明，写清楚何时该用
    input_schema: dict                     # JSON Schema，声明参数
    handler: Callable[[dict], Any]         # 执行函数
    enabled_by_default: bool = True        # 默认是否开启
    requires_explicit_write_enable: bool = False  # 写操作双重开关
    required_scope: str = "read"           # 所需权限作用域（Lesson 5）
```

`tools/call` 的执行流水线：

```
查找工具 → 检查开关 → 检查权限(scope) → 校验输入(schema) → 执行 → 包装结果
```

结果包装为 MCP 标准格式：

```json
{
  "content": [{"type": "text", "text": "..."}],
  "isError": false
}
```

注意：**工具的业务失败**（如查询语法错）应返回 `isError: true` 的 result，
而不是 JSON-RPC error——后者只用于协议级错误。这样 LLM 能"看到"失败原因并自行纠正。

### 能力层：资源注册表

两类资源（对照契约中的 `resources` 数组）：

1. **静态资源**：固定 URI，直接列在 `resources/list`；
2. **模板资源**：URI 含占位符（如 `umodel://workspace/{workspace}/overview`），
   列在 `resources/templates/list`，读取时用正则匹配提取参数。

## 2.3 模块文件划分建议

教程 Lesson 6 的完整服务器按此划分（单文件课程则用类划分代替文件划分）：

```
server.py        # 入口：解析参数、加载配置、装配、启动
config.json      # 配置：传输、端口、认证、工具开关
transport 部分   # StdioTransport / HttpTransport
protocol 部分    # MCPServer（分发器 + 生命周期）
tools 部分       # ToolRegistry + 各工具实现
resources 部分   # ResourceRegistry
auth 部分        # Authenticator（Lesson 5 引入）
```

## 2.4 设计检查清单

- [ ] stdout 只输出协议报文，日志全部走 stderr（stdio 模式）
- [ ] 通知（无 id）不回包
- [ ] 未知 method 返回 -32601 而不是崩溃
- [ ] 工具业务错误用 `isError: true`，协议错误才用 JSON-RPC error
- [ ] 写操作工具默认禁用，需配置显式开启
- [ ] initialize 之前拒绝业务请求（返回错误 "server not initialized"）

下一章：[03-auth-and-security.md](03-auth-and-security.md) —— 认证授权与安全模型。
