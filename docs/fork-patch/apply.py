#!/usr/bin/env python3
"""把 M2 的两项最小补丁打进 AtomCode 的工作树（计划 v0.2 §5.2）。

    python3 apply.py <atomcode 工作树根>

两项补丁，合起来就是一件事：**让部署方能把编码人设整体换成自己的**。

1. **让 model 配置里已有的 `system_prompt` 真正生效。**
   这个字段在三个结构体里都在（`ProviderConfig` / `ModelProfileConfig` /
   `ResolvedModelConfig`），`Config::resolve_model` 一路解析下来，
   **但全仓没有任何地方读它的值去拼 prompt** —— 用户填了不报错也不生效。
   这更像遗留未接线而不是设计。

2. **新增 `system_prompt_file`**：人设动辄几千字，塞进 TOML 字符串不现实。
   两者都配时 inline 的 `system_prompt` 赢；相对路径相对配置目录解析。

未配置时（两个字段都为空）行为**逐字节不变** —— 走的还是原来的
`coding_persona_with_capabilities(...)`。

这个脚本是幂等的：已经打过就跳过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()


def edit(rel: str, fn):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    out = fn(src)
    if out is None:
        print(f"  跳过 {rel}（已是目标状态）")
        return
    p.write_text(out, encoding="utf-8")
    print(f"  改 {rel}")


def insert_after(src: str, anchor: str, addition: str, count: int = 1):
    if addition.strip().splitlines()[-1].strip() in src:
        return None
    assert anchor in src, f"锚点找不到：{anchor[:60]!r}"
    return src.replace(anchor, anchor + addition, count)


# ── 1. atomcode-config：三个结构体 + 解析器 ────────────────────────────────

def patch_provider(src: str):
    if "system_prompt_file" in src:
        return None

    # ProviderConfig（legacy，serde 序列化）
    src = src.replace(
        "    pub base_url: Option<String>,\n    pub system_prompt: Option<String>,\n",
        "    pub base_url: Option<String>,\n"
        "    pub system_prompt: Option<String>,\n"
        "    /// Path to a file whose entire contents replace the built-in coding\n"
        "    /// system prompt. Inline `system_prompt` wins when both are set; a\n"
        "    /// relative path resolves against the config directory. Unset (the\n"
        "    /// default) keeps the built-in persona, byte for byte.\n"
        "    #[serde(default, skip_serializing_if = \"Option::is_none\")]\n"
        "    pub system_prompt_file: Option<String>,\n",
        1,
    )

    # ModelProfileConfig（新 schema，serde 序列化）
    src = src.replace(
        "    /// Optional per-model system-prompt override (carried through resolution,\n"
        "    /// design §14.5). Projected from a legacy provider's `system_prompt`.\n"
        "    #[serde(default, skip_serializing_if = \"Option::is_none\")]\n"
        "    pub system_prompt: Option<String>,\n",
        "    /// Optional per-model system-prompt override (carried through resolution,\n"
        "    /// design §14.5). Projected from a legacy provider's `system_prompt`.\n"
        "    #[serde(default, skip_serializing_if = \"Option::is_none\")]\n"
        "    pub system_prompt: Option<String>,\n"
        "    /// File form of `system_prompt` — the whole file replaces the built-in\n"
        "    /// coding persona. Inline wins when both are set.\n"
        "    #[serde(default, skip_serializing_if = \"Option::is_none\")]\n"
        "    pub system_prompt_file: Option<String>,\n",
        1,
    )

    # ResolvedModelConfig（不 serde，只 Clone）
    src = src.replace(
        "    pub max_tokens: Option<usize>,\n"
        "    pub system_prompt: Option<String>,\n"
        "    pub user_agent: Option<String>,\n",
        "    pub max_tokens: Option<usize>,\n"
        "    pub system_prompt: Option<String>,\n"
        "    pub system_prompt_file: Option<String>,\n"
        "    pub user_agent: Option<String>,\n",
        1,
    )

    # ResolvedModelConfig 上的同名方法（CodingRuntimeConfig::from_config 拿到的是它）
    src = src.replace(
        "    /// Reconstruct a legacy-shaped [`ProviderConfig`] from this resolution.",
        "    /// See [`resolve_system_prompt_override`].\n"
        "    pub fn resolved_system_prompt(\n"
        "        &self,\n"
        "        config_dir: &std::path::Path,\n"
        "    ) -> (Option<String>, Option<String>) {\n"
        "        resolve_system_prompt_override(\n"
        "            self.system_prompt.as_deref(),\n"
        "            self.system_prompt_file.as_deref(),\n"
        "            config_dir,\n"
        "        )\n"
        "    }\n"
        "\n"
        "    /// Reconstruct a legacy-shaped [`ProviderConfig`] from this resolution.",
        1,
    )

    # to_provider_config()
    src = src.replace(
        "            system_prompt: self.system_prompt.clone(),\n",
        "            system_prompt: self.system_prompt.clone(),\n"
        "            system_prompt_file: self.system_prompt_file.clone(),\n",
        1,
    )

    # 所有 `system_prompt: None,`（测试与构造点）补上新字段
    src = re.sub(
        r"^([ \t]*)system_prompt: None,\n",
        lambda m: f"{m.group(1)}system_prompt: None,\n{m.group(1)}system_prompt_file: None,\n",
        src, flags=re.M,
    )

    # 单测：解析器的四条分支
    src = src.replace(
        "mod tests {",
        """mod tests {
    use super::resolve_system_prompt_override as resolve;

    #[test]
    fn inline_system_prompt_wins_over_file() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("p.md"), "FROM FILE").unwrap();
        let (got, warn) = resolve(Some("INLINE"), Some("p.md"), d.path());
        assert_eq!(got.as_deref(), Some("INLINE"));
        assert!(warn.is_none());
    }

    #[test]
    fn file_is_read_relative_to_the_config_dir() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("p.md"), "  FROM FILE\n").unwrap();
        let (got, warn) = resolve(None, Some("p.md"), d.path());
        assert_eq!(got.as_deref(), Some("FROM FILE"), "trimmed file body");
        assert!(warn.is_none());
    }

    #[test]
    fn absolute_file_path_is_used_as_is() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("abs.md");
        std::fs::write(&p, "ABS").unwrap();
        let (got, _) = resolve(None, Some(p.to_str().unwrap()), std::path::Path::new("/nope"));
        assert_eq!(got.as_deref(), Some("ABS"));
    }

    #[test]
    fn nothing_configured_means_no_override() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(resolve(None, None, d.path()), (None, None));
        // Empty / whitespace-only values are "not configured", not "empty prompt".
        assert_eq!(resolve(Some("   "), Some(""), d.path()), (None, None));
    }

    #[test]
    fn unreadable_or_empty_file_warns_and_falls_back() {
        let d = tempfile::tempdir().unwrap();
        let (got, warn) = resolve(None, Some("missing.md"), d.path());
        assert!(got.is_none());
        assert!(warn.unwrap().contains("unreadable"), "a typo must not look like a no-op");

        std::fs::write(d.path().join("blank.md"), "   \n").unwrap();
        let (got, warn) = resolve(None, Some("blank.md"), d.path());
        assert!(got.is_none(), "never boot the agent with an empty system prompt");
        assert!(warn.unwrap().contains("empty"));
    }
