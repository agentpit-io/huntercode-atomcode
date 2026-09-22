# 社区版（opencode 1.2.0）实际注册的 MCP —— I2 实测更正

取证时间：2026-09-22 22:19:55 上海 · 容器 hca-baseline-opencode-1

## 1. opencode 自己合并后的配置（GET /config，这是唯一的权威口径）
```
mcp: ["hunter_cap", "hunter_user", "portfolio", "screener", "uzi", "watchlist"]
plugin: ["hunter-lang.ts", "hunter-audit.ts", "hunter-mcp-context.ts", "hunter-auth.ts", "hunter-guard.ts", "hunter-budget.ts"]
```

## 2. 两个配置文件各自注册了哪几个
```
# /opt/opencode-workspace/.opencode/opencode.jsonc（M2 看的就是这一份）
"provider": {
"permission": {
"mcp": {
"watchlist": {
"portfolio": {
"uzi": {
"hunter_user": {

# /opt/opencode-workspace/opencode.json（M2 漏掉的那一份）
"provider": {
"options": {
"models": {
"mcp": {
"hunter_cap": {
"screener": {
```

## 3. 旁证：M2 正式批次里基线侧真的调到了 screener
```
docs/eval/raw/q3-factor-screen-opencode-r1.json → calls[0].tool = screener_market_screen
```
