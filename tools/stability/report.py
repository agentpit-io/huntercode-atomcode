#!/usr/bin/env python3
"""把 soak.py 的原始记录渲染成 `docs/stability-report.md`。

    python3 tools/stability/report.py --in ~/hca/m5-soak --out docs/stability-report.md

**每个数字都从 rounds.jsonl / samples.jsonl 算出来，没有手填的。**
算不出来的（比如某一轮 docker stats 没采到）一律显示 `—`，不补 0、不插值 ——
把"没采到"显示成 0 会让内存曲线凭空出现一个断崖。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

MiB = 1024 * 1024
CONTAINERS = ["hca-daemon", "hca-web", "hca-api", "hca-postgres", "hca-redis", "hca-llm-shim"]


def load(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


def fmt(v, unit: str = "", digits: int = 0) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{digits}f}{unit}"
    return f"{v:,}{unit}"


def mib(v) -> str:
    return "—" if v is None else f"{v / MiB:,.0f} MiB"


def kib(v) -> str:
    return "—" if v is None else f"{v / 1024:,.0f} KiB"


def series(samples: list[dict], name: str) -> list[tuple[int, int]]:
    """(epoch, bytes) 序列，只取采到值的点。"""
    out = []
    for s in samples:
        v = ((s.get("mem") or {}).get(name) or {}).get("mem_bytes")
        if isinstance(v, int):
            out.append((s.get("epoch") or 0, v))
    return out


def fit_per_hour(pts: list[tuple[int, int]]) -> tuple[float, float] | None:
    """最小二乘斜率（每小时增长多少字节）与 **R²**。点不够就 None。

    **为什么一定要带 R²**：只给一个斜率，读者没法分辨「真在涨」和「在原地抖」。
    daemon 常驻约 1 GiB，四小时里 ±25 MiB 的正常抖动照样能拟合出一个
    「每小时 27 MiB」的斜率 —— 单看那个数会被读成内存泄漏。
    R² 低就说明这条直线根本没解释什么，判据该看抖动区间而不是斜率。
    """
    if len(pts) < 4:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 3600 for t, _ in pts]
    ys = [float(v) for _, v in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    ss_tot = sum((y - my) ** 2 for y in ys)
    if ss_tot == 0:
        return (k, 1.0)                                   # 完全水平：拟合就是它本身
    b = my - k * mx
    ss_res = sum((y - (k * x + b)) ** 2 for x, y in zip(xs, ys))
    return (k, max(0.0, 1.0 - ss_res / ss_tot))


def slope_per_hour(pts: list[tuple[int, int]]) -> float | None:
    f = fit_per_hour(pts)
    return None if f is None else f[0]


def trend_cell(pts: list[tuple[int, int]]) -> str:
    """趋势格：`每小时 X MiB（R²=0.xx，<判读>）`。R² 低就明说「看不出趋势」。"""
    f = fit_per_hour(pts)
    if f is None:
        return "—"
    k, r2 = f
    if r2 < 0.5:
        how = "**看不出趋势 —— 在抖，不是在涨**"
    elif r2 < 0.8:
        how = "趋势弱，判据请看抖动区间"
    else:
        how = "趋势明确"
    return f"{mib(k)} · R²={r2:.2f} · {how}"



def segments_by_restart(samples: list[dict], name: str) -> list[list[tuple[int, int]]]:
    """把某个容器的内存序列按**它自己重起过**切成几段。

    为什么要切：容器一重起，内存回到冷启动水平再慢慢爬到稳态。
    把跨重起的点放进同一条最小二乘里，拟合出来的"每小时增长"是个**伪趋势** ——
    它描述的是"冷启动爬坡"，不是"长期有没有泄漏"。
    这台测试机与另一条链路共用，对方重起 docker 服务时我们的容器会跟着重起
    （M5 浸泡实际碰到过，见 docs/evidence/M5/浸泡-docker守护进程重启事件.txt），
    所以这不是假想情形。切完之后按**最长的那一段**下判断，并把段数写出来。
    """
    segs: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    last_start = None
    for smp in samples:
        v = ((smp.get("mem") or {}).get(name) or {}).get("mem_bytes")
        started = ((smp.get("restarts") or {}).get(name) or {}).get("started_at")
        if not isinstance(v, int):
            continue
        if last_start is not None and started and started != last_start:
            if cur:
                segs.append(cur)
            cur = []
        last_start = started or last_start
        cur.append((smp.get("epoch") or 0, v))
    if cur:
        segs.append(cur)
    return segs


def span_hours(pts: list[tuple[int, int]]) -> float:
    return (pts[-1][0] - pts[0][0]) / 3600 if len(pts) >= 2 else 0.0


def mcp_prefixes() -> list[str]:
    """MCP 服务名前缀。优先读仓库里的注册表，读不到就退回硬编码的 9 个。"""
    for rel in ("distro/mcp-tools.json", "../distro/mcp-tools.json"):
        f = Path(__file__).resolve().parents[2] / rel
        if f.is_file():
            try:
                return sorted(json.loads(f.read_text(encoding="utf-8")).get("servers", {}).keys())
            except Exception:  # noqa: BLE001
                break
    return ["akshare", "hunter_cap", "hunter_user", "kronos", "portfolio",
            "screener", "truesource", "uzi", "watchlist"]


def is_mcp_tool(name: str, prefixes: list[str]) -> bool:
    """归一后的名字形如 `<服务>_<工具>`；原始名形如 `mcp__<服务>__<工具>`。两种都认。"""
    if name.startswith("mcp__"):
        return True
    return any(name.startswith(p + "_") for p in prefixes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--title", default="HunterCode·AtomCode 发行版 · 4 小时稳定性浸泡报告")
    a = ap.parse_args()
    src = Path(a.src)

    rounds = load(src / "rounds.jsonl")
    samples = load(src / "samples.jsonl")
    summary = json.loads((src / "summary.json").read_text(encoding="utf-8")) \
        if (src / "summary.json").is_file() else {}
    base = json.loads((src / "baseline.json").read_text(encoding="utf-8")) \
        if (src / "baseline.json").is_file() else {}
    fin = json.loads((src / "final.json").read_text(encoding="utf-8")) \
        if (src / "final.json").is_file() else {}

    if not rounds:
        print(f"{src} 里没有 rounds.jsonl，出不了报告")
        return 2

    ok = [r for r in rounds if r.get("ok")]
    bad = [r for r in rounds if not r.get("ok")]
    walls = [r["wall_seconds"] for r in ok if isinstance(r.get("wall_seconds"), (int, float))]
    quotas = [r["quota_delta"] for r in ok if isinstance(r.get("quota_delta"), int)]
    tools = [r.get("tool_count") or 0 for r in ok]

    L = []
    w = L.append
    w(f"# {a.title}")
    w("")
    w(f"> 跑在测试服务器 `34.133.8.3`（Ubuntu 24.04 · 2 核 8G）上的真实部署，"
      f"走的是**和用户一模一样的路径**：api 登录 → web BFF 建会话 → 发消息 → 读历史。")
    w(f"> 脚本 `tools/stability/soak.py`，原始记录 `{src}`（`rounds.jsonl` / `samples.jsonl` 各轮前后两次采样）。")
    w(f"> **每个数字都是从原始记录算出来的**；某一轮没采到的显示 `—`，不补 0 也不插值。")
    w("")

    w("## 1. 结论")
    w("")
    dm = series(samples, "hca-daemon")
    sl = slope_per_hour(dm)
    w("| 项 | 实测 |")
    w("|---|---|")
    w(f"| 浸泡时长 | {fmt(summary.get('hours'), ' 小时', 2)}（{summary.get('started_at','—')} → {summary.get('ended_at','—')}，上海时间）|")
    w(f"| 对话轮数 | **{len(rounds)}**（成功 **{len(ok)}** · 失败 **{len(bad)}**）|")
    w(f"| 出题节奏 | 每 {fmt(summary.get('interval_seconds'), ' 秒')}一题，12 题循环（6 个技能 + 6 类 MCP），每 3 轮换一个会话 |")
    dvals = [v for _, v in dm]
    w(f"| daemon 内存趋势 | 每小时 {trend_cell(dm)}"
      f"；起 {mib(dm[0][1] if dm else None)} → 终 {mib(dm[-1][1] if dm else None)}"
      f"，全程在 {mib(min(dvals) if dvals else None)} ~ {mib(max(dvals) if dvals else None)} 之间 |")
    w(f"| 容器重启 | {restart_line(base, fin, samples)} |")
    w(f"| 日志里的错误行 | {err_total(fin)} |")
    w(f"| MCP 连接 | {mcp_line(rounds)} |")
    w(f"| 网关 token 合计 | {fmt(summary.get('quota_total_delta'))} |")
    w("")

    w("## 2. 内存")
    w("")
    w("| 容器 | 起点 | 终点 | 最小 | 最大 | 中位 | 线性趋势（每小时）| 取自 |")
    w("|---|---|---|---|---|---|---|---|")
    split_note = False
    for c in CONTAINERS:
        pts = series(samples, c)
        vals = [v for _, v in pts]
        segs = segments_by_restart(samples, c)
        best = max(segs, key=len) if segs else []
        if len(segs) > 1:
            split_note = True
            src = f"**{len(segs)} 段**中最长的一段（{len(best)} 点 / {span_hours(best):.1f} h）"
        else:
            src = "全程（未重起）"
        w(f"| `{c}` | {mib(vals[0] if vals else None)} | {mib(vals[-1] if vals else None)} | "
          f"{mib(min(vals) if vals else None)} | {mib(max(vals) if vals else None)} | "
          f"{mib(st.median(vals) if vals else None)} | {trend_cell(best)} | {src} |")
    w("")
    w(f"采样点数：每个容器 {len(samples)} 次（每轮前后各一次 + 首尾各一次）。")
    if split_note:
        w("")
        w("> ⚠️ **有容器在浸泡期间重起过**（见第 1 节「容器重启」那一栏）。")
        w("> 容器一重起，内存回到冷启动水平再慢慢爬到稳态 —— 把跨重起的点放进同一条")
        w("> 最小二乘里拟合出来的「每小时增长」描述的是**冷启动爬坡**，不是长期泄漏。")
        w("> 所以趋势那一格取的是**最长的一段不跨重起的序列**，段数与时长写在「取自」栏。")
        w("> 起点 / 终点 / 最小 / 最大 / 中位仍然是全程的原始值，没有剔除。")
    w("")

    w("## 3. 会话文件与日志增长")
    w("")
    bs, fs = (base.get("sessions") or {}), (fin.get("sessions") or {})
    rows = [
        ("会话文件数", "session_file_count", lambda v: fmt(v)),
        ("会话目录字节", "sessions_bytes", kib),
        ("daemon 日志字节", "logs_bytes", kib),
        ("`~/.atomcode` 合计", "atomcode_home_bytes", kib),
        ("审计日志 `audit.jsonl`", "audit_jsonl_bytes", kib),
        ("guard 日志 `guard.jsonl`", "guard_jsonl_bytes", kib),
    ]
    w("| 项 | 起点 | 终点 | 增量 | 每轮均增 |")
    w("|---|---|---|---|---|")
    for label, key, f in rows:
        b, e = bs.get(key), fs.get(key)
        d = (e - b) if isinstance(b, int) and isinstance(e, int) else None
        per = (d / len(rounds)) if d is not None and rounds else None
        w(f"| {label} | {f(b)} | {f(e)} | {f(d)} | {f(per) if per is not None else '—'} |")
    w("")
    da = (fin.get("host") or {}).get("disk_avail_bytes")
    db = (base.get("host") or {}).get("disk_avail_bytes")
    if isinstance(da, int) and isinstance(db, int):
        w(f"宿主根分区可用空间：{db / 2**30:,.1f} GiB → {da / 2**30:,.1f} GiB"
          f"（变化 {(da - db) / 2**20:,.0f} MiB；**这台机器上还有另一条链路在跑，这个数不能全算在本项目头上**）。")
        w("")

    w("## 4. 错误")
    w("")
    ec = ((fin.get("errors") or {}).get("counts") or {})
    es = ((fin.get("errors") or {}).get("samples") or {})
    w("| 容器 | 命中错误词的日志行数 |")
    w("|---|---|")
    for c in CONTAINERS:
        w(f"| `{c}` | {fmt(ec.get(c))} |")
    w("")
    w("> 口径：浸泡开始之后的容器日志里，命中 "
      "`error|panic|fatal|exception|traceback|unhandled`（忽略大小写、词边界）的行数。"
      "**这是个粗口径** —— 正常的业务提示里也可能带这些词，所以下面把去重后的样本原样列出来，"
      "而不是只给一个数字。")
    w("")
    any_sample = False
    for c in CONTAINERS:
        lines = es.get(c) or []
        if not lines:
            continue
        any_sample = True
        w(f"**`{c}`（去重后前 {len(lines)} 条）**：")
        w("")
        w("```")
        for ln in lines:
            w(ln)
        w("```")
        w("")
    if not any_sample:
        measured = [v for v in ec.values() if isinstance(v, int)]
        if not measured:
            # 没采到 ≠ 没有错误。把这两件事混起来就是在编数。
            w("**⚠️ 这一栏没有数据**（`final.json` 里没有 `errors` 采样）—— "
              "是「没采到」，不是「没有错误」。")
        else:
            w(f"**整个浸泡期间，{len(measured)} 个容器的日志里没有任何一行命中上面那些词。**")
        w("")

    w("## 5. 每一轮")
    w("")
    w("| # | 题 | 类型 | 结果 | 墙钟 | 工具 | 技能 | 正文字数 | stop_reason | MCP | 配额Δ | daemon 内存 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rounds:
        m = r.get("mcp") or {}
        dmem = ((r.get("post") or {}).get("mem") or {}).get("hca-daemon", {}).get("mem_bytes")
        w(f"| {r['round']} | {r['id'].split('-', 1)[1]} | {'技能' if r.get('kind') == 'skill' else 'MCP'} | "
          f"{'✅' if r.get('ok') else '❌'} | {fmt(r.get('wall_seconds'), ' s', 1)} | {r.get('tool_count')} | "
          f"{'·'.join(r.get('skills_used') or []) or '—'} | {fmt(r.get('text_len'))} | "
          f"{r.get('stop_reason') or '—'} | {m.get('connected')}/{m.get('total')} | "
          f"{fmt(r.get('quota_delta'))} | {mib(dmem)} |")
    w("")

    w("## 6. 分布")
    w("")
    w("| 指标 | 中位数 | 最小 | 最大 |")
    w("|---|---|---|---|")
    w(f"| 墙钟（秒）| {fmt(st.median(walls) if walls else None, '', 1)} | {fmt(min(walls) if walls else None, '', 1)} | {fmt(max(walls) if walls else None, '', 1)} |")
    w(f"| 单轮 token | {fmt(st.median(quotas) if quotas else None)} | {fmt(min(quotas) if quotas else None)} | {fmt(max(quotas) if quotas else None)} |")
    w(f"| 单轮工具调用 | {fmt(st.median(tools) if tools else None)} | {fmt(min(tools) if tools else None)} | {fmt(max(tools) if tools else None)} |")
    w("")
    w("**`stop_reason` 分布**（待办池 P1-23：`stopped` 区分不了"
      "「做完了」和「做了一半」）：")
    w("")
    w("| stop_reason | 次数 |")
    w("|---|---|")
    dist: dict[str, int] = {}
    for r in rounds:
        dist[str(r.get("stop_reason"))] = dist.get(str(r.get("stop_reason")), 0) + 1
    for k, v in sorted(dist.items(), key=lambda x: -x[1]):
        w(f"| `{k}` | {v} |")
    w("")

    w("## 7. MCP 工具可见性（待办池 P0-10 / P0-13）")
    w("")
    w("`/mcp/status` 全绿**不等于**模型手里有工具 —— 这是 P0-10/P0-13 那一族问题的核心，"
      "而且没有任何可观测手段（`/live` 的 snapshot 里没有工具清单）。")
    w("所以这里用一个**行为指标**兜底：6 道 MCP 题每一道都必须调到至少一个 `mcp__*` 工具；")
    w("一道没调到，就说明那一轮模型很可能又看不见 MCP 了。")
    w("")
    # ⚠️ 工具名到这里已经被 BFF 归一过了（`mcp__watchlist__stock_quickview`
    # → `watchlist_stock_quickview`，`events.ts` 的 normalizeToolName），
    # 所以**不能**按 `mcp__` 前缀判 —— 那样每一轮都会被误判成"失明"。
    # 按 MCP 服务名前缀判，清单来自 distro/mcp-tools.json（真实 tools/list 生成的）。
    prefixes = mcp_prefixes()
    mcp_rounds = [r for r in rounds if r.get("kind") == "mcp" and r.get("ok")]
    blind = [r for r in mcp_rounds
             if not any(is_mcp_tool(str(t), prefixes) for t in (r.get("tools") or []))]
    w(f"- MCP 题成功轮数：**{len(mcp_rounds)}**")
    w(f"- 其中**一个 `mcp__*` 工具都没调到**的：**{len(blind)}**"
      + ("（" + "、".join(r["id"] for r in blind) + "）" if blind else ""))
    w(f"- 每一轮发消息前的 `/mcp/status`：{mcp_line(rounds)}")
    w("")
    if blind:
        w("> ⚠️ 出现了「状态全绿但模型没调到 MCP 工具」的轮次，逐轮的工具序列在 "
          "`rounds.jsonl` 里，请对照 P0-10 分析。")
    else:
        w("> 这四小时里**没有出现** P0-10 那种失效（每一道 MCP 题都真的调到了 MCP 工具）。"
          "这不能证明问题不存在 —— M4 那次是在「浏览器回合中途断网」之后出现的，"
          "而浸泡脚本不会断网。")
    w("")

    w("## 8. 运维建议")
    w("")
    w(OPS)
    w("")
    w("---")
    w("")
    w(f"原始记录：`{src}/rounds.jsonl`（每轮一行，含前后两次完整采样）· "
      f"`{src}/samples.jsonl` · `{src}/summary.json` · 每轮一份 `r0NN-*.json`。")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"已写 {out}（{len(rounds)} 轮）")
    return 0


def restart_line(base: dict, fin: dict, samples: list[dict] | None = None) -> str:
    """容器在浸泡期间有没有被重起过。

    **光比 `RestartCount` 会漏掉最要紧的一种**：docker 守护进程自己被重启时，
    所有容器都会跟着重起，而 `RestartCount` **一动不动**（它只数重启策略触发的次数）。
    M5 浸泡第 21 轮就真碰上了这一种 —— 共用这台测试机的另一条链路重起了 docker
    服务，六个容器的 `StartedAt` 全部跳到 08:08:14Z，而 `RestartCount` 全是 0。
    只比 RestartCount 的话，报告会打出「**0 次**」这种**假的全绿**。
    所以两个都比，`StartedAt` 变了同样算重起过。
    """
    b, f = (base.get("restarts") or {}), (fin.get("restarts") or {})
    hits = []
    # `docker inspect` 取不到（started_at 为 None）本身就是信号：多半是 docker
    # 守护进程正在重起。M5 浸泡被打断那一次，最后一个采样点恰好就是这个形状
    # —— 而它之后没有采样了，所以首尾比对**什么也看不出来**。这一条单独报。
    blind = {}
    for smp in samples or []:
        r = smp.get("restarts") or {}
        for c in CONTAINERS:
            if c in r and (r.get(c) or {}).get("started_at") is None:
                blind[c] = blind.get(c, 0) + 1
    if blind:
        hits.append("采样期间 " + "、".join(f"`{c}` {n} 次" for c, n in sorted(blind.items()))
                    + "读不到 `StartedAt`（`docker inspect` 当时就失败了，"
                    "多半是 docker 守护进程正在重起）")
    if not f:
        return ("—" if not hits else "⚠️ " + "；".join(hits)
                + "。另外这次没有 `final.json`（浸泡没跑完），首尾比对做不了")
    for c in CONTAINERS:
        bc, fc = b.get(c) or {}, f.get(c) or {}
        if isinstance(fc.get("restarts"), int) and isinstance(bc.get("restarts"), int) \
                and fc["restarts"] != bc["restarts"]:
            hits.append(f"`{c}` RestartCount {bc['restarts']}→{fc['restarts']}")
            continue
        bs, fs = bc.get("started_at"), fc.get("started_at")
        if bs and fs and bs != fs:
            hits.append(f"`{c}` 重起过（StartedAt {bs} → {fs}，而 RestartCount 没动"
                        f" —— 多半是 docker 守护进程自己被重起了）")
    return "**0 次**（六个容器的 RestartCount 与 StartedAt 首尾都一致）" if not hits else \
        "⚠️ " + "；".join(hits)


def err_total(fin: dict) -> str:
    c = ((fin.get("errors") or {}).get("counts") or {})
    vals = [v for v in c.values() if isinstance(v, int)]
    if not vals:
        return "—"
    return f"**{sum(vals)} 行**（逐容器见第 4 节）"


def mcp_line(rounds: list[dict]) -> str:
    pairs = {(r.get("mcp") or {}).get("connected") for r in rounds}
    tot = {(r.get("mcp") or {}).get("total") for r in rounds}
    if pairs == {9} and tot == {9}:
        return f"**逐轮 9/9 connected**（{len(rounds)} 次采样无一例外）"
    return f"connected 取值 {sorted(x for x in pairs if x is not None)}，total {sorted(x for x in tot if x is not None)}"


OPS = """### 8.1 内存

