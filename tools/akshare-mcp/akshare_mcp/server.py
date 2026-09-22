#!/usr/bin/env python3
"""AKShare MCP —— 把 AKShare 变成一个你自己跑的能力(`_24` §8.2④)。

## 为什么 AKShare 不能做成「数据源」

数据源的模型是一条记录 = 一个 `(市场, 数据类型, 接口地址)`。AKShare 有
**一千多个函数**(龙虎榜、十大股东、券商研报、宏观、期货、基金……),
按这个模型建模就得枚举一千多条 —— 做不到。

Hunter 主仓原来只从里面挑了 6 条进目录,那 6 条是**我们**用得上的,
不是**你**用得上的。你想查「北向资金持股明细」而我们没挑,你就没辙。

所以改成一个能力:给模型两个工具

    akshare_search("龙虎榜")   → 有哪些函数能查龙虎榜?
    akshare_call("stock_lhb_detail_em", {...})  → 调它

**模型自己去挑函数,不需要我们提前枚举。**

## 为什么要你自己跑

AKShare 是 Python 库不是 HTTP 服务,没法在表单里填一个 URL。
而且它多数接口的上游在国内 —— 从境外容器直连经常打不通
(Hunter 自己就是因为这个才架了个国内跳板)。

跑起来:

    cd tools/akshare-mcp
    docker build -t akshare-mcp .
    docker run -d -p 8931:8931 --name akshare-mcp akshare-mcp

然后在 Hunter 的「能力 → 接入一个工具」里填 `http://你的地址:8931/sse`。

⚠️ **不要填 Hunter 官方的任何地址** —— 这个服务就是为了让你不依赖我们。

## 安全边界

`akshare_call` 只能调 `akshare` 模块里**公开的、可调用的**属性,
参数走 JSON。它不是一个通用的代码执行入口:
  · 名字带 `_` 前缀的拒绝
  · 不在 akshare 命名空间里的拒绝
  · 不做 eval / exec / import 任意模块

即便如此,**这个服务不要暴露到公网**。它没有鉴权,而 AKShare 会向
第三方站点发请求 —— 公网上的任何人都能借你的机器发请求。
绑在内网或加一层反代鉴权。
"""
from __future__ import annotations

import inspect
import json
import os

import akshare as ak
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("akshare-mcp")

# 单次返回的最大行数。AKShare 有些接口一次几万行,原样塞进模型上下文
# 会把真正有用的东西挤掉,而且多数问题看前几十行就够了。
MAX_ROWS = int(os.getenv("AKSHARE_MAX_ROWS", "200"))
# 光限行数不够：财务类接口一行就有八十几列,50 行照样好几十 KB。
# AtomCode 内核对超过 16 KB 的工具返回会砍成头尾各 4 KB、中间不可见
# (output_artifact.rs:79/82,两个常量都是 const,改不了) —— 待办池 P0-11。
# 所以这里再加一道**字节预算**,并且裁完要明说裁了多少。
MAX_BYTES = int(os.getenv("AKSHARE_MAX_BYTES", "15000"))


def _public_funcs() -> dict[str, str]:
    """akshare 里所有能调的公开函数 → 文档首行。

    结果按需计算不缓存:进程生命周期内 akshare 不会变,但这个函数
    只在 search 时调用(不是热路径),缓存的收益不值得多一处状态。
    """
    out: dict[str, str] = {}
    for name in dir(ak):
        if name.startswith("_"):
            continue
        fn = getattr(ak, name, None)
        if not callable(fn):
            continue
        doc = (inspect.getdoc(fn) or "").strip().split("\n")[0][:120]
        out[name] = doc
    return out


