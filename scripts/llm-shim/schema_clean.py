#!/usr/bin/env python3
"""tool schema 清洗 · llm-shim 与 api 共用一份。

为什么要洗:opencode 送出的 function parameters 是完整 JSON Schema,带
`$schema` / `additionalProperties` / `anyOf` 之类关键字。Gemini(经 OneAPI 之类
的 OpenAI 兼容网关)只认 OpenAPI 子集,收到就整个请求报错:

    Invalid JSON payload received. Unknown name "$schema" at
    'tools[0].function_declarations[0].parameters': Cannot find field.

**为什么单独成文件**(设计方案 4.5):向导第 3 步的「工具调用」检测由 api 发出,
必须用与 shim 一模一样的清洗规则,否则检测通过了实际对话仍然失败、或者反过来。
两份实现迟早会漂,所以只留一份。M2 的 api 侧直接 import 这个模块。

**这里不改语义、只删约束**:被删掉的关键字都是更严格的校验,而工具调用的正确性
由 tool 自己的参数校验兜底。
"""

# Gemini 不认的 JSON Schema 关键字。
STRIP = {
    "additionalProperties", "exclusiveMinimum", "exclusiveMaximum",
    "const", "patternProperties", "dependentRequired", "dependentSchemas",
    "if", "then", "else", "not", "minContains", "maxContains",
    "unevaluatedItems", "unevaluatedProperties", "propertyNames",
    "maxProperties", "minProperties",
    "$schema", "$id", "$defs", "$ref",
}


def clean(node):
    if isinstance(node, dict):
        # allOf/oneOf/anyOf 一律塌缩成第一个分支 —— Gemini 不支持组合子句,
        # 保留第一支比整个丢掉更接近原意
        for k in ("allOf", "oneOf", "anyOf"):
            if k in node and isinstance(node[k], list) and node[k]:
                first = clean(node[k][0]) or {}
                sib = {kk: clean(vv) for kk, vv in node.items()
                       if kk not in ("allOf", "oneOf", "anyOf")}
                sib.update(first)
                return clean(sib)
        out = {}
        for k, v in node.items():
            if k in STRIP:
                continue
            out[k] = clean(v)
        # Gemini 要求 array 必须声明 items
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out
    if isinstance(node, list):
        return [clean(x) for x in node]
    return node


def ensure_object_schema(schema):
    """DeepSeek / OpenAI 严格模式要求 tool 的 parameters 必须是 type=object 的
    JSON Schema。opencode 打包某些 MCP tool 时(比如 github-pr-search)会送来
    `null` 或 `{"type": "null"}`,DeepSeek 会直接 400:
        Invalid schema for function 'xxx': schema must be a JSON Schema
        of 'type: "object"', got 'type: "null"'.
    这里统一兜底成合法 object schema,保留其它字段(description 等)。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    t = schema.get("type")
    if t is None or t in ("null", "None"):
        schema = {**schema, "type": "object"}
    if schema.get("type") == "object" and "properties" not in schema:
        schema["properties"] = {}
    return schema


def clean_tools(obj: dict) -> None:
    """就地清洗请求体里的 `tools[].function.parameters`。其余字段一概不碰。"""
    if not isinstance(obj.get("tools"), list):
        return
    for t in obj["tools"]:
        fn = t.get("function") if isinstance(t, dict) and isinstance(t.get("function"), dict) else None
        if fn and "parameters" in fn:
            fn["parameters"] = ensure_object_schema(clean(fn["parameters"]))
