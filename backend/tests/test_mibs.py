"""MIB 管理：源文件发现、pysmi 编译、OID 索引/树/解码 —— 全部面向任意 MIB，无写死清单。"""
import pytest

from app.modules.mibs import service as M


@pytest.fixture(scope="module")
def compiled():
    return M.compile_all()


def test_sources_discovered_from_directory():
    srcs = M.sources()
    assert srcs, "自带目录里应有标准 MIB 源"
    names = {s["module"] for s in srcs}
    assert "SNMPv2-SMI" in names and "IF-MIB" in names
    # 模块名来自文件内容的 DEFINITIONS 行，不是猜文件名
    assert all(s["module"] for s in srcs)


def test_compile_all_bundled(compiled):
    assert compiled["compiled"] == compiled["total"], compiled["modules"]
    assert compiled["oid_count"] > 500


def test_translate_longest_prefix(compiled):
    assert M.translate("1.3.6.1.6.3.1.1.5.3") == "IF-MIB::linkDown"
    assert M.translate("1.3.6.1.2.1.2.2.1.1.12") == "IF-MIB::ifIndex.12"
    # 不在任何 MIB 里的 OID：按最长前缀，诚实带出数字尾巴，不做写死别名
    assert M.translate("1.3.6.1.2.1.15.0.1") == "BGP4-MIB::bgp.0.1"
    assert M.translate("9.9.9") == "9.9.9"


def test_tree_lazy_children(compiled):
    roots = M.tree_children("")
    assert roots
    # 逐级下钻到 mib-2
    node = next(n for n in roots if n["oid"] == "1.3")
    assert node["has_children"]
    level = M.tree_children("1.3")
    assert any(n["oid"] == "1.3.6" for n in level)


def test_lookup_and_search(compiled):
    e = M.lookup("1.3.6.1.6.3.1.1.5.3")
    assert e and e["class"] == "notificationtype"
    assert "ifIndex" in e.get("objects", [])
    hits = M.search("linkdown")
    assert any(h["name"] == "linkDown" for h in hits)


def test_upload_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "USER_DIR", tmp_path)
    with pytest.raises(ValueError):
        M.upload("x.mib", b"not a mib")
    with pytest.raises(ValueError):
        M.upload("x.exe", b"FOO DEFINITIONS ::= BEGIN END")
    r = M.upload("VENDOR-MIB.txt", b"VENDOR-TEST-MIB DEFINITIONS ::= BEGIN\nEND\n")
    assert r["module"] == "VENDOR-TEST-MIB"
    assert any(s["origin"] == "user" and s["module"] == "VENDOR-TEST-MIB"
               for s in M.sources())
    M.delete_source("VENDOR-MIB.txt")
    assert not any(s["module"] == "VENDOR-TEST-MIB" for s in M.sources())


def test_search_ranks_exact_name_first(compiled):
    hits = M.search("linkDown")
    assert hits[0]["name"] == "linkDown"          # 精确名在最前，不被「包含」命中挤掉
    assert any(h["name"] == "linkUp" for h in M.search("link"))
