# I3 评测批次说明（性能收尾轮）

本轮只收尾 I2 剩下的三道题（q3 1.29 / q4 1.26 / q5 1.44 倍），**不动架构**。
两件事各自出数、各自出报告：

| 目录 | 是什么 | 遍数 | 计不计分 |
|---|---|---|---|
| **`opt5-fork-b-12/`** | 「按结构卡」**改对版** —— **本轮的发布档，判定用这个** | **12** | **A / C / D 全套** |
| **`opt3-fork-b-重测-12/`** | I2 发布版（没有结构卡）在**同一天同一台机器**上的对照档 | **12** | 只出 D（对照） |
| `schema-probe/` | U-20：工具 schema 体积 → 模型段耗时的**分档实测**（五臂 × 4 轮 × 每臂 26 次） | — | 不计分，单独出报告 |

每档实际是**两段**跑出来的（见下），仓库里只收合并后的那一份：

| 遍号 | 跑的时间（上海时间） |
|---|---|
| `opt5-fork-b-12` 的 r1–r6 | 03:26–03:56 |
| `opt3-fork-b-重测-12` 的 r1–r6 | 03:58–04:29 |
| `opt5-fork-b-12` 的 r7–r12 | 04:29–05:09 |
| `opt3-fork-b-重测-12` 的 r7–r12 | 05:09–05:39 |

**为什么不把四个分段目录也收进来**：合并只改遍号与记录里的 `id`，
其余逐字段相同 —— 收两份就是同一批数据存两遍（多 13 MB）。
每一段是哪几遍上面这张表写死了，`合并来源.json` 里也有；
要按段重算，按遍号切 r1–r6 / r7–r12 即可。分段目录留在评测机 `~/hca/repo-i3*/docs/eval/i3/`。

## 为什么要有「opt3 重测」这一档，以及为什么每档拆两段

I3 第一批数据出来时，改对版的 q3 从 I2 记的 1.29 涨到 1.51、q4 从 1.26 涨到 1.58 ——
看着就是「把结构卡加回来把速度弄坏了」。**没有直接下结论，而是把 I2 那一版配置原样再跑一遍**：
另放一份仓库副本 `~/hca/repo-i3-opt3`，除 `distro/personas/hunter-research.md` 与
`distro/workspace-template/.atomcode.md` 两个文件外与 `repo-i3` **逐字节相同**，
那两个文件从 `main`（I2 发布的那版）拷来、md5 当场核过。

结果是**同一份配置今天只有 D 21.8 / 逐题 1 的 5**（I2 记的是 23.2 / 2 的 5）。
也就是说**跨天比数字这件事本身不成立**。

所以每档都拆成两段、两档交错跑（opt5 段1 → opt3 段1 → opt5 段2 → opt3 段2），
两档在时间上对称；合并用 `tools/eval/i3_merge_blocks.py`，**只改遍号、一个数不改**
（它会连记录里的 `id` 字段一起改 —— 只改文件名的话两段的 `-r1` 会撞成同一个 id，
打分脚本按 `id` 建表，119 份记录会塌成 60 份，而且后读到的覆盖先读到的）。

## 与 I2 同口径（任务书要求）

`deploy/.env` 从 `~/hca/repo-i2/deploy/.env` **原样拷贝**，其中：

* `KRONOS_API_KEY` 是**空的** —— 理由见 `docs/eval/i2/README.md`：M2 的 q4 考的是
  诚实度（会不会编一个预测出来），给 HCA 一把能用的 key 就换了一道题；
  而社区版**根本没有预测类 MCP**，让 HCA 真跑一次 30～70 秒的 GPU 推理去和它比墙钟，
  比的不是引擎也不是产品形态，是「谁有 key」。
* ⚠️ **I1 那一轮是接上 Kronos 的**，所以 **I3 的 q4 与 I1 的 q4 不可比、与 I2/M2 可比**。

题集是 M2 那五道（`run_ab.py` 的默认 `--question-set m2`）。

## 遍数为什么是偶数

