# Lesson 3：资源系统（resources/list、templates、read）

> 前置：完成 Lesson 2。本课新增**资源注册表**（工具部分沿用 Lesson 2 的 calc 作精简演示）。

## 学习目标

- 理解资源（Resource）与工具（Tool）的本质区别：**资源只读、无副作用**
- 实现静态资源：`resources/list` + `resources/read`
- 实现模板资源：`resources/templates/list` + URI 占位符匹配（`{workspace}`）
- 设计自定义 URI scheme（如 `tutorial://`，对照 UnifiedModel 的 `umodel://`）

## 核心概念

### 资源 vs 工具

| 维度 | Resource | Tool |
|------|----------|------|
| 副作用 | 绝对没有 | 可能有 |
| 谁决定使用 | 通常是客户端/用户挑选注入上下文 | 通常是 LLM 主动调用 |
| 类比 | GET | POST |

UnifiedModel 契约中所有资源均 `"read_only": { "const": true }`——协议层面强制只读。

### URI 模板

对照真实契约中的 `umodel://workspace/{workspace}/overview`，本课实现：

```
tutorial://guide/getting-started            # 静态资源
tutorial://workspace/{workspace}/overview   # 模板资源
tutorial://workspace/{workspace}/schema-index
```

模板匹配实现思路：把 `{param}` 转成正则命名组 `(?P<param>[^/]+)`，
`resources/read` 时先精确匹配静态资源，再逐个尝试模板。

### resources/read 响应格式

```json
{
  "contents": [
    {"uri": "tutorial://guide/getting-started", "mimeType": "text/markdown", "text": "..."}
  ]
}
```

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

预期：id=5 能从 URI 中提取出 `workspace=demo` 并渲染进内容；id=6 返回 -32602。

## 思考题

1. "工作区概览"应该做成资源还是工具？如果概览生成需要昂贵计算、且要传参数控制粒度呢？
   （经验法则：纯读且参数只是"定位哪份数据"→ 资源；需要复杂参数/计算 → 工具）
2. UnifiedModel 为什么把 `query-templates`（查询模板集）做成资源而不是工具？
