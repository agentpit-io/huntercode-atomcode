#!/usr/bin/env python3
"""从 AtomCode 某个版本的源码里抽出「四个外部面」的事实，输出成可 diff 的文本。

为什么是"抽事实"而不是直接 `git diff`：上游一次发布动几万行，直接 diff 看不出
**我们依赖的接口有没有变**。本发行版只依赖四个面（总控规则 技术约束）：

    1. daemon HTTP 路由 与 SSE 事件（`/live` 那条流）
    2. skill 解析（SKILL.md frontmatter 字段、目录约定、`GET /skills` 的回包）
    3. hook 事件与格式（8 个事件、`.hooks.json`、脚本的输入输出字段）
    4. MCP 配置（`.mcp.json` 字段、信任门、超时）

每个面抽成一份排序过的纯文本，两版各抽一份，再 `diff -u`。
**抽不到就写 `!! 抽取失败: <原因>`，不猜、不留空**（抽取失败本身就是信号：
文件被挪走 / 结构大改，必须人看）。

    python3 upstream_diff_extract.py <工作树或 git 仓> <rev> <输出目录>

rev 给 `-` 表示直接读工作树（不走 git show）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ── 源文件定位（改了就要同步改这里；找不到文件会如实报错）──────────────────
F_DAEMON_LIB   = "crates/atomcode-daemon/src/lib.rs"
F_LIVE_API     = "crates/atomcode-daemon/src/live_api.rs"
F_SKILL        = "crates/atomcode-capabilities/src/skills/skill.rs"
F_SKILL_MOD    = "crates/atomcode-capabilities/src/skills/mod.rs"
F_USE_SKILL    = "crates/atomcode-capabilities/src/skills/use_skill.rs"
F_HOOKS        = "crates/atomcode-capabilities/src/cc_hooks.rs"
F_MCP_CONFIG   = "crates/atomcode-capabilities/src/mcp/config.rs"
F_MCP_TRUST    = "crates/atomcode-capabilities/src/mcp/trust.rs"
F_MCP_MOD      = "crates/atomcode-capabilities/src/mcp/mod.rs"


class Src:
    def __init__(self, root: Path, rev: str):
        self.root, self.rev = root, rev
        self.missing: list[str] = []

    def read(self, rel: str) -> str | None:
        if self.rev == "-":
            p = self.root / rel
            if not p.is_file():
                self.missing.append(rel)
                return None
            return p.read_text(encoding="utf-8", errors="replace")
        p = subprocess.run(["git", "-C", str(self.root), "show", f"{self.rev}:{rel}"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            self.missing.append(rel)
            return None
        return p.stdout


# `{` 与声明头之间**允许**出现的字符：泛型 `<T, \'a>`、where 子句、trait bound、
# 返回类型箭头、空白。出现别的（尤其 `;` `}` `=`）就说明这个 `{` 不属于这个声明。
_HEADER_GAP_OK = re.compile(r"^[\sA-Za-z0-9_,:<>'&+()\[\]\.\-]*$")


def brace_block(src: str, header_re: str) -> str | None:
    """从匹配 header_re 的那一行的第一个 `{` 起做花括号配对，返回整块。

    两道防假阴性的闸（都是 M5 对抗性自测抓出来的）：

    1. **匹配到的必须是完整标识符**。原先 `enum\s+HookEvent\s*` 会匹配上
       `enum HookEventKind` 的前缀，然后 `find("{")` 一路找到后面的 `{`，
       于是上游把枚举改个名，这里照样抽出 8 个变体、报告写「无变化」。
       **抽漏了报无变化是这个工具最危险的失败模式** —— 升级方看到一片绿就换底座。
       所以调用方的正则末尾都补了 `\b`（或由调用方保证），这里再校验一次。
    2. **`{` 必须紧跟在声明头后面**。原先 `find("{", ...)` 可以跨几百行找到
       一个毫不相干的块（比如声明其实是 `type HookEvent = ...;`）。
       现在只允许中间出现泛型 / where / 空白这类字符。
    """
    m = re.search(header_re, src)
    if not m:
        return None
    i = src.find("{", m.end() - 1)
    if i < 0:
        return None
    gap = src[m.end():i]
    if not _HEADER_GAP_OK.match(gap):
        return None
    # 匹配的末尾若正好切在标识符中间（HookEvent | Kind），不认
    if m.end() < len(src) and (src[m.end() - 1].isalnum() or src[m.end() - 1] == "_"):
        nxt = src[m.end()]
        if nxt.isalnum() or nxt == "_":
            return None
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    return None


def match_paren(src: str, open_idx: int) -> int:
    """给一个 `(` 的下标，返回配对 `)` 的下标；配不上返回 -1。字符串字面量里的括号跳过。"""
    depth, i, n = 0, open_idx, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(body: str) -> list[str]:
    """按顶层逗号切分（忽略 {} () [] <> 里的逗号，以及字符串字面量里的逗号）。

    泛型那对尖括号必须一起数：`stats: HashMap<String, u64>` 不这么数会被切成两半，
    后半截匹配不上字段正则就被**静默丢掉** —— 正是最危险的那种假阴性。
    但 `<` `>` 在 Rust 里也当比较符和 `->` 用，所以只在「前一个非空字符是标识符
    或 `>`」时才当泛型开头，`->` 的 `>` 一律不算收尾。
    """
    items, depth, angle, cur, i, n = [], 0, 0, [], 0, len(body)
    prev = ""
    while i < n:
        c = body[i]
        if c == '"':
            cur.append(c)
            i += 1
            while i < n and body[i] != '"':
                cur.append(body[i])
                if body[i] == "\\" and i + 1 < n:
                    cur.append(body[i + 1])
                    i += 1
                i += 1
            if i < n:
                cur.append(body[i])
        elif c in "{[(":
            depth += 1
            cur.append(c)
        elif c in "}])":
            depth -= 1
            cur.append(c)
        elif c == "<" and (prev.isalnum() or prev in "_>"):
            angle += 1
            cur.append(c)
        elif c == ">" and angle > 0 and prev != "-":
            angle -= 1
            cur.append(c)
        elif c == "," and depth == 0 and angle == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        if not c.isspace():
            prev = c
        i += 1
    if "".join(cur).strip():
        items.append("".join(cur))
    return [x for x in (t.strip() for t in items) if x]


def fail(name: str, why: str) -> list[str]:
    return [f"!! 抽取失败: {name} —— {why}"]


# ── 面 1：daemon 路由 + SSE 事件 ───────────────────────────────────────────

METHOD_RE = re.compile(r"\b(get|post|put|delete|patch|head)\s*\(")


def extract_routes(s: Src) -> list[str]:
    src = s.read(F_DAEMON_LIB)
    if src is None:
        return fail("daemon 路由", f"读不到 {F_DAEMON_LIB}")
    out = set()
    for m in re.finditer(r"\.route\s*\(", src):
        lp = src.index("(", m.end() - 1)
        rp = match_paren(src, lp)
        if rp < 0:
            continue
        args = split_top_level(src[lp + 1:rp])
        if not args:
            continue
        pm = re.match(r'"([^"]+)"', args[0].strip())
        if not pm:
            continue
        verbs = sorted({v.upper() for a in args[1:] for v in METHOD_RE.findall(a)})
        out.add(f"{','.join(verbs) or '?'} {pm.group(1)}")
    if not out:
        return fail("daemon 路由", f"{F_DAEMON_LIB} 里一个 .route( 都没匹配到")
    return [f"# daemon HTTP 路由（{len(out)} 条）"] + sorted(out)


def extract_sse(s: Src) -> list[str]:
    src = s.read(F_LIVE_API)
    if src is None:
        return fail("SSE 事件", f"读不到 {F_LIVE_API}")
    blk = brace_block(src, r"enum\s+LiveWireEvent\b\s*")
    if blk is None:
        return fail("SSE 事件", "找不到 `enum LiveWireEvent`")
    renames = sorted(set(re.findall(r'#\[serde\(rename\s*=\s*"([^"]+)"\)\]', blk)))
    if not renames:
        return fail("SSE 事件", "LiveWireEvent 里没有 serde rename")

    payloads = []
    for item in split_top_level(blk[1:-1]):
        rn = re.search(r'#\[serde\(rename\s*=\s*"([^"]+)"\)\]', item)
        # 去掉所有属性行与注释行之后，第一个标识符就是变体名
        lines = [ln for ln in item.splitlines()
                 if not ln.strip().startswith(("#[", "//", "///"))]
        clean = "\n".join(lines)
        vm = re.search(r"\b([A-Z][A-Za-z0-9]*)\b", clean)
        if not vm:
            continue
        name = rn.group(1) if rn else vm.group(1)
        # **在去掉注释/属性的正文上、按变体名精确匹配**。原先是拿宽松正则
        # `[A-Z][A-Za-z0-9]*` 去扫 item 原文 —— 文档注释里随便一个大写词
        # （`/// Persistence warning: …`）就会先被匹配上。加了 brace_block 的
        # 「`{` 必须紧跟声明头」那道闸之后，这种情况会变成 `(无字段)`，
        # 等于把载荷字段静默丢掉。
        inner = brace_block(clean, rf"\b{re.escape(vm.group(1))}\b\s*")
        if inner is None:
            payloads.append(f"{name}: (无字段)")
            continue
        fields = []
        for f in split_top_level(inner[1:-1]):
            fl = [ln for ln in f.splitlines() if not ln.strip().startswith(("#[", "//", "///"))]
            fm = re.search(r"(?:pub\s+)?([a-z_][a-z0-9_]*)\s*:", "\n".join(fl))
            if fm:
                fields.append(fm.group(1))
        payloads.append(f"{name}: {{{', '.join(sorted(set(fields)))}}}")
    return ([f"# SSE 事件类型（type 字段取值，{len(renames)} 个）"] + renames +
            [f"# SSE 事件载荷字段（{len(payloads)} 个变体）"] + sorted(payloads))


# ── 面 2：skill 解析 ───────────────────────────────────────────────────────

def struct_fields(src: str, name: str) -> list[str] | None:
    blk = brace_block(src, rf"struct\s+{name}\b\s*")
    if blk is None:
        return None
    out = []
    pending: list[str] = []
    for raw in blk.splitlines():
        ln = raw.strip()
        if ln.startswith("#["):
            pending.append(ln)
            continue
        m = re.match(r"(?:pub\s+)?([a-z_][a-z0-9_]*)\s*:\s*(.+?),\s*$", ln)
        if m:
            attrs = " ".join(a for a in pending if "serde" in a)
            out.append(f"{m.group(1)}: {m.group(2)}" + (f"  {attrs}" if attrs else ""))
        pending = []
    return sorted(out)


def extract_skill(s: Src) -> list[str]:
    out: list[str] = []
    src = s.read(F_SKILL)
    if src is None:
        return fail("skill 解析", f"读不到 {F_SKILL}")
    hit = False
    for name in re.findall(r"struct\s+([A-Z][A-Za-z0-9]*)", src):
        f = struct_fields(src, name)
        if f:
            hit = True
            out.append(f"## struct {name}")
            out += [f"  {x}" for x in f]
    if not hit:
        return fail("skill 解析", f"{F_SKILL} 里没抽到任何结构体字段")
    # 目录 / 文件名约定与字面量
    lits = set()
    for f in (F_SKILL, F_SKILL_MOD, F_USE_SKILL):
        t = s.read(f)
        if t is None:
            continue
        lits |= set(re.findall(r'"((?:SKILL\.md|skills|\.atomcode|references|[a-z_]+/)[^"\n]{0,40})"', t))
        lits |= set(re.findall(r'"(allowed-tools|allowed_tools|needs_tools|description|name|license|metadata|version)"', t))
    out.append("## 字面量约定")
    out += [f"  {x}" for x in sorted(lits)]
    # GET /skills 的回包形状：`SkillInfo` 结构体 + get_skills 里给它赋了哪几个字段
    lib = s.read(F_DAEMON_LIB)
    out.append("## GET /skills 的回包字段")
    if lib is None:
        out.append(f"  !! 抽取失败: 读不到 {F_DAEMON_LIB}")
        return out
    info = struct_fields(lib, "SkillInfo")
    blk = brace_block(lib, r"(?:async\s+)?fn\s+get_skills\s*\(")
    if not info and not blk:
        out.append("  !! 抽取失败: 既找不到 struct SkillInfo 也找不到 fn get_skills")
        return out
    if info:
        out += [f"  SkillInfo.{x}" for x in info]
    else:
        out.append("  !! 抽取失败: 找不到 struct SkillInfo")
    if blk:
        assigned = sorted(set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*s\.", blk, re.M)))
        out += [f"  get_skills 赋值: {x}" for x in assigned] or \
               ["  !! 抽取失败: get_skills 里没看到任何 `字段: s.xxx` 形式的赋值"]
    else:
        out.append("  !! 抽取失败: 找不到 fn get_skills")
    return out


# ── 面 3：hook 事件与格式 ─────────────────────────────────────────────────

HOOK_OUT_KEYS = ["hookSpecificOutput", "hookEventName", "permissionDecision",
                 "permissionDecisionReason", "updatedInput", "additionalContext",
                 "continue", "stopReason", "suppressOutput", "decision", "reason",
                 "systemMessage", "hook_event_name", "tool_name", "tool_input",
                 "tool_response", "session_id", "transcript_path", "cwd", "prompt"]


def extract_hooks(s: Src) -> list[str]:
    src = s.read(F_HOOKS)
    if src is None:
        return fail("hook", f"读不到 {F_HOOKS}")
    blk = brace_block(src, r"enum\s+HookEvent\b\s*")
    if blk is None:
        return fail("hook", "找不到 `enum HookEvent`")
    variants = sorted({v for v in re.findall(r"^\s*([A-Z][A-Za-z0-9]*)\s*,", blk, re.M)})
    names = sorted(set(re.findall(r'=>\s*"([A-Za-z]+)"', src)))
    aliases = sorted(set(re.findall(r'"([A-Za-z_]+)"\s*(?:\||=>)\s*', src)))
    out = [f"# HookEvent 变体（{len(variants)} 个）"] + variants
    out += [f"# cc_name() 返回值（{len(names)} 个）"] + names
    out += ["# from_str 接受的写法"] + aliases
    # 配置文件名
    cfg = sorted(set(re.findall(r'"(\.?hooks?\.json|\.hooks|hooks)"', src)))
    out += ["# 配置文件名字面量"] + (cfg or ["  !! 没匹配到 .hooks.json 这类字面量"])
    # 执行方式（只支持 shell 就应当只有 command 这一种）
    for name in ("HookDefinition", "HookMatcher", "HookCommand", "HooksConfig", "HookConfig",
                 "HookEntry", "CcHooksFile"):
        f = struct_fields(src, name)
        if f:
            out.append(f"# struct {name}")
            out += [f"  {x}" for x in f]
    present = [k for k in HOOK_OUT_KEYS if f'"{k}"' in src]
    out += [f"# 输入/输出 JSON 键（在 {F_HOOKS} 里出现的，{len(present)} 个）"] + present
    return out


# ── 面 4：MCP 配置 ────────────────────────────────────────────────────────

def extract_mcp(s: Src) -> list[str]:
    out: list[str] = []
    src = s.read(F_MCP_CONFIG)
    if src is None:
        return fail("MCP 配置", f"读不到 {F_MCP_CONFIG}")
    hit = False
    for name in re.findall(r"struct\s+([A-Z][A-Za-z0-9]*)", src):
        f = struct_fields(src, name)
        if f:
            hit = True
            out.append(f"## struct {name}")
            out += [f"  {x}" for x in f]
    for name in re.findall(r"enum\s+([A-Z][A-Za-z0-9]*)", src):
        blk = brace_block(src, rf"enum\s+{name}\b\s*")
        if blk:
            vs = sorted(set(re.findall(r'#\[serde\(rename\s*=\s*"([^"]+)"\)\]', blk))) or \
                 sorted(set(re.findall(r"^\s*([A-Z][A-Za-z0-9]*)\s*[,{(]", blk, re.M)))
            out.append(f"## enum {name}")
            out += [f"  {v}" for v in vs]
    if not hit:
        return fail("MCP 配置", f"{F_MCP_CONFIG} 里没抽到任何结构体字段")
    lits = set()
    for f in (F_MCP_CONFIG, F_MCP_TRUST, F_MCP_MOD):
        t = s.read(f)
        if t is None:
            continue
        lits |= set(re.findall(r'"(\.?mcp\.json|mcpServers|autoApprove|auto_approve|trust|timeout_ms|timeoutMs|env|command|args|url|type|headers|disabled|enabled)"', t))
    out.append("## 配置字面量（.mcp.json 的键与文件名）")
    out += [f"  {x}" for x in sorted(lits)]
    trust = s.read(F_MCP_TRUST)
    if trust is not None:
        consts = sorted(set(re.findall(r"const\s+([A-Z_]+)\s*:\s*[^=]+=\s*([^;]+);", trust)))
        out.append("## 信任门常量")
        out += [f"  {k} = {v.strip()}" for k, v in consts] or ["  （无）"]
    nums = set()
    for f in (F_MCP_CONFIG, F_MCP_MOD):
        t = s.read(f)
        if t is None:
            continue
        nums |= set(re.findall(r"(?:const|static)\s+([A-Z_]*TIMEOUT[A-Z_]*)\s*:\s*[^=]+=\s*([^;]+);", t))
    out.append("## 超时常量")
    out += [f"  {k} = {v.strip()}" for k, v in sorted(nums)] or ["  （无）"]
    return out


SURFACES = [
    ("1-daemon-routes-sse", "daemon 路由与 SSE 事件", lambda s: extract_routes(s) + [""] + extract_sse(s)),
    ("2-skill",             "skill 解析",             extract_skill),
    ("3-hook",              "hook 事件与格式",        extract_hooks),
    ("4-mcp",               "MCP 配置",               extract_mcp),
]


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    root, rev, outdir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    outdir.mkdir(parents=True, exist_ok=True)
    s = Src(root, rev)
    rc = 0
    for key, title, fn in SURFACES:
        try:
            lines = fn(s)
        except Exception as e:  # noqa: BLE001
            lines = fail(title, f"{type(e).__name__}: {e}")
        body = "\n".join(lines) + "\n"
        if "!! 抽取失败" in body:
            rc = 3
        (outdir / f"{key}.txt").write_text(f"### {title} @ {rev}\n{body}", encoding="utf-8")
    if s.missing:
        (outdir / "missing.txt").write_text("\n".join(sorted(set(s.missing))) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
