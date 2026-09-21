"""首启向导的门禁 · 来源判断 / 初始化口令 / 初始化会话 / test_token / 限流。

对应设计方案 4.2（第 0 步 · 初始化口令）与第六节（安全设计汇总）。

## 一、来源地址怎么来的（实测，不是推理）

浏览器打的是 **web**（Next.js），web 的 BFF（`apps/web/app/api/[...path]/route.ts`）
把 `/api/*` 原样转给 api 容器。所以 api 看到的 `request.client.host` 永远是 **web
容器在 docker 网络里的地址**（172.x），拿它判断「用户是不是在本机」一定是错的。

真实来源只能靠转发头：

  · 演示站这类前面有 nginx 的部署 —— nginx 写 `X-Forwarded-For`；
  · 裸 `docker compose up`（没有反代）—— **Next.js 自己会补**：
    `next start` 在 `base-server` 里对 `x-forwarded-for` 做 `??=`，值取
    `req.socket.remoteAddress`。所以 BFF 收到的请求里一定有这个头。

BFF 把它作为 `X-Hunter-Forwarded-For` 透给 api（原样转发 `x-forwarded-for` 会和
反代链路混在一起，单独一个头才说得清"这是 web 看到的来源"）。

## 二、转发头能信到什么程度（2026-09-18 在演示站上实测过）

分两种部署形态，结论完全不同：

**前面有反向代理（演示站这类，nginx）—— 能信。** nginx 的
`proxy_set_header X-Real-IP $remote_addr` 是**覆盖**写：客户端自己带的
`X-Real-IP` 会被丢掉。所以优先看这个头（web BFF 也是优先取它）。
退一步看 `X-Forwarded-For` 时取**最右边**那一项：
`$proxy_add_x_forwarded_for` 是**追加**写，客户端带的值原样留在最左边，
最右边那个才是 nginx 亲手写的。

> 实测:给演示站发 `X-Forwarded-For: 127.0.0.1`,改之前被判成「本机」直接放行;
> 改成取最右边之后判成公网、正确拒绝。

**没有任何反代（裸 `docker compose`）—— 仍然可以伪造。** Next.js 对
`x-forwarded-for` 是 `??=`：客户端自己带了就不会被覆盖，而从 HTTP 头里
分不出「Next 补的」和「客户端塞的」。**这一段没有纯技术解法。**

所以门禁规则是（**与设计方案 4.2 有一处收紧，理由见下**）：

| 条件 | 行为 |
|---|---|
| `HUNTER_SETUP_TOKEN` 非空 | **一律要口令**，不管来源看起来是本机还是公网 |
| 口令为空 + 来源判为本机 / 内网 | 跳过第 0 步（这是 `git clone && up -d` 的主路径） |
| 口令为空 + 来源判为公网 | 拒绝进入向导，提示去部署平台设 `HUNTER_SETUP_TOKEN` |

设计方案原文是「公网访问且配置了口令 → 必须输入口令」，言下之意本机访问即使配了
口令也跳过。**改成"配了就一律要"**：云平台模板必然会生成 `HUNTER_SETUP_TOKEN`，
于是"公网实例必须有口令才能初始化"这条就不再依赖那个可伪造的来源判断了 ——
安全性从「判据正确」变成「有没有口令」，后者是伪造不了的。
代价只是本机部署的人如果主动设了口令，自己也要敲一次，这是他自己的选择。

口令为空时来源判断仍然在用（决定"放行"还是"拒绝"）。按上面第二节：
**有反代时它是可信的，裸 compose 且暴露在公网时仍可被伪造**。所以
`GET /api/setup/status` 会把 `token_configured=false` 明确报出来，
向导第 1 步对它打黄色警告，README、`.env.example` 与云模板一律要求设口令。
没有口令、没有反代时，能打开这个页面的人和"本机用户"在协议层没有可区分的特征 ——
这一条只能靠口令解决，不能靠更聪明的解析。

## 三、状态存在进程内存里

初始化会话、失败计数、限流窗口全部是进程内的字典。api 是**单 worker 的 uvicorn**
（`boot.sh`），所以这没问题；而且这些状态本来就该在重启后清零（重启 = 运维介入）。
**改成多 worker 时必须一起改成 Redis**，否则锁定和限流会按 worker 各算一份。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from dataclasses import dataclass, field

from loguru import logger

# ── 可调参数（设计方案 4.2 / 第六节）──────────────────────────────
MAX_UNLOCK_FAILS = 5          # 连续错几次锁
LOCK_SECONDS = 15 * 60        # 锁多久
SESSION_SECONDS = 30 * 60     # 初始化会话有效期
TEST_TOKEN_SECONDS = 10 * 60  # test_token 有效期
TEST_RATE_PER_MIN = 5         # 检测接口每分钟上限

_FWD_HEADER = "X-Hunter-Forwarded-For"   # web BFF 注入（见模块文档）


# ── 来源判断 ────────────────────────────────────────────────────────
@dataclass
class Source:
    ip: str                 # 判定用的地址（拿不到时空字符串）
    kind: str               # "local" | "lan" | "public" | "unknown"
    via: str                # "forwarded" | "peer" | "none"
    peer: str = ""          # api 直接看到的对端（正常就是 web 容器）

    @property
    def is_trusted_zone(self) -> bool:
        """本机 / 内网 —— 口令为空时允许直接进向导的那一类。

        `unknown` **算公网**：判不出来就往严处理，不能因为拿不到地址就放行。
        """
        return self.kind in ("local", "lan")

    def to_dict(self) -> dict:
        return {"ip": self.ip, "kind": self.kind, "via": self.via, "peer": self.peer}


def _classify(ip: str) -> str:
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.is_loopback:
        return "local"
    # link-local（169.254/16、fe80::/10）也当内网：docker / k8s 里会出现。
    #
    # ⚠️ `is_private` 的范围比"RFC1918 三段"大：文档/示例网段
    # （192.0.2.0/24、198.51.100.0/24、203.0.113.0/24）也算 private。
    # 写测试时拿 203.0.113.x 当"公网 IP"会得到 lan —— 用 8.8.8.8 这类真实公网地址。
    # 对真实部署没有影响：没有人的客户端会来自文档网段。
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "public"


def _first_forwarded(raw: str) -> str:
    """从转发头里取判定用的地址。

    ⚠️ **取最右边那个，不是最左边。**

    `X-Forwarded-For` 的常规写法是 `$proxy_add_x_forwarded_for`（nginx 默认，演示站
    实测就是它）：把**客户端自己带的值原样保留**，再把它看到的对端追加到末尾。
    所以：

        客户端发 `X-Forwarded-For: 127.0.0.1`
        → nginx 转出去的是 `127.0.0.1, <客户端真实公网 IP>`

    取最左边就正好取到了攻击者写的那个 —— 公网访问者加一个头就能把自己伪装成本机
    （2026-09-18 在演示站上实测复现）。最右边那个是**离我们最近的那一跳亲手写的**，
    是这条链里唯一不受客户端摆布的。

    代价：前面还有 CDN（Cloudflare → nginx）时，最右边是 CDN 的地址，会被判成公网 ——
    方向是安全的那一边（要求设口令），不是放行。

    没有反代时（裸 `docker compose`），Next.js 只在客户端**没带**这个头时才补一个
    socket 地址，链里只有一项，左右一样。
    """
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    v = parts[-1]
    # IPv6 带端口时是 `[::1]:1234`；IPv4 是 `1.2.3.4:1234`
    if v.startswith("["):
        v = v[1:].split("]")[0]
    elif v.count(":") == 1:
        v = v.split(":")[0]
    return v


def source_of(request) -> Source:
    """判断这次请求的真实来源。

    优先用 web BFF 注入的头；**只有在直接对端本身是内网地址时才采信** ——
    对端是公网意味着有人绕开 web 直接打 api 的宿主端口，他给的头一个字都不能信。
    """
    peer = ""
    try:
        peer = request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        peer = ""

    fwd = request.headers.get(_FWD_HEADER) or request.headers.get("X-Forwarded-For") or ""
    fwd_ip = _first_forwarded(fwd)
    peer_kind = _classify(peer)

    if fwd_ip and peer_kind in ("local", "lan"):
        return Source(ip=fwd_ip, kind=_classify(fwd_ip), via="forwarded", peer=peer)
    if peer:
        return Source(ip=peer, kind=peer_kind, via="peer", peer=peer)
    return Source(ip="", kind="unknown", via="none", peer="")


# ── 口令 ────────────────────────────────────────────────────────────
def configured_token() -> str:
    return (os.environ.get("HUNTER_SETUP_TOKEN") or "").strip()


def token_configured() -> bool:
    return bool(configured_token())


@dataclass
class _Gate:
    fails: int = 0
    locked_until: float = 0.0
    sessions: dict = field(default_factory=dict)      # token -> 过期时间戳
    test_hits: list = field(default_factory=list)     # 检测接口的调用时间戳


_gate = _Gate()


def reset_state() -> None:
    """只给测试用 —— 把进程内状态清空。"""
    global _gate
    _gate = _Gate()


def lock_remaining() -> int:
    left = int(_gate.locked_until - time.time())
    return left if left > 0 else 0


def _secret() -> bytes:
    """签名密钥。JWT_SECRET 派生，和 crypto.py 同一把根密钥。

    JWT_SECRET 没配时 boot.sh 会生成一把，所以正常永远非空；真为空时
    回落到一个进程内随机值（重启即失效），**不能回落到固定字符串** ——
    那等于所有部署共用一把签名密钥。
    """
    root = (os.environ.get("JWT_SECRET") or "").encode()
    if not root:
        root = _PROCESS_FALLBACK
    return hashlib.sha256(b"hunter-setup-v1|" + root).digest()


_PROCESS_FALLBACK = secrets.token_bytes(32)


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


# ── 初始化会话 ──────────────────────────────────────────────────────
def issue_session() -> tuple[str, int]:
    """签发初始化会话。返回 (token, 有效秒数)。"""
    exp = int(time.time()) + SESSION_SECONDS
    nonce = secrets.token_urlsafe(18)
    token = f"{nonce}.{exp}.{_sign(f'{nonce}.{exp}')}"
    _gate.sessions[token] = exp
    _prune_sessions()
    return token, SESSION_SECONDS


def _prune_sessions() -> None:
    now = time.time()
    for t, exp in list(_gate.sessions.items()):
        if exp <= now:
            _gate.sessions.pop(t, None)


def session_valid(token: str) -> bool:
    """会话有效吗。

    **既验签名又查进程内的表**：签名保证没被篡改，表保证重启后旧会话立刻失效
    （重启通常意味着换了配置或有人在运维，旧的初始化会话不该继续有效）。
    """
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    nonce, exp_s, sig = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp <= time.time():
        _gate.sessions.pop(token, None)
        return False
    if not hmac.compare_digest(sig, _sign(f"{nonce}.{exp}")):
        return False
    return token in _gate.sessions


def verify_token(given: str) -> dict:
    """校验初始化口令。返回 {"ok", "reason", "locked_for"}。

    锁定是**全实例**的，不按来源 IP 分 —— 一台自部署实例只有一个管理员，
    按 IP 分只是给攻击者一个「换个 IP 重来」的口子。
    """
    left = lock_remaining()
    if left > 0:
        return {"ok": False, "reason": "locked", "locked_for": left,
                "message": f"口令连续输错 {MAX_UNLOCK_FAILS} 次，已锁定，请 {left // 60 + 1} 分钟后再试"}

    want = configured_token()
    if not want:
        return {"ok": False, "reason": "not_configured", "locked_for": 0,
                "message": "这台实例没有设置初始化口令（HUNTER_SETUP_TOKEN）"}

    if hmac.compare_digest(given.strip(), want):
        _gate.fails = 0
        return {"ok": True, "reason": "", "locked_for": 0, "message": ""}

    _gate.fails += 1
    remain = MAX_UNLOCK_FAILS - _gate.fails
    if remain <= 0:
        _gate.locked_until = time.time() + LOCK_SECONDS
        _gate.fails = 0
        logger.warning("[setup] 初始化口令连续错 {} 次 · 锁定 {} 分钟",
                       MAX_UNLOCK_FAILS, LOCK_SECONDS // 60)
        return {"ok": False, "reason": "locked", "locked_for": LOCK_SECONDS,
                "message": f"口令连续输错 {MAX_UNLOCK_FAILS} 次，已锁定 {LOCK_SECONDS // 60} 分钟"}
    return {"ok": False, "reason": "bad_token", "locked_for": 0,
            "message": f"口令不对，还可以再试 {remain} 次"}


# ── test_token（设计方案 4.5）─────────────────────────────────────
def config_digest(base_url: str, model: str, api_key: str) -> str:
    """配置摘要。key 只进摘要，**不以任何形式回显**。"""
    raw = "|".join([
        (base_url or "").strip().rstrip("/"),
        (model or "").strip(),
        hashlib.sha256((api_key or "").encode()).hexdigest(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def issue_test_token(base_url: str, model: str, api_key: str,
                     sanitize_suggest: str = "") -> str:
    """凭证里带上检测得出的 schema 清洗建议。

    为什么要带:检测报文里写着「这个模型需要打开 schema 清洗(保存时会自动设为开)」,
    而保存接口原来只看调用方传的 `sanitize`——网页端会把建议回传,直接调接口的人
    不会,于是落到默认 `auto`,与那句承诺不符。建议签在凭证里,保存时就不必再信任
    调用方,也不用在服务端另存一张表。
    """
    exp = int(time.time()) + TEST_TOKEN_SECONDS
    dig = config_digest(base_url, model, api_key)
    sug = sanitize_suggest if sanitize_suggest in ("0", "1", "auto") else "-"
    return f"{dig}.{exp}.{sug}.{_sign(f'{dig}.{exp}.{sug}')}"


def verify_test_token(token: str, base_url: str, model: str, api_key: str) -> dict:
    """返回 {"ok", "reason", "sanitize_suggest"}。reason 区分「过期」和「配置被改过」——
    两者的下一步动作不一样（重测 vs 检查你改了什么）。

    兼容旧版三段式凭证(没有清洗建议那一段):滚动升级时手里握着旧凭证的人照样能保存,
    只是拿不到建议、按调用方给的值走。"""
    if not token:
        return {"ok": False, "reason": "missing", "sanitize_suggest": "",
                "message": "这份配置还没有通过检测，请先点「开始检测」"}
    parts = token.split(".")
    if len(parts) == 3:
        dig, exp_s, sig = parts
        sug, signed = "-", f"{dig}.{exp_s}"
    elif len(parts) == 4:
        dig, exp_s, sug, sig = parts
        signed = f"{dig}.{exp_s}.{sug}"
    else:
        return {"ok": False, "reason": "malformed", "sanitize_suggest": "",
                "message": "检测凭证格式不对，请重新检测"}
    try:
        exp = int(exp_s)
    except ValueError:
        return {"ok": False, "reason": "malformed", "sanitize_suggest": "",
                "message": "检测凭证格式不对，请重新检测"}
    if not hmac.compare_digest(sig, _sign(signed)):
        return {"ok": False, "reason": "bad_signature", "sanitize_suggest": "",
                "message": "检测凭证无效，请重新检测"}
    if exp <= time.time():
        return {"ok": False, "reason": "expired", "sanitize_suggest": "",
                "message": f"检测结果已超过 {TEST_TOKEN_SECONDS // 60} 分钟，请重新检测后再保存"}
    if not hmac.compare_digest(dig, config_digest(base_url, model, api_key)):
        return {"ok": False, "reason": "mismatch", "sanitize_suggest": "",
                "message": "要保存的配置和刚才检测的不是同一份，请重新检测"}
    return {"ok": True, "reason": "", "message": "",
            "sanitize_suggest": sug if sug in ("0", "1", "auto") else ""}


# ── 限流 ────────────────────────────────────────────────────────────
def take_test_slot() -> dict:
    """检测接口限流。返回 {"ok", "retry_after"}。"""
    now = time.time()
    _gate.test_hits = [t for t in _gate.test_hits if now - t < 60]
    if len(_gate.test_hits) >= TEST_RATE_PER_MIN:
        retry = int(60 - (now - _gate.test_hits[0])) + 1
        return {"ok": False, "retry_after": retry,
                "message": f"检测太频繁（每分钟最多 {TEST_RATE_PER_MIN} 次），请 {retry} 秒后再试"}
    _gate.test_hits.append(now)
    return {"ok": True, "retry_after": 0, "message": ""}
