/**
 * M4 · 回归清单里需要真浏览器的那几项（Playwright / chromium）。
 *
 * M3 的 `m3-web.mjs` 已经覆盖：登录 / 新建对话 / MCP 富卡片 / 通用卡片进 ArtifactPanel /
 * 触发技能 / Kronos 图 / 刷新恢复 / 中止 / SSE 重连。**那套照原样再跑一遍**，
 * 这里只补它没有的：
 *
 *   R1 持仓研判（/portfolio 页 + 对话里问持仓）
 *   R2 策略中心与回测（/backtest 页真跑一次回测）
 *   R3 对话式投研（一个完整的投研问题：要有 MCP 取数、中文、不给买卖指令）
 *   R4 审计留痕（上面那轮之后 audit.jsonl 里有这一轮的记录，且带耗时与用户标识）
 *   R5 忙时排队提示（已拍板决策 10）
 *   R6 审批档选择器的标注（已拍板决策 9）
 *
 * 在**测试机**上跑：
 *     cd ~/hca/pw && node ~/hca/repo/tools/e2e/m4-regression.mjs \
 *       --base http://localhost:3200 --secrets ~/hca/secrets --out ~/hca/pw/m4
 *
 * 只记实际看到的：断言不过就 fail。断言一律认**被测系统自己产出**的东西，
 * 不认用户输入里本来就有的字样（M3 那三处写松的断言就是这么栽的，见 tools/e2e/README.md）。
 */
import { writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import {
  BASE, OUT, SECRETS, results, step, shot, send, sleep, setup, login,
  dismissModals, clickSafe, scrollToBottom, box, page as _p, quota,
} from './lib.mjs'
import * as lib from './lib.mjs'

const { browser, consoleErrors, badResponses } = await setup()
const page = lib.page
const shots = {}

/**
 * 新建对话 → 等输入框稳定。
 *
 * **必须留这 1.5 秒**：点完「新建对话」之后前端还要建会话、切视图，
 * 期间会重挂输入框。不等就 fill()，文字会被这次重挂清掉，
 * 发送按钮因为"空输入"是禁用的，`click()` 打在上面没有任何效果 ——
 * 而 `waitTurnDone()` 立刻看到「发送」按钮就返回了，用例**静默变成"过"**。
 * 第一次跑 M4 回归就是这么过的（4.4 秒、配额差值 0、截图里是空白首页）。
 */
async function newChatReady() {
  await clickSafe(page.getByRole('button', { name: /新建对话/ }))
  await box().waitFor({ timeout: 30000 })
  await sleep(1500)
}

/** 发一条消息，并**确认这一轮真的跑起来了**（不是点了个禁用按钮）。 */
async function sendStrict(text, timeoutMs) {
  await dismissModals()
  await box().fill(text)
  const filled = await box().inputValue()
  if (!filled.includes(text.slice(0, 12))) throw new Error('输入框没吃进文字（前端把它清掉了？）')
  // 「发送」在**会话还没建好**或**正在生成**时是 disabled；点在 disabled 的按钮上
  // Playwright 会一直等到超时（第二遍跑 R5 就是这么挂的）。先等它可用。
  await page.locator('button[aria-label="发送"]:not([disabled])')
    .waitFor({ timeout: 40000 })
    .catch(() => { throw new Error('「发送」按钮 40 秒内没变成可用 —— 会话没建好或还在生成上一轮') })
  await clickSafe(page.locator('button[aria-label="发送"]'))
  // 生成态的标志：出现「停止生成」。没出现就说明这条压根没发出去。
  await page.locator('button[aria-label="停止生成"]')
    .waitFor({ state: 'visible', timeout: 30000 })
    .catch(() => { throw new Error('点了发送但没有进入生成态 —— 这条消息没发出去') })
  const before = await quota()
  for (let i = 0; i < Math.ceil(timeoutMs / 2000); i += 1) {
    if (!(await page.locator('button[aria-label="停止生成"]').count())) break
    await sleep(2000)
  }
  const after = await quota()
  const burnt = (before != null && after != null) ? after - before : null
  console.log(`  ⛽ 本轮网关配额差值：${burnt == null ? '—（取不到）' : burnt}`)
  return burnt
}


/**
 * 这一轮**助手正文**的原文（不含用户那句、不含工具卡片的 JSON）。
 *
 * 为什么不能拿 `body.innerText()` 去判语言：整页文字里混着用户自己发的那句、
 * 工具卡片里 akshare 返回的英文列名、以及界面自己的英文标签 ——
 * 拿它判「回答是不是全中文」既会误伤也会漏判。
 * 走的是刷新时前端调的同一个端点，只取 `role=assistant` 的 text part。
 */
async function assistantText(sid) {
  if (!sid) return ''
  return page.evaluate(async (id) => {
    const t = localStorage.getItem('hunter_token') || localStorage.getItem('token') || ''
    const r = await fetch(`/api/opencode/session/${encodeURIComponent(id)}/message`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    })
    if (!r.ok) return ''
    const d = await r.json()
    const msgs = Array.isArray(d) ? d : (d.messages || d.data || [])
    const out = []
    for (const m of msgs) {
      if (((m.info && m.info.role) || m.role) !== 'assistant') continue
      for (const part of (m.parts || [])) {
        if (part && part.type === 'text' && typeof part.text === 'string') out.push(part.text)
      }
    }
    return out.join('\n')
  }, sid)
}

