"""批量导入任务：文件先落盘、逐个处理、逐个入库，进度可轮询。

手册目录可能有成百上千份文档，所以这里的原则是**内存里不攒东西**：
  · 浏览器上传的文件由 FastAPI 直接写到临时目录（UploadFile 本身就是磁盘假脱机），
    任务只拿到一份路径清单；
  · 服务器目录导入更简单，就是递归 walk 出路径清单；
  · 每个文件读 → 提取 → 写 SQLite → 释放，任务里只累计计数和最近的几条错误；
  · 结果不回传，命令清单本来就在数据库里，页面按需分页查。
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import threading
import uuid
from typing import Any, Dict, List, Optional

from . import service

DOC_EXTS = (".docx", ".md", ".markdown", ".txt")
MAX_ERRORS_KEPT = 30

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_MAX_JOBS = 20


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def create(total: int, source: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "status": "running", "source": source,
                         "total": total, "done": 0, "found": 0, "added": 0,
                         "skipped": 0, "failed": 0, "current": "",
                         "errors": [], "started_at": _now(), "finished_at": ""}
        if len(_JOBS) > _MAX_JOBS:
            for k in sorted(_JOBS, key=lambda x: _JOBS[x]["started_at"])[:-_MAX_JOBS]:
                _JOBS.pop(k, None)
    return job_id


def get(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j, errors=list(j["errors"])) if j else None


def _update(job_id: str, **fields: Any) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.update(fields)


def _bump(job_id: str, **inc: Any) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            for k, v in inc.items():
                j[k] = j.get(k, 0) + v


def _fail(job_id: str, name: str, msg: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j["failed"] += 1
            if len(j["errors"]) < MAX_ERRORS_KEPT:
                j["errors"].append({"file": name, "error": msg[:200]})


def _process(job_id: str, items: List[Dict[str, str]], engine: str,
             cleanup_dir: str = "") -> None:
    """items: [{"path": 磁盘路径, "name": 展示名}]。逐个处理，逐个入库。"""
    try:
        for it in items:
            _update(job_id, current=it["name"])
            raw = b""
            try:
                with open(it["path"], "rb") as fh:
                    raw = fh.read()
                r = service.import_doc(it["name"], raw, engine)
                if r.get("duplicate"):
                    _bump(job_id, skipped=1)
                else:
                    _bump(job_id, found=r.get("found", 0), added=r.get("added", 0))
            except Exception as exc:
                _fail(job_id, it["name"], "{0}: {1}".format(type(exc).__name__, exc))
            finally:
                raw = b""          # 这份文档的字节到此释放，不随任务累积
                _bump(job_id, done=1)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        _update(job_id, status="done", current="", finished_at=_now())


def start(items: List[Dict[str, str]], engine: str, source: str,
          cleanup_dir: str = "") -> str:
    job_id = create(len(items), source)
    threading.Thread(target=_process, args=(job_id, items, engine, cleanup_dir),
                     daemon=True, name="kb-import-" + job_id).start()
    return job_id


def walk_dir(root: str) -> List[Dict[str, str]]:
    """递归列出目录下的文档；展示名带相对路径，便于在清单里分辨来源。"""
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise ValueError("目录不存在: {0}".format(root))
    items: List[Dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(filenames):
            if f.startswith(".") or f.startswith("~$") or not f.lower().endswith(DOC_EXTS):
                continue
            full = os.path.join(dirpath, f)
            items.append({"path": full, "name": os.path.relpath(full, root)})
    return items
