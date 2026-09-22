/**
 * 品牌调整（2026-09-22）的真浏览器验收。
 *
 *     cd ~/hca/pw && node <repo>/tools/e2e/branding-web.mjs \
 *       --base http://34.92.73.207:3200 --secrets <装 admin.txt 的目录> --out <截图目录>
 *
 * 三件事，逐条只认**被测系统这一次产出的东西**（M3/M4 两次松断言的教训，
 * 见 tools/e2e/README.md）：
 *   1. 模型选择器显示真实模型名（`Gemini 3.8 Flash（全局生效）`）、分组标题
 *      是 `Google Gemini` —— 并且**断言旧文案已经不在**（`hunter-chat` / `ONEAPI`）
 *   2. 页头 / 标签页 / 侧栏的品牌是 `Hunter-AtomCode`
 *   3. 页脚有「开源地址」链接指向 GitCode 主仓，旁边的 AtomGit logo **真的加载出来了**
 *      （查 naturalWidth/naturalHeight，不是"有个 <img> 标签就算过"）
 *
 * ⚠️ **这套脚本一句对话都不发**：跑的时候 M5 的 4 小时浸泡正在同一个环境里跑，
 * 不能往里插一轮生成。
 */
import { setup, login, step, results, BASE, OUT, sleep } from './lib.mjs'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const GITCODE = 'https://gitcode.com/agentpit-io/huntercode-atomcode'

const { browser, page, consoleErrors, badResponses } = await setup()
mkdirSync(OUT, { recursive: true })
let n = 0
const shot = async (tag, locator) => {
  n += 1
  const f = join(OUT, `${String(n).padStart(2, '0')}-${tag}.png`)
  await (locator || page).screenshot({ path: f })
  console.log(`  📸 ${f}`)
  return f
}
const facts = {}

await step('1-登录并打开对话页', async () => {
  await login()
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await sleep(4000)
  await page.locator('textarea').first().waitFor({ timeout: 30000 })
  return '已登录，/chat 已渲染'
})

await step('2-浏览器标签页标题', async () => {
  const t = await page.title()
  facts.title = t
  if (!t.includes('Hunter-AtomCode')) throw new Error(`<title> 里没有 Hunter-AtomCode，实得「${t}」`)
  if (/Hunter(?!-AtomCode)/.test(t)) throw new Error(`<title> 里还留着旧的 Hunter，实得「${t}」`)
  await shot('title-tab')
  return `<title> = ${t}`
})

await step('3-页头品牌', async () => {
  // TopNav 的根节点是个普通 <div>（不是 <header>），所以从品牌链接往上取一层
  const brandLink = page.locator('a[href="/"]').filter({ hasText: '猎鹿人' }).first()
  await brandLink.waitFor({ timeout: 30000 })
  const header = brandLink.locator('xpath=..')
  const txt = (await header.innerText()).replace(/\s+/g, ' ').trim()
  facts.header = txt
  if (!txt.includes('猎鹿人')) throw new Error(`页头没有「猎鹿人」，实得「${txt}」`)
  if (!txt.includes('Hunter-AtomCode')) throw new Error(`页头不是 Hunter-AtomCode，实得「${txt}」`)
  await shot('header-brand', header)
  return `页头 = ${txt.slice(0, 60)}`
})

await step('4-侧栏品牌', async () => {
  const brand = page.locator('text=Hunter-AtomCode')
  const cnt = await brand.count()
  facts.brandNodes = cnt
  if (cnt < 2) throw new Error(`页面上带 Hunter-AtomCode 的节点只有 ${cnt} 个（页头+侧栏应该至少 2）`)
  // 名字变长会把侧栏那行挤到换行（第一版就是这样），所以顺手钉住「不换行」：
  // 「猎鹿人」三个字必须落在**同一行**里（getClientRects 只有一个矩形）。
  const deer = page.locator('aside strong', { hasText: '猎鹿人' }).first()
  const rects = await deer.evaluate((el) => el.getClientRects().length)
  facts.sidebarBrandLines = rects
  if (rects !== 1) throw new Error(`侧栏品牌「猎鹿人」被挤成了 ${rects} 行`)
  // 一行放不下时 CSS 会用省略号截成「· Hunter-…」—— 截断也是不合格，
  // 所以再查一次：这段文字的 scrollWidth 不能超过可见宽度。
  const suffix = page.locator('aside span', { hasText: /^Hunter-AtomCode$/ }).first()
  const cut = await suffix.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }))
  facts.sidebarSuffix = cut
  if (cut.sw > cut.cw + 1) throw new Error(`侧栏的 Hunter-AtomCode 被截断了（${cut.sw} > ${cut.cw}）`)
  await shot('sidebar-brand', page.locator('aside').first())
  return `Hunter-AtomCode 出现在 ${cnt} 处（页头 / 侧栏 / 页脚），侧栏品牌 ${rects} 行不换行`
})

await step('5-模型选择器按钮（收起态）', async () => {
  const btn = page.locator('button[title="切换 model"]')
  await btn.waitFor({ timeout: 20000 })
  const label = (await btn.innerText()).trim()
  facts.pickerLabel = label
  if (!label.includes('全局生效')) throw new Error(`按钮上没有「全局生效」标注（决策 9），实得「${label}」`)
  if (label.includes('hunter-chat')) throw new Error(`按钮上还是模型 ID hunter-chat，实得「${label}」`)
  if (!/Gemini/.test(label)) throw new Error(`按钮上没有真实模型名，实得「${label}」`)
  await shot('picker-collapsed', btn)
  return `按钮文字 = ${label}`
})

