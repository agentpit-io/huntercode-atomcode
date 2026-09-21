# M0 探针脚本

2026-09-22 在测试机上跑 M0 实测用的脚本，原样保留，便于复现与后续里程碑复用。
原始输出在 `docs/evidence/M0/`，结论在 `docs/开发文档/M0-预研结论.md`。

## 前置

```bash
# 二进制（校验见 pins.lock）
cd ~/hca/probe && npm pack @atomgit.com/atomcode@5.1.0-linux-x64
tar xzf atomgit.com-atomcode-5.1.0-linux-x64.tgz
sha256sum package/bin/atomcode   # 必须是 40d86fa3…8c8763
install -m755 package/bin/atomcode ~/hca/bin/atomcode

# daemon
nohup ~/hca/bin/atomcode daemon --port 13456 --idle-timeout 0 --no-telemetry &
python3 -c "import json;print('export HCA_TOK='+json.load(open('$HOME/.atomcode/daemon-13456.json'))['token'])" > tok.env
chmod 600 tok.env

# akshare MCP（测试机没有 python3-venv，用 uv 绕开）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv-akshare --python 3.12
uv pip install --python venv-akshare/bin/python agentpit-akshare-mcp==0.1.1
```

## 脚本

| 脚本 | 用途 |
|---|---|
| `chat.sh <输出文件> <请求体JSON> [超时秒]` | 打 `POST /chat` 并把 SSE 存盘 |
| `live.sh <输出文件> <消息JSON> [等待秒]` | 开 `GET /live` 长连 + 发 `POST /live/message` |
| `answer_perm.py <SSE文件> <allow\|deny> <秒>` | 后台盯着 SSE 里的 `permission_request`，自动回决策 |
| `perm_test3.sh <模式>` | 四档权限的越界/敏感用例，跑完打印工具放行情况并核对文件是否落盘 |
| `mcp_probe.py` | 不经 LLM，直连 akshare-mcp 的 stdio JSON-RPC 探针 |
| `stub_llm.py` + `stub_script.json` | 本地 OpenAI 兼容 stub provider。**不伪造业务数据**，只把"模型该发哪个工具调用"写死，用于在额度耗尽时确定性地压测 AtomCode 运行时（权限门 / hook / MCP 挂载 / 并发隔离）。理由见 M0 报告 §10 |

## stub provider 用法

```bash
export HCA_STUB_PORT=18080
export HCA_STUB_SCRIPT=$PWD/stub_script.json
nohup python3 stub_llm.py &

# 注册进 ~/.atomcode/config.toml
cat >> ~/.atomcode/config.toml <<'EOF'

[providers.stub]
type = "openai"
api_key = "stub"
model = "stub-model"
base_url = "http://127.0.0.1:18080/v1"
context_window = 200000
EOF
curl -s -X POST -H "Authorization: Bearer $HCA_TOK" http://127.0.0.1:13456/config/reload

# 用 "provider":"stub" 指定
./chat.sh out/x.sse '{"message":"执行脚本","provider":"stub","working_dir":"…","approval_mode":"plan"}'
```

`stub_script.json` 是一个数组，第 N 步对应本轮已出现的第 N 条 tool 消息
（只数最后一条 user 消息之后的，所以同一会话可以反复重跑）：

```json
[
  {"type":"tool","name":"read_file","arguments":{"file_path":"probe-read.txt"}},
  {"type":"tool","name":"bash","arguments":{"command":"echo hi"}},
  {"type":"text","text":"完了。"}
]
```
