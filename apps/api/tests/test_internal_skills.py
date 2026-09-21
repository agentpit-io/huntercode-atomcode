# -*- coding: utf-8 -*-
"""用户 SKILL 导出接口的回归用例 · `/api/internal/skills/{key}/*`

    cd apps/api && HUNTER_INTERNAL_KEY=xxx python -m pytest tests/test_internal_skills.py -q

## 为什么这些用例值得存在

这个接口有两个特点,决定了它必须有单测护着:

1. **它把磁盘上的文件原样喂给大模型**。路径穿越一旦漏掉,
   `../../.env` 就会变成模型上下文里的一段文本 —— 不报错、不告警,
   没人会发现。所以下面 `test_traversal_*` 那一组每一条都是一个具体的绕过手法。

2. **`version` 的稳定性直接决定用户体验**。opencode 拿它判断要不要重下
   (`skill/discovery.ts`):算法里混进时间戳/随机数,每次 refresh 都会
   全量重下几十个文件;反过来内容变了 version 没变,模型就永远读旧的。
   两种错都不会报错,只会让人觉得「时快时慢」「改了不生效」。

⚠️ 不要跑整个 `tests/` 目录 —— `test_screen_fix.py` 在 import 阶段就
`sys.exit()`,会把整个 pytest 进程带走。那是既有问题,与本文件无关。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers import internal_skills                  # noqa: E402
from app.services import skill_files                     # noqa: E402

KEY = "testkey123"
BASE = f"/api/internal/skills/{KEY}"

PROBE = """---
name: d_probe
description: "导出接口探针"
---

# 导出接口探针

