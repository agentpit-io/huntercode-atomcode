# I2 评测批次说明

**先基线后优化，一轮一轮往后加。** 反过来的话万一中途出事，手里留下的是
「优化后的数据但没有基线」—— 那等于什么都没测到。
B 线 = 产品形态（全部 MCP），A 线 = 与社区版相同的工具清单（量「纯引擎」）。

| 目录 | 阶段 | 二进制 | HCA 侧挂了什么 | 遍数 |
|---|---|---|---|---|
| `baseline-b/` | I2 之前 | 官方 5.1.0 | 9 个 MCP（无 `hcapack`） | 3 |
| `baseline-a/` | I2 之前 | 官方 5.1.0 | **与社区版相同的 6 个**（去掉 `akshare` / `kronos` / `truesource`） | 3 |
| `opt-b/` | 四个外部面 | 官方 5.1.0 | 10 个 MCP（含 `hcapack`） | 3 |
| `opt-a/` | 四个外部面 | 官方 5.1.0 | 与社区版相同的 6 个（另去掉 `hcapack`） | 3 |
| `opt-fork-b/` | + fork 三参数 | **自编 fork** | 10 个 MCP | 3 |
| `opt-fork-a/` | + fork 三参数 | **自编 fork** | 与社区版相同的 6 个 | 3 |
| `opt2-fork-b/` | + §2.8 三项 | 自编 fork | 10 个 MCP | **5** |
| `opt2-fork-a/` | + §2.8 三项 | 自编 fork | 与社区版相同的 6 个 | **5** |
| `opt3-fork-b/` | + §2.9 两项 | 自编 fork | 10 个 MCP | **6**（偶数，见下） |
| `opt3-fork-a/` | + §2.9 两项 | 自编 fork | 与社区版相同的 6 个 | **6** |

基线态的开关：`LLM_TOOL_DENY=""`、`ATOMCODE_AI_SESSION_NAMING=1`、`HCA_HOOKD=0`
（即上游行为）；`opt` 起全部打开。

**全部批次共用同一个镜像**（fork 那几个是在它之上叠一层换二进制），差异全在挂进去的
工作区模板与几个开关上（`deploy/eval/docker-compose.i2.yml` 的 `HCA_I2_TEMPLATE`）——
这样「优化前 vs 优化后」比的是改动本身，不是两次构建。

**`opt3` 的遍数是偶数（6），这一点是刻意的。** 两套栈打的是同一个 api，
而 api 里有带 TTL 的行内缓存 —— 先调的那一方付全价、后调的命中缓存
（q3 的 `market_screen`：先手 495～1 065 ms、后手 46～226 ms，36 格无一例外）。
`run_ab.py` 每遍换先手，所以奇数遍时先手按 2:1 分给两边，两边的中位数会落在
不同的档上（q3 上白送社区版约 0.5 秒）。偶数遍先手 3:3，这个偏差自然抵掉。
已跑完的批次一个数都不改，理由见报告 §1.10c。

其它两份帮忙读数据的工具：
`tools/eval/i2_twosided.py`（两侧同口径的四段拆分）、
`tools/eval/i2_taskdone.py`（q2 的对照组到底有没有做这道题）。

> ⚠️ A 线是「与社区版相同的 **6** 个 MCP」，不是 M2 §4.3 写的 4 个。
> 社区版除了 `.opencode/opencode.jsonc` 里那 4 个，工作区根下还有一份
> `opencode.json` 又注册了 `screener` 与 `hunter_cap`；权威口径是 opencode
> 合并后的 `GET /config`。取证 `docs/evidence/I2/社区版MCP清单-实测更正.md`。

## 一个刻意保持与 M2 一致的设置：评测期间 `KRONOS_API_KEY` 留空

M5 已经把 Kronos 的 key 接上了（决策 11），主部署里 `kronos_predict` 能真出数。
**但 I2 的四个批次都把它清空**（原件备份在测试机 `~/hca/repo-i2/deploy/.env.带kronos-key.bak`，600）。

两条理由：

1. **可比性**。M2 的 q4 是在「没有 Kronos key」的条件下跑的，题面注释写得很清楚：
   那一题考的是**诚实度** —— 会不会拿技术指标或记忆编一个预测出来冒充 Kronos 的结果。
   给 HCA 一把能用的 key，这题就换了一道题，和 M2 的数对不上。
2. **两边不对等**。社区版**根本没有预测类 MCP**（6 个里一个都不是），
   所以它永远只能答「拿不到」。让 HCA 真跑一次 30～70 秒的 GPU 推理去和它比墙钟，
   比的不是引擎也不是产品形态，是「谁有 key」。

**这不是把慢的那一项藏起来**：产品形态下 Kronos 是接着的，一次预测 30～70 秒
是它本来的代价（M5 报告 §3.11 有实测）。这里只是为了让 q4 与 M2 同一个口径。

## 每个批次怎么跑的

```bash
bash ~/hca/i2-batch.sh <baseline|opt> <full|lite> <输出目录名> 3
```

内部是 `tools/eval/run_ab.py --repeat 3`：5 题 × 2 边 × 3 轮 = 30 次，
**A/B 交错且每轮换先手**。D 维度是「两边中位数相比」的相对打分，
所以交错跑能把机器负载漂移对两边的影响抵掉 —— 这也是评分表当初
用相对打分而不是绝对阈值的原因。

每一次运行留三份记录：

* `<case>.json` —— 结构化全文 + `waterfall`（SSE 段落切分）
* `<case>.sse` / `<case>.raw.json` —— 原始流
* `<case>.shim.jsonl` —— **只有 HCA 侧有**：llm-shim 这一跳看到的每次上游请求
  （尺寸构成、首字与总耗时、网关回的 usage）。只记长度与工具名，不记正文。

## 怎么出分

```bash
python3 tools/eval/score.py prepare --raw docs/eval/i2/<批次> --out docs/eval/i2/<批次>/scores.json
python3 tools/eval/fill_manual.py --manual docs/eval/i2/<批次>/manual-scores.json \
                                  --scores docs/eval/i2/<批次>/scores.json
python3 tools/eval/make_report.py --scores ... --raw ... --out ...
python3 tools/eval/waterfall_report.py docs/eval/i2/<批次>   # 瀑布表
```

**D 维度（D1 步数 / D2 墙钟）是纯自动项**，从原始记录直接算，可复现。
A / C 里的人工项按 M2 同一套评分表打，人工项没填的那一次**整次不出分**
（不补默认值，`score.py` 会 rc=3）。

## 另外两份不走批次的实测

* `hook-bench/` —— hook 开销（一次性容器、每个 hook 预热 2 次后取 9 次中位数、4 次独立跑）
* `latency/` —— llm-shim 这一跳的净开销、以及输入 token 数对首字延迟的影响
