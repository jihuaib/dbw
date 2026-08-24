"""多模型接入 —— Claude / DeepSeek / GLM / 任意 OpenAI 兼容端点。

两条实现路径：
  anthropic          用官方 Anthropic SDK，结构化输出走 output_config.format
  openai_compatible  走 /chat/completions，结构化输出用 response_format=json_object
                     + 把 schema 写进系统提示，返回后再做一次校验

对一致性的影响：**provider + model + base_url 全部进诊断指纹**。
换模型等于换诊断口径，旧的冻结答案自动失效 —— 这是有意的。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# 预设：用户选一个预设就自动填好 base_url 与常用模型
PRESETS: List[Dict[str, Any]] = [
    {
        "id": "claude", "label": "Claude（Anthropic）", "provider": "anthropic",
        "base_url": "", "models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        "hint": "官方 SDK，结构化输出最稳，推荐 claude-opus-5",
    },
    {
        "id": "deepseek", "label": "DeepSeek", "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "hint": "OpenAI 兼容端点；deepseek-reasoner 推理更强但更慢",
    },
    {
        "id": "glm", "label": "智谱 GLM", "provider": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.6", "glm-4-plus", "glm-4-air"],
        "hint": "OpenAI 兼容端点",
    },
    {
        "id": "qwen", "label": "通义千问", "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus"],
        "hint": "阿里云 DashScope 的 OpenAI 兼容模式",
    },
    {
        "id": "custom", "label": "自定义（OpenAI 兼容）", "provider": "openai_compatible",
        "base_url": "", "models": [],
        "hint": "任何 OpenAI 兼容端点：vLLM / Ollama / 自建网关等",
    },
]

PRESET_BY_ID = {p["id"]: p for p in PRESETS}

JSON_INSTRUCTION = (
    "\n\n【输出格式】只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。"
    "必须严格符合下面这个 JSON Schema：\n{schema}"
)


class ProviderError(Exception):
    pass


def _extract_json(text: str) -> Dict[str, Any]:
    """从模型回复里取出 JSON。兼容它偶尔套一层代码块的情况。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            return json.loads(s[start:end + 1])
        raise


def call_anthropic(api_key: str, model: str, system: str, content: str,
                   schema: Dict[str, Any], max_tokens: int, effort: str) -> Dict[str, Any]:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        raise ProviderError("模型拒绝了本次请求")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def call_openai_compatible(api_key: str, base_url: str, model: str, system: str,
                           content: str, schema: Dict[str, Any],
                           max_tokens: int) -> Dict[str, Any]:
    import httpx
    if not base_url:
        raise ProviderError("未配置 base_url")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        # 温度置 0 只是尽力而为 —— 一致性靠的是指纹冻结，不靠这个参数
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system",
             "content": system + JSON_INSTRUCTION.format(
                 schema=json.dumps(schema, ensure_ascii=False))},
            {"role": "user", "content": content},
        ],
    }
    with httpx.Client(timeout=300.0) as client:
        r = client.post(url, json=payload,
                        headers={"Authorization": "Bearer " + api_key,
                                 "Content-Type": "application/json"})
        if r.status_code >= 400:
            raise ProviderError("HTTP {0}: {1}".format(r.status_code, r.text[:300]))
        body = r.json()
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise ProviderError("返回体结构异常：{0}".format(json.dumps(body)[:300]))
    return _extract_json(text)


def validate(data: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    """轻量 schema 校验：只查顶层必填字段与类型。

    Anthropic 的 json_schema 是服务端强约束，这里主要是给 OpenAI 兼容端点兜底 ——
    它们只保证「是合法 JSON」，不保证「符合 schema」。
    """
    if not isinstance(data, dict):
        raise ProviderError("返回的不是 JSON 对象")
    props = schema.get("properties") or {}
    for field in schema.get("required") or []:
        if field not in data:
            raise ProviderError("缺少必填字段 {0}".format(field))
        want = (props.get(field) or {}).get("type")
        got = data[field]
        if want == "array" and not isinstance(got, list):
            raise ProviderError("字段 {0} 应为数组".format(field))
        if want == "string" and not isinstance(got, str):
            raise ProviderError("字段 {0} 应为字符串".format(field))
        if want == "object" and not isinstance(got, dict):
            raise ProviderError("字段 {0} 应为对象".format(field))
    return data
