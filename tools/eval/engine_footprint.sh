#!/usr/bin/env bash
# 两边引擎的常驻内存与镜像大小 —— 补 docs/对比-opencode版.md §6 的「未测」（I1）。
#
#     bash tools/eval/engine_footprint.sh <标签> [输出目录]
#
# 关键是**同一时刻**取：`docker stats --no-stream` 一次把两个容器一起问出来，
# 而不是先量一个再量另一个 —— 那样两个数落在不同的负载条件下，比出来的差值没有意义。
#
# 采什么：
#   · `hca-i1-daemon`（AtomCode 引擎，v0.2.0 fork 变体）
#   · `hca-baseline-opencode-1`（opencode 引擎 1.2.0）
#   · 两边各自的 llm-shim（同一份实现，作为「两边一样的东西量出来是不是也一样」的对照，
#     它要是差很多，说明这次采样本身受了别的干扰）
#   · 宿主 free / loadavg（说明这一次采样是在什么条件下取的）
#   · 镜像大小：`docker images` 的 SIZE（**解压后**占盘），另记镜像 Id 以便核对
#
# 镜像大小两边不完全可比，报告里要写明：HCA 的 daemon 镜像里打包了 AtomCode 二进制
# + 9 个 MCP 的 Python 运行环境 + 技能与工作区模板；社区版的 opencode 镜像里是
# Node 运行时 + opencode + 它那套 MCP。两个数放在一起看的是「一台机器要吃多少盘」，
# 不是「谁的代码写得省」。
set -uo pipefail
TAG="${1:?标签，例如 idle / during-eval}"
OUT="${2:-docs/eval/c1/footprint}"
HCA_CT="${HCA_ENGINE_CONTAINER:-hca-i1-daemon}"
OC_CT="${OC_ENGINE_CONTAINER:-hca-baseline-opencode-1}"
HCA_SHIM="${HCA_SHIM_CONTAINER:-hca-i1-llm-shim}"
OC_SHIM="${OC_SHIM_CONTAINER:-hca-baseline-llm-shim-1}"
mkdir -p "$OUT"
F="${OUT}/footprint-${TAG}.json"

STATS=$(docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}|{{.MemPerc}}|{{.CPUPerc}}|{{.PIDs}}' \
        "$HCA_CT" "$OC_CT" "$HCA_SHIM" "$OC_SHIM" 2>&1)
IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}' 2>&1)
FREE=$(free -b | sed -n 2p)
LOAD=$(cat /proc/loadavg)
# 容器各自的镜像（不靠名字猜）
HCA_IMG=$(docker inspect "$HCA_CT" --format '{{.Config.Image}}' 2>/dev/null)
OC_IMG=$(docker inspect "$OC_CT" --format '{{.Config.Image}}' 2>/dev/null)
UP_HCA=$(docker inspect "$HCA_CT" --format '{{.State.StartedAt}}' 2>/dev/null)
UP_OC=$(docker inspect "$OC_CT" --format '{{.State.StartedAt}}' 2>/dev/null)

python3 - "$F" "$TAG" "$HCA_CT" "$OC_CT" "$HCA_IMG" "$OC_IMG" "$UP_HCA" "$UP_OC" <<PY
import json, re, subprocess, sys, socket, time
f, tag, hca_ct, oc_ct, hca_img, oc_img, up_hca, up_oc = sys.argv[1:9]
stats = """$STATS"""
images = """$IMAGES"""
free = """$FREE"""
load = """$LOAD"""

UNIT = {"B":1,"KIB":1024,"MIB":1024**2,"GIB":1024**3,"KB":1000,"MB":1000**2,"GB":1000**3}
def parse_mem(u):
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)\s*/", u)
    return int(float(m.group(1)) * UNIT.get(m.group(2).upper(), 0)) if m else None

mem = {}
for ln in stats.strip().splitlines():
    p = ln.split("|")
    if len(p) != 5:
        continue
    mem[p[0]] = {"mem_bytes": parse_mem(p[1]), "mem_raw": p[1].strip(),
                 "mem_pct": p[2].strip(), "cpu_pct": p[3].strip(), "pids": p[4].strip()}

def img_size(name):
    for ln in images.strip().splitlines():
        p = ln.split("|")
        if len(p) == 3 and p[0] == name:
            return {"image": name, "id": p[1], "size_human": p[2]}
    return {"image": name, "id": None, "size_human": None}

ff = free.split()
out = {
    "tag": tag,
    "machine": socket.gethostname(),
    "ts_shanghai": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 8*3600)),
    "ts_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    "note": "两个引擎由同一条 docker stats --no-stream 同时取，负载条件见 host",
    "engines": {
        "hca": {"container": hca_ct, "image": hca_img, "started_at": up_hca,
                **(mem.get(hca_ct) or {}), **img_size(hca_img)},
        "opencode": {"container": oc_ct, "image": oc_img, "started_at": up_oc,
                     **(mem.get(oc_ct) or {}), **img_size(oc_img)},
    },
    "shims_for_control": {k: mem.get(k) for k in mem if "shim" in k},
    "host": {"mem_total": int(ff[1]) if len(ff) > 2 else None,
             "mem_used": int(ff[2]) if len(ff) > 2 else None,
             "mem_available": int(ff[6]) if len(ff) > 6 else None,
             "loadavg": load.split()[:3]},
    "raw_stats": stats.strip().splitlines(),
}
open(f, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps({"tag": tag,
                  "hca": out["engines"]["hca"].get("mem_raw"),
                  "hca_image": out["engines"]["hca"].get("size_human"),
                  "opencode": out["engines"]["opencode"].get("mem_raw"),
                  "opencode_image": out["engines"]["opencode"].get("size_human"),
                  "loadavg": out["host"]["loadavg"]}, ensure_ascii=False))
print("已写 " + f)
PY