探针口令是 `D-SKILL-777`。
"""


@pytest.fixture()
def user_dir(tmp_path, monkeypatch):
    """把 USER_SKILLS_DIR 指到临时目录。

    直接改模块属性而不是环境变量:`skill_files` 在 import 时就把
    env 读成了模块级 Path,改 env 已经晚了。导出函数体内引用的是
    模块全局,所以改属性立刻生效。
    """
    d = tmp_path / "user-skills"
    d.mkdir()
    monkeypatch.setattr(skill_files, "USER_SKILLS_DIR", d)
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", KEY)
    return d


@pytest.fixture()
def client(user_dir):
    app = FastAPI()
    app.include_router(internal_skills.router, prefix="/api")
    return TestClient(app)


def write_skill(root: Path, name: str, body: str = PROBE, extra: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    for rel, content in (extra or {}).items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            f.write_bytes(content)
        else:
            f.write_text(content, encoding="utf-8")
    return d


# ══════════════════════════════════════════════════════════════
# ① 鉴权 —— 口令不对必须是 404,不是 401
# ══════════════════════════════════════════════════════════════

def test_index_ok(client, user_dir):
    write_skill(user_dir, "d_probe")
    r = client.get(f"{BASE}/index.json")
    assert r.status_code == 200
    data = r.json()
    assert list(data.keys()) == ["skills"], "schema 必须逐字是 {skills:[...]},多一层 opencode 就解析失败"
    assert len(data["skills"]) == 1
    item = data["skills"][0]
    assert item["name"] == "d_probe"
    assert "SKILL.md" in item["files"]
    assert len(item["version"]) == 64                     # sha256 hex


def test_wrong_key_is_404_not_401(client, user_dir):
    """401 等于告诉扫描者「这个路径存在,只是口令不对」。必须 404。"""
    write_skill(user_dir, "d_probe")
    for bad in ("wrong", "testkey12", "testkey1234", "", "TESTKEY123"):
        r = client.get(f"/api/internal/skills/{bad}/index.json")
        assert r.status_code == 404, f"{bad!r} 竟然通过了"


def test_wrong_key_blocks_file_endpoint(client, user_dir):
    write_skill(user_dir, "d_probe")
    assert client.get(f"{BASE}/d_probe/SKILL.md").status_code == 200
    assert client.get("/api/internal/skills/wrong/d_probe/SKILL.md").status_code == 404


def test_empty_internal_key_closes_endpoint(client, user_dir, monkeypatch):
    """没配 HUNTER_INTERNAL_KEY 时整个接口关闭 —— **不许**退化成「不校验」。"""
    write_skill(user_dir, "d_probe")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "")
    assert client.get(f"{BASE}/index.json").status_code == 404
    assert client.get("/api/internal/skills//index.json").status_code == 404
    assert client.get(f"{BASE}/d_probe/SKILL.md").status_code == 404


# ══════════════════════════════════════════════════════════════
# ② 路径穿越 —— 每一条都是一个具体的绕过手法
# ══════════════════════════════════════════════════════════════

def test_traversal_rejected_at_unit_level(user_dir):
    """`resolve_user_skill_file` 是唯一的防线,直接按最坏输入打一遍。"""
    write_skill(user_dir, "d_probe")
    secret = user_dir.parent / "secret.txt"
    secret.write_text("绝密", encoding="utf-8")

    bad = [
        "../secret.txt",
        "../../secret.txt",
        "refs/../../secret.txt",
        "./../secret.txt",
        "/etc/passwd",
        "//etc/passwd",
        "..",
        ".",
        "",
        ".env",                       # 隐藏文件不导出
        "__pycache__/x.pyc",
    ]
    for rel in bad:
        assert skill_files.resolve_user_skill_file("d_probe", rel) is None, f"{rel!r} 没挡住"
    # 正常路径仍然放行,别把防线做成「全拒」
    assert skill_files.resolve_user_skill_file("d_probe", "SKILL.md") is not None


def test_traversal_rejected_over_http(client, user_dir):
    write_skill(user_dir, "d_probe")
    (user_dir.parent / "secret.txt").write_text("绝密", encoding="utf-8")
    for rel in ("%2e%2e/secret.txt", "%2e%2e%2f%2e%2e%2fsecret.txt",
                "refs/%2e%2e/%2e%2e/secret.txt", "%2fetc%2fpasswd"):
        r = client.get(f"{BASE}/d_probe/{rel}")
        assert r.status_code == 404, f"{rel!r} 返回了 {r.status_code}"
        assert "绝密" not in r.text


def test_symlink_pointing_outside_rejected(client, user_dir):
    """软链是纯字符串检查挡不住的那一类 —— resolve() 之后才看得出来。"""
    write_skill(user_dir, "d_probe")
    secret = user_dir.parent / "secret.txt"
    secret.write_text("绝密", encoding="utf-8")
    link = user_dir / "d_probe" / "leak.md"
    link.symlink_to(secret)

    assert skill_files.resolve_user_skill_file("d_probe", "leak.md") is None
    r = client.get(f"{BASE}/d_probe/leak.md")
    assert r.status_code == 404
    # 也不该出现在清单里
    files = client.get(f"{BASE}/index.json").json()["skills"][0]["files"]
    assert "leak.md" not in files


def test_symlinked_skill_dir_not_exported(client, user_dir, tmp_path):
    outside = tmp_path / "outside_skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text(PROBE, encoding="utf-8")
    (user_dir / "linked").symlink_to(outside, target_is_directory=True)

    assert skill_files.user_skill_dir("linked") is None
    assert client.get(f"{BASE}/index.json").json()["skills"] == []
    assert client.get(f"{BASE}/linked/SKILL.md").status_code == 404


def test_bad_skill_names_rejected(client, user_dir):
    write_skill(user_dir, "d_probe")
    for name in ("..", ".", ".hidden", "a/b", "", "x" * 80):
        assert skill_files.user_skill_dir(name) is None, f"{name!r} 没挡住"


# ══════════════════════════════════════════════════════════════
# ③ version 稳定性
# ══════════════════════════════════════════════════════════════

def _version(client, name="d_probe"):
    for s in client.get(f"{BASE}/index.json").json()["skills"]:
        if s["name"] == name:
            return s["version"]
    return None


def test_version_stable_when_content_unchanged(client, user_dir):
    """内容不变 version 必须不变,否则每次 refresh 都全量重下。"""
    write_skill(user_dir, "d_probe", extra={"refs/a.md": "甲"})
    v1 = _version(client)
    v2 = _version(client)
    assert v1 == v2
    # 只改 mtime 不改内容 —— version 不能因此变
    os.utime(user_dir / "d_probe" / "SKILL.md", (0, 0))
    assert _version(client) == v1


def test_version_changes_when_content_changes(client, user_dir):
    write_skill(user_dir, "d_probe")
    v1 = _version(client)
    (user_dir / "d_probe" / "SKILL.md").write_text(
        PROBE.replace("D-SKILL-777", "D-SKILL-888"), encoding="utf-8")
    assert _version(client) != v1


def test_version_changes_when_file_added_or_removed(client, user_dir):
    write_skill(user_dir, "d_probe")
    v1 = _version(client)
    (user_dir / "d_probe" / "refs").mkdir()
    (user_dir / "d_probe" / "refs" / "a.md").write_text("甲", encoding="utf-8")
    v2 = _version(client)
    assert v2 != v1
    (user_dir / "d_probe" / "refs" / "a.md").unlink()
    assert _version(client) == v1                  # 删回去应当回到原值


def test_version_distinguishes_file_boundaries(user_dir):
    """`a="xy" b=""` 与 `a="x" b="y"` 不能撞成同一个 version。"""
    v1 = skill_files.user_skill_version([("a.md", b"xy"), ("b.md", b"")])
    v2 = skill_files.user_skill_version([("a.md", b"x"), ("b.md", b"y")])
    assert v1 != v2
    # 重命名也必须换版
    v3 = skill_files.user_skill_version([("a.md", b"x"), ("c.md", b"y")])
    assert v3 != v2


def test_version_independent_of_input_order(user_dir):
    a = skill_files.user_skill_version([("a.md", b"1"), ("b.md", b"2")])
    b = skill_files.user_skill_version([("b.md", b"2"), ("a.md", b"1")])
    assert a == b


# ══════════════════════════════════════════════════════════════
# ④ 清单内容 —— 该在的在、不该在的不在
# ══════════════════════════════════════════════════════════════

def test_skill_without_skill_md_is_skipped(client, user_dir):
    """opencode 会跳过不含 SKILL.md 的条目并打 warning,我们别让它进清单。"""
    write_skill(user_dir, "good")
    broken = user_dir / "broken"
    broken.mkdir()
    (broken / "readme.md").write_text("没有 SKILL.md", encoding="utf-8")

    names = [s["name"] for s in client.get(f"{BASE}/index.json").json()["skills"]]
    assert names == ["good"]
    assert skill_files.list_user_skill_names() == ["good"]
    assert client.get(f"{BASE}/broken/readme.md").status_code == 404


def test_builtin_skills_not_exported(client, user_dir, tmp_path, monkeypatch):
    """内置 SKILL 已随镜像打进 opencode,再导一份会让它出现两次。"""
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "quote").mkdir()
    (builtin / "quote" / "SKILL.md").write_text(PROBE, encoding="utf-8")
    monkeypatch.setattr(skill_files, "SKILLS_DIR", builtin)
    write_skill(user_dir, "d_probe")

    names = [s["name"] for s in client.get(f"{BASE}/index.json").json()["skills"]]
    assert names == ["d_probe"]
    assert client.get(f"{BASE}/quote/SKILL.md").status_code == 404


def test_hidden_and_junk_files_excluded(client, user_dir):
    write_skill(user_dir, "d_probe", extra={
        "refs/a.md": "甲",
        ".env": "SECRET=1",
        "__pycache__/x.pyc": b"\x00",
        ".git/config": "[core]",
    })
    files = client.get(f"{BASE}/index.json").json()["skills"][0]["files"]
    assert files == ["SKILL.md", "refs/a.md"]


def test_nested_file_served(client, user_dir):
    write_skill(user_dir, "d_probe", extra={"refs/data-sources.md": "数据源规则"})
    r = client.get(f"{BASE}/d_probe/refs/data-sources.md")
    assert r.status_code == 200
    assert r.text == "数据源规则"


def test_skill_md_media_type(client, user_dir):
    write_skill(user_dir, "d_probe")
    r = client.get(f"{BASE}/d_probe/SKILL.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "D-SKILL-777" in r.text


def test_binary_passthrough(client, user_dir):
    blob = bytes(range(256))
    write_skill(user_dir, "d_probe", extra={"logo.png": blob})
    r = client.get(f"{BASE}/d_probe/logo.png")
    assert r.status_code == 200
    assert r.content == blob                      # 一个字节都不许改


# ══════════════════════════════════════════════════════════════
# ⑤ 上限 —— 防止有人把 opencode 的缓存盘撑爆
# ══════════════════════════════════════════════════════════════

def test_oversized_file_skipped(client, user_dir, monkeypatch):
    monkeypatch.setattr(skill_files, "EXPORT_MAX_FILE_BYTES", 1024)
    write_skill(user_dir, "d_probe", extra={"big.md": "x" * 2048, "small.md": "x"})
    files = client.get(f"{BASE}/index.json").json()["skills"][0]["files"]
    assert files == ["SKILL.md", "small.md"]
    assert client.get(f"{BASE}/d_probe/big.md").status_code == 404
    assert client.get(f"{BASE}/d_probe/small.md").status_code == 200


def test_file_count_capped(client, user_dir, monkeypatch):
    monkeypatch.setattr(skill_files, "EXPORT_MAX_FILES", 5)
    extra = {f"refs/f{i:03d}.md": str(i) for i in range(20)}
    write_skill(user_dir, "d_probe", extra=extra)
    files = client.get(f"{BASE}/index.json").json()["skills"][0]["files"]
    assert len(files) == 5
    # 超限截断时 SKILL.md 必须还在,否则 opencode 直接跳过整个条目
    assert "SKILL.md" in files


# ══════════════════════════════════════════════════════════════
# ⑥ 直读磁盘 —— 不走给 UI 用的 _cache
# ══════════════════════════════════════════════════════════════

def test_export_reads_disk_not_cache(client, user_dir, monkeypatch):
    """`_cache` 晚一拍,表现就是「用户刚存完、模型拉到的还是上一版」。"""
    write_skill(user_dir, "d_probe")
    monkeypatch.setattr(skill_files, "_cache", [])        # 故意把缓存清空成「什么都没有」
    assert len(client.get(f"{BASE}/index.json").json()["skills"]) == 1

    # 写入一个新的,不碰缓存 —— 导出必须立刻看得到
    write_skill(user_dir, "later", body=PROBE.replace("d_probe", "later"))
    names = [s["name"] for s in client.get(f"{BASE}/index.json").json()["skills"]]
    assert names == ["d_probe", "later"]

    # 删掉之后也必须立刻消失(opencode 的 pull 只扫清单里的目录)
    import shutil
    shutil.rmtree(user_dir / "later")
    names = [s["name"] for s in client.get(f"{BASE}/index.json").json()["skills"]]
    assert names == ["d_probe"]


def test_missing_user_dir_is_empty_index(client, user_dir):
    """用户目录还没建出来(全新部署)时返回空清单,不是 500。"""
    import shutil
    shutil.rmtree(user_dir)
    r = client.get(f"{BASE}/index.json")
    assert r.status_code == 200 and r.json() == {"skills": []}
