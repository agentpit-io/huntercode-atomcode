#!/usr/bin/env python3
"""建首个管理员账号（M3 · 总控「端口与暴露」要求）。

web 对公网，所以单用户免登录必须关掉（`HUNTER_SINGLE_USER=0`），
那就得有一个真账号。api 的规矩是 **第一个注册成功的人自动是 admin**
（`apps/api/app/routers/auth.py` 的 `is_first` 分支），`REGISTRATION_MODE=invite`
时这条对第一个人仍然成立，之后的人才要邀请码。

口令随机生成，写进 `<secrets>/admin.txt`（600）。
**脚本任何时候都不把口令打到 stdout / 日志里**（总控红线 2）——
邮件里也只写这个文件的位置。

    python3 deploy/create_admin.py --api http://127.0.0.1:8200 --secrets ~/hca/secrets

已经存在同邮箱账号时不报错：用文件里的口令登录一次做核对，
登录不上就明确告诉你「账号在但口令对不上」，而不是悄悄覆盖。
幂等 —— `deploy/up.sh` 每次拉起都会跑一遍。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets as pysecrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 不能用 `.local` / `.localhost` / `.example` / `.test` / `.invalid`：
# api 用 email_validator 校验，这些是 RFC 6761/6762 的 special-use 域名，
# 一律 422（M3 首次部署就栽在这上面，见报告 §3.2）。用自有域名的子域。
DEFAULT_EMAIL = "admin@hca.agentpit.io"
DISPLAY = "HCA 管理员"


def req(method: str, url: str, body=None, token: str = "", timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:400]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_raw": f"{type(e).__name__}: {e}"}


def read_password(path: Path) -> str:
    """已有的口令只读不改。重新生成会让已经存在的账号登不上（api 不认新口令）。"""
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("password:"):
            pw = line.split(":", 1)[1].strip()
            if pw:
                return pw
    return ""


def write_password(path: Path, email: str, pw: str) -> None:
    """**只在账号真的建出来之后才写**。

    M3 首次部署时这里是先写文件后注册的，结果注册 422 失败、文件却留下了
    —— 一份「口令对应一个不存在的账号」的记录，比没有文件更坏。
    """
    path.write_text(
        "# HunterCode · AtomCode 发行版（HCA）· 管理员账号\n"
        "# 这台实例的 web 对公网，单用户免登录已关闭，用这个账号登录。\n"
        f"url: http://{os.environ.get('HCA_PUBLIC_HOST', '127.0.0.1')}:"
        f"{os.environ.get('HCA_WEB_HOST_PORT', '3200')}/login\n"
        f"email: {email}\n"
        f"password: {pw}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True, help="api 的地址，例如 http://127.0.0.1:8200")
    ap.add_argument("--secrets", required=True, help="密钥目录，admin.txt 写在这里")
    ap.add_argument("--email", default=os.environ.get("HCA_ADMIN_EMAIL") or DEFAULT_EMAIL)
    args = ap.parse_args()

    api = args.api.rstrip("/")
    secrets_dir = Path(os.path.expanduser(args.secrets))
    secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o700)
    admin_file = secrets_dir / "admin.txt"
    known = read_password(admin_file)

    # ① 先拿已有的口令登一次。
    #    不能靠「register 返回 409」来判断账号已存在 —— `REGISTRATION_MODE=invite`
    #    下，已存在的邮箱再注册返回的是 **400「此实例需要邀请码」**（邀请码校验排在
    #    查重之前），400 里看不出到底是「已经有这个人了」还是「真要邀请码」。
    #    M3 第二次部署就是这么把幂等判挂的：栈明明好好的，却报「管理员没建成」。
    if known:
        st, body = req("POST", f"{api}/api/auth/login", {"email": args.email, "password": known})
        if st == 200:
            role = (body.get("user") or {}).get("role") or "?"
            print(f"[admin] {args.email} 已存在，用 {admin_file} 里的口令登录成功（role={role}）")
            return 0
        print(f"[admin] {admin_file} 里的口令登不上（HTTP {st}），试着注册一次", file=sys.stderr)

    # ② 没有口令记录（或记录对不上）才去注册。
    password = known or pysecrets.token_urlsafe(18)
    st, body = req("POST", f"{api}/api/auth/register",
                   {"email": args.email, "password": password, "display_name": DISPLAY})
    if st == 200:
        role = (body.get("user") or {}).get("role") or "?"
        write_password(admin_file, args.email, password)
        # 建完立刻用它登一次：register 成功但登不上要当场发现，
        # 而不是等用户在浏览器前面发现。
        st_login, _ = req("POST", f"{api}/api/auth/login",
                          {"email": args.email, "password": password})
        if st_login != 200:
            print(f"[admin] ✗ 账号建出来了但登录回验失败（HTTP {st_login}）", file=sys.stderr)
            return 3
        print(f"[admin] 已创建 {args.email}（role={role}）并登录回验通过，"
              f"口令在 {admin_file}（600，不打印）")
        if role != "admin":
            print("[admin] ⚠ 这个账号不是 admin —— 说明库里之前就有用户了。"
                  "要重新拿一个管理员，先清 postgres 数据卷再跑。", file=sys.stderr)
        return 0

    if known:
        print(f"[admin] ✗ {args.email} 登不上、也注册不了（注册 HTTP {st}：{body}）。"
              f"不覆盖 {admin_file} —— 请人工处理。", file=sys.stderr)
        return 2
    print(f"[admin] ✗ 注册失败 HTTP {st}：{body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
