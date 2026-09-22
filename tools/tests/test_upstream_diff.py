"""`tools/upstream_diff_extract.py` 的抽取器单测。

为什么要钉住它：整个 `upstream_diff.sh` 的结论（「四个面无变化」）完全取决于
抽取器抽没抽全。**抽漏了就会报「无变化」，那是最危险的假阴性** —— 升级方看到
一片绿就换底座。所以这里用合成的 Rust 片段，逐个面验：
  · 该抽到的抽到了（路由的方法动词不许串到下一条、SSE 变体不许漏）
  · 抽不到时**必须**吐 `!! 抽取失败` 并让整次 rc=3，不许静默返回空
"""
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import upstream_diff_extract as ux  # noqa: E402


def mktree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def src(tmp_path: Path) -> ux.Src:
    return ux.Src(tmp_path, "-")


# ── 面 1 · 路由 ───────────────────────────────────────────────────────────

ROUTES_RS = """
pub fn router() -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/live", get(live_stream).post(live_post))
        .route(
            "/projects/:hash/sessions/:id",
            get(session_get).delete(session_delete),
        )
        .route("/cd", post(change_dir))
}
"""


def test_路由的方法动词不会串到下一条(tmp_path):
    """老实现用「往后看三行」取动词，`/cd` 会把上一条的 get 也算进来。"""
    t = mktree(tmp_path, {ux.F_DAEMON_LIB: ROUTES_RS})
    got = ux.extract_routes(src(t))
    assert "POST /cd" in got
    assert "GET,POST /cd" not in got
    assert "GET /health" in got
    assert "GET,POST /live" in got
    assert "DELETE,GET /projects/:hash/sessions/:id" in got  # 换行写的也要认


def test_路由文件读不到时明确报失败(tmp_path):
    got = ux.extract_routes(src(tmp_path))
    assert any("!! 抽取失败" in x for x in got)


def test_路由一条都匹配不到也报失败(tmp_path):
    t = mktree(tmp_path, {ux.F_DAEMON_LIB: "fn main() {}\n"})
    got = ux.extract_routes(src(t))
    assert any("!! 抽取失败" in x for x in got)


# ── 面 1 · SSE ───────────────────────────────────────────────────────────

SSE_RS = """
#[derive(Serialize)]
#[serde(tag = "type")]
pub(crate) enum LiveWireEvent {
    #[serde(rename = "snapshot")]
    Snapshot {
        messages: Vec<MessageInfo>,
        /// 注释里写 rename = "骗人的" 也不许当真
        session_id: String,
    },
    #[serde(rename = "text")]
    Text { content: String },
    #[serde(rename = "tool_start")]
    ToolStart {
        id: String,
        name: String,
        arguments: serde_json::Value,
    },
    #[serde(rename = "steered")]
    Steered { count: usize, inputs: Vec<String> },
}
"""


def test_SSE_变体一个都不漏(tmp_path):
    t = mktree(tmp_path, {ux.F_LIVE_API: SSE_RS})
    got = "\n".join(ux.extract_sse(src(t)))
    for name in ("snapshot", "text", "tool_start", "steered"):
        assert f"\n{name}\n" in got + "\n", f"事件类型漏了 {name}"
    assert "SSE 事件载荷字段（4 个变体）" in got
    assert "tool_start: {arguments, id, name}" in got
    assert "text: {content}" in got


def test_SSE_找不到枚举时报失败(tmp_path):
    t = mktree(tmp_path, {ux.F_LIVE_API: "pub enum Other { A }\n"})
    got = ux.extract_sse(src(t))
    assert any("!! 抽取失败" in x for x in got)


# ── 面 3 · hook ──────────────────────────────────────────────────────────

HOOKS_RS = """
pub enum HookEvent {
    PreToolUse,
    PostToolUse,
    Stop,
}
impl HookEvent {
    pub fn cc_name(&self) -> &'static str {
        match self {
            HookEvent::PreToolUse => "PreToolUse",
            HookEvent::PostToolUse => "PostToolUse",
            HookEvent::Stop => "Stop",
        }
    }
}
const HOOKS_FILE: &str = ".hooks.json";
fn payload() {
    json!({"hook_event_name": x, "tool_name": y, "hookSpecificOutput": z,
           "permissionDecision": d, "updatedInput": u});
}
"""


