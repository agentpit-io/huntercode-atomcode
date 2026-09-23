#!/usr/bin/env python3
"""I3 · 把同一档配置的两个**时间段**批次合成一个批次目录（12 遍）。

    python3 tools/eval/i3_merge_blocks.py \
        --out docs/eval/i3/opt5-fork-b-12 \
        docs/eval/i3/opt5-fork-b docs/eval/i3/opt5-fork-b-2

## 为什么要分两段跑再合

I3 第一批数据出来时撞到一件事：**同一份配置（opt3）在 9 月 22 日夜里量到
D 23.2 / 逐题达标 2/5，9 月 23 日清晨重测只有 D 21.7 / 1/5**。
也就是说「跨天比数字」这件事本身不成立 —— 网关那一侧每一轮模型的耗时会漂。

于是 I3 把每一档都拆成两段跑、两档交错（opt5 段1 → opt3 段1 → opt5 段2 → opt3 段2），
两档在时间上对称。合并之后每档每题 12 遍，中位数不再被某一段的漂移牵着走。
**合并只是把 `-r1..-r6` 改名成 `-r7..-r12` 接到后面**，一个数都不改。

## 规矩

* 遍号按输入目录顺序累加（第一个目录保持原号）；
* `index.json` 不合（它记的是那一次批次的执行流水），改为写一份 `合并来源.json`；
* 已存在的输出目录拒绝覆盖 —— 合并是幂等重建，不是增量追加。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

R_RE = re.compile(r"^(?P<stem>.+)-r(?P<r>\d+)(?P<ext>\..+)$")


def _rewrite_id(path: Path, old_id: str, new_id: str) -> None:
    """把记录里的 `id` 一并改成新遍号（只认整串相等，不做子串替换）。"""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return                              # .raw.json 之类不是对象的，原样留着
    if isinstance(obj, dict) and obj.get("id") == old_id:
        obj["id"] = new_id
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    if args.out.exists():
        print(f"✗ {args.out} 已存在 —— 先删掉再合（合并是重建，不是追加）")
        return 2
    args.out.mkdir(parents=True)

    offset, sources = 0, []
    for d in args.dirs:
        if not d.is_dir():
            print(f"✗ 找不到 {d}")
            return 2
        max_r, n = 0, 0
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name == "index.json":
                continue
            m = R_RE.match(f.name)
            if not m:                      # scores.json / manual-scores.json 之类不带遍号的
                continue
            r = int(m["r"])
            max_r = max(max_r, r)
            dst = args.out / f"{m['stem']}-r{r + offset}{m['ext']}"
            shutil.copy2(f, dst)
            # ⚠️ 光改文件名不够：记录**里面**还有一个 `id` 字段，打分脚本是按 `id`
            # 建表的（`score.py` 的 load_runs）。只改名的话两段的 `-r1` 会撞成同一个
            # id，119 份记录塌成 60 份，而且**后读到的那份覆盖先读到的**——
            # 人工评分就会悄悄贴到另一次运行上。I3 第一次合并就是这样，当场发现。
            if dst.suffix == ".json" and offset:
                _rewrite_id(dst, f"{m['stem']}-r{r}", f"{m['stem']}-r{r + offset}")
            n += 1
        sources.append({"目录": str(d), "遍号": f"r{offset + 1}..r{offset + max_r}", "文件数": n})
        print(f"[merge] {d} → r{offset + 1}..r{offset + max_r}（{n} 个文件）")
        offset += max_r

    (args.out / "合并来源.json").write_text(
        json.dumps({"说明": "由 tools/eval/i3_merge_blocks.py 合并，只改遍号、不改任何数据",
                    "来源": sources}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[merge] 已写 {args.out}（共 {offset} 遍）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