@mcp.tool()
def akshare_search(keyword: str, limit: int = 30) -> str:
    """按关键词找 AKShare 函数。

    keyword 可以是中文(龙虎榜 / 股东 / 研报 / 宏观)也可以是英文片段
    (lhb / holder / macro)。返回匹配的函数名和它文档的第一行。

    **先 search 再 call。**AKShare 的函数名不好猜(龙虎榜是
    stock_lhb_detail_em 而不是 stock_dragon_tiger),硬猜会一直 404。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return json.dumps({"error": "keyword 不能为空"}, ensure_ascii=False)

    hits = []
    for name, doc in _public_funcs().items():
        if kw in name.lower() or kw in doc.lower():
            hits.append({"func": name, "doc": doc})
        if len(hits) >= limit:
            break
    if not hits:
        return json.dumps({
            "found": 0,
            "hint": f"没找到含「{keyword}」的函数。换个词试试 —— "
                    f"AKShare 的命名多数是英文缩写(龙虎榜=lhb、股东=holder、"
                    f"研报=research、资金流=fund_flow)",
        }, ensure_ascii=False)
    return json.dumps({"found": len(hits), "functions": hits}, ensure_ascii=False)


@mcp.tool()
def akshare_signature(func: str) -> str:
    """看某个函数要什么参数 —— 调之前先看这个,省一次试错。"""
    fn = _resolve(func)
    if isinstance(fn, str):
        return fn
    try:
        sig = str(inspect.signature(fn))
    except (TypeError, ValueError):
        sig = "(取不到签名)"
    return json.dumps({
        "func": func, "signature": sig,
        "doc": (inspect.getdoc(fn) or "")[:1500],
    }, ensure_ascii=False)


@mcp.tool()
def akshare_call(func: str, kwargs: dict | None = None,
                 columns: list[str] | None = None) -> str:
    """调用一个 AKShare 函数,返回它的数据。

    func     函数名,先用 akshare_search 找
    kwargs   参数字典,先用 akshare_signature 看要什么
    columns  只要这几列(**强烈建议填**)。财务类接口动辄八十几列,
             不投影的话绝大部分预算都花在你用不到的列上。
             填了不存在的列名会在返回里告诉你哪些没命中。

    返回最多 MAX_ROWS 行、且总字节不超过 MAX_BYTES。
    **被裁时会明确告诉你裁了多少**,不会假装这就是全部。
    """
    fn = _resolve(func)
    if isinstance(fn, str):
        return fn
    try:
        df = fn(**(kwargs or {}))
    except TypeError as e:
        # 参数不对是最常见的失败 —— 把签名一起给出来,让模型能自己改对,
        # 而不是把同一个错误重试三遍
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = "?"
        return json.dumps({
            "error": "bad_arguments", "func": func,
            "message": str(e)[:300], "signature": sig,
        }, ensure_ascii=False)
    except Exception as e:                                     # noqa: BLE001
        return json.dumps({
            "error": "call_failed", "func": func,
            "message": f"{type(e).__name__}: {str(e)[:300]}",
            "hint": "多数 AKShare 接口的上游在国内。如果是超时或连接被拒,"
                    "多半是这台机器到上游的网络问题,不是参数错",
        }, ensure_ascii=False)

    return _to_json(func, df, columns)


def _resolve(func: str):
    """函数名 → 可调用对象。不合法时返回一段 JSON 错误字符串。

    **只认 akshare 命名空间里的公开属性。**这不是通用代码执行入口:
    带下划线前缀的、不存在的、不可调用的一律拒绝。
    """
    name = (func or "").strip()
    if not name or name.startswith("_") or "." in name:
        return json.dumps({
            "error": "bad_func", "func": func,
            "message": "函数名不合法 —— 只接受 akshare 顶层的公开函数名",
        }, ensure_ascii=False)
    fn = getattr(ak, name, None)
    if fn is None or not callable(fn):
        return json.dumps({
            "error": "not_found", "func": name,
            "message": f"akshare 里没有 {name} 这个函数",
            "hint": "用 akshare_search 先找一下正确的名字",
        }, ensure_ascii=False)
    return fn


def _to_json(func: str, df, columns: list[str] | None = None) -> str:
    """DataFrame → JSON。**裁了多少要说出来。**

    两道闸:先按 `MAX_ROWS` 限行,再按 `MAX_BYTES` 二分把行数压到预算以内。
    第二道是 M5 补的 —— 只限行数压不住宽表(待办池 P0-11)。
    """
    try:
        total = len(df)
    except TypeError:
        # 有些接口返回的不是 DataFrame(比如单个 str/dict)
        return json.dumps({"func": func, "data": str(df)[:4000]}, ensure_ascii=False)

    dropped_cols: list[str] = []
    missing_cols: list[str] = []
    all_cols = [str(c) for c in getattr(df, "columns", [])]
    if columns:
        want = [c for c in columns if c in all_cols]
        missing_cols = [c for c in columns if c not in all_cols]
        if want:
            dropped_cols = [c for c in all_cols if c not in want]
            df = df[want]

    def build(n: int) -> str:
        head = df.head(n)
        try:
            # NaN 不能进 JSON,而 pandas 的 to_json 会把它变成 null —— 那是对的。
            # 走 to_json 再 loads 是为了让日期/Decimal 这些也被正确序列化
            records = json.loads(head.to_json(orient="records", date_format="iso"))
        except Exception:                                      # noqa: BLE001
            records = [{"_repr": str(r)[:500]} for _, r in head.iterrows()]
        out = {"func": func, "rows": len(records), "total": total,
               "columns": [str(c) for c in getattr(df, "columns", [])],
               "data": records}
        if missing_cols:
            out["columns_not_found"] = missing_cols
        if dropped_cols:
            out["columns_dropped_by_projection"] = len(dropped_cols)
            out["columns_available"] = all_cols
        if total > len(records):
            out["truncated"] = True
            out["note"] = (f"只返回了前 {len(records)} 行(共 {total} 行)。"
                           f"**其余 {total - len(records)} 行没有返回给你,不是不存在** —— "
                           f"不要凭记忆或推断补齐。需要更多请缩小时间范围、"
                           f"加筛选参数、用 columns 只取需要的列,"
                           f"或调大 AKSHARE_MAX_ROWS / AKSHARE_MAX_BYTES。")
        return json.dumps(out, ensure_ascii=False)

    cap = min(MAX_ROWS, total)
    s = build(cap)
    if len(s.encode("utf-8")) <= MAX_BYTES or cap <= 1:
        return s
    # 二分找能塞进字节预算的最大行数
    lo, hi, best = 1, cap, build(1)
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = build(mid)
        if len(trial.encode("utf-8")) <= MAX_BYTES:
            best, lo = trial, mid + 1
        else:
            hi = mid - 1
    return best


def main() -> None:
    """入口 —— PyPI 的 console_scripts 指向这里。

    **默认 stdio**:Claude Desktop / Cursor / uvx 走这个,是 MCP 的默认形态。
    Docker 镜像里 ENV 把它设成了 sse,所以原来那条
    `docker run -p 8931:8931` 的用法一个字都不用改。

    host/port 在 mcp 2.x 里是 run() 的关键字参数,不再是 1.x 的 mcp.settings。
    """
    transport = (os.getenv("AKSHARE_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 见文件头的安全提醒 —— **不要把它暴露到公网**,它没有鉴权
    host = os.getenv("AKSHARE_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("AKSHARE_MCP_PORT", "8931"))
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise SystemExit(
            f"未知的 AKSHARE_MCP_TRANSPORT={transport!r} —— "
            f"只支持 stdio(默认)/ streamable-http / sse")


if __name__ == "__main__":
    main()
