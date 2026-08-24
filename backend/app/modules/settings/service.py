"""设置读写。

API key 存本地库，页面可配 —— 但**不回显明文**，只回显掩码与是否已设置。
模型 id 参与诊断指纹：换模型等于换诊断口径，旧答案必须失效。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Optional

from ...core.config import DEFAULT_MODEL
from ...core.db import execute, query_one
from . import models  # noqa: F401  建表注册

DEFAULTS = {
    "provider": "anthropic",
    "preset": "claude",
    "base_url": "",
    "api_key": "",
    "model": DEFAULT_MODEL,
    "effort": "high",
    "vote_k": "1",          # 首次生成的自洽投票次数（1 = 不投票）
    "auto_freeze": "1",     # 首答自动冻结；关掉则需人工确认后才冻结
    # 导入手册时排除的「观测者自照镜子」命令（逗号分隔）：CLI 会话/审计类命令的
    # 输出必然带着采集自身的痕迹，进了证据快照就永不一致。按设备厂商增删。
    "kb_exclude_commands": "show cli history, show cli client, show cli context, "
                           "show cli command-info, show line, display users, "
                           "display logbuffer, show users, show logging",
}


def get(key: str) -> str:
    row = query_one("SELECT value FROM setting WHERE key=?", (key,))
    return row["value"] if row else DEFAULTS.get(key, "")


def put(key: str, value: str) -> None:
    execute("INSERT OR REPLACE INTO setting(key, value, updated_at) VALUES (?,?,?)",
            (key, value, _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")))
    _backup()


def _backup_path():
    """备份放在仓库**之外**（~/.detops，可用 DETOPS_HOME 改），里面有 API Key 明文。"""
    import os
    from pathlib import Path
    home = Path(os.environ.get("DETOPS_HOME") or (Path.home() / ".detops"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "settings.bak.json"


def _backup() -> None:
    """把设置另存一份到 data/ 之外。

    开发期经常整目录重建数据库，凭据不该跟着一起没了 —— 这是被坑过一次才加的。
    """
    import json
    try:
        rows = {r["key"]: r["value"] for r in
                __import__("app.core.db", fromlist=["query"]).query(
                    "SELECT key, value FROM setting")}
        _backup_path().write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def restore_from_backup() -> int:
    """数据库为空时，从备份恢复设置。返回恢复的条数。"""
    import json
    from ...core.db import query_one
    path = _backup_path()
    if not path.exists():
        return 0
    if query_one("SELECT 1 FROM setting LIMIT 1"):
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return 0
    n = 0
    for k, v in data.items():
        execute("INSERT OR REPLACE INTO setting(key, value, updated_at) VALUES (?,?,?)",
                (k, v, _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")))
        n += 1
    return n


def api_key() -> str:
    import os
    return get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")


def model() -> str:
    return get("model") or DEFAULT_MODEL


def llm_config() -> Dict[str, str]:
    """模型调用层要的全部配置。provider/base_url/model 都会进诊断指纹。"""
    return {
        "provider": get("provider") or "anthropic",
        "base_url": get("base_url") or "",
        "api_key": api_key(),
        "model": model(),
        "effort": get("effort") or "high",
    }


def vote_k() -> int:
    try:
        return max(1, min(int(get("vote_k") or 1), 5))
    except ValueError:
        return 1


def auto_freeze() -> bool:
    return (get("auto_freeze") or "1") == "1"


def _mask(value: str) -> str:
    if not value:
        return ""
    return "{0}…{1}".format(value[:7], value[-4:]) if len(value) > 14 else "已设置"


def public() -> Dict[str, Any]:
    key = api_key()
    import os
    from ...core.providers import PRESETS
    return {
        "api_key_set": bool(key),
        "api_key_mask": _mask(key),
        "api_key_from_env": bool(not get("api_key") and os.environ.get("ANTHROPIC_API_KEY")),
        "provider": get("provider") or "anthropic",
        "preset": get("preset") or "claude",
        "base_url": get("base_url") or "",
        "model": model(),
        "effort": get("effort") or "high",
        "vote_k": vote_k(),
        "auto_freeze": auto_freeze(),
        "kb_exclude_commands": get("kb_exclude_commands"),
        "presets": PRESETS,
    }