await step('6-模型选择器展开态（分组标题 + 下拉项）', async () => {
  const btn = page.locator('button[title="切换 model"]')
  await btn.click()
  await sleep(800)
  // 下拉是 button 的兄弟 div（position:absolute），整块截下来
  const panel = btn.locator('xpath=following-sibling::div[1]')
  const txt = (await panel.innerText()).replace(/\s+/g, ' ').trim()
  facts.dropdown = txt
  if (!txt.includes('Google Gemini')) throw new Error(`分组标题不是 Google Gemini，实得「${txt}」`)
  if (/ONEAPI|oneapi/.test(txt)) throw new Error(`分组标题还是 provider id，实得「${txt}」`)
  if (!txt.includes('Gemini 3.8 Flash（全局生效）')) throw new Error(`下拉项不是真实模型名，实得「${txt}」`)
  await shot('picker-open', btn.locator('xpath=..'))
  await shot('picker-open-fullpage')
  await page.keyboard.press('Escape').catch(() => {})
  await page.mouse.click(10, 10)
  await sleep(400)
  return `下拉内容 = ${txt}`
})

await step('7-页脚开源地址 + AtomGit logo', async () => {
  const footer = page.locator('footer').first()
  const link = footer.locator(`a[href="${GITCODE}"]`)
  if (!(await link.count())) throw new Error('页脚没有指向 GitCode 主仓的链接')
  const href = await link.first().getAttribute('href')
  facts.footerHref = href
  const img = link.locator('img')
  const dims = await img.first().evaluate((el) => ({
    nw: el.naturalWidth, nh: el.naturalHeight,
    w: Math.round(el.getBoundingClientRect().width),
    h: Math.round(el.getBoundingClientRect().height),
    src: el.getAttribute('src'),
  }))
  facts.logo = dims
  // "有个 <img> 标签" 不算过 —— 必须真的解码出了像素
  if (!dims.nw || !dims.nh) throw new Error(`AtomGit logo 没加载出来（naturalWidth=${dims.nw}）`)
  if (dims.nw !== 244 || dims.nh !== 78) throw new Error(`加载到的不是那张 244×78 的原图：${dims.nw}×${dims.nh}`)
  if (dims.h < 18 || dims.h > 26) throw new Error(`logo 显示高度 ${dims.h}px，不在要求的 20–24px 附近`)
  const ratio = dims.w / dims.h
  if (Math.abs(ratio - 244 / 78) > 0.15) throw new Error(`logo 变形了：显示 ${dims.w}×${dims.h}，比例 ${ratio.toFixed(2)}`)
  await shot('footer-opensource', footer)
  return `href=${href} · logo 原图 ${dims.nw}×${dims.nh} → 显示 ${dims.w}×${dims.h}px`
})

await step('8-点开源链接，确认落到 GitCode 仓库地址', async () => {
  const link = page.locator('footer').first().locator(`a[href="${GITCODE}"]`)
  const [popup] = await Promise.all([
    page.context().waitForEvent('page', { timeout: 20000 }),
    link.first().click(),
  ])
  await popup.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {})
  const url = popup.url()
  facts.popupUrl = url
  // 开发机与香港都在 GCP：GitCode 的 HTTPS 对这些 IP 返 418（总控规则已记录），
  // 所以这里**只断言浏览器确实去了那个地址**，页面能不能渲染另说，如实记下来。
  let status = null
  try {
    const r = await popup.request.get(GITCODE, { timeout: 20000 })
    status = r.status()
  } catch (e) { status = `请求失败：${String(e.message).slice(0, 80)}` }
  facts.gitcodeStatus = status
  await shot('gitcode-popup', popup)
  await popup.close()
  if (!url.startsWith(GITCODE)) throw new Error(`新标签页的地址不是 GitCode 主仓，实得 ${url}`)
  return `新标签页 URL = ${url} · 该地址从本机取回 HTTP ${status}（GCP IP 被 GitCode 418，总控规则已记录）`
})

await step('9-设置页「关于」里的开源地址', async () => {
  await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' })
  await sleep(2500)
  const about = page.getByRole('button', { name: '关于' })
  if (await about.count()) { await about.first().click(); await sleep(1200) }
  const link = page.locator(`a[href="${GITCODE}"]`).first()
  await link.waitFor({ timeout: 15000 })
  const dims = await link.locator('img').first().evaluate((el) => ({
    nw: el.naturalWidth, h: Math.round(el.getBoundingClientRect().height),
  }))
  facts.aboutLogo = dims
  if (!dims.nw) throw new Error('关于页里的 AtomGit logo 没加载出来')
  const txt = (await page.locator('h2:has-text("关于")').first().innerText()).trim()
  if (!txt.includes('Hunter-AtomCode')) throw new Error(`关于卡片标题不是 Hunter-AtomCode，实得「${txt}」`)
  await shot('settings-about')
  return `关于卡片 = ${txt} · logo 高 ${dims.h}px`
})

const bad = badResponses.filter((s) => !/\/api\/(quota|auth\/me)/.test(s))
console.log(`\n控制台 error ${consoleErrors.length} 条；>=400 响应 ${bad.length} 条`)
if (bad.length) console.log(bad.slice(0, 10).join('\n'))

const pass = results.filter((r) => r.ok).length
const fail = results.filter((r) => r.ok === false).length
writeFileSync(join(OUT, 'result.json'), JSON.stringify({
  base: BASE, at: new Date().toISOString(), pass, fail, results, facts,
  consoleErrors: consoleErrors.slice(0, 20), badResponses: bad.slice(0, 20),
}, null, 2))
console.log(`\n== 品牌验收 ${pass}/${results.length} 通过，失败 ${fail} ==`)
await browser.close()
process.exit(fail ? 1 : 0)
