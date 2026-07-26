# Lesson 3：资源系统（resources/list、templates、read）

> 前置：完成 Lesson 2。餐厅比喻：炒菜档口（工具）之外，这一课开一个**自助餐台**（资源）——
> 顾客自己走过去取，不用下单等厨师，而且只能"取用"不能"改动"。

## 学习目标

- 理解资源（Resource）与工具（Tool）的本质区别：**资源只读、无副作用**
- 实现静态资源：`resources/list` + `resources/read`
- 实现模板资源：`resources/templates/list` + URI 占位符匹配（`{workspace}`）
- 设计自定义 URI scheme（如 `tutorial://`，对照 UnifiedModel 的 `umodel://`）

## 核心概念

### 资源 vs 工具：什么时候用哪个？

| 维度 | Resource（自助餐台） | Tool（炒菜档口） |
|------|----------|------|
| 副作用 | 绝对没有（只能看不能动） | 可能有（写库、发请求…） |
| 谁决定使用 | 通常是客户端/用户挑选注入上下文 | 通常是 LLM 主动调用 |
| 参数形式 | 只有 URI（"哪一份数据"） | 任意 JSON 参数（"怎么做"） |
| 类比 | HTTP GET | HTTP POST |

UnifiedModel 契约中所有资源均声明 `"read_only": { "const": true }`——
用 JSON Schema 的 `const` 把"只读"写死在契约里，谁想加个可写资源，CI 直接拦下。

**选型经验法则**（思考题 1 的答案预告）：
纯读、参数只是"定位哪份数据"→ 做成资源；
需要复杂参数、昂贵计算或有副作用 → 做成工具。

### 资源注册表的原理分解

和工具注册表一样，资源注册表也是"字典 + 几个方法"，但多了一个新概念——**URI 模板**。

**第 1 步：两类资源，两种数据结构**

```python
@dataclass
class Resource:              # 静态资源：URI 固定，像"每天都有的例汤"
    uri: str                 # tutorial://guide/getting-started
    provider: Callable[[], str]              # 无参函数：URI 即唯一定位

@dataclass
class ResourceTemplate:      # 模板资源：URI 带占位符，像"任选口味的现做面"
    uri_template: str        # tutorial://workspace/{workspace}/overview
    provider: Callable[[Dict[str, str]], str]  # 入参 = 从 URI 提取的参数
```

**第 2 步：模板怎么匹配？一次正则转换**

把 `{param}` 变成正则命名组，之后匹配即提取：

```python
def template_to_regex(uri_template):
    # 原始:  tutorial://workspace/{workspace}/overview
    # ① re.escape 先把 URI 里的特殊字符转义（. / : 等）
    pattern = re.escape(uri_template)
    # ② 把 \{workspace\} 替换成命名组 (?P<workspace>[^/]+)
    pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", pattern)
    # 结果:  tutorial://workspace/(?P<workspace>[^/]+)/overview
    return re.compile(f"^{pattern}$")

# 用法：m = regex.match("tutorial://workspace/demo/overview")
#       m.groupdict()  →  {"workspace": "demo"}   ← 直接传给 provider！
```

`[^/]+` 的含义："一段不含斜杠的字符"——保证 `{workspace}` 只匹配一层路径，
不会把 `demo/xxx/yyy` 整个吞掉。

**第 3 步：读取时的查找顺序**

```python
def read(self, uri):
    # ① 先查静态资源（精确匹配，O(1) 字典查找，快）
    if uri in self._static: ...
    # ② 再逐个尝试模板（正则匹配，慢一些，所以放后面）
    for tpl in self._templates:
        m = template_to_regex(tpl.uri_template).match(uri)
        if m:
            return ... tpl.provider(m.groupdict()) ...
    # ③ 都没中 → -32602
    raise InvalidParamsError(f"unknown resource uri: {uri}")
```

### 本课的资源清单

对照真实契约中的 `umodel://workspace/{workspace}/overview`，本课实现：

```
tutorial://guide/getting-started            # 静态资源（例汤）
tutorial://workspace/{workspace}/overview   # 模板资源（现做面，口味=workspace）
tutorial://workspace/{workspace}/schema-index
```

### resources/read 响应格式

```json
{
  "contents": [
    {"uri": "tutorial://guide/getting-started", "mimeType": "text/markdown", "text": "..."}
  ]
}
```

`mimeType` 告诉客户端内容怎么渲染（markdown 渲染成富文本、json 高亮显示）。

## 运行与验证

```powershell
py server.py
```

握手后尝试：

```json
{"jsonrpc":"2.0","id":2,"method":"resources/list"}
{"jsonrpc":"2.0","id":3,"method":"resources/templates/list"}
{"jsonrpc":"2.0","id":4,"method":"resources/read","params":{"uri":"tutorial://guide/getting-started"}}
{"jsonrpc":"2.0","id":5,"method":"resources/read","params":{"uri":"tutorial://workspace/demo/overview"}}
{"jsonrpc":"2.0","id":6,"method":"resources/read","params":{"uri":"tutorial://nonexistent"}}
```

预期结果逐条对照：

| 发送 | 预期 | 验证的知识点 |
|------|------|--------------|
| id=2 | 只列出 1 个静态资源 | 静态与模板分开列 |
| id=3 | 列出 2 个 uriTemplate | 模板专用列表 |
| id=4 | 返回入门指南 markdown | 静态资源精确匹配 |
| id=5 | 内容里出现 "Workspace: demo" | 模板成功提取 `workspace=demo` |
| id=6 | -32602 unknown resource uri | 查找失败的兜底 |

## 动手实验

1. **加一个模板参数**：新增模板 `tutorial://workspace/{workspace}/dataset/{name}`，
   provider 返回"workspace X 的数据集 Y 详情"。验证两个参数都能提取到。
2. **验证 `[^/]+` 的作用**：读取 `tutorial://workspace/a/b/overview`（workspace 里带斜杠），
   确认匹配失败返回 -32602——这正是我们想要的严格性。
3. **试试"不存在的工作区"**：读取 `tutorial://workspace/nope/overview`，
   观察错误来自 provider 内部（workspace not found）而不是 URI 匹配——
   体会"URI 合法"与"数据存在"是两层检查。

## 思考题

1. "工作区概览"应该做成资源还是工具？如果概览生成需要昂贵计算、且要传参数控制粒度呢？
   （用上文的经验法则回答：纯读且参数只是"定位" → 资源；复杂参数/计算 → 工具。）
2. UnifiedModel 为什么把 `query-templates`（查询模板集）做成资源而不是工具？
   提示：它是一份"给 LLM 参考的静态知识"，读它没有任何副作用，
   而且通常是客户端主动注入上下文，而非 LLM 临时调用。

✅ 下一课 [lesson04-http-transport](../lesson04-http-transport/README.md)：
换一个"服务员"——把同一家餐厅从 stdio 搬到 HTTP 上，验证分层架构的价值。
