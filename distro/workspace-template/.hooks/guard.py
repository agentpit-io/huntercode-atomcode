#!/usr/bin/env python3
"""HCA 研究守卫 · PreToolUse · 本发行版唯一的硬约束。

## 为什么必须是 hook，而不是权限档

M0 §4 把四档权限逐条实测过，结论是**没有一档能在无人值守下守住投研工作区**：

* `build`（默认）：工作区内写文件**直接放行、连权限都不弹**（`write_approval.rs`
  的判定顺序里"全部目标在工作区内"排在前面）。持仓文件一手就能改。
* `accept_edits`：比 build 更松，工作区**外**的写也放行。
* `plan`：能把 4 个写类工具拦死，但**拦不住 bash 的写**（`echo x > f` 实测落盘），
  而且它会在每一轮请求尾部注入 `PLAN_MODE_REMINDER_BODY`
  （"不要写实现、给个方案然后停下来等用户批准"）—— 那是写给编码场景的，
  投研助手照做就变成"我打算去查行情，请批准"。
* `bypass`：全放行。

而 M0 §7.4 实测：**在 `bypass` 档下 PreToolUse hook 的 deny 依然生效** ——
hook middleware 排在所有审批门之前。所以本发行版取 `build` 档 + 这个 hook。

## 判定

1. **写类工具**（write_file / edit_file / search_replace / parallel_edit_files）
   —— 只放行 `reports/` 与 `theses/` 两个目录，其余一律 deny。
   放行这两个目录是**按研究需要**定的：研报和投资论点就是这套工作流的交付物，
   `.atomcode.md` 让模型把报告写进 `reports/`，全拦死等于让它只能把报告糊在对话里。
   `holdings/`（持仓）与 `factors/`（因子定义）是用户资产，模型不许改；
   `scripts/` 也拦 —— "自己写个 python 去抓数据"正是要压住的那条编码路径。
2. **bash** —— 只允许只读/纯计算。命中下面任何一条就 deny：
   危险命令、包管理/版本控制、网络抓取（curl/wget/nc…，取数一律走 MCP）、
   写重定向 / tee / sed -i、以及带 `open(...,'w')` 之类写文件的内联脚本。
3. **任何工具** —— 参数里出现工作区外的路径就 deny（含只读工具）。
4. **hunter 系 MCP** —— 补 `_hermes_user_id`（见下）。

## 顺带解决 P0-5：hunter 系 MCP 的用户身份

`uzi` / `watchlist` / `portfolio` / `hunter_cap` / `hunter_user` 这几个薄代理
从**工具参数**里取 `_hermes_user_id`（opencode 那边由 hunter-mcp-context 插件注入），
AtomCode 没有等价插件点。这里用 PreToolUse 的 `hookSpecificOutput.updatedInput`
把用户 id 补进参数 —— 上游 `cc_hooks.rs:795-810` 会拿它整体替换
`call.arguments`，且只给 `updatedInput`、不给 `permissionDecision` 时折叠结果是
`Proceed`（`cc_hooks.rs:832` 的 `_ => BeforeOutcome::Proceed`），不会多弹一次权限。

### 身份从哪来（M3 改）

M2 用的是容器级环境变量 `HUNTER_USER_ID` —— 那是**单用户评测环境**的简化。
网页上线之后每个登录用户是不同的 hermes user_id，继续用一个容器常量
等于所有人共用一份持仓与自选，**那是数据串户，不是体验问题**。

所以优先**按会话查**：hook 事件里带 `session_id`，拿它调
`GET {HERMES_API_URL}/api/internal/session/{sid}/user`（带 `X-Hunter-Internal-Key`）。
这个端点在 hunter-community 1.2.0 的 api 镜像里**已经存在**
（`apps/api/app/routers/internal_tools.py`，当初就是给 hunter-mcp-context 插件写的），
所以 api 侧零改动。归属表 `chat_session_owner` 是服务端权威，浏览器改不了；
`session_id` 由 daemon 自己填，模型也伪造不了。

查不到就回落到 `HUNTER_USER_ID`（单机 / 离线部署仍然走这条），
两条都没有就**不注入** —— 让下游 MCP 自己报「缺用户身份」，
而不是默默用别人的账本。

## 契约

stdin 一条 JSON（`tool_input` 是**解析好的对象**）；决策看 stdout **最后一行** JSON。
本脚本恒 exit 0 —— 上游对"exit 2 且无输出"的处理是当成脚本坏了并放行，
用 stdout 表达语义更直白。安全（总控红线 3）：只做 `json.loads`，不 eval，
不把输入拼进命令行。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request

EVENT = "PreToolUse"

# 身份反查（见文件头「身份从哪来」）。超时给得很短：guard 的 timeout_ms 是 5000，
# 反查是本地 compose 内网的一跳，慢到 2 秒就说明 api 有问题，宁可回落也不要拖垮对话。
HERMES_API_URL = (os.environ.get("HERMES_API_URL") or "").rstrip("/")
HUNTER_INTERNAL_KEY = os.environ.get("HUNTER_INTERNAL_KEY") or ""
LOOKUP_TIMEOUT_S = 2.0
# 反查结果缓存在工作区里 —— hook 是**一次调用一个进程**，进程内缓存活不过一次调用。
LOOKUP_CACHE_TTL_S = 300
# 这几个薄代理从工具参数里取 `_hermes_user_id`（M0 §5 / 待办池 P0-5）
HUNTER_MCP_SERVERS = ("uzi", "watchlist", "portfolio", "hunter_cap", "hunter_user")

# 写类工具：只有这两个目录放行
WRITE_TOOLS = {"write_file", "edit_file", "search_replace", "parallel_edit_files"}
WRITABLE_DIRS = ("reports", "theses")

# 参数里可能藏路径的键（上游工具的真实字段名）
PATH_KEYS = ("file_path", "path", "filename", "file", "dir", "directory")

# bash 第一个词命中就拒。分三类，理由不同，报错里会说清是哪一类。
BASH_DENY = {
    # 破坏性 / 改文件系统
    "rm": "删文件", "rmdir": "删目录", "mv": "移动/改名", "cp": "复制写入",
    "dd": "块写入", "truncate": "截断文件", "shred": "擦除", "ln": "建链接",
    "chmod": "改权限", "chown": "改属主", "chgrp": "改属组", "install": "安装文件",
    "mkfs": "格式化", "mount": "挂载", "umount": "卸载",
    # 提权 / 系统
    "sudo": "提权", "su": "提权", "doas": "提权", "pkexec": "提权",
    "systemctl": "操作系统服务", "service": "操作系统服务", "docker": "操作容器",
    "kill": "杀进程", "pkill": "杀进程", "killall": "杀进程",
    "crontab": "改定时任务", "at": "改定时任务", "reboot": "重启", "shutdown": "关机",
    # 装包 / 版本控制（投研工作区不做这些）
    "pip": "装包", "pip3": "装包", "uv": "装包", "npm": "装包", "pnpm": "装包",
    "yarn": "装包", "apt": "装包", "apt-get": "装包", "cargo": "装包",
    "git": "版本控制", "make": "构建", "cmake": "构建",
    # 网络抓取：取数一律走 MCP 工具，不许自己写爬虫
    "curl": "网络抓取", "wget": "网络抓取", "nc": "网络", "ncat": "网络",
    "telnet": "网络", "ssh": "网络", "scp": "网络", "sftp": "网络",
    "rsync": "网络/复制", "ftp": "网络",
}

# 内联脚本里出现这些就按"要写文件/要联网"处理
INLINE_WRITE_RE = re.compile(
    r"""(?x)
    open\s*\(\s*[^)]*['"][waxr]\+?['"]      # open(..., 'w'/'a'/'x'/'r+')
  | \.to_csv\s*\( | \.to_excel\s*\( | \.write_text\s*\( | \.write_bytes\s*\(
  | shutil\.(copy|move|rmtree)
  | os\.(remove|unlink|rmdir|rename|makedirs|mkdir)
    """
)
INLINE_NET_RE = re.compile(
    r"""(?x)
    \brequests\.(get|post|put|delete|head)\b
  | \bhttpx\.(get|post|put|delete|head|Client|AsyncClient)\b
  | \burllib\.request\b | \burlopen\b | \baiohttp\b
  | \bsocket\.(socket|create_connection)\b
    """
)
# 命令分隔符：命中就开一个新"段"，每段单独判首词。
# `(` `)` 也算分隔符，这样 `$(rm -rf x)` 里的 rm 会被当成一段的首词抓到。
SEPARATORS = {";", "|", "||", "&&", "&", "\n", "(", ")", "$("}


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def decide(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": EVENT,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def rewrite(new_input: dict) -> dict:
    """只改参数、不表态 —— 折叠结果是 Proceed（cc_hooks.rs:832）。"""
    return {"hookSpecificOutput": {"hookEventName": EVENT, "updatedInput": new_input}}


def _cache_path(workspace: str) -> str:
    return os.path.join(workspace, ".atomcode", "session-user.json")


def _cache_read(workspace: str, sid: str):
    try:
        with open(_cache_path(workspace), encoding="utf-8") as f:
            rec = json.load(f).get(sid)
    except Exception:  # noqa: BLE001  缓存坏了就当没有，重新查
        return None
    if not isinstance(rec, dict):
        return None
    if (datetime.datetime.now(datetime.timezone.utc).timestamp() - rec.get("at", 0)) > LOOKUP_CACHE_TTL_S:
        return None
    return rec.get("uid") or None


def _cache_write(workspace: str, sid: str, uid: str) -> None:
    try:
        d = os.path.join(workspace, ".atomcode")
        os.makedirs(d, exist_ok=True)
        path = _cache_path(workspace)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        data[sid] = {"uid": uid, "at": now}
        # 只留最近 200 条，免得这个文件无限长
        if len(data) > 200:
            keep = sorted(data.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:200]
            data = dict(keep)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001  写不进缓存不影响本次判定
        pass


def lookup_user(session_id: str, workspace: str) -> str:
    """session_id → hermes user_id。查不到返回空串，**不抛异常、不猜**。"""
    sid = (session_id or "").strip()
    if not sid or not HERMES_API_URL or not HUNTER_INTERNAL_KEY:
        return ""
    cached = _cache_read(workspace, sid)
    if cached:
        return cached
    url = "{}/api/internal/session/{}/user".format(HERMES_API_URL, urllib.parse.quote(sid, safe=""))
    req = urllib.request.Request(url, headers={"X-Hunter-Internal-Key": HUNTER_INTERNAL_KEY})
    try:
        with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT_S) as r:
            uid = str((json.loads(r.read().decode("utf-8")) or {}).get("user_id") or "").strip()
    except urllib.error.HTTPError as e:
        # 404 = 这个会话没有归属记录（运维在容器里手工发起的那种），是正常情况
        if e.code != 404:
            print("[guard] 身份反查 HTTP {}".format(e.code), file=sys.stderr)
        return ""
    except Exception as e:  # noqa: BLE001
        print("[guard] 身份反查失败：{}".format(type(e).__name__), file=sys.stderr)
        return ""
    if uid:
        _cache_write(workspace, sid, uid)
    return uid


def norm(path: str, workspace: str) -> str:
    """把一个可能是相对路径的字符串折成绝对路径（纯词法，不碰文件系统）。"""
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p):
        p = os.path.join(workspace, p)
    return os.path.normpath(p)


def inside(path: str, root: str) -> bool:
    p, r = os.path.normpath(path), os.path.normpath(root)
    return p == r or p.startswith(r.rstrip("/") + "/")


def in_writable(path: str, workspace: str) -> bool:
    return any(inside(path, os.path.join(workspace, d)) for d in WRITABLE_DIRS)


def collect_paths(value, acc: list) -> None:
    """递归收参数里所有看起来是路径的字符串。"""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in PATH_KEYS and isinstance(v, str) and v.strip():
                acc.append(v)
            else:
                collect_paths(v, acc)
    elif isinstance(value, list):
        for v in value:
            collect_paths(v, acc)


def looks_like_path(tok: str) -> bool:
    """bash 参数里哪些 token 当路径看。只挑明确形态，避免把普通参数误判成路径。"""
    t = tok.strip()
    if not t or t.startswith("-"):
        return False
    return t.startswith("/") or t.startswith("~") or t.startswith("../") or t == ".."


def lex(command: str):
    """按 shell 语法切词，**引号内的内容不参与切分**。

    用 `punctuation_chars=True` 让 `;` `|` `&&` `>` `(` 这些操作符成为独立 token，
    这样 `python3 -c "import statistics;print(...)"` 里那个引号内的 `;` 不会
    被当成命令分隔符（第一版用正则切，正是栽在这里）。
    """
    sh = shlex.shlex(command, posix=True, punctuation_chars=True)
    sh.whitespace_split = True
    return list(sh)  # 引号不配对时抛 ValueError


def is_redirect(tok: str) -> bool:
    """写重定向的操作符 token。`>&` / `>>&` 是复制 fd（`2>&1`），不算写文件。"""
    return ">" in tok and set(tok) <= set("<>&0123456789") and not tok.endswith("&")


def head_name(tok: str) -> str:
    """命令首词归一：剥掉 `$` 反引号引号，再取 basename。"""
    return os.path.basename(tok.strip("$`'\"\\"))


def check_bash(command: str, workspace: str):
    """返回 deny 理由，None 表示放行。"""
    if INLINE_WRITE_RE.search(command):
        return ("内联脚本里有写文件的调用。研究结论请直接回答，需要留档就用 "
                "write_file 写进 reports/ 或 theses/，不要绕过审计用 bash 落盘。")
    if INLINE_NET_RE.search(command):
        return ("内联脚本在自己发 HTTP 请求。行情 / 财务 / 新闻 / 龙虎榜都有现成的 "
                "MCP 工具，请调工具，不要写爬虫 —— 自己抓的数据没有来源可追溯。")

    if "`" in command:
        # 反引号命令替换：shlex 不把 ` 当特殊字符，`echo \`rm -rf x\`` 里的 rm 会被
        # 当成 echo 的参数而漏掉。`$(...)` 走的是 punctuation_chars，首词照样会判，
        # 所以这里只堵反引号这一种写法，成本低、说得清。
        return ("bash 里有反引号命令替换，研究工作区不放行（判定不了里面真正执行的是什么）。"
                "需要命令替换请用 $(...)。")

    try:
        toks = lex(command)
    except ValueError:
        return "bash 命令引号不配对，解析不了 —— 研究工作区不放行解析不出来的命令。"

    segments, cur = [], []
    for t in toks:
        if is_redirect(t):
            return ("bash 里有写重定向（> / >>）。这条路绕过 write_file 的审批与差异审阅，"
                    "研究工作区一律不许。要留档就用 write_file 写 reports/ 或 theses/。")
        if t in SEPARATORS:
            segments.append(cur)
            cur = []
        else:
            cur.append(t)
    segments.append(cur)

    for seg in segments:
        # 跳过 VAR=值 前缀
        i = 0
        while i < len(seg) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", seg[i]):
            i += 1
        if i >= len(seg):
            continue
        head = head_name(seg[i])
        if head == "tee":
            return "bash 用 tee 写文件。要留档请用 write_file 写 reports/ 或 theses/。"
        if head in ("sed", "perl") and any(t.startswith("-i") for t in seg[i + 1:]):
            return f"bash 用 {head} -i 原地改文件。研究工作区不许用 bash 改文件。"
        why = BASH_DENY.get(head)
        if why:
            return (f"bash 命令 `{head}`（{why}）在研究工作区被禁。"
                    "取数据请调 MCP 工具；需要计算就用只读的 python 表达式。")
        for t in seg[i + 1:]:
            if looks_like_path(t) and not inside(norm(t, workspace), workspace):
                return f"bash 参数 `{t}` 指向工作区外。研究会话只在 {workspace} 内活动。"
    return None


def log(workspace: str, rec: dict) -> None:
    try:
        d = os.path.join(workspace, ".atomcode")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "guard.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001  写不进日志绝不能拖垮对话
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
        if not isinstance(ev, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        # 解析不了就放行：拿不准的时候宁可让下游的审批门去判，也不要静默拦住所有工具
        return 0

    tool = ev.get("tool_name") or ""
    args = ev.get("tool_input")
    args = args if isinstance(args, dict) else {}
    workspace = os.path.normpath(
        os.environ.get("HCA_WORKSPACE") or ev.get("cwd") or os.getcwd()
    )

    verdict = None
    reason = ""

    if tool in WRITE_TOOLS:
        targets = []
        collect_paths(args, targets)
        if not targets:
            reason = f"`{tool}` 没给出可识别的目标路径，研究工作区不放行判定不了的写操作。"
            verdict = decide("deny", reason)
        else:
            bad = [t for t in targets if not in_writable(norm(t, workspace), workspace)]
            if bad:
                reason = (
                    f"研究工作区禁止用 `{tool}` 写 {', '.join(bad[:3])}。"
                    f"只有 {'/ '.join(WRITABLE_DIRS)}/ 两个目录可写："
                    "holdings/ 与 factors/ 是用户资产、scripts/ 属于写代码，都不许改。"
                    "结论请直接回答；要留档就写进 reports/ 或 theses/。"
                )
                verdict = decide("deny", reason)

    elif tool == "bash":
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            reason = "bash 没有 command 参数。"
            verdict = decide("deny", reason)
        else:
            why = check_bash(cmd, workspace)
            if why:
                reason = why
                verdict = decide("deny", reason)

    if verdict is None and not tool.startswith("mcp__"):
        # 任何工具（含只读）都不许碰工作区外的路径
        targets = []
        collect_paths(args, targets)
        outside = [t for t in targets if not inside(norm(t, workspace), workspace)]
        if outside:
            reason = (f"`{tool}` 的路径 {outside[0]} 在工作区外。"
                      f"研究会话只在 {workspace} 内活动。")
            verdict = decide("deny", reason)

    if verdict is None and tool.startswith("mcp__"):
        # P0-5：给 hunter 系薄代理补用户身份（来源见文件头「身份从哪来」）
        server = tool.split("__")[1] if "__" in tool else ""
        if server in HUNTER_MCP_SERVERS and "_hermes_user_id" not in args:
            uid = lookup_user(ev.get("session_id") or "", workspace)
            source = "session"
            if not uid:
                uid = (os.environ.get("HUNTER_USER_ID") or "").strip()
                source = "env"
            if uid:
                new = dict(args)
                new["_hermes_user_id"] = uid
                verdict = rewrite(new)
                reason = "注入 _hermes_user_id（来源：{}）".format(source)
            else:
                # 不注入。下游 MCP 会自己报「缺用户身份」，
                # 这比默默用别人的账本强得多。
                reason = "查不到用户身份，未注入 _hermes_user_id"

    if verdict is not None:
        out(verdict)
    log(workspace, {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": ev.get("session_id"),
        "tool": tool,
        "decision": ("deny" if verdict and "permissionDecision" in
                     verdict.get("hookSpecificOutput", {}) else
                     ("rewrite" if verdict else "proceed")),
        "reason": reason,
        "args_head": json.dumps(args, ensure_ascii=False)[:300],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
