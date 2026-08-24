"""知识库 API。"""
from __future__ import annotations

from typing import Any, Dict, List

import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/kb", tags=["kb"])

ALLOWED = (".docx", ".md", ".markdown", ".txt")


@router.get("/summary")
def summary() -> Dict[str, Any]:
    return service.summary()


@router.get("/docs")
def docs() -> List[Dict[str, Any]]:
    return service.list_docs()


@router.post("/docs")
async def upload(file: UploadFile = File(...), engine: str = Form("rule")) -> Dict[str, Any]:
    if not file.filename.lower().endswith(ALLOWED):
        raise HTTPException(400, "只支持 {0}".format(" / ".join(ALLOWED)))
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    try:
        return service.import_doc(file.filename, raw, engine)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


SAMPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "samples")


@router.get("/samples")
def samples() -> List[str]:
    """内置示例资料（samples 目录，支持按厂商分子目录）。"""
    out: List[str] = []
    if not os.path.isdir(SAMPLE_DIR):
        return out
    for root, _dirs, files in os.walk(SAMPLE_DIR):
        for f in files:
            if f.startswith(".") or not f.lower().endswith((".md", ".txt", ".docx")):
                continue
            out.append(os.path.relpath(os.path.join(root, f), SAMPLE_DIR))
    return sorted(out)


class SampleBatchIn(BaseModel):
    filenames: List[str] = []
    engine: str = "auto"


@router.post("/samples/batch")
def import_samples(body: SampleBatchIn) -> Dict[str, Any]:
    """批量导入 —— CLI 手册通常是一整个目录，一份份点太笨。"""
    names = body.filenames or samples()
    total_found = total_added = 0
    results = []
    for name in names:
        path = os.path.join(SAMPLE_DIR, name)
        if not os.path.isfile(os.path.realpath(path)):
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            r = service.import_doc(os.path.basename(name), raw, body.engine)
        except Exception as exc:
            results.append({"file": name, "error": str(exc)})
            continue
        total_found += r["found"]
        total_added += r["added"]
        results.append({"file": name, "engine": r["engine"],
                        "found": r["found"], "added": r["added"]})
    return {"files": len(results), "found": total_found, "added": total_added,
            "results": results}


class SampleIn(BaseModel):
    filename: str
    engine: str = "rule"


@router.post("/samples")
def import_sample(body: SampleIn) -> Dict[str, Any]:
    path = os.path.join(SAMPLE_DIR, body.filename)
    if not os.path.isfile(os.path.realpath(path)):
        raise HTTPException(404, "示例文件不存在")
    with open(path, "rb") as fh:
        raw = fh.read()
    return service.import_doc(os.path.basename(body.filename), raw, body.engine)


@router.delete("/docs/{doc_id}")
def delete(doc_id: int) -> Dict[str, Any]:
    service.delete_doc(doc_id)
    return {"ok": True}


@router.get("/commands")
def commands(q: str = "", enabled_only: bool = False) -> List[Dict[str, Any]]:
    return service.list_commands(q, enabled_only)


@router.patch("/commands/{cmd_id}")
def toggle(cmd_id: int, enabled: bool) -> Dict[str, Any]:
    return service.set_enabled(cmd_id, enabled)
