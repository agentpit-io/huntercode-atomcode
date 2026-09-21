/**
 * 发送前处理 SKILL 模板里的 `{占位符}`。
 *
 * ## 为什么需要(2026-09-17 本地实测)
 *
 * 模板「看看 66 位大佬对 {股票} 的投票结果」填入输入框时会选中整个 `{股票}`,
 * 但用户常常双击只选中「股票」两个字、或手动删字,于是发出去的是 `{goog}` ——
 * 花括号原样进了对话。模型多半能猜,但下游按文本抽股票(辩论 / 预测路径)
 * 和标题都会带着括号,而且看上去像没填完的模板。
 *
 * ## 口径(只在「这次输入来自模板」时生效)
 *
 * - 花括号里还是模板原来的占位名(`{股票}`)→ 没填,拦下不发;
 * - 花括号里换成了别的内容(`{goog}` / `{贵州茅台}`)→ 用户填过了,去掉括号;
 * - 花括号里有空白、引号、冒号、嵌套括号或过长 → 不像占位符(可能是 JSON / 代码),不动。
 *
 * 没有模板来源时一律不动 —— 用户自己打的花括号有可能就是想要的。
 * `股票` 是所有内置模板共用的占位名,来源丢了也认它没填。
 */
const SLOT_RE = /\{([^{}\s"'`:：,，]{1,30})\}/g
const ALWAYS_PLACEHOLDER = ['股票']

// 目标 ES2017、没开 downlevelIteration,for...of matchAll 过不了类型检查,用 exec 循环
function slotMatches(text: string): RegExpExecArray[] {
  const re = new RegExp(SLOT_RE.source, 'g')
  const out: RegExpExecArray[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(text || ''))) out.push(m)
  return out
}

export function templateSlots(tpl: string): string[] {
  return slotMatches(tpl).map((m) => m[1])
}

export type FillResult =
  | { ok: true; text: string }
  | { ok: false; unfilled: string; index: number }

export function fillTemplate(text: string, slots: string[]): FillResult {
  const names = new Set(ALWAYS_PLACEHOLDER.concat(slots))
  const hit = slotMatches(text).find((m) => names.has(m[1]))
  if (hit) return { ok: false, unfilled: hit[0], index: hit.index }
  if (!slots.length) return { ok: true, text }
  return { ok: true, text: text.replace(SLOT_RE, (_all, inner: string) => inner) }
}