- **daemon 是这套栈里最重的一个**（常驻约 1 GiB：Rust 进程 + 9 个 stdio MCP 子进程）。
  `docker-compose.yml` 没有给它设内存上限 —— 单机私有化下这是对的（设了反而可能在
  长报告那种峰值上被 OOM Kill），但**部署前要确认这台机器至少 8 G**。
- 监控只看一个数的话，看 `hca-daemon` 的 RSS。绝对值本来就接近 1 GiB，
  一惊一乍没意义。
- **但「每小时净增」这个数要连着 R² 一起看**。第 2 节每一格都给了 R²，
  原因是：daemon 常驻约 1 GiB，几十 MiB 的正常抖动照样能拟合出一个
  几十 MiB/小时 的斜率 —— 单看那个数会被读成内存泄漏。
  **R² < 0.5 就是在抖，不是在涨**；判泄漏要的是「斜率明显为正 **且** R² 高」，
  两个条件缺一不可。这一版的实测值见第 2 节。

### 8.2 会话文件

- 会话落在 daemon 容器的 `/data/atomcode/sessions`（compose 卷 `hca_atomcode-home`），
  **每个会话是 5–6 个文件**（`.jsonl` 正文 + `.meta` + `.ui.json` + `.rewind.json` + 锁）。
  上游**没有自动清理**，长期跑会一直涨。