两套栈打的是同一套基线 api，而 api 里有带 TTL 的行内缓存 —— 先调的那一方付全价、
后调的命中缓存。`run_ab.py` 每遍换先手，奇数遍时先手按 n:n±1 分给两边，
两边的中位数会落在不同的档上（I2 实测 q3 上白送社区版约 0.5 秒）。
偶数遍先手对半分，这个偏差自然抵掉。I2 的 `opt3` 起就是这么定的，I3 沿用。

## q2 必须给两套数

社区版在 q2 上**不稳定**：I2 的 40 次里只有 13 次真去读了论点文件，
其余 27 次直接回「你没存买入理由，请贴给我」就结束（8～17 秒）。
于是「q2 达不达标」取决于这一批抽到了哪一类，**不取决于 HCA 变快还是变慢**。

所以本轮按任务书要求**分两套口径分别给出**，两套都写进报告、不合并成一个数：

1. **原样口径** —— 全部运行取中位数（与 M2 / I2 的正式判定同口径）；
2. **都真做题口径** —— 只看「调了至少一个能拿到论点原文的工具」的那些次
   （判据看调用不看正文，见 `tools/eval/i2_taskdone.py` 的 docstring）。

## 怎么跑的

```bash
# 改对版（repo-i3）
bash ~/hca/i3-batch.sh opt-fork full opt5-fork-b   6
bash ~/hca/i3-batch.sh opt-fork full opt5-fork-b-2 6
# 对照档（repo-i3-opt3，只有 distro/ 两个文件不同）
HCA_EVAL_REPO=/home/support/hca/repo-i3-opt3 bash ~/hca/i3-batch.sh opt-fork full opt3-fork-b-重测   6
HCA_EVAL_REPO=/home/support/hca/repo-i3-opt3 bash ~/hca/i3-batch.sh opt-fork full opt3-fork-b-重测-2 6
# 合并成 12 遍
python3 tools/eval/i3_merge_blocks.py --out docs/eval/i3/opt5-fork-b-12 \
    docs/eval/i3/opt5-fork-b docs/eval/i3/opt5-fork-b-2
# U-20 五臂（第 3、4 轮用 CYC0=3 续上，不覆盖前两轮）
bash deploy/eval/i3-schema-probe.sh
CYC0=3 CYCLES=2 N=8 bash deploy/eval/i3-schema-probe.sh
```

## 出表 / 复算

```bash
python3 tools/eval/i2_compare.py  docs/eval/i3 --batches opt5-fork-b-12,opt3-fork-b-重测-12   # D 维度
python3 tools/eval/i2_twosided.py docs/eval/i3 --batches opt5-fork-b-12,opt3-fork-b-重测-12   # 四段拆分
python3 tools/eval/i2_taskdone.py docs/eval/i3 --batches opt5-fork-b-12,opt3-fork-b-重测-12   # q2 两套口径
python3 tools/eval/i3_guard_check.py docs/eval/i3 --batches opt3-fork-b-重测-12,opt5-fork-b-12 # 两处护栏全量核
python3 tools/eval/i3_schema_report.py --dir docs/eval/i3/schema-probe --warm 2               # U-20
```

## 作废的数据（不进仓库，留在评测机 `~/hca/i3-作废/`）

U-20 的第一批（五臂 × 2 轮）整批作废，两个原因叠在一起：
① 改完 compose 与 `i2-up.sh` **之后没有重新 rsync**，于是 `t-full` 那一臂悄悄退回成
`t-rel`（两臂量出一模一样的 39 个工具 / 39 642 字节）；
② 换臂要重建 daemon，而 daemon 一起来就并发冷启 10 个 MCP server，
把 8 核机器的 1 分钟负载顶到 6～7 —— 头几次运行量的是「机器忙不忙」。
探针已加**静置**（等负载回到 1.5 以下再打，最多 90 秒）。

栈起停统一走 `deploy/eval/i3-up.sh`（目录 `~/hca/repo-i3`、compose 项目 `hca-i3`、
追踪 `~/hca/i3-trace`）—— 它只是把这几个环境变量传给 `i2-up.sh`，
**起栈逻辑一行不分叉**，理由和 I1 那份一样。

## 怎么出分

与 I2 / I1 同一套（`score.py prepare` → `fill_manual.py` → `make_report.py`），
人工项没填的那一次**整次不出分**，不补默认值。
