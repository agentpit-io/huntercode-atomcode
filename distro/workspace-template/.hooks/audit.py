#!/usr/bin/env python3
"""HCA 审计 hook 的实体 · PostToolUse · 追加一行 JSONL。

为什么单独一个 .py 而不是塞进 audit.sh 的 heredoc：heredoc 会占掉 stdin，
python 读到的就是脚本自己而不是 hook 的输入（本地实测踩过，输出里
session_id / tool 全是 null）。

契约（M0 §7 实测）：stdin 一条 JSON；tool_input 是解析好的对象；
决策看 stdout 最后一行 JSON，只有 exit 2 才拦。本脚本恒 exit 0、不输出决策。
安全（总控红线 3）：只做 json.loads，不 eval，不把输入拼进命令行。
"""
import datetime
import json
import os
import sys


def main() -> int:
    log = (sys.argv[1] if len(sys.argv) > 1 else "") or ""
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:
        ev = {"_parse_error": True, "_raw_len": len(raw)}
    if not isinstance(ev, dict):
        ev = {"_parse_error": True, "_raw_len": len(raw)}

    cwd = ev.get("cwd") or os.getcwd()
    if not log:
        log = os.path.join(cwd, ".atomcode", "audit.jsonl")

    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": ev.get("session_id"),
        "event": ev.get("hook_event_name"),
        "tool": ev.get("tool_name"),
        "cwd": cwd,
    }
    # tool_response 可能很大（一次 akshare 返回几十 KB），只记长度与前 200 字符
    resp = ev.get("tool_response")
    if resp is not None:
        s = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
        rec["response_len"] = len(s)
        rec["response_head"] = s[:200]
    if ev.get("_parse_error"):
        rec["parse_error"] = True

    try:
        parent = os.path.dirname(log)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # 写日志失败（磁盘满 / 只读挂载）绝不能拖垮对话
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
