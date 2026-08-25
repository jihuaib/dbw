"""批量导入任务：递归遍历、逐个落库、重复跳过、错误计数 —— 内存里不攒结果。"""
import os
import time

from app.core.db import query_one
from app.modules.kb import jobs, service


def _wait(job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get(job_id)
        if j and j["status"] == "done":
            return j
        time.sleep(0.05)
    raise AssertionError("任务未完成")


def test_walk_dir_recursive_and_filtered(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.md").write_text("| 命令 | 视图 | 说明 |\n|---|---|---|\n| `show a` | any | a |\n")
    (tmp_path / "b.txt").write_text("<Sys> show b\nout\n")
    (tmp_path / "skip.pdf").write_bytes(b"%PDF")
    (tmp_path / "~$lock.docx").write_bytes(b"")
    items = jobs.walk_dir(str(tmp_path))
    assert sorted(i["name"] for i in items) == ["a/x.md", "b.txt"]


def test_batch_job_persists_and_dedups(tmp_path):
    (tmp_path / "one.md").write_text("| 命令 | 视图 | 说明 |\n|---|---|---|\n| `show jobtest one` | any | 1 |\n")
    (tmp_path / "dup.md").write_text("| 命令 | 视图 | 说明 |\n|---|---|---|\n| `show jobtest one` | any | 1 |\n")
    (tmp_path / "bad.docx").write_bytes(b"not a docx")
    items = jobs.walk_dir(str(tmp_path))
    j = _wait(jobs.start(items, "auto", "test"))
    assert j["done"] == 3
    assert j["added"] == 1                      # 命令落库一次
    assert j["skipped"] == 1                    # 内容相同的文档跳过
    assert j["failed"] == 1 and j["errors"][0]["file"] == "bad.docx"
    assert query_one("SELECT id FROM kb_command WHERE command='show jobtest one'")
    # 清理
    for d in service.list_docs():
        if d["name"] in ("one.md", "dup.md"):
            service.delete_doc(d["id"])
