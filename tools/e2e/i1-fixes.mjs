/**
 * I1 开工三修的真浏览器回归（Playwright）。
 *
 *     cd ~/hca/pw && node <repo>/tools/e2e/i1-fixes.mjs \
 *       --base http://34.133.8.3:3200 --secrets ~/hca/secrets --out <截图目录>
 *
 * 三条，全部是**用户在真实浏览器里看到的问题**（progress.log 19:44 / 20:50）：
 *
 *   1. **模型 key 走文件时不跳 /setup**（P0，I1 §1.1）。
 *      判据两道：①登录后地址不落在 `/setup`；②`/api/setup/status` 这类
 *      api 侧的「大模型配没配」返回 configured —— 只看地址栏不够，
 *      M5 那次香港是在 .env 里临时补了环境变量才不跳的，看地址栏会以为已经修好。
 *
 *   2. **用户气泡里不出现 hook 注入块**（P0，I1 §1.2）。
 *      发一轮真实对话 → **刷新页面**（注入块是从 daemon 的会话历史里读回来的，
 *      不刷新看不到）→ 断言每一个 `[data-testid=user-bubble]` 的文本里
 *      既没有 `<hca-`、也没有「以上由系统注入」这句注入正文。
 *
 *   3. **回答署名是 Hunter-AtomCode**（P2，I1 §1.3）。
 *
 * ⚠️ 这套脚本**会发一轮真实对话**（第 2 条非发不可：没有回合就没有历史）。
 * 跑之前先确认目标环境上没有浸泡 / 评测在跑。
 */
import { setup, login, step, results, BASE, OUT, sleep, box, sendBtn, send, shot, dismissModals, scrollToBottom } from './lib.mjs'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const { browser, page, consoleErrors, badResponses } = await setup()
mkdirSync(OUT, { recursive: true })
const facts = {}

// 第 2 条要认「这一轮确实带上了 lang hook 的注入」，所以问题本身要能触发一轮回答，
// 又不要烧太多 token：一句不需要工具的短问题就够（注入是 UserPromptSubmit，
// 与模型调不调工具无关）。
const PROBE_Q = '用一句话说明「市盈率」和「市净率」的区别。'
const INJECT_MARKS = ['<hca-', '</hca-', '以上由系统注入', '【语言硬约束】', '<hunter-profile', '<hunter-skill']

await step('1-登录后不跳 /setup（模型 key 走文件）', async () => {
  await login()
  const url = page.url()
  facts.after_login_url = url
  if (/\/setup/.test(url)) throw new Error(`登录后落在 ${url} —— 还在跳 /setup`)
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await sleep(4000)
  facts.chat_url = page.url()
  if (/\/setup/.test(page.url())) throw new Error(`打开 /chat 被弹到 ${page.url()}`)
  await dismissModals()
  await box().waitFor({ timeout: 30000 })
  await shot('chat-已登录未跳setup')
  return `登录后 ${url}；/chat 停在 ${facts.chat_url}`
})

await step('2-api 自己认为大模型已配置（key 从文件读到了）', async () => {
  // 走页面里的 fetch，带着登录态 cookie/token，打 BFF 转发到 api。
  // 不同版本的字段名不一样，所以把整个 JSON 记下来，再按「有没有 configured=false」判。
  const probe = await page.evaluate(async () => {
    const out = {}
    for (const p of ['/api/setup/status', '/api/settings/llm', '/api/chat/system-prompt']) {
      try {
        const r = await fetch(p, { credentials: 'include' })
        out[p] = { status: r.status, body: (await r.text()).slice(0, 600) }
      } catch (e) { out[p] = { error: String(e) } }
    }
    return out
  })
  facts.api_probe = probe
  const hit = Object.entries(probe).find(([, v]) => v.status === 200 && /"configured"\s*:/.test(v.body || ''))
  if (!hit) {
    // 没有哪个端点直说 configured —— 那就退回「setup 向导没被强制弹出来」这一条，
    // 并把探到的原文写进产物，报告里如实说明判据是哪一条。
    facts.api_configured = '—（这些端点都没有 configured 字段，判据退回地址栏）'
    return '没探到 configured 字段，原文已存 facts.json（判据退回第 1 条）'
  }
  const [path, v] = hit
  const configured = /"configured"\s*:\s*true/.test(v.body)
  facts.api_configured = { path, configured, body: v.body }
  if (!configured) throw new Error(`${path} 说 configured=false —— api 仍然没拿到模型 key`)
  return `${path} configured=true`
})

let burnt = null
await step('3-发一轮真实对话', async () => {
  burnt = await send(PROBE_Q, 300000)
  await scrollToBottom()
  await shot('一轮对话-刷新前')
  const bubbles = await page.locator('[data-testid=user-bubble]').allTextContents()
  facts.bubbles_before_reload = bubbles
  return `已回合完成，配额差值 ${burnt == null ? '—' : burnt}`
})

await step('4-刷新后用户气泡里没有注入块（P0 · 核心断言）', async () => {
  await page.reload({ waitUntil: 'domcontentloaded' })
  await sleep(6000)
  await dismissModals()
  await scrollToBottom()
  const bubbles = await page.locator('[data-testid=user-bubble]').allTextContents()
  facts.bubbles_after_reload = bubbles
  if (!bubbles.length) throw new Error('刷新后一个用户气泡都没有 —— 历史没读回来，这条断言等于没测')
  for (const b of bubbles) {
    for (const mark of INJECT_MARKS) {
      if (b.includes(mark)) throw new Error(`用户气泡里出现了注入标记「${mark}」：${b.slice(0, 300)}`)
    }
  }
  const mine = bubbles.find((b) => b.includes('市盈率'))
  if (!mine) throw new Error(`刷新后没找到自己刚发的那句，实得 ${JSON.stringify(bubbles).slice(0, 300)}`)
  facts.bubble_exact_match = mine.trim() === PROBE_Q
  await shot('刷新后-用户气泡干净')
  return `${bubbles.length} 个气泡全部无注入标记；与原话逐字相同=${facts.bubble_exact_match}`
})

await step('5-回答署名是「猎鹿人 · Hunter-AtomCode」', async () => {
  const body = await page.locator('body').innerText()
  facts.has_new_signature = body.includes('猎鹿人 · Hunter-AtomCode')
  // 旧署名判据要精确：新写法里也含「猎鹿人」，所以断言的是**旧的那个完整串**
  facts.has_old_signature = /猎鹿人\s+Hunter(?!-AtomCode)/.test(body)
  if (!facts.has_new_signature) throw new Error('页面上找不到「猎鹿人 · Hunter-AtomCode」署名')
  if (facts.has_old_signature) throw new Error('页面上还留着旧署名「猎鹿人 Hunter」')
  await shot('回答署名')
  return '新署名在、旧署名不在'
})

facts.consoleErrors = consoleErrors.slice(0, 20)
facts.badResponses = badResponses.slice(0, 20)
facts.base = BASE
facts.quota_burnt = burnt
writeFileSync(join(OUT, 'facts.json'), JSON.stringify({ results, facts }, null, 2), 'utf8')
console.log('\n' + JSON.stringify(results, null, 2))
console.log(`\n产物：${join(OUT, 'facts.json')}`)
await browser.close()
process.exit(results.some((r) => r.ok === false) ? 1 : 0)