""",
        1,
    )

    # 解析器：inline > 文件 > None
    anchor = "impl ResolvedModelConfig {"
    helper = '''
/// Resolve a configured system-prompt OVERRIDE: inline text wins, else the file
/// is read from disk (a relative path resolves against `config_dir`).
///
/// Returns `(override, warning)`. This crate has no logger — it hands diagnostics
/// back to the caller (the same shape `Config::load_with_diagnostics` uses), so
/// `atomcode-coding` / `atomcode-daemon` log them with their own `tracing`.
///
/// An empty value or an unreadable file yields `None` **plus a warning**: falling
/// back to the built-in persona is far better than booting an agent with an empty
/// system prompt, but a silent fallback would make a typo look like the feature
/// simply does not work.
pub fn resolve_system_prompt_override(
    inline: Option<&str>,
    file: Option<&str>,
    config_dir: &std::path::Path,
) -> (Option<String>, Option<String>) {
    if let Some(text) = inline.map(str::trim).filter(|s| !s.is_empty()) {
        return (Some(text.to_string()), None);
    }
    let Some(raw) = file.map(str::trim).filter(|s| !s.is_empty()) else {
        return (None, None);
    };
    let path = std::path::Path::new(raw);
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        config_dir.join(path)
    };
    match std::fs::read_to_string(&path) {
        Ok(body) if !body.trim().is_empty() => (Some(body.trim().to_string()), None),
        Ok(_) => (
            None,
            Some(format!(
                "system_prompt_file {} is empty; using the built-in persona",
                path.display()
            )),
        ),
        Err(e) => (
            None,
            Some(format!(
                "system_prompt_file {} is unreadable ({e}); using the built-in persona",
                path.display()
            )),
        ),
    }
}

impl ProviderConfig {
    /// See [`resolve_system_prompt_override`]. `None` when neither field is set,
    /// which is what keeps the default path byte-for-byte unchanged.
    pub fn resolved_system_prompt(
        &self,
        config_dir: &std::path::Path,
    ) -> (Option<String>, Option<String>) {
        resolve_system_prompt_override(
            self.system_prompt.as_deref(),
            self.system_prompt_file.as_deref(),
            config_dir,
        )
    }
}

'''
    assert anchor in src
    src = src.replace(anchor, helper + anchor, 1)
    return src


def patch_config_mod(src: str):
    if "system_prompt_file" in src:
        return None
    src = src.replace(
        "            system_prompt: model.system_prompt.clone(),\n",
        "            system_prompt: model.system_prompt.clone(),\n"
        "            system_prompt_file: model.system_prompt_file.clone(),\n",
        1,
    )
    src = src.replace(
        "        system_prompt: p.system_prompt.clone(),\n",
        "        system_prompt: p.system_prompt.clone(),\n"
        "        system_prompt_file: p.system_prompt_file.clone(),\n",
        1,
    )
    src = re.sub(
        r"^([ \t]*)system_prompt: None,\n",
        lambda m: f"{m.group(1)}system_prompt: None,\n{m.group(1)}system_prompt_file: None,\n",
        src, flags=re.M,
    )
    return src


def patch_none_only(src: str):
    """只需要补 `system_prompt: None,` 的文件。"""
    if "system_prompt_file" in src:
        return None
    out = re.sub(
        r"^([ \t]*)system_prompt: None,\n",
        lambda m: f"{m.group(1)}system_prompt: None,\n{m.group(1)}system_prompt_file: None,\n",
        src, flags=re.M,
    )
    return out if out != src else None


# ── 2. atomcode-coding：persona 覆盖 ──────────────────────────────────────

def patch_persona(src: str):
    if "fn resolve_persona" in src:
        return None
    anchor = "pub fn coding_persona(model: &str, todo_enabled: bool, request_user_input_enabled: bool) -> String {"
    helper = '''/// The system prompt for this run.
///
/// `override_text` is the deployment's configured replacement (model config
/// `system_prompt` / `system_prompt_file`). When it is present and non-empty it
/// replaces the built-in coding persona WHOLESALE — that is the point: a
/// vertical-domain distribution (finance research, legal review, …) needs the
/// coding workflow rules GONE, not appended to. Project instruction files
/// (`AGENTS.md`, `.atomcode.md`) can only ever append, so they cannot do this.
///
/// When it is absent the built-in persona is returned unchanged, so every
/// existing deployment keeps its current prompt byte for byte.
pub fn resolve_persona(override_text: Option<&str>, built_in: impl FnOnce() -> String) -> String {
    match override_text.map(str::trim).filter(|s| !s.is_empty()) {
        Some(text) => text.to_string(),
        None => built_in(),
    }
}

'''
    src = src.replace(anchor, helper + anchor, 1)
    # 单测：覆盖生效 / 未配置时逐字节不变
    src = src.replace(
        "mod tests {\n    use super::*;",
        """mod tests {
    use super::*;

    #[test]
    fn persona_override_replaces_the_built_in_persona() {
        let got = resolve_persona(Some("  DOMAIN PERSONA  "), || unreachable!());
        assert_eq!(got, "DOMAIN PERSONA", "trimmed, and the built-in is never built");
    }

    #[test]
    fn no_override_keeps_the_built_in_persona_byte_for_byte() {
        let built_in = coding_persona("deepseek-v4-flash", false, false);
        for empty in [None, Some(""), Some("   \\n ")] {
            assert_eq!(
                resolve_persona(empty, || coding_persona("deepseek-v4-flash", false, false)),
                built_in,
                "an unset / blank override must not change existing behavior"
            );
        }
    }
""",
        1,
    )
    return src


def patch_coding_config(src: str):
    if "persona_override" in src:
        return None
    # CodingAgentConfig 字段
    src = src.replace(
        "pub struct CodingAgentConfig {\n    pub api_key: String,\n",
        "pub struct CodingAgentConfig {\n"
        "    /// Deployment-configured system prompt that REPLACES the built-in coding\n"
        "    /// persona (model config `system_prompt` / `system_prompt_file`). `None`\n"
        "    /// keeps the built-in persona — the default for every existing install.\n"
        "    pub persona_override: Option<String>,\n"
        "    pub api_key: String,\n",
        1,
    )
    # CodingRuntimeConfig 字段
    src = src.replace(
        "pub struct CodingRuntimeConfig {\n",
        "pub struct CodingRuntimeConfig {\n"
        "    /// See [`CodingAgentConfig::persona_override`].\n"
        "    pub persona_override: Option<String>,\n",
        1,
    )
    # CodingAgentConfig::new 的默认值
    src = src.replace(
        "        let model = model.into();\n        Self {\n"
        "            api_key: api_key.into(),\n",
        "        let model = model.into();\n        Self {\n"
        "            // No override by default — the built-in coding persona.\n"
        "            persona_override: None,\n"
        "            api_key: api_key.into(),\n",
        1,
    )

    # from_config 的字面量（CLI / TUI 都走这条）
    src = src.replace(
        "        let r = resolved.as_ref();\n        Self {\n",
        "        let r = resolved.as_ref();\n"
        "        // Deployment-configured persona replacement. `None` for every install\n"
        "        // that has not set `system_prompt` / `system_prompt_file`.\n"
        "        let (persona_override, persona_warning) = r\n"
        "            .map(|r| r.resolved_system_prompt(&atomcode_config::config::Config::config_dir()))\n"
        "            .unwrap_or((None, None));\n"
        "        if let Some(w) = persona_warning {\n"
        "            tracing::warn!(\"{w}\");\n"
        "        }\n"
        "        Self {\n"
        "            persona_override,\n",
        1,
    )

    # agent_config() 透传
    src = src.replace(
        "        config.context_window = self.context_window;\n",
        "        config.persona_override = self.persona_override.clone();\n"
        "        config.context_window = self.context_window;\n",
        1,
    )
    # apply_provider_config：/model 切换时跟着换
    src = src.replace(
        "    config.model = provider.model.clone();\n"
        "    config.supports_vision = provider.accepts_images();\n",
        "    config.model = provider.model.clone();\n"
        "    // A `/model` swap must also pick up the new selection's persona override,\n"
        "    // otherwise switching models silently restores the built-in coding persona.\n"
        "    let (persona_override, persona_warning) =\n"
        "        provider.resolved_system_prompt(&atomcode_config::config::Config::config_dir());\n"
        "    if let Some(w) = persona_warning {\n"
        "        tracing::warn!(\"{w}\");\n"
        "    }\n"
        "    config.persona_override = persona_override;\n"
        "    config.supports_vision = provider.accepts_images();\n",
        1,
    )
    return src


def patch_assemble(src: str):
    if "persona_override" in src:
        return None
    old = """    let mut persona = coding_persona_with_language(
        &cfg.model,
        cfg.preferred_language,
        todo_enabled,
        crate::persona::request_user_input_switch_enabled(),
    );"""
    new = """    let mut persona = crate::persona::resolve_persona(cfg.persona_override.as_deref(), || {
        coding_persona_with_language(
            &cfg.model,
            cfg.preferred_language,
            todo_enabled,
            crate::persona::request_user_input_switch_enabled(),
        )
    });"""
    assert old in src
    return src.replace(old, new, 1)


def patch_parts(src: str):
    if "persona_override" in src:
        return None
    old = """        .persona(coding_persona_with_capabilities(
            &cfg.model,
            cfg.preferred_language,
            parts.todo_enabled,
            parts.request_user_input_enabled,
            parts.review_provider.is_some(),
            parts.subagent_provider.is_some(),
            parts.has_external_subagents,
        ))"""
    new = """        .persona(crate::persona::resolve_persona(
            cfg.persona_override.as_deref(),
            || {
                coding_persona_with_capabilities(
                    &cfg.model,
                    cfg.preferred_language,
                    parts.todo_enabled,
                    parts.request_user_input_enabled,
                    parts.review_provider.is_some(),
                    parts.subagent_provider.is_some(),
                    parts.has_external_subagents,
                )
            },
        ))"""
    assert old in src
    src = src.replace(old, new, 1)

    # reconcile：resume / model-swap 路径也要走同一个解析
    old_r = """    let persona = coding_persona_with_capabilities(
        &cfg.model,
        cfg.preferred_language,
        todo_enabled,
        request_user_input_enabled,
        review_enabled,
        subagents_enabled,
        external_subagents_enabled,
    );
    let is_persona = |message: &Message| {
        message.role == Role::System && message.text.starts_with(ATOMCODE_PERSONA_PREFIX)
    };"""
    new_r = """    let overridden = cfg
        .persona_override
        .as_deref()
        .map(str::trim)
        .is_some_and(|s| !s.is_empty());
    let persona = crate::persona::resolve_persona(cfg.persona_override.as_deref(), || {
        coding_persona_with_capabilities(
            &cfg.model,
            cfg.preferred_language,
            todo_enabled,
            request_user_input_enabled,
            review_enabled,
            subagents_enabled,
            external_subagents_enabled,
        )
    });
    // With an override active the stored persona does NOT start with the built-in
    // identity line, so the prefix test alone would leave the previous override in
    // place and insert a second system message ahead of it. The persona is always
    // the LEADING system message (both assemble sites insert it at index 0), so
    // treat that one as the persona too — this keeps a changed override file from
    // accumulating stale copies across resumes.
    let leading_system_id = if overridden {
        snapshot
            .messages
            .first()
            .filter(|m| m.role == Role::System)
            .map(|m| m.text.clone())
    } else {
        None
    };
    let is_persona = |message: &Message| {
        message.role == Role::System
            && (message.text.starts_with(ATOMCODE_PERSONA_PREFIX)
                || leading_system_id.as_deref() == Some(message.text.as_str()))
    };"""
    assert old_r in src
    return src.replace(old_r, new_r, 1)


def patch_live_api(src: str):
    if "persona_override" in src:
        return None
    old = """    atomcode_coding::CodingRuntimeConfig {
        api_key: p.and_then(|p| p.api_key.clone()).unwrap_or_default(),"""
    new = """    atomcode_coding::CodingRuntimeConfig {
        // Deployment-configured persona replacement (model `system_prompt` /
        // `system_prompt_file`). `None` for every install that has not set it.
        persona_override: p
            .map(|p| {
                p.resolved_system_prompt(&atomcode_config::config::Config::config_dir())
            })
            .and_then(|(prompt, warning)| {
                if let Some(w) = warning {
                    tracing::warn!("{w}");
                }
                prompt
            }),
        api_key: p.and_then(|p| p.api_key.clone()).unwrap_or_default(),"""
    assert old in src
    return src.replace(old, new, 1)


def main() -> int:
    print(f"打补丁到 {ROOT}")
    # 先改两个"有真内容"的文件：结构体字段、解析器、投影。
    edit("crates/atomcode-config/src/config/provider.rs", patch_provider)
    edit("crates/atomcode-config/src/config/mod.rs", patch_config_mod)

    # 再**全树扫**一遍 `system_prompt: None,` 补上同级的新字段。
    #
    # 一开始是手列文件清单，结果被编译器逼着追加了四轮：
    # tuix 的 openrouter_connect.rs / provider_panel.rs（`cargo check --workspace` 才报）、
    # config 的 tests/config_store.rs（`cargo check` 不编 tests/，要 `cargo test`）、
    # coding 的 runtime.rs（藏在 `#[cfg(test)]` 里的 helper）。
    # 给一个 pub 结构体加字段就是这么回事 —— 与其猜哪些文件有字面量，不如扫全树。
    touched = 0
    for f in sorted(ROOT.glob("crates/**/*.rs")):
        rel = f.relative_to(ROOT).as_posix()
        if rel in ("crates/atomcode-config/src/config/provider.rs",
                   "crates/atomcode-config/src/config/mod.rs"):
            continue  # 上面已单独处理
        body = f.read_text(encoding="utf-8")
        if "system_prompt: None," not in body or "system_prompt_file" in body:
            continue
        out = patch_none_only(body)
        if out is not None:
            f.write_text(out, encoding="utf-8")
            print(f"  补字段 {rel}")
            touched += 1
    print(f"  —— 全树补了 {touched} 个文件的 `system_prompt: None,`")

    edit("crates/atomcode-coding/src/persona.rs", patch_persona)
    edit("crates/atomcode-coding/src/config.rs", patch_coding_config)
    edit("crates/atomcode-coding/src/assemble.rs", patch_assemble)
    edit("crates/atomcode-coding/src/parts.rs", patch_parts)
    edit("crates/atomcode-daemon/src/live_api.rs", patch_live_api)
    print("完成。接下来：cargo fmt --all && cargo test -p atomcode-config -p atomcode-coding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
