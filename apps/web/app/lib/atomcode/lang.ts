/**
 * 出口语言守卫（待办池 P1-21）。
 *
 * 原 opencode 的 `hunter-lang` 插件挂在 `experimental.text.complete` —— **出口**：
 * 整段回答写完之后送 api 的 `/api/internal/lang/guard` 净化/翻译，再给用户。
 * 它引的铁律 A10 明写「prompt 里的中文约束单独用无效，必须 prompt + 出口强校验
 * 两道一起上」。
 *
 * AtomCode 的 8 个 hook 事件里**没有「助手正文写完」这个点**（`Stop` 拿不到正文），
 * 所以 M4 的 `hca-lang` 只做了提示词侧那一道。这里补上出口那道：
 * BFF 本来就坐在 SSE 流上，回合结束时手里有每个文本 part 的终态。
 *
 * 判据与翻译**不在这里实现** —— 它们在 api 的 `agents/text_sanitizer.py` +
 * `agents/translation.py` 里，有反误伤用例。这边只做搬运，跟原插件一样。
 * 同一件事两处实现 = 改一处漏一处。
 *
 * 几条刻意的取舍：
 *  · **失败一律放行原文**。守卫是保险，不是闸门；api 挂了不该让用户收不到回答。
 *  · `changed=false` 是绝大多数情况，api 侧是纯正则判据、不调 LLM，开销可以忽略。
 *  · 结果按原文缓存。刷新页面时历史是从 daemon 原样读回来的（我们不改写 daemon
 *    的会话存档），靠这个缓存把同一段文字的修正复用上；web 进程重启后缓存没了，
 *    历史会显示未修正的原文 —— 这是已知限制，写在 docs/web-adapter-design.md。
 */

const API = (process.env.HERMES_API_URL || 'http://api:8000').replace(/\/+$/, '')
const INTERNAL_KEY = process.env.HUNTER_INTERNAL_KEY || 'hunter-internal-local'
const ENABLED = (process.env.HCA_LANG_EXIT_GUARD ?? '1').trim() !== '0'
const TIMEOUT_MS = Number(process.env.HCA_LANG_EXIT_TIMEOUT_MS || 20_000)
/** 短文本不送检：判据要「连续英文词 run + 功能词命中」，几十个字符不可能构成散文。 */
const MIN_LEN = Number(process.env.HCA_LANG_EXIT_MIN_LEN || 40)
const CACHE_MAX = 500

/** 原文 → 修正后。只存**改过的**，没改过的不占位置。 */
const cache = new Map<string, string>()

function remember(raw: string, fixed: string): void {
  if (cache.size >= CACHE_MAX) {
    const first = cache.keys().next()
    if (!first.done) cache.delete(first.value)
  }
  cache.set(raw, fixed)
}

/** 历史投影用：只查缓存，不发请求（同步，不把 async 传染给 projectHistory）。 */
export function cachedFix(raw: string): string | null {
  return cache.get(raw) ?? null
}

export function langGuardEnabled(): boolean {
  return ENABLED
}

/** 送检一段面向用户的正文。**任何异常都返回原文**。 */
export async function guardText(raw: string): Promise<{ changed: boolean; text: string }> {
  const text = typeof raw === 'string' ? raw : ''
  if (!ENABLED || text.trim().length < MIN_LEN) return { changed: false, text }

  const hit = cache.get(text)
  if (hit !== undefined) return { changed: hit !== text, text: hit }

  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS)
  try {
    const r = await fetch(`${API}/api/internal/lang/guard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Hunter-Internal-Key': INTERNAL_KEY },
      body: JSON.stringify({ text }),
      signal: ctl.signal,
    })
    if (!r.ok) return { changed: false, text }
    const d: any = await r.json()
    if (!d?.changed) return { changed: false, text }
    const fixed = String(d.text ?? '')
    // api 明确约定：`text` 为空串 = 净化后没有可用中文且翻译也失败，
    // 调用方**不要**把原文透出去。但这里是"已经流给用户看过的正文"，
    // 抹成空白比留着更糟（用户会以为回答丢了），所以留原文并让日志记下来。
    if (!fixed.trim()) {
      console.warn('[hca-lang] 守卫判定命中英文散文但翻译失败，保留原文')
      return { changed: false, text }
    }
    remember(text, fixed)
    return { changed: fixed !== text, text: fixed }
  } catch (e) {
    console.warn('[hca-lang] 出口守卫调用失败，放行原文：', (e as Error)?.message)
    return { changed: false, text }
  } finally {
    clearTimeout(timer)
  }
}