def test_hook_事件与_JSON_键都抽得到(tmp_path):
    t = mktree(tmp_path, {ux.F_HOOKS: HOOKS_RS})
    got = "\n".join(ux.extract_hooks(src(t)))
    assert "HookEvent 变体（3 个）" in got
    assert ".hooks.json" in got
    for k in ("hookSpecificOutput", "permissionDecision", "updatedInput", "tool_name"):
        assert k in got, f"JSON 键漏了 {k}"


def test_hook_找不到枚举时报失败(tmp_path):
    t = mktree(tmp_path, {ux.F_HOOKS: "// 空的\n"})
    got = ux.extract_hooks(src(t))
    assert any("!! 抽取失败" in x for x in got)


# ── 面 4 · MCP ───────────────────────────────────────────────────────────

MCP_RS = """
#[derive(Deserialize)]
pub struct McpServerConfig {
    pub command: Option<String>,
    #[serde(default, rename = "autoApprove")]
    pub auto_approve: Vec<String>,
    pub timeout_ms: Option<u64>,
}
pub enum McpConfigSource {
    Project,
    User,
}
"""


def test_MCP_结构体与枚举都抽得到(tmp_path):
    t = mktree(tmp_path, {ux.F_MCP_CONFIG: MCP_RS})
    got = "\n".join(ux.extract_mcp(src(t)))
    assert "## struct McpServerConfig" in got
    assert "auto_approve: Vec<String>" in got
    assert "autoApprove" in got          # serde rename 要带出来，不然改名看不见
    assert "timeout_ms: Option<u64>" in got
    assert "## enum McpConfigSource" in got


def test_MCP_读不到文件时报失败(tmp_path):
    got = ux.extract_mcp(src(tmp_path))
    assert any("!! 抽取失败" in x for x in got)


# ── 整体：抽取失败必须让 rc=3 ─────────────────────────────────────────────

def test_抽不到任何源码时整次退出码为3(tmp_path):
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(TOOLS / "upstream_diff_extract.py"),
                        str(tmp_path), "-", str(out)], capture_output=True, text=True)
    assert p.returncode == 3, p.stdout + p.stderr
    body = (out / "1-daemon-routes-sse.txt").read_text(encoding="utf-8")
    assert "!! 抽取失败" in body
    assert (out / "missing.txt").is_file()


def test_顶层逗号切分不被嵌套结构骗到():
    got = ux.split_top_level('a: X, b: Foo<A, B>, c: {d: 1, e: 2}, f: "含,逗号"')
    assert len(got) == 4, got
    assert got[1] == "b: Foo<A, B>"
    assert got[3] == 'f: "含,逗号"'


def test_泛型里的逗号不切(tmp_path):
    """`stats: HashMap<String, u64>` 被切两半的话，后半截会被静默丢掉。"""
    got = ux.split_top_level("a: HashMap<String, u64>, b: Vec<(A, B)>, c: X")
    assert got == ["a: HashMap<String, u64>", "b: Vec<(A, B)>", "c: X"], got
    t = mktree(tmp_path, {ux.F_LIVE_API: """
#[serde(tag = "type")]
pub(crate) enum LiveWireEvent {
    #[serde(rename = "state")]
    State { stats: HashMap<String, u64>, running: bool },
}
"""})
    got = "\n".join(ux.extract_sse(src(t)))
    assert "state: {running, stats}" in got, got


# ── 面 2 · GET /skills 的回包形状 ────────────────────────────────────────

SKILLS_LIB_RS = """
struct SkillInfo {
    name: String,
    description: String,
}
async fn get_skills(State(state): State<AppState>) -> impl IntoResponse {
    let skills: Vec<SkillInfo> = registry
        .user_invocable()
        .map(|s| SkillInfo {
            name: s.name.clone(),
            description: s.description.clone(),
        })
        .collect();
    Json(skills)
}
"""

SKILL_RS = """
pub struct Skill {
    pub name: String,
    pub description: String,
    pub allowed_tools: Vec<String>,
}
const SKILL_FILE: &str = "SKILL.md";
"""


def test_GET_skills_的回包字段抽得到(tmp_path):
    t = mktree(tmp_path, {ux.F_SKILL: SKILL_RS, ux.F_DAEMON_LIB: SKILLS_LIB_RS})
    got = "\n".join(ux.extract_skill(src(t)))
    assert "SkillInfo.name: String" in got
    assert "get_skills 赋值: description" in got
    assert "!! 抽取失败" not in got