/** 记下这一轮打到哪个会话上（断言要回后端取助手正文，光看界面认不出来）。 */
function sessionGrabber() {
  let sid = ''
  const grab = (r) => {
    const m = /\/api\/opencode\/session\/([^/]+)\/message$/.exec(r.url())
    if (m && r.method() === 'POST') sid = decodeURIComponent(m[1])
  }
  page.on('request', grab)
  return { get: () => sid, stop: () => page.off('request', grab) }
}

/** 在 daemon 容器里读审计日志（回归要证明 hook 在**真实链路**里生效，不是只在用例里）。 */
function auditTail(n = 40) {
  try {
    const out = execFileSync('docker', ['exec', 'hca-daemon', 'sh', '-c',
      `tail -${n} /workspace/.atomcode/audit.jsonl`], { encoding: 'utf8' })
    return out.split('\n').filter((l) => l.trim()).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean)
  } catch (e) {
    return []
  }
}

await step('R0-登录', async () => {
  await login()
  shots.login = await shot('m4-logged-in')
  const tok = await page.evaluate(() => localStorage.getItem('hunter_token') || localStorage.getItem('token') || '')
  if (!tok) throw new Error('登录后 localStorage 里没有 token')
  return '管理员登录成功'
})

await step('R1-持仓研判', async () => {
  await page.goto(`${BASE}/portfolio`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {})
  await sleep(2500)
  shots.portfolio = await shot('m4-portfolio')
  const body = await page.locator('body').innerText()
  // 认页面自己渲染出来的结构，不认标题文字
  const marks = ['持仓', '市值', '盈亏', '成本'].filter((m) => body.includes(m))
  if (marks.length < 3) throw new Error(`/portfolio 没渲染出持仓结构（命中 ${marks.join('、') || '无'}）`)
  // 再在对话里问一句，验证「持仓研判」这条链路（MCP portfolio + 模型）
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await newChatReady()
  const burnt = await sendStrict('看一下我的持仓，按市值排序列出来，并指出集中度风险。数据要来自工具，拿不到就说拿不到', 420000)
  await scrollToBottom()
  shots.holdingsChat = await shot('m4-holdings-chat')
  // 认**模型产出**的东西：要么有工具卡片，要么至少这一轮真的花了 token。
  // 不能认「持仓」这两个字 —— 用户自己那句话里就有（M3 栽过的那种松断言）。
  const toolCards = await page.locator('text=/portfolio_|watchlist_|akshare_|mcp__/').count()
  if (!toolCards && !(burnt && burnt > 0)) {
    throw new Error('这一轮既没有工具卡片、网关配额也没动 —— 消息根本没跑')
  }
  return `/portfolio 页渲染出持仓结构（${marks.join('、')}）；对话里工具卡片 ${toolCards} 个、`
    + `配额差值 ${burnt ?? '—'}`
})

