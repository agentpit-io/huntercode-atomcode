/**
 * 端到端脚本的公共件（Playwright）。
 *
 * 这些 helper 原来长在 `m3-web.mjs` 里。M4 要再写一套回归用例，就把它们提出来共用；
 * **`m3-web.mjs` 保持原样不动** —— 它是 M3 那轮验收的证据文件，改它等于改证据。
 * 所以这里和 m3-web.mjs 有一段重复代码，是有意的，别去"顺手合并"。
 *
 * 用法（在测试机 ~/hca/pw 下跑，node_modules 在那儿）：
 *     import { setup, step, shot, send, ... } from '<repo>/tools/e2e/lib.mjs'
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

export const argv = process.argv.slice(2)
export const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d }
export const BASE = arg('--base', 'http://localhost:3200').replace(/\/$/, '')
export const SECRETS = arg('--secrets', `${process.env.HOME}/hca/secrets`)
export const OUT = arg('--out', `${process.env.HOME}/hca/pw/out`)
export const ONLY = (arg('--only', '') || '').split(',').filter(Boolean)

mkdirSync(OUT, { recursive: true })

// ── 管理员口令：从 admin.txt 读，**任何时候不打印** ───────────────────────
export function admin() {
  const txt = readFileSync(join(SECRETS, 'admin.txt'), 'utf8')
  const get = (k) => (txt.split('\n').find((l) => l.startsWith(k + ':')) || '').split(':').slice(1).join(':').trim()
  const email = get('email'), password = get('password')
  if (!email || !password) throw new Error('admin.txt 里没读到 email/password')
  return { email, password }
}

export const results = []
let shotN = 0
export const step = async (name, fn) => {
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

export let page
export const shot = async (tag) => {
  shotN += 1
  const f = join(OUT, `${String(shotN).padStart(2, '0')}-${tag}.png`)
  await page.screenshot({ path: f, fullPage: false })
  console.log(`  📸 ${f}`)
  return f
}

/** 网关配额（真实数字，用来说明每一步烧了多少）。拿不到返回 null。 */
export async function quota() {
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

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 首次登录会连着弹两层全屏遮罩，都会挡住输入框和「新建对话」，真人也得先处理：
 *   1. 合规声明（ComplianceAckModal，登录后 2.5 秒才出现）—— 勾选 + 「确认继续」
 *   2. 偏好引导（ProfileEditor 的 wizard，profile.onboarded=false 时弹）—— 三步「跳过」
 * 两个都点完，服务端会记住（compliance-ack / onboarded），之后不再弹。
 */
export async function dismissModals() {
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
export async function clickSafe(locator, timeoutMs = 20000) {
  for (let i = 0; i < 4; i += 1) {
    try { await locator.click({ timeout: timeoutMs / 4 }); return } catch (e) {
      if (!/intercepts pointer events|not stable|Timeout/.test(String(e.message))) throw e
      await dismissModals()
    }
  }
  await locator.click({ timeout: timeoutMs / 4 })
}

/** 滚到消息列表最底下 —— 不然截图里只看得见自己发的那句，看不到回答。 */
export async function scrollToBottom() {
  await page.evaluate(() => {
    for (const el of Array.from(document.querySelectorAll('*'))) {
      if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight
    }
    window.scrollTo(0, document.body.scrollHeight)
  })
  await sleep(600)
}

/** 输入框（InputBox 的 textarea）。 */
export const box = () => page.locator('textarea').first()
/** 发送/停止按钮 —— InputBox 里 aria-label 在两态间切。 */
export const sendBtn = () => page.locator('button[aria-label="发送"]')
export const stopBtn = () => page.locator('button[aria-label="停止生成"]')

/** 等一轮跑完：停止按钮消失（前端 setBusy(false)）。 */
export async function waitTurnDone(timeoutMs) {
  await stopBtn().waitFor({ state: 'detached', timeout: timeoutMs }).catch(async () => {
    // 有的实现是按钮还在但 aria-label 变回「发送」——两种都认
    await sendBtn().waitFor({ state: 'visible', timeout: 5000 })
  })
}

export async function send(text, timeoutMs = 240000) {
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


/** 起浏览器；返回 { browser, ctx, page, consoleErrors, badResponses }。 */
export async function setup() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' })
  page = await ctx.newPage()
  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)) })
  const badResponses = []
  page.on('response', (r) => {
    if (r.status() >= 400) badResponses.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`)
  })
  return { browser, ctx, page, consoleErrors, badResponses }
}

/** 登录并清掉首次登录的两层遮罩。 */
export async function login() {
  const { email, password } = admin()
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input[type="email"]').waitFor({ timeout: 30000 })
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30000 })
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await sleep(3500)
  await dismissModals()
}
