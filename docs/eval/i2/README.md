# I2 评测批次说明

六个批次，**先基线、再四个外部面、最后 fork**。反过来的话万一中途出事，
手里留下的是「优化后的数据但没有基线」—— 那等于什么都没测到；
而把 fork 排在最后，是为了「四个外部面拿到多少」这个问题单独有答案。

| 目录 | 阶段 | 二进制 | HCA 侧挂了什么 | 开关 |
|---|---|---|---|---|
| `baseline-b/` | I2 之前 | 官方 5.1.0 | 9 个 MCP（无 `hcapack`） | `LLM_TOOL_DENY=""`、`ATOMCODE_AI_SESSION_NAMING=1`、`HCA_HOOKD=0` |
| `baseline-a/` | I2 之前 | 官方 5.1.0 | **与社区版相同的 6 个**（去掉 `akshare` / `kronos` / `truesource`） | 同上 |
| `opt-b/` | 四个外部面 | 官方 5.1.0 | 10 个 MCP（含 `hcapack`） | 白名单开、会话起名关、常驻 hook 开 |
| `opt-a/` | 四个外部面 | 官方 5.1.0 | 与社区版相同的 6 个（另去掉 `hcapack`） | 同上 |
| `opt-fork-b/` | 外部面 + fork | **自编 fork** | 10 个 MCP | 同 `opt-b`，另加 `system_prompt_file` 与 `[tools] deny` |
| `opt-fork-a/` | 外部面 + fork | **自编 fork** | 与社区版相同的 6 个 | 同 `opt-a`，另加同两项 |

`opt` 与 `opt-fork` 之间**只差那一层二进制和它才认得的两个 `config.toml` 参数**
（唯一的额外覆盖是 `deploy/eval/docker-compose.i2-fork.yml`）——
这样「fork 值不值」能单独回答，不和四个外部面的收益混在一起。

### 两批作废的数据

| 目录 | 为什么作废 |
|---|---|
| 测试机 `hunter-test-01` 上那批 | 机器负载 17–22（与另一条链路共用），同一道 q4 一次 133 秒（M2 同题 9.8 秒）。计时全部作废 |
| `_废弃-baseline-b-未铺账本/` | 评测账本没铺进 HCA 工作区，q2 对 HCA 侧结构性不可能完成。**而且它的表现是 16.1 秒、4 次调用 —— 又快又干净**，D 维度甚至会给高分 |

两批原始记录都留着。第二批之后又发现一条更安静的（`HUNTER_INTERNAL_KEY` 与基线
api 对不上，6 个 hunter 系 MCP 每次调用都回 `internal auth failed`，而
`/mcp/status` 全程 9/9 connected），见 `docs/开发文档/I2-性能优化报告.md` §3.3。

**六个批次共用同一个镜像**（fork 那两个在它之上叠一层换二进制），差异全在挂进去的工作区模板与上面几个开关上
（`deploy/eval/docker-compose.i2.yml` 的 `HCA_I2_TEMPLATE`）——
这样「优化前 vs 优化后」比的是改动本身，不是两次构建。

> ⚠️ A 线是「与社区版相同的 **6** 个 MCP」，不是 M2 §4.3 写的 4 个。
> 社区版除了 `.opencode/opencode.jsonc` 里那 4 个，工作区根下还有一份
> `opencode.json` 又注册了 `screener` 与 `hunter_cap`；权威口径是 opencode
> 合并后的 `GET /config`。取证 `docs/evidence/I2/社区版MCP清单-实测更正.md`。

## 一个刻意保持与 M2 一致的设置：评测期间 `KRONOS_API_KEY` 留空

M5 已经把 Kronos 的 key 接上了（决策 11），主部署里 `kronos_predict` 能真出数。
**但 I2 的六个批次都把它清空**（评测机 `deploy/.env` 里该项留空；改动前先 `cp deploy/.env deploy/.env.bak-<时分>`）。

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