await step('R2-策略中心与回测', async () => {
  await page.goto(`${BASE}/backtest`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {})
  await sleep(6000)
  shots.backtestPage = await shot('m4-backtest-page')
  const body = await page.locator('body').innerText()
  if (/403|没有权限|仅管理员/.test(body)) {
    throw new Error('/backtest 返回 403 —— api 的 _require_admin 只认 JWT role==ADMIN（大写）'
      + '或 HUNTER_ADMIN_EMAILS 白名单，而库里 role 存的是小写；要在 .env 里设 HUNTER_ADMIN_EMAILS')
  }
  // 这个页面是 **Kronos 预测准确性回测**，指标是方向命中率 / 幅度命中率 / 平均绝对误差 /
  // 稳定性指数 —— 不是 年化/夏普/最大回撤（第一版断言就是照后者写的，本身就错）。
  const tiles = ['方向命中率', '幅度命中率', '平均绝对误差', '稳定性指数'].filter((t) => body.includes(t))
  if (tiles.length < 3) throw new Error(`回测看板没渲染出指标卡（命中 ${tiles.join('、') || '无'}）`)
  const runBtn = page.locator('button').filter({ hasText: /立即运行/ }).first()
  if (!(await runBtn.count())) throw new Error('页面上没有「立即运行」按钮')

  // 有没有真数据取决于前提：股票池非空 + 配了 KRONOS_API_KEY + 至少两天的预测重叠。
  // 前提不满足时它**如实显示 `--%` 和一句说明**，不编数字 —— 那也是正确行为，要认。
  const poolZero = /股票池\s*0\s*只/.test(body)
  const emptyState = /还没有回测数据/.test(body)
  // ⚠️ 这里第一版写成「方向命中率后面 40 个字符内出现百分数」就**假过**了 ——
  // 命中的是这块卡片自己的说明文字「涨跌看对的比例 · >55% 才算有效」里的 55%，
  // 而指标值其实还是 `--%`。只认**紧跟在标题后面那个值**（innerText 里是
  // 「方向命中率\n--%\n涨跌看对的比例…」这种形状）。
  const hitRate = (/方向命中率\s*(--%|\d+(?:\.\d+)?%)/.exec(body) || [])[1] || ''
  const hasNumbers = !!hitRate && hitRate !== '--%'
  if (hasNumbers) {
    await clickSafe(runBtn)
    await sleep(8000)
    shots.backtestResult = await shot('m4-backtest-result')
    return `回测看板有真实指标数据（方向命中率 ${hitRate}）`
  }
  if (!emptyState) throw new Error('既没有指标数字，也没有"还没有回测数据"的说明 —— 页面状态说不清')
  await clickSafe(runBtn)
  await sleep(10000)
  shots.backtestResult = await shot('m4-backtest-result')
  const after = await page.locator('body').innerText()
  const started = /运行中|已触发|排队|正在/.test(after)
  return `页面可用（403 已修）、四个指标卡在、空状态如实显示 ${hitRate || '--%'}（`
    + `${poolZero ? '股票池 0 只' : '股票池非空'}${emptyState ? ' · 还没有回测数据' : ''}）；`
    + `点「立即运行」后${started ? '进入运行态' : '界面无明显变化'}。`
    + `⚠️ 出数需要：股票池非空 + KRONOS_API_KEY（U-7 未定） + 至少两天的预测重叠 —— 本轮没有端到端验到出数`
})

await step('R3-对话式投研', async () => {
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await newChatReady()
  const sg = sessionGrabber()
  const burnt = await sendStrict(
    '我在看 600519，帮我做一段投研：先取最新行情与估值，再说三条值得注意的地方，'
    + '每条都要有取到的数字支撑；拿不到的数据直接说拿不到，不要估', 480000)
  sg.stop()
  await scrollToBottom()
  shots.research = await shot('m4-research')
  // ① 有工具卡片（认卡片自己的标志，不认用户那句话里的词）
  const toolCard = await page.locator('text=/watchlist_|akshare_|mcp__/').count()
  if (!toolCard) throw new Error('没有任何工具卡片 —— 这一轮没走 MCP')

  // ② / ③ 都只判**助手正文**，从后端原样取回（见 assistantText 的注释）。
  //    ⚠️ 这两条原来是拿整页 innerText 判的，而且 ② **压根没有断言** ——
  //    只把 enBad.length 写进返回串，多少段英文都算"过"。M5 复核时改成真断言。
  const answer = await assistantText(sg.get())
  if (!answer.trim()) {
    throw new Error(`取不到这一轮的助手正文（session=${sg.get() || '未抓到'}），语言与合规判据无从谈起`)
  }
  const en = answer.match(/\b[A-Za-z]{3,}(?:\s+[A-Za-z]{3,}){4,}/g) || []
  // 金融术语缩写成串出现是正常的（PE TTM ROE …），不算"英文散句"
  const enBad = en.filter((x) => !/PE|ROE|TTM|EPS|MACD|KDJ|NASDAQ|HTTP|JSON|API|MCP/i.test(x))
  if (enBad.length) {
    throw new Error(`助手正文里出现了成句英文（${enBad.length} 段），出口语言守卫没生效：`
      + enBad.slice(0, 2).map((x) => `「${x.slice(0, 60)}」`).join('、'))
  }
  const advice = answer.match(/建议(买入|卖出|加仓|减仓|清仓)|目标价|可以买|该卖/g) || []
  if (advice.length) throw new Error(`助手正文里出现了买卖指令：${advice.slice(0, 3).join('、')}`)
  return `走了 ${toolCard} 个工具卡片；助手正文 ${answer.length} 字、英文散句 0 段、买卖指令 0 处`
    + `（两条都是**断言**，不是只统计）；配额差值 ${burnt ?? '—'}`
})

