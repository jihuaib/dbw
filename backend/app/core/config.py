"""版本锚与路径。

这些版本号是「一致性契约」的一部分：它们参与诊断指纹的计算。
改动任何一个，都意味着诊断口径变了，旧的冻结答案自动失效 —— 这是有意的。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DETOPS_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "detops.db"

for _d in (DATA_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── 参与诊断指纹的版本锚 ────────────────────────────────────────────────
NORMALIZE_VERSION = "NORM-1.0.0"   # 归一化规则版本（决定快照怎么算）
PROMPT_VERSION = "PROMPT-1.0.0"    # 提示词模板版本（决定送给 AI 的字节）
PLAN_VERSION = "PLAN-1.0.0"        # 采集编排版本

DEFAULT_MODEL = "claude-opus-5"

# 采集：固定退避，**不加随机 jitter** ——
# jitter 会把「采到 / 没采到」变成概率事件，直接破坏一致性
RETRY_TIMES = 2
RETRY_BACKOFF_MS = 200
