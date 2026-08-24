"""模型调用层 —— 全系统唯一的模型出口，支持 Claude / DeepSeek / GLM / 任意兼容端点。

一致性的关键约定：
  · **每次调用都按「请求内容哈希」缓存**。同一份 prompt 只会真正调用一次模型，
    之后永远命中缓存 —— 这不是优化，这就是一致性本身。
  · provider / base_url / model 都进哈希：换任何一项，缓存自动失效。
  · 模型不可用时不抛错，返回 ok=False，由上层走兜底阶梯。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, Optional

from . import providers
from .canon import sha256_of
from .db import execute, query_one, register_schema

register_schema("""
CREATE TABLE IF NOT EXISTS llm_call (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    request TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
""")

RETRY_ON_SCHEMA_ERROR = 2   # 兜底 F2：结构不合规时的固定重试次数


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _cfg() -> Dict[str, str]:
    from ..modules.settings import service as settings
    return settings.llm_config()


def available() -> bool:
    return bool(_cfg().get("api_key"))


def identity() -> str:
    """进指纹的模型身份串。"""
    c = _cfg()
    return "{0}|{1}|{2}".format(c.get("provider", ""), c.get("base_url", ""),
                                c.get("model", ""))


def cache_key(purpose: str, system: str, content: str, schema: Any) -> str:
    return sha256_of({"purpose": purpose, "system": system, "content": content,
                      "identity": identity(), "schema": schema})


def cached(key: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT response FROM llm_call WHERE cache_key=?", (key,))
    if not row:
        return None
    try:
        return json.loads(row["response"])
    except ValueError:
        return None


def call_json(purpose: str, system: str, content: str, schema: Dict[str, Any],
              max_tokens: int = 8000, use_cache: bool = True) -> Dict[str, Any]:
    """结构化调用。返回 {ok, data, cached, error, attempts}。"""
    cfg = _cfg()
    key = cache_key(purpose, system, content, schema)

    if use_cache:
        hit = cached(key)
        if hit is not None:
            return {"ok": True, "data": hit, "cached": True, "cache_key": key,
                    "error": "", "attempts": 0}

    if not cfg.get("api_key"):
        return {"ok": False, "data": {}, "cached": False, "cache_key": key,
                "error": "未配置 API Key（可在「设置」页填写）", "attempts": 0}

    last_err = ""
    for attempt in range(1, RETRY_ON_SCHEMA_ERROR + 2):
        try:
            if cfg["provider"] == "anthropic":
                data = providers.call_anthropic(
                    cfg["api_key"], cfg["model"], system, content, schema,
                    max_tokens, cfg.get("effort") or "high")
            else:
                data = providers.call_openai_compatible(
                    cfg["api_key"], cfg.get("base_url", ""), cfg["model"],
                    system, content, schema, max_tokens)
            data = providers.validate(data, schema)
        except Exception as exc:
            last_err = "{0}: {1}".format(type(exc).__name__, exc)
            # 兜底 F2：结构不合规就重试；网络/鉴权类错误重试也无用，直接退出
            if not isinstance(exc, (providers.ProviderError, ValueError)):
                break
            continue
        execute("INSERT OR REPLACE INTO llm_call(cache_key, provider, model, purpose,"
                " request, response, created_at) VALUES (?,?,?,?,?,?,?)",
                (key, cfg["provider"], cfg["model"], purpose, content[:20000],
                 json.dumps(data, ensure_ascii=False), _now()))
        return {"ok": True, "data": data, "cached": False, "cache_key": key,
                "error": "", "attempts": attempt}

    return {"ok": False, "data": {}, "cached": False, "cache_key": key,
            "error": last_err, "attempts": RETRY_ON_SCHEMA_ERROR + 1}


def ping() -> Dict[str, Any]:
    """连通性自检 —— 不走缓存，真发一次最小请求。"""
    cfg = _cfg()
    if not cfg.get("api_key"):
        return {"ok": False, "error": "未配置 API Key"}
    schema = {"type": "object", "properties": {"pong": {"type": "boolean"}},
              "required": ["pong"], "additionalProperties": False}
    try:
        if cfg["provider"] == "anthropic":
            providers.call_anthropic(cfg["api_key"], cfg["model"],
                                     "回复 JSON。", '返回 {"pong": true}',
                                     schema, 256, "low")
        else:
            providers.call_openai_compatible(cfg["api_key"], cfg.get("base_url", ""),
                                             cfg["model"], "回复 JSON。",
                                             '返回 {"pong": true}', schema, 256)
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
                "error": ""}
    except Exception as exc:
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"],
                "error": "{0}: {1}".format(type(exc).__name__, exc)}


def stats() -> Dict[str, Any]:
    row = query_one("SELECT COUNT(*) n FROM llm_call")
    cfg = _cfg()
    return {"available": bool(cfg.get("api_key")), "provider": cfg.get("provider"),
            "model": cfg.get("model"), "cached_calls": row["n"] if row else 0}
