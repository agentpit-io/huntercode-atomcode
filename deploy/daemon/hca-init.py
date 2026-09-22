#!/usr/bin/env python3
"""daemon 容器的启动前初始化：铺工作区 + 渲染 config.toml。

由 `deploy/daemon/entrypoint.sh` 在起 daemon **之前**调用。做两件事：

## 1. 把工作区模板铺进 $HCA_WORKSPACE

`distro/workspace-template/` 在镜像里是 `/opt/hca/workspace-template/`（只读原件），
工作区 `/workspace` 是数据卷。两类文件区别对待：

* **发行版托管**（`.atomcode.md` `.mcp.json` `.hooks.json` `.hooks/`
  `.atomcode/skills/` `README.md` `requirements.txt`）—— 每次启动重铺，
  这样升级镜像就等于升级技能和 MCP 清单，不用用户手动合并。
  `HCA_WORKSPACE_REFRESH=0` 可以关掉（自己改过这些文件的人用）。
* **用户资产**（`holdings/` `theses/` `factors/` `reports/` `scripts/`）——
  **只在不存在时铺一次**，之后再也不碰。这些是用户的研究资料，重铺等于删数据。

`.hooks.json` 里的 `${VAR}` / `${VAR:-默认值}` 在这一步替换成容器环境变量。

**`.mcp.json` 不在这一步替换，原样铺过去** —— AtomCode 自己会展开
（上游 `mcp/config.rs:315-336`，`command` / `args` / `env` 三处都走 `expand_env_vars`，
支持的正是 `${VAR}` 与 `${VAR:-默认值}` 这两种写法，`config.rs:797+` 有对应单测）。
交给它展开比我们先渲染好：**kronos / truesource / hunter 系那几把 key 就不会以明文
落进工作区数据卷**，只在 daemon 进程的环境里存在。

`.hooks.json` 则必须我们自己渲染：hook 的 `command` **没有**这一层展开
（`cc_hooks.rs` 直接拿字符串当命令），而且那个文件是严格 JSON、连 `//` 注释都不许有。

⚠️ 一个已知取舍：AtomCode 的 `/live/permission` 有个 `allow_persist`，会把工具写进
**工作区** `.mcp.json` 的 `autoApprove`。默认重铺会把这种运行时追加冲掉 ——
模板里已经把所有工具都预置进 `autoApprove` 了，正常用不到 `allow_persist`；
真要留住就设 `HCA_WORKSPACE_REFRESH=0`。

## 2. 渲染 ~/.atomcode/config.toml

provider 只有一个（OpenAI 兼容），地址/模型/key 全来自环境变量
（已拍板决策 3：默认 OneAPI · Gemini 3.8，也允许自建地址）。key 支持走
`HCA_LLM_API_KEY_FILE` 从文件读 —— compose 里用 secrets / 挂载文件时不用把
密钥塞进环境变量，`docker inspect` 就看不到。

写完 chmod 0600。**任何情况下都不打印 key**，只打印长度和前缀。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATE_DIR = Path(os.environ.get("HCA_TEMPLATE_DIR", "/opt/hca/workspace-template"))
WORKSPACE = Path(os.environ.get("HCA_WORKSPACE", "/workspace"))
ATOMCODE_HOME = Path(os.environ.get("ATOMCODE_HOME", "/data/atomcode"))

# 每次启动重铺的「发行版托管」条目（相对模板根）
MANAGED = [
    ".atomcode.md",
    ".mcp.json",
    ".hooks.json",
    ".hooks",
    ".atomcode/skills",
    "README.md",
    "requirements.txt",
]
# 只在缺失时铺一次的用户资产目录
SEED_ONCE = ["holdings", "theses", "factors", "reports", "scripts"]

# ${VAR} 与 ${VAR:-默认值}
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def render_vars(text: str) -> tuple[str, list[str]]:
    """替换 ${VAR} / ${VAR:-默认}。返回 (结果, 用了默认值的变量名)。"""
    fell_back: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        val = os.environ.get(name)
        if val is None or val == "":
            if default is None:
                fell_back.append(f"{name}（无默认值，渲染成空串）")
                return ""
            fell_back.append(name)
            return default
        return val

    return VAR_RE.sub(sub, text), fell_back


def copy_tree(src: Path, dst: Path) -> int:
    """整目录覆盖复制，返回文件数。目标里多出来的文件删掉 ——
    否则上一版留下的技能目录会一直赖着，`GET /skills` 里多出一个幽灵技能。"""
    n = 0
    wanted: set[Path] = set()
    for f in sorted(src.rglob("*")):
        if f.is_dir():
            continue
        target = dst / f.relative_to(src)
        wanted.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    if dst.is_dir():
        for f in sorted(dst.rglob("*")):
            if f.is_file() and f not in wanted:
                f.unlink()
    return n


def seed_workspace() -> None:
    refresh = env_bool("HCA_WORKSPACE_REFRESH", True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"[hca-init] 工作区 {WORKSPACE}（模板 {TEMPLATE_DIR}，重铺托管文件={'是' if refresh else '否'}）")

    for rel in MANAGED:
        src = TEMPLATE_DIR / rel
        dst = WORKSPACE / rel
        if not src.exists():
            print(f"[hca-init]   ⚠ 模板里没有 {rel}，跳过")
            continue
        if dst.exists() and not refresh:
            print(f"[hca-init]   保留 {rel}（HCA_WORKSPACE_REFRESH=0）")
            continue
        if src.is_dir():
            n = copy_tree(src, dst)
            print(f"[hca-init]   铺开 {rel}/  {n} 个文件")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            if rel == ".hooks.json":
                # 只渲染这一个：hook 的 command 不走 AtomCode 的环境变量展开。
                # .mcp.json 交给 AtomCode 自己展开，密钥不落盘。
                text, fell_back = render_vars(text)
                if fell_back:
                    print(f"[hca-init]   {rel} 用默认值的变量：{'、'.join(sorted(set(fell_back)))}")
                try:
                    json.loads(text)  # 严格 JSON —— 解析不过 AtomCode 会静默当没有 hook
                except json.JSONDecodeError as e:
                    print(f"[hca-init]   ✗ {rel} 不是合法 JSON（{e}）——"
                          f"AtomCode 会静默忽略整个文件，hook 一条都不会生效")
            dst.write_text(text, encoding="utf-8")
            print(f"[hca-init]   铺开 {rel}")

    # hook 脚本要可执行 —— copy2 保留权限，但从 git 检出/rsync 过的模板可能丢 x 位
    hooks_dir = WORKSPACE / ".hooks"
    if hooks_dir.is_dir():
        for f in hooks_dir.iterdir():
            if f.suffix in (".sh", ".py"):
                f.chmod(0o755)

    for rel in SEED_ONCE:
        src = TEMPLATE_DIR / rel
        dst = WORKSPACE / rel
        if dst.exists():
            print(f"[hca-init]   已存在 {rel}/，不动（用户资产）")
            continue
        if src.is_dir():
            n = copy_tree(src, dst)
            print(f"[hca-init]   初始化 {rel}/  {n} 个文件")
        else:
            dst.mkdir(parents=True, exist_ok=True)
            print(f"[hca-init]   初始化 {rel}/（空）")


def render_config() -> None:
    if not env_bool("HCA_CONFIG_REFRESH", True):
        print("[hca-init] HCA_CONFIG_REFRESH=0，不动 config.toml")
        return

    provider = os.environ.get("HCA_LLM_PROVIDER_NAME") or "hunter"
    base_url = (os.environ.get("HCA_LLM_BASE_URL") or "").strip()
    model = os.environ.get("HCA_LLM_MODEL") or "hunter-chat"
    ctx = os.environ.get("HCA_LLM_CONTEXT_WINDOW") or "1000000"

    key = (os.environ.get("HCA_LLM_API_KEY") or "").strip()
    key_file = (os.environ.get("HCA_LLM_API_KEY_FILE") or "").strip()
    if not key and key_file:
        # 这里以前是 `p.is_file()` 裸调 —— 文件存在但**没有读权限**时
        # `os.stat` 直接抛 PermissionError，容器就死在启动脚本里（只留一段 traceback）。
        # 容器里跑的是 uid 10001(hca)，而宿主上的 key 文件默认是 0600 owner=运维自己，
        # 两边对不上就是这个下场（M4 从零安装实测撞到）。改成给一句能照着做的话。
        p = Path(key_file)
        try:
            key = p.read_text(encoding="utf-8").strip()
            print(f"[hca-init] key 从 {key_file} 读入")
        except PermissionError:
            import os as _os
            st = None
            try:
                st = _os.stat(_os.path.dirname(key_file))
            except OSError:
                pass
            print(f"[hca-init] ✗ {key_file} 存在但**读不到**（容器里是 uid "
                  f"{_os.getuid()}:{_os.getgid()}"
                  + (f"，宿主目录是 {oct(st.st_mode & 0o777)} owner={st.st_uid}:{st.st_gid}" if st else "")
                  + "）。", file=sys.stderr)
            print("[hca-init]   修法一：在宿主上跑 `bash deploy/up.sh`，它会把密钥目录"
                  "改成 group=10001 / 0750、key 文件 0640（只改组，不改属主）。", file=sys.stderr)
            print("[hca-init]   修法二：改用环境变量 HCA_LLM_API_KEY=…（不落盘，但 "
                  "docker inspect 看得到）。", file=sys.stderr)
        except FileNotFoundError:
            print(f"[hca-init] ⚠ HCA_LLM_API_KEY_FILE={key_file} 不存在")
        except OSError as e:
            print(f"[hca-init] ⚠ 读 {key_file} 失败：{type(e).__name__}: {e}")

    # 人设替换（**只有 fork 二进制认这一项**，见 docs/fork-patches.md）。
    # 官方 5.1.0 会把 `system_prompt_file` 当成未知键读进来又原样忽略 ——
    # 不报错，但也不生效。所以这里只在显式给了环境变量时才写这一行，
    # 免得官方二进制的 config.toml 里躺着一行看起来生效、实际没生效的配置。
    persona_file = (os.environ.get("HCA_LLM_SYSTEM_PROMPT_FILE") or "").strip()
    if persona_file and not Path(persona_file).is_file():
        print(f"[hca-init] ⚠ HCA_LLM_SYSTEM_PROMPT_FILE={persona_file} 不存在，不写这一行")
        persona_file = ""

    if not base_url:
        print("[hca-init] ⚠ 没有 HCA_LLM_BASE_URL，daemon 起得来但没有可用模型")
    # 只说长度和前缀，绝不打印 key 本身
    shown = f"{key[:11]}****（{len(key)} 字符）" if key else "（空）"
    print(f"[hca-init] provider={provider} model={model} base_url={base_url or '（空）'} key={shown}")
    print(f"[hca-init] system_prompt_file={persona_file or '（未设，用内置人设）'}")

    ATOMCODE_HOME.mkdir(parents=True, exist_ok=True)
    cfg = ATOMCODE_HOME / "config.toml"
    # TOML 基本字符串的转义规则与 JSON 一致，用 json.dumps 生成，避免手搓引号出错
    q = json.dumps
    body = "\n".join([
        "# 由 deploy/daemon/hca-init.py 在容器启动时生成，改这里没用 —— 改环境变量。",
        "# 想手工维护就设 HCA_CONFIG_REFRESH=0。",
        f"default_provider = {q(provider)}",
        "auto_update = false",
        "auto_commit = false",
        'offline_mode = "off"',
        "",
        f"[providers.{provider}]",
        'type = "openai"',
        f"api_key = {q(key)}",
        f"model = {q(model)}",
        f"base_url = {q(base_url)}",
        f"context_window = {int(ctx)}",
    ] + ([f"system_prompt_file = {q(persona_file)}"] if persona_file else []) + [
        "",
    ])
    cfg.write_text(body, encoding="utf-8")
    cfg.chmod(0o600)
    print(f"[hca-init] 已写 {cfg}（0600）")


def main() -> int:
    if not TEMPLATE_DIR.is_dir():
        print(f"[hca-init] ✗ 模板目录不存在：{TEMPLATE_DIR}", file=sys.stderr)
        return 2
    seed_workspace()
    render_config()
    print("[hca-init] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