def test_找不到SkillInfo时明说而不是留空(tmp_path):
    """留空最危险：diff 两边都空 → 报『无变化』，其实是没抽到。"""
    t = mktree(tmp_path, {ux.F_SKILL: SKILL_RS, ux.F_DAEMON_LIB: "fn other() {}\n"})
    got = "\n".join(ux.extract_skill(src(t)))
    assert "!! 抽取失败" in got


# ── 稳定性报告里的 MCP 工具判定 ──────────────────────────────────────────

def test_归一后的工具名也要认成MCP():
    """BFF 的 normalizeToolName 把 `mcp__a__b` 归一成 `a_b`。

    报告里要是按 `mcp__` 前缀判，每一轮都会被误判成「模型看不见 MCP 工具」——
    那会在稳定性报告里凭空造出一堆 P0-10 告警。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hca_soak_report", TOOLS / "stability" / "report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    pf = m.mcp_prefixes()
    assert "watchlist" in pf and "akshare" in pf
    for name in ("watchlist_stock_quickview", "akshare_akshare_search",
                 "screener_market_screen", "mcp__uzi__stock_deep_analysis"):
        assert m.is_mcp_tool(name, pf), name
    for name in ("read_file", "bash", "glob", "use_skill", "write_file"):
        assert not m.is_mcp_tool(name, pf), name


# ── brace_block 的两道防假阴性闸（M5 对抗性自测抓出来的）────────────────────

def test_brace_block不认前缀碰撞():
    """回归：`enum HookEvent` 曾经会匹配上 `enum HookEventKind` 的前缀。

    匹配到之后 `find("{")` 一路找到后面那个 `{`，于是上游把枚举改个名，
    抽取器照样抽出 8 个变体、报告写「无变化」。**这是这个工具最危险的失败模式**
    —— 升级方看到一片绿就换底座。
    """
    src = "pub enum HookEventKind {\n    PreToolUse,\n    Stop,\n}\n"
    assert ux.brace_block(src, r"enum\s+HookEvent\b\s*") is None
    # 名字真对上时照样抽得到
    ok = "pub enum HookEvent {\n    PreToolUse,\n    Stop,\n}\n"
    blk = ux.brace_block(ok, r"enum\s+HookEvent\b\s*")
    assert blk is not None and "PreToolUse" in blk


def test_brace_block不跨声明抓花括号():
    """回归：声明其实是 `type X = ...;` 时，原先会跨几百行抓到一个不相干的块。"""
    src = ("pub type HookEvent = LegacyHookEvent;\n"
           "\n"
           "pub enum SomethingElse {\n    A,\n    B,\n}\n")
    assert ux.brace_block(src, r"HookEvent\b\s*") is None


def test_改名会如实报抽取失败而不是无变化(tmp_path):
    """端到端：把 `enum HookEvent` 改名，hook 面必须吐 `!! 抽取失败`。"""
    root = mktree(tmp_path, {
        "crates/atomcode-capabilities/src/cc_hooks.rs":
            "pub enum HookEventKind {\n    PreToolUse,\n    Stop,\n}\n",
    })
    s = ux.Src(root, "-")
    lines = ux.extract_hooks(s)
    assert any("抽取失败" in x for x in lines), lines
    assert not any(x.strip() == "PreToolUse" for x in lines), "改名了却还抽出变体 = 假阴性"


def test_浸泡报告不把机器写死():
    """回归：报告头部曾经硬编码「测试服务器 `34.133.8.3`（2 核 8G）」。

    M5 的正式浸泡改到了香港那台（8 核 29G），而生成器照样会在报告第一行
    写「跑在测试服务器 34.133.8.3（2 核 8G）」—— **报告里一句实打实的假话**，
    而且它看起来和其它数字一样可信。机器描述必须由调用方传进来。
    """
    src = (TOOLS / "stability" / "report.py").read_text(encoding="utf-8")
    # 只看**真正写进报告正文**的那些行（`w(...)`），注释与 --machine 的 help
    # 里出现这个地址是举例，不算写死。
    emitted = [ln for ln in src.splitlines() if ln.lstrip().startswith("w(")]
    assert not any("34.133.8.3" in ln for ln in emitted), "机器地址又被写进报告正文了"
    assert any("a.machine" in ln for ln in emitted), "报告正文必须用调用方传进来的机器描述"
    assert "--machine" in src and "required=True" in src, "机器描述必须是必填参数"