- 建议：按月看一次 `du -sh`，超过一两 GiB 再按修改时间清理旧会话
  （`find /data/atomcode/sessions -mtime +90`）。**别清正在用的**：
  清之前确认 daemon 上没有活跃回合。
- `.atomcode/audit.jsonl` 与 `guard.jsonl` 在工作区里，`HCA_AUDIT_MAX_MB`（默认 64）
  会轮转一代；guard 日志目前**不轮转**，需要的话按同样的方式加。

### 8.3 错误与告警

- `/health` 与 `/mcp/status` 全绿**不代表能用** —— P0-10/P0-12/P0-13 三条都是
  「状态面板全绿但模型其实废了」。**唯一可靠的健康判据是真发一条消息**。
  建议的探活：每天一次 `python3 tools/e2e/web_turn.py --ask "600519 现在什么价？"`，
  判据是「调到了 `mcp__*` 工具」而不是「HTTP 200」。
- **不要在网页之外再开 `/live` 消费者**（P0-13）。调试也走 `tools/e2e/web_turn.py`。
  这四小时的浸泡就是按这条规矩跑的。

### 8.4 给 P0-10 装一个兜底闸（强烈建议）

P0-10 / P0-13 那一族失效（模型手里没有 MCP 工具、退化成 `bash`/`glob` 乱翻）
**救不回当时那一轮**，但可以限制它的爆炸半径。两次实测的代价：
M1 §7 那次连发 26 次 bash、约 **102 万** token；M4 §4.3 那次 30 次调用、
`stop_reason=max_rounds`、网关配额差值 **982 548**。

