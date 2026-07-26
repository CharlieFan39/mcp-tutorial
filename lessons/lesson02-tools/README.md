# Lesson 2：工具系统（tools/list 与 tools/call）

> 前置：完成 Lesson 1。餐厅比喻：这一课我们开出第一批炒菜档口（工具），
> 挂出菜单（tools/list），并建立一套"点菜 → 验食材 → 炒菜 → 上菜"的标准流程（tools/call）。

## 学习目标

- 设计 `Tool` 数据结构与 `ToolRegistry` 注册表
- 实现 `tools/list`：向 LLM 声明工具清单（含 JSON Schema）
- 实现 `tools/call`：查找 → 校验输入 → 执行 → 包装结果
- 区分**协议错误**（JSON-RPC error）与**工具业务错误**（`isError: true`）
- 手写一个迷你 JSON Schema 校验器

## 核心概念

### 工具注册表是怎么工作的？（原理分解）

"注册表"听起来玄乎，其实就是一个字典 + 三个方法。逐步拆开看：

**第 1 步：定义"一个工具长什么样"**——元数据 + 处理函数：

```python
@dataclass
class Tool:
    name: str            # 菜名（唯一标识，LLM 用它点菜）
    description: str     # 菜单描述（给 LLM 看，决定它何时点这道菜）
    input_schema: dict   # 食材清单（JSON Schema，声明需要哪些参数）
    handler: Callable    # 厨师本人（真正执行的函数）
    enabled_by_default: bool = True   # 是否上架
```

对照真实契约 `UnifiedModel/api/mcp/tools.schema.json`：每个工具必须有
`name` / `input_schema` / `output_schema` / `enabled_by_default`，字段一一对应。

**第 2 步：注册**——把工具收进字典，重名直接报错：

```python
def register(self, tool: Tool) -> None:
    if tool.name in self._tools:
        raise ValueError(f"duplicate tool name: {tool.name}")   # 启动即失败
    self._tools[tool.name] = tool
```

为什么重名要**启动时就崩**而不是运行时再说？
配置错误越早暴露修复成本越低——服务起不来，一眼就能发现；
带病运行到线上，排查成本翻百倍。这叫 **fail fast** 原则。

**第 3 步：列出**——`tools/list` 只暴露元数据，绝不暴露 handler：

```python
def list_tools(self) -> List[dict]:
    return [{"name": t.name, "description": t.description,
             "inputSchema": t.input_schema}          # 注意：没有 handler！
            for t in self._tools.values() if t.enabled_by_default]
```

**第 4 步：调用**——`tools/call` 的四步流水线（Lesson 5 会插入权限检查扩成六步）：

```
查找（菜单上有吗）→ 校验（食材齐吗）→ 执行（开火）→ 包装（摆盘）
```

### description 是写给 LLM 看的——这是最重要的提示工程

传统 API 文档是给程序员看的；MCP 工具的 `description` 是 LLM 决策的唯一依据。
对比感受一下：

```
❌ 差描述："查询指标"
   （LLM 不知道什么时候该用它、参数怎么填）

✅ 好描述："查询指定服务的时序指标数据。当用户询问某服务的延迟、QPS、
   错误率等监控数据时使用。返回按分钟采样的数据点列表。"
   （何时用 + 参数含义 + 返回什么，三要素齐全）
```

### tools/list 响应格式

```json
{
  "tools": [
    {
      "name": "query_metrics",
      "description": "查询指定服务的指标数据……",
      "inputSchema": {"type": "object", "properties": {...}, "required": [...]}
    }
  ]
}
```

`inputSchema` 让 LLM 在调用前就知道参数的类型、哪些必填、枚举有哪些选项——
相当于菜单上写清楚"微辣/中辣/特辣三选一"，而不是让顾客瞎猜。

### tools/call 的两类失败（本课最重要的概念）

| 失败类型 | 例子 | 返回方式 | 直觉记忆 |
|----------|------|----------|----------|
| 协议错误 | 工具不存在、参数缺失 | JSON-RPC `error`（-32602） | 点菜单写错了 |
| 业务错误 | 除零、查询超时 | `result` 中 `isError: true` | 菜炒糊了 |

为什么要分两条路？**业务错误是"合法的结果"**：
LLM 能读到 `isError` 里的错误文本，自己修正参数后重试
（比如把除数 0 换成别的值）；而协议错误意味着客户端代码有 bug，重试也没用。

### 迷你 JSON Schema 校验器（server.py 中 validate_schema）

本课手写了一个约 30 行的校验器，覆盖 `type` / `required` / `enum` /
`properties` / `minimum` / `maxLength` 六个最常用关键字。两个实现细节值得注意：

```python
# 细节 1：Python 的 bool 是 int 的子类！不排除的话 True 会通过 "type": "integer" 校验
if expected in ("integer", "number") and isinstance(value, bool):
    raise InvalidParamsError(...)

# 细节 2：错误消息带字段路径（如 "arguments(calc).op"），
# LLM 看到具体哪个字段错了，才能精准自我纠正
raise InvalidParamsError(f"{path}: value {value!r} not in enum {schema['enum']}")
```

## 本课的三个示例工具

| 工具 | 功能 | 教学目的 | 对应真实工具 |
|------|------|----------|--------------|
| `query_metrics` | 查询模拟指标数据 | 演示"好的 description"写法 | `query_spl_execute` |
| `query_explain` | 解释查询语句结构 | 演示只读分析型工具 | `query_spl_explain` |
| `calc` | 四则运算 | 演示 enum 校验 + 业务错误 | —— |

## 运行与验证

```powershell
py server.py
```

握手后（报文同 Lesson 1），尝试：

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"calc","arguments":{"op":"add","a":2,"b":3}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"calc","arguments":{"op":"div","a":1,"b":0}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"calc","arguments":{"op":"pow"}}}
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"query_metrics","arguments":{"service":"checkout","metric":"latency_p99"}}}
```

预期结果逐条对照（重点体会三条失败路径的不同）：

| 发送 | 预期 | 走的是哪条路 |
|------|------|--------------|
| id=3 add 2+3 | `"text": "5"`，`isError: false` | 成功 |
| id=4 div 1/0 | `"text": "tool failed: division by zero"`，`isError: true` | 业务错误（菜炒糊了） |
| id=5 op=pow 且缺 a/b | JSON-RPC error -32602，消息指明具体字段 | 协议错误（菜单写错了） |
| id=6 查指标 | 返回 5 个数据点 | 成功 |

## 动手实验

1. **加一道新菜**：仿照 `calc` 注册一个 `string_length` 工具
   （入参 `{"text": string}`，返回长度）。跑通 `tools/list` 能看到它、`tools/call` 能调它。
2. **体验 fail fast**：把 `string_length` 注册两次，观察服务器启动即报
   `duplicate tool name`——这比运行时才发现好得多。
3. **体验校验器**：给 `calc` 传 `"a": true`（布尔值），确认被
   "bool 不是 number"的检查拦下（细节 1 的价值）。

## 思考题

1. 为什么 `tools/list` 里要带 `inputSchema` 而不是让 LLM 猜参数？
   提示：想想菜单上不写"辣度三选一"会发生什么。
2. 如果两个工具重名，注册表应该怎么处理？
   本课的答案是启动即抛异常（fail fast）。还有别的策略吗？各有什么代价？
   （如"后注册覆盖先注册"——灵活但容易静默出错；"自动改名"——不可预测。）

✅ 下一课 [lesson03-resources](../lesson03-resources/README.md)：
炒菜档口有了，再开一个"自助餐台"（只读资源系统）。
