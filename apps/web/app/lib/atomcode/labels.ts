/**
 * 模型 / 通道的**显示名**（本发行版品牌层，2026-09-22）。
 *
 * 为什么要这一层：网页右下角的模型选择器显示的是 **请求用的模型 ID**，
 * 在 OneAPI 通道下就是 `hunter-chat`、分组标题是 provider id `oneapi`。
 * 用户看不懂 —— 他想知道背后到底是哪个模型。
 *
 * 实测（2026-09-22 19:51，香港环境调 `https://hunter.agentpit.io/api/saas/llm/v1/models`）：
 *   hunter-chat → display_name "Gemini 3.8 Flash"（模型自报 `gemini-3.8-flash`）
 *   hunter-deep → display_name "Gemini 3.1 Pro"
 * 所以 OneAPI 通道的默认显示名按实测写，**由 install.sh 落到 deploy/.env**，
 * 不在代码里写死：自建 OneAPI 的 `hunter-chat` 背后可能是别的模型，
 * 代码里猜一个名字就是 mock 冒充（总控规则红线 1）。
 *
 * 取不到配置时一律**回落到原始 ID**，宁可难看也不说谎。
 *
 * 两个环境变量都支持两种写法：
 *   · 整串当一个名字        `Gemini 3.8 Flash`
 *   · 按 id 分别指定        `hunter-chat=Gemini 3.8 Flash,hunter-deep=Gemini 3.1 Pro`
 * 第一种写法只有在**只有一个候选 id**时才套用（见 `labelFor`），
 * 免得多模型时把两个模型显示成同一个名字。
 */

/** 解析 `a=A,b=B` 形式；没有 `=` 就是整串默认名（键 `*`）。 */
export function parseLabelSpec(spec: string | undefined | null): Map<string, string> {
  const out = new Map<string, string>()
  const s = (spec || '').trim()
  if (!s) return out
  if (!s.includes('=')) {
    out.set('*', s)
    return out
  }
  for (const seg of s.split(',')) {
    const i = seg.indexOf('=')
    if (i <= 0) continue
    const k = seg.slice(0, i).trim()
    const v = seg.slice(i + 1).trim()
    if (k && v) out.set(k, v)
  }
  return out
}

/**
 * 取 `id` 的显示名。
 * @param ids 同一层级里的全部 id（用来决定「整串默认名」能不能套用）
 */
export function labelFor(spec: string | undefined | null, id: string, ids: string[]): string {
  const map = parseLabelSpec(spec)
  const exact = map.get(id)
  if (exact) return exact
  const star = map.get('*')
  // 整串写法是给「一个通道一个模型」的常见情形用的；有多个候选时不套，
  // 否则两个模型会被显示成同一个名字，比显示 ID 更糟。
  if (star && ids.length === 1) return star
  return id
}
