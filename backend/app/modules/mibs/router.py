"""MIB 管理 API：源文件、编译、树形浏览、OID 解码。"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, File, HTTPException, UploadFile

from . import models  # noqa: F401
from . import service

router = APIRouter(prefix="/api/mibs", tags=["mibs"])


@router.get("/sources")
def sources() -> List[Dict[str, Any]]:
    return service.sources()


@router.post("/sources")
async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        return service.upload(file.filename or "upload.mib", await file.read())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/sources/{filename}")
def delete_source(filename: str) -> Dict[str, Any]:
    service.delete_source(filename)
    return {"ok": True}


@router.post("/compile")
def compile_all() -> Dict[str, Any]:
    return service.compile_all()


@router.get("/status")
def status() -> Dict[str, Any]:
    return service.status()


@router.get("/tree")
def tree(parent: str = "") -> List[Dict[str, Any]]:
    return service.tree_children(parent)


@router.get("/node")
def node(oid: str) -> Dict[str, Any]:
    e = service.lookup(oid)
    if not e:
        raise HTTPException(404, "OID 不在索引里")
    return e


@router.get("/search")
def search(q: str = "") -> List[Dict[str, Any]]:
    return service.search(q)


@router.get("/translate")
def translate(oid: str) -> Dict[str, str]:
    return {"oid": oid, "name": service.translate(oid)}
