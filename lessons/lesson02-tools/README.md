# Lesson 2：工具系统（tools/list 与 tools/call）

> 前置：完成 Lesson 1。本课在其基础上新增**工具注册表**。

## 学习目标

- 设计 `Tool` 数据结构与 `ToolRegistry` 注册表
- 实现 `tools/list`：向 LLM 声明工具清单（含 JSON Schema）
- 实现 `tools/call`：查找 → 校验输入 → 执行 → 包装结果
- 区分**协议错误**（JSON-RPC error）与**工具业务错误**（`isError: true`）
- 手写一个迷你 JSON Schema 校验器

## 核心概念

### 工具定义 = 元数据 + 处理函数

对照 `UnifiedModel/api/mcp/tools.schema.json`，每个工具必须有
`name` / `input_schema` / `output_schema` / `enabled_by_default`。
本课实现三个示例工具（模拟 UnifiedModel 的查询场景）：

| 工具 | 功能 | 对应真实工具 |
|------|------|--------------|
| `query_metrics` | 查询模拟指标数据 | `query_spl_execute` |
| `query_explain` | 解释查询语句结构 | `query_spl_explain` |
| `calc` | 四则运算（演示参数校验） | —— |

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

`description` 是给 LLM 看的——写得越清楚（何时用、参数含义、返回什么），
模型的调用准确率越高。这是 MCP 工具设计中**最重要的提示工程**。

### tools/call 的两类失败

| 失败类型 | 例子 | 返回方式 |
|----------|------|----------|
| 协议错误 | 工具不存在、参数缺失 | JSON-RPC `error`（-32602） |
| 业务错误 | 除零、查询超时 | `result` 中 `isError: true` |

业务错误让 LLM 能"看见"错误文本并自行重试纠正；协议错误则表示客户端代码写错了。

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

预期：id=3 返回 5；id=4 返回 `isError: true`（业务错误）；
id=5 返回 -32602（schema 校验失败：op 不在 enum 中且缺少 a/b）。

## 思考题

1. 为什么 `tools/list` 里要带 `inputSchema` 而不是让 LLM 猜参数？
2. 如果两个工具重名，注册表应该怎么处理？（本课实现：直接抛异常，启动即失败——尽早暴露配置错误）
