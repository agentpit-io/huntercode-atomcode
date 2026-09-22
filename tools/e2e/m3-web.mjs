/**
 * M3 · HunterCode 网页接在 AtomCode 底座上的真浏览器验收（Playwright / chromium）。
 *
 * 在**测试机**上跑（总控：开发机不跑 Next.js 相关的重活）：
 *
 *     cd ~/hca/pw && node ~/hca/repo/tools/e2e/m3-web.mjs \
 *       --base http://localhost:3200 --secrets ~/hca/secrets --out ~/hca/pw/out
 *
 * 每一步存一张截图 + 一条结论，最后写 `result.json`。
 * **只记实际看到的**：断言不过就 fail，不补图、不编数字。
 *
 * 用真实模型的步骤（问 MCP、触发技能、中止）会烧网关额度，
 * 所以问题都挑最短的，且每步跑之前会打一行当时的配额。
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const argv = process.argv.slice(2)
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d }
const BASE = arg('--base', 'http://localhost:3200').replace(/\/$/, '')
const SECRETS = arg('--secrets', `${process.env.HOME}/hca/secrets`)
const OUT = arg('--out', `${process.env.HOME}/hca/pw/out`)
const ONLY = (arg('--only', '') || '').split(',').filter(Boolean)

mkdirSync(OUT, { recursive: true })

// ── 管理员口令：从 admin.txt 读，**任何时候不打印** ───────────────────────
function admin() {
  const txt = readFileSync(join(SECRETS, 'admin.txt'), 'utf8')
  const get = (k) => (txt.split('\n').find((l) => l.startsWith(k + ':')) || '').split(':').slice(1).join(':').trim()
  const email = get('email'), password = get('password')
  if (!email || !password) throw new Error('admin.txt 里没读到 email/password')
  return { email, password }
}

const results = []
let shotN = 0
const step = async (name, fn) => {
  if (ONLY.length && !ONLY.includes(name)) { results.push({ name, ok: null, note: '本次未跑（--only）' }); return }
  const t0 = Date.now()
  process.stdout.write(`\n── ${name} ───────────────────────\n`)
  try {
    const note = await fn()
    const r = { name, ok: true, ms: Date.now() - t0, note: note || '' }
    results.push(r); console.log(`  ✅ ${r.note}  (${r.ms} ms)`)
  } catch (e) {
    const r = { name, ok: false, ms: Date.now() - t0, note: String(e && e.message || e).slice(0, 500) }
    results.push(r); console.log(`  ❌ ${r.note}  (${r.ms} ms)`)
  }
}

let page
const shot = async (tag) => {
  shotN += 1
  const f = join(OUT, `${String(shotN).padStart(2, '0')}-${tag}.png`)
  await page.screenshot({ path: f, fullPage: false })
  console.log(`  📸 ${f}`)
  return f
}

/** 网关配额（真实数字，用来说明每一步烧了多少）。拿不到返回 null。 */
async function quota() {
  try {
    const key = readFileSync(join(SECRETS, 'llm-test-key'), 'utf8').trim()
    const r = await fetch('https://hunter.agentpit.io/api/saas/llm/quota', {
      headers: { Authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(15000),
    })
    if (!r.ok) return null
    const d = await r.json()
    return typeof d.used_today === 'number' ? d.used_today : null
  } catch { return null }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 首次登录会连着弹两层全屏遮罩，都会挡住输入框和「新建对话」，真人也得先处理：
 *   1. 合规声明（ComplianceAckModal，登录后 2.5 秒才出现）—— 勾选 + 「确认继续」
 *   2. 偏好引导（ProfileEditor 的 wizard，profile.onboarded=false 时弹）—— 三步「跳过」
 * 两个都点完，服务端会记住（compliance-ack / onboarded），之后不再弹。
 */
async function dismissModals() {
  for (let i = 0; i < 8; i += 1) {
    const compliance = page.getByRole('button', { name: '确认继续' })
    if (await compliance.count()) {
      await page.locator('input[type="checkbox"]').first().check({ timeout: 5000 }).catch(() => {})
      await compliance.click({ timeout: 5000 }).catch(() => {})
      await sleep(1200); continue
    }
    const skip = page.getByRole('button', { name: '跳过' })
    if (await skip.count()) { await skip.click({ timeout: 5000 }).catch(() => {}); await sleep(900); continue }
    const wizardX = page.getByRole('button', { name: '×' })
    if (await wizardX.count()) { await wizardX.first().click({ timeout: 5000 }).catch(() => {}); await sleep(900); continue }
    return
  }
  throw new Error('遮罩层清不掉（合规弹层 / 偏好引导），后面的交互没法做')
}

/**
 * 点一个元素；被遮罩挡住就先清遮罩再重试。
 *
 * 合规弹层是**登录后 2.5 秒**才由定时器弹出来的，而且只在整页加载时起算，
 * 所以「先清一次再操作」挡不住它 —— 它可能正好在你填完输入框之后冒出来。
 */
async function clickSafe(locator, timeoutMs = 20000) {
  for (let i = 0; i < 4; i += 1) {
    try { await locator.click({ timeout: timeoutMs / 4 }); return } catch (e) {
      if (!/intercepts pointer events|not stable|Timeout/.test(String(e.message))) throw e
      await dismissModals()
    }
  }
  await locator.click({ timeout: timeoutMs / 4 })
}

/** 滚到消息列表最底下 —— 不然截图里只看得见自己发的那句，看不到回答。 */
async function scrollToBottom() {
  await page.evaluate(() => {
    for (const el of Array.from(document.querySelectorAll('*'))) {
      if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight
    }
    window.scrollTo(0, document.body.scrollHeight)
  })
  await sleep(600)
}

/** 输入框（InputBox 的 textarea）。 */
const box = () => page.locator('textarea').first()
/** 发送/停止按钮 —— InputBox 里 aria-label 在两态间切。 */
const sendBtn = () => page.locator('button[aria-label="发送"]')
const stopBtn = () => page.locator('button[aria-label="停止生成"]')

/** 等一轮跑完：停止按钮消失（前端 setBusy(false)）。 */
async function waitTurnDone(timeoutMs) {
  await stopBtn().waitFor({ state: 'detached', timeout: timeoutMs }).catch(async () => {
    // 有的实现是按钮还在但 aria-label 变回「发送」——两种都认
    await sendBtn().waitFor({ state: 'visible', timeout: 5000 })
  })
}

async function send(text, timeoutMs = 240000) {
  await dismissModals()
  const before = await quota()
  await box().fill(text)
  await clickSafe(sendBtn())
  await sendBtn().waitFor({ state: 'detached', timeout: 20000 }).catch(() => {})
  await waitTurnDone(timeoutMs)
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  console.log(`  ⛽ 本轮网关配额差值：${burnt == null ? '—（取不到）' : burnt}`)
  return burnt
}

// ══════════════════════════════════════════════════════════════════════
const browser = await chromium.launch({ args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' })
page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)) })
const badResponses = []
page.on('response', (r) => {
  if (r.status() >= 400) badResponses.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`)
})

const shots = {}

await step('01-登录页', async () => {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input[type="email"]').waitFor({ timeout: 30000 })
  shots.login = await shot('login')
  return '单用户免登录已关，看到的是真的登录表单'
})

await step('02-登录', async () => {
  const { email, password } = admin()
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30000 })
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await sleep(4000)
  shots.onboarding = await shot('onboarding-wizard')   // 首次登录的偏好引导，真实存在，如实留图
  await dismissModals()
  shots.home = await shot('logged-in')
  const tok = await page.evaluate(() => localStorage.getItem('hunter_token') || localStorage.getItem('token') || '')
  if (!tok) throw new Error('登录后 localStorage 里没有 token')
  return `管理员登录成功，落到 ${new URL(page.url()).pathname}`
})

await step('03-新建对话', async () => {
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await clickSafe(page.getByRole('button', { name: /新建对话/ }))
  await box().waitFor({ timeout: 30000 })
  await sleep(1500)
  shots.newSession = await shot('new-session')
  return '侧栏「新建对话」→ 会话建出来，输入框可用'
})

await step('04-问一个会调 MCP 的问题', async () => {
  const burnt = await send('用 stock_quickview 看一下 600519 现在的行情，一句话说结论', 300000)
  await scrollToBottom()
  shots.mcp = await shot('mcp-tool-card')
  const body = await page.locator('body').innerText()
  // **不能拿 `stock_quickview` 当证据** —— 那四个字在用户自己发的那句里就有，
  // 工具压根没调也会命中。认富卡片自己才画得出来的东西：
  // 「52 周区间」「加自选」「深度分析」三个都只出现在 StockQuickviewCard 里。
  const marks = ['52 周区间', '加自选', '深度分析'].filter((m) => body.includes(m))
  if (marks.length < 2) {
    throw new Error(`没看到 StockQuickviewCard 富卡片（命中 ${marks.length}/3 个标志：${marks.join('、') || '无'}）`)
  }
  return `StockQuickviewCard 富卡片渲染出来了（命中 ${marks.join('、')}），`
    + `说明 mcp__watchlist__stock_quickview 已归一成 watchlist_stock_quickview；`
    + `本轮网关配额差值 ${burnt == null ? '—' : burnt}`
})

await step('05-通用工具卡片展开 + 进 ArtifactPanel', async () => {
  // 上一版这一步是「点一下富卡片就算过」—— 实测前后两张截图逐字节一样，
  // 富卡片（StockQuickviewCard）根本没有展开态，也没有「在右侧查看」入口，
  // 那个 ✅ 是假的。真正有这两件事的是**通用卡片**（ToolCallCard 的 fallback），
  // 而且要 output 超过 200 字符才会出「在右侧查看」按钮。
  // 所以这里专门问一个只会走通用卡片的工具（akshare）。
  const burnt = await send('用 akshare 取一下 600519 最近 5 个交易日的日线，原样给我数据就行', 300000)
  await scrollToBottom()
  const card = page.locator('text=/^mcp__akshare__|^akshare_/').first()
  if (!(await card.count())) throw new Error('页面上没有 akshare 的通用工具卡片')
  await clickSafe(card)                                   // 展开，看原始输出
  await sleep(1000)
  shots.toolExpanded = await shot('tool-card-expanded')
  const openBtn = page.locator('button[title="在右侧查看"]').first()
  if (!(await openBtn.count())) throw new Error('通用卡片上没有「在右侧查看」按钮（output 可能没超过 200 字符）')
  await clickSafe(openBtn)
  await sleep(1500)
  shots.toolArtifact = await shot('tool-card-artifact-panel')
  // 工具模式的 ArtifactPanel 头上是工具名，正文是 INPUT / OUTPUT 两段（report
  // 模式才有 Copy / 预览-源码 那一排，别拿它当判据 —— 第一版就是这么误判「没打开」的）。
  const body2 = await page.locator('body').innerText()
  const panelMarks = ['INPUT', 'OUTPUT'].filter((m) => body2.includes(m))
  if (panelMarks.length < 2) throw new Error(`点了「在右侧查看」，ArtifactPanel 没打开（只命中 ${panelMarks.join('、') || '无'}）`)
  const toolName = (await card.innerText()).trim().split(/\s+/)[0]
  return `通用工具卡片可展开；「在右侧查看」把 ${toolName} 的 INPUT/OUTPUT 原文送进了 ArtifactPanel；`
    + `本轮网关配额差值 ${burnt == null ? '—' : burnt}`
})

await step('06-触发一个技能', async () => {
  // **从能力库入口进**，不是自己打一句像技能的话。
  //
  // 上一版是直接发 risk_profile 的模板文本，实测**模型不一定会去调 use_skill**
  // （同一句话，一次调了一次没调），断言又写得太松（认「风险 / 保守」——
  // 用户自己那句里就有），于是「技能没触发」也会判过。
  // 真正的产品路径是首页/能力库点卡片：前端把 `skillKey` 发给 BFF，
  // BFF 转成一条「本轮必须使用这个 SKILL」的 system 硬指令（route.ts §skillKey）。
  await dismissModals()
  await clickSafe(page.getByRole('button', { name: /新建对话/ }))
  await box().waitFor({ timeout: 30000 })
  await sleep(1500)
  await clickSafe(page.locator('text=风险画像').first())
  await sleep(1200)
  const filled = await box().inputValue()
  if (!filled.trim()) throw new Error('点了「风险画像」卡片，输入框却没被填上模板')
  shots.skillPick = await shot('skill-picked')
  const before = await quota()
  await clickSafe(sendBtn())
  await sendBtn().waitFor({ state: 'detached', timeout: 20000 }).catch(() => {})
  await waitTurnDone(300000)
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  await scrollToBottom()
  shots.skill = await shot('skill')
  const body = await page.locator('body').innerText()
  // 同样不能拿「风险 / 保守」当证据 —— 用户那句里就有。认技能本身的名字与
  // 调用它的内置工具，这两个串都不可能出自用户输入。
  const marks = ['risk_profile', 'use_skill', 'update_risk_profile'].filter((m) => body.includes(m))
  if (!marks.length) throw new Error('页面上看不到 risk_profile / use_skill 的任何痕迹，技能没被触发')
  return `技能被触发（页面上出现 ${marks.join('、')}）；本轮网关配额差值 ${burnt == null ? '—' : burnt}`
})

await step('07-Kronos 预测图进 ArtifactPanel', async () => {
  await dismissModals()
  const before = await quota()
  await box().fill('预测 600519 未来 5 天走势')
  await clickSafe(sendBtn())
  // kpred 完全不经过 agent（走 hermes-api 的 Kronos），但要等 HTML 生成
  await page.locator('iframe').first().waitFor({ state: 'attached', timeout: 300000 })
  await sleep(4000)
  shots.kpred = await shot('kpred-artifact')
  const fr = page.frameLocator('iframe').first()
  const inner = await fr.locator('body').innerText({ timeout: 20000 }).catch(() => '')
  if (!/600519|贵州茅台/.test(inner)) throw new Error('ArtifactPanel 的 iframe 里没有 Kronos 报告内容')
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  return `Kronos HTML 报告在 ArtifactPanel 的 iframe 里渲染出来了；网关配额差值 ${burnt == null ? '—' : burnt}（应为 0，它不走 agent）`
})

await step('08-刷新后会话恢复', async () => {
  const urlBefore = page.url()
  const beforeText = (await page.locator('body').innerText()).replace(/\s+/g, '')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await box().waitFor({ timeout: 40000 })
  // 两条恢复路径都要覆盖：
  //   · agent 那半边 —— 走 daemon 的会话详情投影（适配层 history.ts）
  //   · Kronos 那半边 —— 走 hermes-api 的 chat_kpred.reports（与 agent 底座无关）
  const keep = ['600519', 'use_skill', 'risk_profile', '52 周区间', 'Kronos']
    .filter((k) => beforeText.includes(k))
  if (keep.length < 2) throw new Error(`刷新前页面上就只有 ${keep.length} 个可核对的标志，这一步没有意义`)
  // 恢复要拉 daemon 的会话详情 + hermes 的 debate/kpred，轮询到齐或超时
  let afterText = ''
  const deadline = Date.now() + 60000
  for (;;) {
    afterText = (await page.locator('body').innerText()).replace(/\s+/g, '')
    if (keep.every((k) => afterText.includes(k)) || Date.now() > deadline) break
    await sleep(2000)
  }
  await dismissModals()
  shots.reload = await shot('after-reload')
  const missing = keep.filter((k) => !afterText.includes(k))
  if (missing.length) throw new Error(`刷新后这些内容没恢复（等了 60 秒）：${missing.join(' / ')}；URL=${page.url()}`)
  if (page.url() !== urlBefore) console.log(`  （URL 从 ${urlBefore} 变成 ${page.url()}）`)
  return '刷新后历史消息与工具卡片都从 daemon 的会话详情里恢复了'
})

await step('09-中止生成', async () => {
  const before = await quota()
  await dismissModals()
  await clickSafe(page.getByRole('button', { name: /新建对话/ }))
  await box().waitFor({ timeout: 30000 })
  await sleep(1500)
  await box().fill('把 600036 招商银行近三年的财务、估值、股东结构、分红、同业对比全查一遍，写一份长报告')
  await clickSafe(sendBtn())
  await stopBtn().waitFor({ state: 'visible', timeout: 60000 })
  await sleep(12000)               // 让它真的跑起来再停
  shots.running = await shot('generating')
  const t0 = Date.now()
  await stopBtn().click()
  await waitTurnDone(90000)
  const stopMs = Date.now() - t0
  await sleep(1500)
  shots.aborted = await shot('aborted')
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  return `点「停止生成」后 ${stopMs} ms 内回到可输入状态；本轮网关配额差值 ${burnt == null ? '—' : burnt}`
})

await step('10-SSE 断线重连（浏览器层）', async () => {
  await dismissModals()
  await clickSafe(page.getByRole('button', { name: /新建对话/ }))
  await box().waitFor({ timeout: 30000 })
  await sleep(1500)

  // 记下这一轮打到哪个会话上 —— 断网之后界面会退回首页，光看界面认不出来了
  let sid = ''
  const grab = (r) => {
    const m = /\/api\/opencode\/session\/([^/]+)\/message$/.exec(r.url())
    if (m && r.method() === 'POST') sid = decodeURIComponent(m[1])
  }
  page.on('request', grab)

  const before = await quota()
  // 问法故意和第 4 步不一样：会话标题取自首句，两条都以「用 stock_quickview 看一下」
  // 开头的话，刷新后在侧栏根本认不出该点哪一条（侧栏标题是截断的）。
  await box().fill('断线重连用例：看一下 000001 平安银行的实时行情（stock_quickview），一句话说结论')
  await clickSafe(sendBtn())
  await stopBtn().waitFor({ state: 'visible', timeout: 60000 })
  await sleep(6000)

  // 真断网 —— EventSource 会 onerror，然后自己重连
  await ctx.setOffline(true)
  shots.offline = await shot('sse-offline')
  await sleep(6000)
  await ctx.setOffline(false)
  await waitTurnDone(300000)
  await sleep(3000)
  page.off('request', grab)
  await scrollToBottom()
  shots.online = await shot('sse-reconnected')
  const inlineText = await page.locator('body').innerText()
  const keptInline = /52 周区间|加自选/.test(inlineText)
  if (!sid) throw new Error('没抓到这一轮的 session id，后面的核对无从谈起')

  // ① 数据层：这一轮在后端有没有完整落下来（走的就是刷新时前端调的那个端点）
  const hist = await page.evaluate(async (id) => {
    const t = localStorage.getItem('hunter_token') || ''
    const r = await fetch(`/api/opencode/session/${encodeURIComponent(id)}/message`, {
      headers: t ? { Authorization: 'Bearer ' + t } : {}, cache: 'no-store',
    })
    return { status: r.status, text: (await r.text()).slice(0, 200000) }
  }, sid)
  if (hist.status !== 200) throw new Error(`断网重连后拉历史失败 HTTP ${hist.status}`)
  const hasTool = /stock_quickview/.test(hist.text)
  if (!hasTool) throw new Error('断网重连后，后端这一轮里找不到 stock_quickview 的工具调用')

  // ② 界面层：刷新 + 在侧栏点回这个会话，看内容在不在
  await page.reload({ waitUntil: 'domcontentloaded' })
  await box().waitFor({ timeout: 40000 })
  await dismissModals()
  // 侧栏这一条的标题由 BFF 按首句自动改过名，直接按文字点回去
  const row = page.getByText(/断线重连用例/).first()
  await row.waitFor({ timeout: 30000 })
  await clickSafe(row)
  let found = false
  const dl2 = Date.now() + 60000
  for (;;) {
    found = /52 周区间|加自选/.test(await page.locator('body').innerText())
    if (found || Date.now() > dl2) break
    await sleep(2000)
  }
  await scrollToBottom()
  shots.offlineRestored = await shot('sse-after-reload')
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  if (!found) {
    throw new Error('刷新后在侧栏点回这个会话，界面上仍然看不到这一轮的工具卡片')
  }
  return `断网 6 秒再恢复：整轮照常跑完；断网瞬间界面${keptInline ? '保住了已渲染的内容' : '退回了空白（空窗期的 delta 不补发）'}，`
    + `后端这一轮完整（${hist.text.length} 字符），刷新后点回该会话内容全在；`
    + `本轮网关配额差值 ${burnt == null ? '—' : burnt}`
})

writeFileSync(join(OUT, 'result.json'), JSON.stringify({
  base: BASE,
  at: new Date().toISOString(),
  results,
  consoleErrors: consoleErrors.slice(0, 30),
  badResponses: Array.from(new Set(badResponses)).slice(0, 40),
}, null, 2), 'utf8')

console.log('\n══════ 汇总 ══════')
for (const r of results) console.log(`  ${r.ok === null ? '·' : r.ok ? '✅' : '❌'} ${r.name}  ${r.note}`)
if (badResponses.length) {
  console.log(`\nHTTP ≥400 的请求（去重）：`)
  for (const b of Array.from(new Set(badResponses))) console.log('   · ' + b)
}
if (consoleErrors.length) {
  console.log(`\n浏览器 console error ${consoleErrors.length} 条（前 5）：`)
  for (const e of consoleErrors.slice(0, 5)) console.log('   · ' + e)
}
await browser.close()
const failed = results.filter((r) => r.ok === false)
console.log(`\n${results.filter(r => r.ok).length} 过 / ${failed.length} 败 / ${results.filter(r => r.ok === null).length} 未跑`)
process.exit(failed.length ? 1 : 0)
