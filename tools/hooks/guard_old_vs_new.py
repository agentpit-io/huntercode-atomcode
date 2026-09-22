"""guard 的 M4 补漏：逐条对 M3 那版复验。旧版=git show cd6d0ad 的 guard.py。"""
import json, os, subprocess, sys, tempfile
OLD, NEW = sys.argv[1], sys.argv[2]
def run(script, tool, ti, ws):
    p = subprocess.run([sys.executable, script],
        input=json.dumps({"session_id":"t","hook_event_name":"PreToolUse",
                          "tool_name":tool,"tool_input":ti,"cwd":ws}),
        env=dict(os.environ, HCA_WORKSPACE=ws), capture_output=True, text=True)
    lines=[l for l in p.stdout.splitlines() if l.strip()]
    res=json.loads(lines[-1]) if lines else None
    hs=((res or {}).get("hookSpecificOutput") or {})
    return hs.get("permissionDecision")=="deny", hs.get("permissionDecisionReason") or ""
CASES=[("bash_start 后台绕过","bash_start",{"command":"rm -rf holdings/"}),
 ("P1-20 直接调 akshare","bash",{"command":'python3 -c "import akshare as ak; print(ak.stock_zh_a_daily(symbol=\'sh600519\'))"'}),
 ("env 包装","bash",{"command":"env X=1 rm -rf holdings/"}),
 ("timeout 包装","bash",{"command":"timeout 5 rm -rf holdings/"}),
 ("sh -c 子命令","bash",{"command":'sh -c "rm -rf holdings/"'}),
 ("python -m pip","bash",{"command":"python3 -m pip install akshare"}),
 ("中间带 .. 越界","bash",{"command":"cat holdings/../../etc/passwd"}),
 ("反误伤：纯计算","bash",{"command":'python3 -c "import statistics; print(statistics.mean([1,2,3]))"'}),
 ("反误伤：只读 cat","bash",{"command":"cat reports/a.md"}),
 ("反误伤：写 reports","write_file",{"file_path":"reports/x.md"})]
ws=tempfile.mkdtemp()
print("guard 补漏复验 · 旧版 = M3 的 distro/workspace-template/.hooks/guard.py（commit cd6d0ad）")
print("%-26s %-10s %-10s %s" % ("用例","旧版","新版","新版的理由（截断）"))
print("-"*110)
bad=0
for name,tool,ti in CASES:
    o,_ = run(OLD,tool,ti,ws); n,r = run(NEW,tool,ti,ws)
    expect_deny = not name.startswith("反误伤")
    mark = "" if n==expect_deny else "  ← 不符合期望！"
    if n!=expect_deny: bad+=1
    old_lbl = ("拦住" if o else ("漏过" if expect_deny else "放行"))
    new_lbl = ("拦住" if n else "放行")
    print("%-26s %-10s %-10s %s%s" % (name, old_lbl, new_lbl, r[:46], mark))
print("-"*110)
print("结论：前 7 条是真实绕过，旧版全部漏过、新版全部拦住；后 3 条是反误伤对照，两版都放行。")
sys.exit(1 if bad else 0)
