"""首启向导门禁的单元测试(M2 · 设计方案 4.2 / 4.5 / 第六节)。

不连库、不联网。覆盖:
  · 来源判断(含"转发头只有在直接对端是内网时才采信")
  · 口令:对 / 错 / 未配置 / 连错 5 次锁定
  · 初始化会话:签发、验签、篡改、过期
  · test_token:正常、过期、改了配置、改了 key、签名被换
  · 限流窗口
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JWT_SECRET", "unit-test-secret-for-setup-guard-0123456789")

from app.services import setup_guard as g  # noqa: E402


class FakeClient:
    def __init__(self, host):
        self.host = host


class FakeReq:
    def __init__(self, peer="172.18.0.7", headers=None):
        self.client = FakeClient(peer) if peer else None
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _clean():
    g.reset_state()
    yield
    g.reset_state()


# ── 来源判断 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("ip,kind", [
    ("127.0.0.1", "local"),
    # ⚠️ 文档/示例网段(192.0.2.0/24、198.51.100.0/24、203.0.113.0/24)在 Python
    # 的 ipaddress 里 is_private=True,会被判成内网 —— 写测试时别拿它们当"公网 IP"。
    ("203.0.113.9", "lan"),
    ("::1", "local"),
    ("10.0.0.5", "lan"),
    ("172.18.0.7", "lan"),
    ("192.168.1.9", "lan"),
    ("169.254.169.254", "lan"),     # 链路本地 —— 内网,不是公网
    ("8.8.8.8", "public"),
    ("2001:4860:4860::8888", "public"),
    ("", "unknown"),
    ("not-an-ip", "unknown"),
])
def test_classify(ip, kind):
    assert g._classify(ip) == kind


def test_forwarded_used_when_peer_is_internal():
    r = FakeReq(peer="172.18.0.7", headers={"X-Hunter-Forwarded-For": "8.8.8.8"})
    s = g.source_of(r)
    assert (s.ip, s.kind, s.via) == ("8.8.8.8", "public", "forwarded")
    assert not s.is_trusted_zone


def test_forwarded_ignored_when_peer_is_public():
    """有人绕开 web 直接打 api 的宿主端口 —— 他给的头一个字都不能信。"""
    r = FakeReq(peer="8.8.8.8", headers={"X-Hunter-Forwarded-For": "127.0.0.1"})
    s = g.source_of(r)
    assert (s.ip, s.kind, s.via) == ("8.8.8.8", "public", "peer")


def test_forwarded_chain_takes_rightmost():
    """链里只有最右边那个是"离我们最近的那一跳亲手写的",左边的可以是客户端伪造的。"""
    r = FakeReq(headers={"X-Hunter-Forwarded-For": "8.8.8.8, 10.0.0.1, 172.18.0.1"})
    assert g.source_of(r).ip == "172.18.0.1"


def test_client_cannot_forge_localhost_behind_appending_proxy():
    """演示站实测的那次:客户端带 `X-Forwarded-For: 127.0.0.1`,nginx 的
    $proxy_add_x_forwarded_for 把真实公网 IP 追加在后面。必须判成公网。"""
    r = FakeReq(headers={"X-Hunter-Forwarded-For": "127.0.0.1, 8.8.8.8"})
    s = g.source_of(r)
    assert (s.ip, s.kind) == ("8.8.8.8", "public")
    assert not s.is_trusted_zone


def test_forwarded_strips_port():
    assert g._first_forwarded("8.8.8.8:51515") == "8.8.8.8"
    assert g._first_forwarded("[2001:db8::1]:443") == "2001:db8::1"
    assert g._first_forwarded("") == ""
    assert g._first_forwarded("  ,  ") == ""


def test_unknown_source_is_not_trusted():
    """判不出来往严处理 —— 不能因为拿不到地址就当本机放行。"""
    s = g.source_of(FakeReq(peer="", headers={}))
    assert s.kind == "unknown" and not s.is_trusted_zone


# ── 口令 ────────────────────────────────────────────────────────────
def test_token_not_configured(monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    assert not g.token_configured()
    r = g.verify_token("whatever")
    assert r["ok"] is False and r["reason"] == "not_configured"


def test_token_right_and_wrong(monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "s3cr3t-token")
    assert g.verify_token("s3cr3t-token")["ok"] is True
    bad = g.verify_token("nope")
    assert bad["ok"] is False and bad["reason"] == "bad_token"
    # 一次成功要把计数清零
    g.verify_token("s3cr3t-token")
    assert g._gate.fails == 0


def test_lock_after_five_fails(monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "s3cr3t-token")
    for i in range(g.MAX_UNLOCK_FAILS - 1):
        r = g.verify_token("bad")
        assert r["reason"] == "bad_token", i
    r = g.verify_token("bad")
    assert r["reason"] == "locked" and r["locked_for"] == g.LOCK_SECONDS
    # 锁定期间连**正确**的口令也不放行
    assert g.verify_token("s3cr3t-token")["reason"] == "locked"
    assert g.lock_remaining() > 0


def test_lock_expires(monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "s3cr3t-token")
    for _ in range(g.MAX_UNLOCK_FAILS):
        g.verify_token("bad")
    g._gate.locked_until = time.time() - 1
    assert g.lock_remaining() == 0
    assert g.verify_token("s3cr3t-token")["ok"] is True


# ── 初始化会话 ──────────────────────────────────────────────────────
def test_session_roundtrip():
    tok, ttl = g.issue_session()
    assert ttl == g.SESSION_SECONDS
    assert g.session_valid(tok)


def test_session_tampered():
    tok, _ = g.issue_session()
    nonce, exp, sig = tok.split(".")
    assert not g.session_valid(f"{nonce}.{int(exp) + 3600}.{sig}")   # 改有效期
    assert not g.session_valid(f"{nonce}.{exp}.{'0' * len(sig)}")    # 改签名
    assert not g.session_valid("garbage")
    assert not g.session_valid("")


def test_session_expired():
    tok, _ = g.issue_session()
    g._gate.sessions[tok] = time.time() - 1
    nonce, _exp, _sig = tok.split(".")
    expired = f"{nonce}.{int(time.time()) - 5}.{g._sign(f'{nonce}.{int(time.time()) - 5}')}"
    assert not g.session_valid(expired)


def test_session_dies_with_process():
    """签名还对,但进程内的表没了 —— 重启后旧会话必须失效。"""
    tok, _ = g.issue_session()
    g._gate.sessions.clear()
    assert not g.session_valid(tok)


# ── test_token ──────────────────────────────────────────────────────
CFG = ("https://api.example.com/v1", "some-model", "sk-abcdef")


def test_test_token_ok():
    t = g.issue_test_token(*CFG)
    assert g.verify_test_token(t, *CFG)["ok"] is True


def test_test_token_missing():
    r = g.verify_test_token("", *CFG)
    assert r["ok"] is False and r["reason"] == "missing"


def test_test_token_config_changed():
    t = g.issue_test_token(*CFG)
    assert g.verify_test_token(t, CFG[0], "other-model", CFG[2])["reason"] == "mismatch"
    assert g.verify_test_token(t, "https://elsewhere/v1", CFG[1], CFG[2])["reason"] == "mismatch"
    assert g.verify_test_token(t, CFG[0], CFG[1], "sk-different")["reason"] == "mismatch"


def test_test_token_expired():
    dig = g.config_digest(*CFG)
    exp = int(time.time()) - 1
    t = f"{dig}.{exp}.{g._sign(f'{dig}.{exp}')}"
    assert g.verify_test_token(t, *CFG)["reason"] == "expired"


def test_test_token_forged_signature():
    """把有效期往后推、签名照抄 —— 必须被签名校验挡住。"""
    t = g.issue_test_token(*CFG)
    dig, exp, sug, sig = t.split(".")
    forged = f"{dig}.{int(exp) + 86400}.{sug}.{sig}"
    assert g.verify_test_token(forged, *CFG)["reason"] == "bad_signature"


def test_test_token_carries_sanitize_suggest():
    """检测建议签在凭证里 —— 保存接口据此兜底,不必信任调用方。"""
    t = g.issue_test_token(*CFG, sanitize_suggest="1")
    r = g.verify_test_token(t, *CFG)
    assert r["ok"] is True and r["sanitize_suggest"] == "1"
    # 没有建议时是空的,保存接口会退回默认值
    assert g.verify_test_token(g.issue_test_token(*CFG), *CFG)["sanitize_suggest"] == ""
    # 非法取值不进凭证
    assert g.verify_test_token(
        g.issue_test_token(*CFG, sanitize_suggest="yes"), *CFG)["sanitize_suggest"] == ""


def test_test_token_forged_sanitize_suggest():
    """改建议那一段 = 改了被签名的内容,必须被挡。否则谁都能把清洗开关篡改掉。"""
    t = g.issue_test_token(*CFG, sanitize_suggest="1")
    dig, exp, _sug, sig = t.split(".")
    assert g.verify_test_token(f"{dig}.{exp}.0.{sig}", *CFG)["reason"] == "bad_signature"


def test_legacy_three_part_token_still_valid():
    """滚动升级时旧版签发的三段式凭证照样能用,只是没有建议。"""
    dig = g.config_digest(*CFG)
    exp = int(time.time()) + 600
    legacy = f"{dig}.{exp}.{g._sign(f'{dig}.{exp}')}"
    r = g.verify_test_token(legacy, *CFG)
    assert r["ok"] is True and r["sanitize_suggest"] == ""


def test_test_token_malformed():
    assert g.verify_test_token("a.b", *CFG)["reason"] == "malformed"
    assert g.verify_test_token("a.notanint.c", *CFG)["reason"] == "malformed"


def test_config_digest_normalizes_trailing_slash():
    assert g.config_digest("https://x/v1/", "m", "k") == g.config_digest("https://x/v1", "m", "k")


def test_digest_does_not_leak_key():
    """摘要里不能出现 key 原文 —— 它会被放进 test_token 发给浏览器。"""
    assert "sk-abcdef" not in g.issue_test_token(*CFG)


# ── 限流 ────────────────────────────────────────────────────────────
def test_rate_limit():
    for _ in range(g.TEST_RATE_PER_MIN):
        assert g.take_test_slot()["ok"] is True
    blocked = g.take_test_slot()
    assert blocked["ok"] is False and blocked["retry_after"] > 0


def test_rate_limit_window_slides():
    for _ in range(g.TEST_RATE_PER_MIN):
        g.take_test_slot()
    g._gate.test_hits = [t - 61 for t in g._gate.test_hits]
    assert g.take_test_slot()["ok"] is True
