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
        assert_eq!(
            got, "DOMAIN PERSONA",
            "trimmed, and the built-in is never built"
        );
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
    }""",
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


# ── 3. 工具挂载过滤：[tools] allow / deny ────────────────────────────────
#
# 为什么是「挂载」而不是「权限」：`[permissions]` 管的是"调用要不要弹窗"，
# 工具照样出现在发给模型的 `tools` 数组里 —— schema 的 token 照付、模型照样
# 会去试。垂直领域发行版要的是让它**根本不出现**。内核 `ToolRegistry::mount`
# 的注释已经写明「Unmounted tools never produce a ToolDef」，所以这一层落在
# 挂载名单上，注册侧一个字不动。

TOOLFILTER_RS = '''//! Deployment-configured tool **mount** filter (`[tools] allow` / `deny`).
//!
//! `[permissions]` decides whether a call prompts; this decides whether the tool is
//! offered to the model at all. An unmounted tool produces no `ToolDef`, so its
//! schema never reaches the request and the model cannot ask for it — which is what
//! a vertical-domain distribution needs when the built-in coding toolset is simply
//! not part of its product.
//!
//! Both lists are empty by default, and every entry point short-circuits on that,
//! so an install that has not configured `[tools]` mounts exactly what it mounts today.

/// Pattern-based allow/deny over mounted tool names.
///
/// A pattern is one of:
/// * an exact tool name — `read_file`
/// * a trailing-`*` prefix — `mcp__screener__*`
/// * a named family — `group:coding`, `group:codeintel`, `group:atomgit`,
///   `group:skills`, `group:subagent`, `group:mcp`
///
/// `deny` is evaluated after `allow`, so deny wins. A non-empty `allow` is a
/// whitelist: anything it does not match is dropped.
#[derive(Debug, Clone, Default)]
pub struct ToolFilter {
    allow: Vec<String>,
    deny: Vec<String>,
}

impl ToolFilter {
    /// Build from the `[tools]` table. Blank entries are dropped so a stray `""`
    /// in TOML cannot turn into a whitelist that matches nothing.
    pub fn new(allow: &[String], deny: &[String]) -> Self {
        let clean = |v: &[String]| -> Vec<String> {
            v.iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect()
        };
        Self {
            allow: clean(allow),
            deny: clean(deny),
        }
    }

    /// Nothing configured. Callers check this first so the default path keeps its
    /// current tool list byte for byte (and pays no per-name matching).
    pub fn is_noop(&self) -> bool {
        self.allow.is_empty() && self.deny.is_empty()
    }

    /// Should `tool` be mounted?
    pub fn keeps(&self, tool: &str) -> bool {
        if self.is_noop() {
            return true;
        }
        if self.deny.iter().any(|p| matches_pattern(p, tool)) {
            return false;
        }
        self.allow.is_empty() || self.allow.iter().any(|p| matches_pattern(p, tool))
    }

    /// Filter a mount list in place.
    pub fn retain(&self, names: &mut Vec<String>) {
        if self.is_noop() {
            return;
        }
        names.retain(|n| self.keeps(n));
    }

    /// Owned form of [`retain`](Self::retain), for struct-literal positions.
    pub fn retained(&self, mut names: Vec<String>) -> Vec<String> {
        self.retain(&mut names);
        names
    }
}

fn matches_pattern(pattern: &str, tool: &str) -> bool {
    if let Some(group) = pattern.strip_prefix("group:") {
        return in_group(group, tool);
    }
    match pattern.strip_suffix('*') {
        Some(prefix) => tool.starts_with(prefix),
        None => pattern == tool,
    }
}

/// Named families, so a deployment can switch off a whole capability without
/// having to track every tool a later upstream release adds to it.
///
/// `atomgit` matches by the `atomgit_` name prefix rather than calling
/// `atomgit_tool_names()`: that module is behind an optional Cargo feature, and a
/// mount filter must not change meaning depending on how the binary was built.
fn in_group(group: &str, tool: &str) -> bool {
    match group {
        "coding" => atomcode_capabilities::tools::coding_tool_names().contains(&tool),
        "codeintel" => {
            atomcode_capabilities::codeintel::codeintel_tool_names().contains(&tool)
                || tool == "lsp"
        }
        "atomgit" => tool.starts_with("atomgit_"),
        "skills" => atomcode_capabilities::skills::skill_tool_names().contains(&tool),
        "subagent" => tool == "task",
        "mcp" => tool.starts_with("mcp__"),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn unconfigured_keeps_every_tool() {
        let f = ToolFilter::default();
        assert!(f.is_noop());
        for name in ["read_file", "bash", "mcp__x__y", "list_symbols"] {
            assert!(
                f.keeps(name),
                "{name} must still mount when nothing is configured"
            );
        }
        // Blank-only lists are "not configured", not "a whitelist matching nothing".
        let blank = ToolFilter::new(&v(&["", "  "]), &v(&[]));
        assert!(blank.is_noop());
        assert!(blank.keeps("bash"));
    }

    #[test]
    fn allow_is_a_whitelist() {
        let f = ToolFilter::new(&v(&["read_file", "mcp__screener__*"]), &v(&[]));
        assert!(f.keeps("read_file"));
        assert!(f.keeps("mcp__screener__market_screen"));
        assert!(!f.keeps("bash"));
        assert!(!f.keeps("mcp__other__thing"));
    }

    #[test]
    fn deny_wins_over_allow() {
        let f = ToolFilter::new(&v(&["group:coding"]), &v(&["bash"]));
        assert!(f.keeps("read_file"));
        assert!(!f.keeps("bash"), "deny is checked after allow");
    }

    #[test]
    fn groups_cover_the_families_they_name() {
        let f = ToolFilter::new(
            &v(&[]),
            &v(&["group:codeintel", "group:atomgit", "group:mcp"]),
        );
        assert!(!f.keeps("list_symbols"));
        assert!(
            !f.keeps("lsp"),
            "lsp belongs to codeintel even though it mounts separately"
        );
        assert!(!f.keeps("atomgit_pr"));
        assert!(!f.keeps("mcp__screener__market_screen"));
        assert!(f.keeps("read_file"), "an unnamed family is untouched");
    }

    #[test]
    fn retain_filters_a_mount_list_in_place() {
        let f = ToolFilter::new(&v(&[]), &v(&["group:coding", "group:codeintel"]));
        let mut names = v(&[
            "read_file",
            "bash",
            "use_skill",
            "list_symbols",
            "mcp__a__b",
        ]);
        f.retain(&mut names);
        assert_eq!(names, v(&["use_skill", "mcp__a__b"]));

        // And the no-op case does not even reorder.
        let untouched = v(&["b", "a"]);
        let mut same = untouched.clone();
        ToolFilter::default().retain(&mut same);
        assert_eq!(same, untouched);
    }
}
'''


def patch_tools_config(src: str):
    """atomcode-config：`[tools]` 加 allow / deny 两个列表。"""
    if "pub allow: Vec<String>," in src and "pub struct ToolsConfig" in src:
        # 已打过（ToolsConfig 里有 allow 就算打过）
        if "/// Mount ONLY the tools matching these patterns" in src:
            return None
    old = """pub struct ToolsConfig {
    pub todo: TodoToolConfig,
}"""
    new = """pub struct ToolsConfig {
    pub todo: TodoToolConfig,
    /// Mount ONLY the tools matching these patterns. Empty (the default) mounts
    /// every tool, exactly as before. A pattern is an exact name (`read_file`), a
    /// trailing-`*` prefix (`mcp__screener__*`), or a family (`group:coding`,
    /// `group:codeintel`, `group:atomgit`, `group:skills`, `group:subagent`,
    /// `group:mcp`). See `atomcode_coding::toolfilter`.
    ///
    /// This is a MOUNT filter, not a permission rule: an unmounted tool never
    /// reaches the model's `tools` array at all, so its schema costs no tokens
    /// and the model cannot ask for it. `[permissions]` still governs whether a
    /// mounted tool's call needs approval.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub allow: Vec<String>,
    /// Never mount tools matching these patterns. Evaluated AFTER `allow`, so
    /// deny wins. Same pattern grammar as `allow`.
    ///
    /// ```toml
    /// [tools]
    /// deny = ["group:codeintel", "group:atomgit"]
    /// ```
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub deny: Vec<String>,
}"""
    assert old in src, "ToolsConfig 锚点找不到"
    src = src.replace(old, new, 1)

    # 同文件里有一个把 ToolsConfig 全字段写死的测试字面量 —— 给结构体加字段就会
    # 把它编译炸掉（E0063）。补 `..Default::default()` 而不是逐个列字段：
    # 这样下一个加字段的人不必再回来改一次。
    old_lit = '            tools: ToolsConfig {\n                todo: TodoToolConfig {\n                    enabled: false,\n                    eager: TodoEagerness::Always,\n                },\n            },'
    new_lit = '            tools: ToolsConfig {\n                todo: TodoToolConfig {\n                    enabled: false,\n                    eager: TodoEagerness::Always,\n                },\n                ..Default::default()\n            },'
    if old_lit in src:
        src = src.replace(old_lit, new_lit, 1)
    return src


def patch_coding_lib(src: str):
    """atomcode-coding：挂上新模块。"""
    if "pub mod toolfilter;" in src:
        return None
    # rustfmt 的 reorder_modules 会把 `pub mod` 排字典序 —— 插在 persona 后面
    # 第一次 `cargo fmt --check` 就炸了。toolfilter 排在 telemetry 与 vision 之间。
    anchor = "pub mod telemetry;"
    assert anchor in src, "lib.rs 里找不到 `pub mod telemetry;`"
    return src.replace(anchor, anchor + "\npub mod toolfilter;", 1)


def patch_coding_config_tools(src: str):
    """CodingAgentConfig / CodingRuntimeConfig 带上过滤器，并在两条构建路径上填值。"""
    if "tool_filter" in src:
        return None

    # CodingAgentConfig 字段（紧挨 todo，二者都来自 `[tools]`）
    src = src.replace(
        "    /// Resolved `[tools.todo]` policy for this runtime generation.\n"
        "    pub todo: atomcode_config::config::TodoToolConfig,\n",
        "    /// Resolved `[tools.todo]` policy for this runtime generation.\n"
        "    pub todo: atomcode_config::config::TodoToolConfig,\n"
        "    /// Resolved `[tools] allow` / `deny` mount filter. Default = no filtering,\n"
        "    /// which mounts exactly the tools every existing install mounts today.\n"
        "    pub tool_filter: crate::toolfilter::ToolFilter,\n",
        1,
    )
    # CodingRuntimeConfig 字段
    src = src.replace(
        "pub struct CodingRuntimeConfig {\n"
        "    /// See [`CodingAgentConfig::persona_override`].\n"
        "    pub persona_override: Option<String>,\n",
        "pub struct CodingRuntimeConfig {\n"
        "    /// See [`CodingAgentConfig::persona_override`].\n"
        "    pub persona_override: Option<String>,\n"
        "    /// See [`CodingAgentConfig::tool_filter`].\n"
        "    pub tool_filter: crate::toolfilter::ToolFilter,\n",
        1,
    )
    # 两处 `todo: config.tools.todo.clone(),` —— from_config 只有一处
    src = src.replace(
        "            todo: config.tools.todo.clone(),\n",
        "            todo: config.tools.todo.clone(),\n"
        "            tool_filter: crate::toolfilter::ToolFilter::new(\n"
        "                &config.tools.allow,\n"
        "                &config.tools.deny,\n"
        "            ),\n",
        1,
    )
    # 其余字面量靠 `todo: Default::default(),` 认 —— CodingAgentConfig::new 与测试里的
    # CodingRuntimeConfig 都长这样。**不要**再按 persona_override 那行插一次：
    # `new()` 里两行都有，会插出一个重复字段（E0062，第一次构建就撞上了）。
    # agent_config() 透传
    src = src.replace(
        "        config.persona_override = self.persona_override.clone();\n",
        "        config.persona_override = self.persona_override.clone();\n"
        "        config.tool_filter = self.tool_filter.clone();\n",
        1,
    )
    # 测试里的 CodingRuntimeConfig 字面量（`todo: Default::default(),`）
    src = src.replace(
        "            todo: Default::default(),\n",
        "            todo: Default::default(),\n"
        "            tool_filter: Default::default(),\n",
    )
    return src


def patch_live_api_tools(src: str):
    if "tool_filter" in src:
        return None
    old = "        todo: config.tools.todo.clone(),\n"
    assert old in src, "live_api.rs 里找不到 `todo: config.tools.todo.clone(),`"
    return src.replace(
        old,
        old + "        tool_filter: atomcode_coding::toolfilter::ToolFilter::new(\n"
        "            &config.tools.allow,\n"
        "            &config.tools.deny,\n"
        "        ),\n",
        1,
    )


def patch_parts_tools(src: str):
    """三处挂载名单都过一遍过滤器：基础工具、MCP 就绪发布、单台 MCP 连上时的发布。"""
    if "tool_filter" in src:
        return None

    # 1) 基础工具名单（CodingParts 构造）
    src = src.replace(
        "        registry,\n        tool_names: names,\n",
        "        registry,\n"
        "        // Deployment mount filter. Unconfigured → `names` unchanged.\n"
        "        tool_names: cfg.tool_filter.retained(names),\n"
        "        tool_filter: cfg.tool_filter.clone(),\n",
        1,
    )
    # CodingParts 字段声明
    src = src.replace(
        "    tool_names: Vec<String>,\n",
        "    tool_names: Vec<String>,\n"
        "    /// `[tools] allow` / `deny`. Applied to the base toolset at construction and\n"
        "    /// to every MCP publication below, so a filtered-out tool can never be republished\n"
        "    /// by a late server connection.\n"
        "    tool_filter: crate::toolfilter::ToolFilter,\n",
        1,
    )
    # 2) 后台发布任务：把过滤器带进去
    src = src.replace(
        "            let base_names = self.tool_names.clone();\n",
        "            let base_names = self.tool_names.clone();\n"
        "            let tool_filter = self.tool_filter.clone();\n",
        1,
    )
    src = src.replace(
        "                            publish_ready_mcp_tools(\n"
        "                                Arc::clone(&mcp_registry),\n"
        "                                tool_registry.clone(),\n"
        "                                base_names.clone(),\n",
        "                            publish_ready_mcp_tools(\n"
        "                                Arc::clone(&mcp_registry),\n"
        "                                tool_registry.clone(),\n"
        "                                base_names.clone(),\n"
        "                                tool_filter.clone(),\n",
        1,
    )
    src = src.replace(
        "                                    publish_connected_mcp_server(\n"
        "                                        Arc::clone(&mcp_registry),\n"
        "                                        name,\n"
        "                                        tool_registry.clone(),\n"
        "                                        base_names.clone(),\n",
        "                                    publish_connected_mcp_server(\n"
        "                                        Arc::clone(&mcp_registry),\n"
        "                                        name,\n"
        "                                        tool_registry.clone(),\n"
        "                                        base_names.clone(),\n"
        "                                        tool_filter.clone(),\n",
        1,
    )
    # 3) 两个发布函数的签名与 discovered 过滤
    src = src.replace(
        "async fn publish_ready_mcp_tools(\n"
        "    mcp_registry: Arc<McpRegistry>,\n"
        "    mut tool_registry: ToolRegistry,\n"
        "    base_names: Vec<String>,\n",
        "async fn publish_ready_mcp_tools(\n"
        "    mcp_registry: Arc<McpRegistry>,\n"
        "    mut tool_registry: ToolRegistry,\n"
        "    base_names: Vec<String>,\n"
        "    tool_filter: crate::toolfilter::ToolFilter,\n",
        1,
    )
    src = src.replace(
        "async fn publish_connected_mcp_server(\n"
        "    mcp_registry: Arc<McpRegistry>,\n"
        "    server: String,\n"
        "    mut tool_registry: ToolRegistry,\n"
        "    base_names: Vec<String>,\n",
        "async fn publish_connected_mcp_server(\n"
        "    mcp_registry: Arc<McpRegistry>,\n"
        "    server: String,\n"
        "    mut tool_registry: ToolRegistry,\n"
        "    base_names: Vec<String>,\n"
        "    tool_filter: crate::toolfilter::ToolFilter,\n",
        1,
    )
    # discovered 过滤 —— 两处措辞不同，分别替换
    old_ready = (
        "    let discovered = mcp::register_mcp_tools(&mut tool_registry, adapters);\n"
        "    match mcp_tool_names.write() {\n"
    )
    new_ready = (
        "    // Filter BEFORE recording: `mcp_tool_names` is what `selected_tool_names`\n"
        "    // re-reads on every later mount, so an unfiltered name recorded here would\n"
        "    // come back on the next publication.\n"
        "    let discovered = tool_filter.retained(mcp::register_mcp_tools(&mut tool_registry, adapters));\n"
        "    match mcp_tool_names.write() {\n"
    )
    assert old_ready in src, "publish_ready_mcp_tools 的 discovered 锚点找不到"
    src = src.replace(old_ready, new_ready, 1)

    old_conn = (
        "    let discovered = mcp::register_mcp_tools(&mut tool_registry, adapters);\n"
        "    let mut selected = base_names;\n"
    )
    new_conn = (
        "    let discovered = tool_filter.retained(mcp::register_mcp_tools(&mut tool_registry, adapters));\n"
        "    let mut selected = base_names;\n"
    )
    assert old_conn in src, "publish_connected_mcp_server 的 discovered 锚点找不到"
    src = src.replace(old_conn, new_conn, 1)

    # 4) 运行时注入的额外工具（/loop 的 schedule_wakeup 走这条）也过一遍 ——
    #    否则 `deny = ["schedule_wakeup"]` 在 TUI 与 daemon 上都是一句空话。
    old_extra = '    pub fn register_extra_tool(&mut self, tool: Arc<dyn atomcode_kernel::tool::Tool>) {\n        let name = tool.name().to_string();\n        if !self.tool_names.iter().any(|n| n == &name) {\n            self.tool_names.push(name);\n        }\n        self.registry.register(tool);\n    }'
    new_extra = '    pub fn register_extra_tool(&mut self, tool: Arc<dyn atomcode_kernel::tool::Tool>) {\n        let name = tool.name().to_string();\n        // The deployment mount filter covers runtime-injected tools too. Registering\n        // without mounting is the documented no-op: the tool stays resolvable for a\n        // caller that already holds a reference, but it is never offered to the model.\n        if self.tool_filter.keeps(&name) && !self.tool_names.iter().any(|n| n == &name) {\n            self.tool_names.push(name);\n        }\n        self.registry.register(tool);\n    }'
    assert old_extra in src, "register_extra_tool 锚点找不到"
    src = src.replace(old_extra, new_extra, 1)
    return src


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

    # ── 第 3 组：工具挂载过滤（[tools] allow / deny）──────────────────────
    # 顺序有依赖：patch_coding_config_tools 的两个锚点（persona_override 字段、
    # agent_config() 里那行透传）是上面 persona 补丁插进去的。
    tf = ROOT / "crates/atomcode-coding/src/toolfilter.rs"
    if tf.exists():
        print("  跳过 crates/atomcode-coding/src/toolfilter.rs（已存在）")
    else:
        tf.write_text(TOOLFILTER_RS, encoding="utf-8")
        print("  新建 crates/atomcode-coding/src/toolfilter.rs")
    edit("crates/atomcode-config/src/config/mod.rs", patch_tools_config)
    edit("crates/atomcode-coding/src/lib.rs", patch_coding_lib)
    edit("crates/atomcode-coding/src/config.rs", patch_coding_config_tools)
    edit("crates/atomcode-coding/src/parts.rs", patch_parts_tools)
    edit("crates/atomcode-daemon/src/live_api.rs", patch_live_api_tools)

    print("完成。接下来：cargo fmt --all && cargo test -p atomcode-config -p atomcode-coding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