两道现成的闸：

```ini
# deploy/.env
ATOMCODE_TURN_MAX_ROUNDS=30          # 已是默认值，别调大
HCA_BUDGET_ENABLED=1                 # 默认关，长期运行的部署建议打开
HCA_BUDGET_SESSION_TOOL_CALLS=80     # 单会话工具调用上限（这个计数最可靠）
HCA_BUDGET_DAILY_TOOL_CALLS=500
```

工具调用次数这个计数一路可靠，token 那个不可靠（待办池 P1-12），所以**硬限额优先用次数**。

v0.1.1 起 BFF 还会在回合结束时做一次**行为判定**：一轮调了 ≥3 次工具、
一个 `mcp__*` 都没有、而且确实用了 `bash`/`glob`/`read_file` 这类兜底工具，
就往容器日志里打一条 `⚠️ 疑似 P0-10` 并自动重挂一次 MCP（让**下一轮**能恢复）。
这是目前唯一的观测点 —— 监控建议直接 grep 这条：

```bash
docker compose -p hca logs web | grep '疑似 P0-10'
```

### 8.5 成本

- 投研会话**贵**：这四小时的实测中位数与最大值见第 6 节。技能类问题
  （深度分析 / UZI 扫描）比行情类贵一个数量级。
- 私有化部署如果按量计费，建议在网关侧配日限额，**别指望在 agent 这一侧限**
  —— `/live` 的 `tokens` 事件多数轮次是 0（待办池 P1-12），本地量不准。

### 8.6 升级

- 换 AtomCode 底座之前先跑 `tools/upstream_diff.sh --to <新版本>`，
  确认四个外部面没变；变了就逐条看。**但源码比对只能证明接口没动，不能证明行为没变**，
  所以差异报告第 4 节那 5 项冒烟必须实跑。
- 升级与回滚都已实测（M4：451 s / 61 s）。回滚靠 `install.sh --rollback`。"""


if __name__ == "__main__":
    raise SystemExit(main())
