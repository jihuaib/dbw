"""规范化序列化与哈希 —— 所有「一致性」的度量单位。"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def canonical_json(value: Any) -> str:
    """确定性序列化：键排序、无多余空白。同一语义对象总得到同一字符串。"""
    return json.dumps(_canon(value), ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True)


def sha256_of(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def short(digest: str, n: int = 12) -> str:
    return (digest or "")[:n]