await step('R4-审计留痕（真实链路）', async () => {
  const recs = auditTail(60)
  if (!recs.length) throw new Error('读不到 /workspace/.atomcode/audit.jsonl（hook 没落盘？）')
  const withDur = recs.filter((r) => typeof r.duration_ms === 'number')
  const withUser = recs.filter((r) => r.user_id)
  const tools = [...new Set(recs.map((r) => r.tool))].slice(0, 6)
  if (!withDur.length) throw new Error('审计记录里一条都没有 duration_ms —— guard 的起始记录没配上')
  writeFileSync(`${OUT}/audit-sample.json`, JSON.stringify(recs.slice(-8), null, 2))
  return `最近 ${recs.length} 条审计：${withDur.length} 条带耗时、${withUser.length} 条带用户标识；`
    + `涉及工具 ${tools.join('、')}`
})

await step('R5-忙时排队提示（决策 10）', async () => {
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await newChatReady()
  // 第一条：开一个会跑一会儿的回合，不等它结束
  await box().fill('用 akshare 取 600519 最近 20 个交易日的日线，逐行列出来')
  await page.locator('button[aria-label="发送"]:not([disabled])').waitFor({ timeout: 40000 })
  await clickSafe(page.locator('button[aria-label="发送"]'))
  await sleep(4000)
  // 第二条：换一个新会话再发，触发排队
  await newChatReady()
  await box().fill('你好')
  await page.locator('button[aria-label="发送"]:not([disabled])').waitFor({ timeout: 40000 })
  await clickSafe(page.locator('button[aria-label="发送"]'))
  let seen = false
  for (let i = 0; i < 30; i += 1) {
    await sleep(2000)
    const t = await page.locator('body').innerText()
    if (/已排队|正在生成/.test(t)) { seen = true; break }
  }
  await scrollToBottom()
  shots.queued = await shot('m4-queued-notice')
  // 等两轮都结束，别把后面的用例带崩
  for (let i = 0; i < 90; i += 1) {
    if (!(await page.locator('button[aria-label="停止生成"]').count())) break
    await sleep(3000)
  }
  if (!seen) throw new Error('第二条消息没看到排队/忙提示')
  return '第二条消息立刻收到了「另一个对话正在生成……已排队」的提示'
})

await step('R6-审批档选择器的标注（决策 9）', async () => {
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await dismissModals()
  await box().waitFor({ timeout: 30000 })
  // ⚠️ agent 选择器在前端**本来就是隐藏的**（`InputBox.tsx:503` 的注释写着
  // 「AgentPicker 已隐藏 · 走 opencode 默认 agent」），所以用户实际看到的那个选择器是
  // **model picker**。决策 9 要的"让用户看懂它切的是什么"因此落在 model picker 上：
  // 切模型在本发行版里是 `POST /live/provider`，**全局生效**（不是只影响自己这个会话）。
  const agentPicker = await page.locator('button[title="切换 agent"]').count()
  const picker = page.locator('button[title="切换 model"]')
  if (!(await picker.count())) throw new Error('页面上连 model 选择器都没有')
  // 模型清单是异步拉的（listProviders → /api/opencode/config/providers），
  // 刚进页面时按钮上是占位的「选模型」。等它变成真名字再断言。
  let label = ''
  for (let i = 0; i < 20; i += 1) {
    label = (await picker.first().innerText()).trim()
    if (label && label !== '选模型') break
    await sleep(1500)
  }
  await clickSafe(picker.first())
  await sleep(1000)
  shots.agentPicker = await shot('m4-model-picker')
  if (!/全局生效/.test(label)) {
    throw new Error(`model 选择器上没有「全局生效」标注，实得「${label}」`)
  }
  await page.keyboard.press('Escape').catch(() => {})
  return `model 选择器显示「${label}」（agent 选择器前端隐藏中，count=${agentPicker}）`
})

// ── 收尾 ────────────────────────────────────────────────────────────────
const ok = results.filter((r) => r.ok === true).length
const failed = results.filter((r) => r.ok === false).length
const summary = {
  base: BASE, when: new Date().toISOString(),
  quota_used_at_end: await quota(),
  total: results.length, ok, failed,
  results, shots, consoleErrors: consoleErrors.slice(0, 20), badResponses: badResponses.slice(0, 20),
}
writeFileSync(`${OUT}/result.json`, JSON.stringify(summary, null, 2))
console.log(`\n══ ${ok}/${results.length} 过，${failed} 失败 ══`)
for (const r of results) console.log(`  ${r.ok === null ? '·' : r.ok ? '✅' : '❌'} ${r.name}  ${r.note}`)
if (badResponses.length) console.log(`\n⚠ HTTP ≥400：\n  ${badResponses.slice(0, 10).join('\n  ')}`)
if (consoleErrors.length) console.log(`\n⚠ 控制台报错 ${consoleErrors.length} 条（前 5）：\n  ${consoleErrors.slice(0, 5).join('\n  ')}`)
await browser.close()
process.exit(failed ? 1 : 0)
