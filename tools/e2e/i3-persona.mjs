/**
 * I3 ·「按结构卡（改对版）」在**真部署、真浏览器**上的回归（Playwright）。
 *
 *     cd ~/hca/pw && node <repo>/tools/e2e/i3-persona.mjs \
 *       --base http://localhost:3200 --secrets ~/hca/secrets --out <截图目录>
 *
 * 为什么非要在浏览器里再验一遍：这一轮改的是**人设与项目指令**，
 * 而 I2 §2.9.4 那次回退的两处（q2 的持仓段、q4 的免责段）**评测脚本看不见其中一处**
 * —— C1～C5 里根本没有「末尾风险提示在不在」这一项，是人读出来的。
 * 评测机上的批次已经全量核过（`tools/eval/i3_guard_check.py`），
 * 但那是打 daemon 的 API；**用户看到的是网页**，两者之间还隔着一层 BFF 渲染。
 *
 * 问的是一道会真的调工具、会给评分、也会带风险提示的题，判据四条：
 *
 *   1. 回答里有 **0–100 的评分**（人设要求评分必须给）；
 *   2. 回答里有**风险项**（评分必须附风险项）；
 *   3. 回答**末尾带那段 AI 生成标识与风险提示** —— 这就是 opt4 砍掉过的那一段；
 *   4. 回答里的**数字带来源/口径**（至少出现工具名或报告期）。
 *
 * ⚠️ 会发一轮真实对话（要烧几万 token）。跑之前确认目标环境上没有浸泡/评测在跑。
 */
import { setup, login, step, results, BASE, OUT, quota, send, shot, dismissModals, scrollToBottom } from './lib.mjs'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const { browser, page, consoleErrors, badResponses } = await setup()
mkdirSync(OUT, { recursive: true })
const facts = {}

// 与评测题 q1 同一类（单股基本面 + 评分），但**不照抄题面** ——
// 照抄的话等于把评测题搬到产品里跑，两边的数会被当成同一个来源。
const Q = '帮我看看贵州茅台（600519）现在的基本面，给个 0-100 的评分。'
const DISCLAIMER = ['本内容由 AI', '不构成投资建议', '风险自担']

const before = await quota()
let answer = ''

await step('1-登录并发一轮真实对话', async () => {
  await login()
  await dismissModals()
  await send(Q, 300000)
  await scrollToBottom()
  answer = await page.locator('body').innerText()
  facts.answer_len = answer.length
  await shot('真实回答')
  if (answer.length < 200) throw new Error(`页面文本太短（${answer.length}），这一轮多半没答出来`)
  return `页面文本 ${answer.length} 字符`
})

await step('2-评分给了（0–100）', async () => {
  const m = answer.match(/(\d{1,3})\s*\/\s*100|评分[^0-9]{0,8}(\d{1,3})/)
  facts.score_text = m ? m[0] : null
  if (!m) throw new Error('回答里找不到 0–100 的评分')
  return `命中「${m[0]}」`
})

await step('3-风险项在', async () => {
  facts.has_risk = /风险项|风险与|主要风险|局限/.test(answer)
  if (!facts.has_risk) throw new Error('回答里没有风险项 —— 人设要求评分必须附风险项')
  return '有'
})

await step('4-末尾那段 AI 生成标识与风险提示在（opt4 砍掉过的就是它）', async () => {
  const missing = DISCLAIMER.filter((s) => !answer.includes(s))
  facts.disclaimer_missing = missing
  if (missing.length) throw new Error(`免责段缺这几句：${missing.join('、')}`)
  await shot('末尾免责段')
  return '三句齐全'
})

await step('5-数字带来源或口径', async () => {
  facts.has_source = /mcp__|hcapack|AKShare|新浪|巨潮|报告期|20\d{2}-\d{2}-\d{2}|20\d{2} 年半年报/.test(answer)
  if (!facts.has_source) throw new Error('回答里的数字看不出来源/口径')
  return '有'
})

const after = await quota()
// quota() 回的是 used_today（只增不减），所以是 after - before
facts.quota_burnt = before != null && after != null ? after - before : null
facts.base = BASE
facts.question = Q
facts.consoleErrors = consoleErrors.slice(0, 20)
facts.badResponses = badResponses.slice(0, 20)
writeFileSync(join(OUT, 'facts.json'), JSON.stringify({ results, facts }, null, 2), 'utf8')
console.log('\n' + JSON.stringify(results, null, 2))
console.log(`\n产物：${join(OUT, 'facts.json')}`)
await browser.close()
process.exit(results.some((r) => r.ok === false) ? 1 : 0)
