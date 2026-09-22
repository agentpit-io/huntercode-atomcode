/**
 * `AGENT_BACKEND=atomcode` 适配层的单测。
 *
 *     node --test apps/web/tests/atomcode.test.ts
 *     （在**仓库根目录**跑 —— 夹具是按相对根目录找的；
 *       别写成 `node --test apps/web/tests/`，那样 node 会把目录名当模块解析，报 MODULE_NOT_FOUND）
 *
 * **夹具全部是真实抓取的原文**，不是手写的假事件：
 *   · `docs/eval/raw/*.sse`        —— M2 A/B 正式评测的 14 份 `/live` 原始流
 *   · `docs/evidence/M0/t2-bash.sse` —— M0 抓的 `/chat` 流，含 artifact 三联
 *   · `docs/evidence/M3/session-detail-*.json` —— 测试机上一条真实会话的详情
 *
 * 用 node 自带的测试运行器 + 类型剥离（Node ≥ 22.18），不引第三方依赖：
 * web 的运行时依赖不该为了跑测试而变大。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { TurnProjector, normalizeToolName, stripInjected, type OcEvent } from '../app/lib/atomcode/events.ts'
import { projectHistory } from '../app/lib/atomcode/history.ts'
import { permissionDenyPlan } from '../app/lib/atomcode/live-hub.ts'

const REPO = join(import.meta.dirname, '..', '..', '..')
const LIVE_DIR = join(REPO, 'docs', 'eval', 'raw')

/** 把一份 SSE 原文拆成事件数组（心跳注释行、非 data 行全丢掉）。 */
function parseSse(text: string): any[] {
  const out: any[] = []
  for (const line of text.split('\n')) {
    if (!line.startsWith('data:')) continue
    const raw = line.slice(5).trim()
    if (!raw || raw === '[DONE]') continue
    try { out.push(JSON.parse(raw)) } catch { /* 半截帧，跳过 */ }
  }
  return out
}

function liveFixtures(): Array<{ name: string; events: any[] }> {
  return readdirSync(LIVE_DIR)
    .filter((f) => f.endsWith('.sse'))
    .sort()
    .map((f) => ({ name: f, events: parseSse(readFileSync(join(LIVE_DIR, f), 'utf-8')) }))
}

/** 把一份 `/live` 流整个喂给投影器，收集产出的前端事件。 */
function replay(sessionId: string, events: any[]): { out: OcEvent[]; p: TurnProjector } {
  const p = new TurnProjector({ sessionId, startedAt: 1_700_000_000_000 })
  const out: OcEvent[] = [...p.begin('用户原话')]
  for (const ev of events) out.push(...p.project(ev))
  return { out, p }
}

// ─────────────────────────────────────────────────────────────
// 工具名归一
// ─────────────────────────────────────────────────────────────

test('工具名归一 · 7 张富卡片全部对得上前端的 dispatch 名', () => {
  const pairs: Array<[string, string]> = [
    ['mcp__watchlist__stock_quickview', 'watchlist_stock_quickview'],
    ['mcp__watchlist__stock_news', 'watchlist_stock_news'],
    ['mcp__watchlist__watchlist_digest', 'watchlist_watchlist_digest'],
    ['mcp__portfolio__portfolio_rebalance', 'portfolio_portfolio_rebalance'],
    ['mcp__portfolio__portfolio_stress', 'portfolio_portfolio_stress'],
    ['mcp__portfolio__update_risk_profile', 'portfolio_update_risk_profile'],
    ['mcp__uzi__stock_deep_analysis', 'uzi_stock_deep_analysis'],
  ]
  for (const [from, to] of pairs) assert.equal(normalizeToolName(from), to)
})

test('工具名归一 · 内置工具原样透传', () => {
  for (const t of ['bash', 'read_file', 'use_skill', 'write_file', 'glob']) {
    assert.equal(normalizeToolName(t), t)
  }
})

test('工具名归一 · 评测原始流里出现过的每一个 mcp__ 工具都能归一', () => {
  const seen = new Set<string>()
  for (const { events } of liveFixtures()) {
    for (const ev of events) {
      if (ev?.type === 'tool_start' && typeof ev.name === 'string') seen.add(ev.name)
    }
  }
  const mcp = [...seen].filter((n) => n.startsWith('mcp__'))
  assert.ok(mcp.length >= 8, `原始流里应该有多个 mcp__ 工具，实际 ${mcp.length}`)
  for (const name of mcp) {
    const norm = normalizeToolName(name)
    assert.ok(!norm.startsWith('mcp__'), `${name} 没有被归一`)
    assert.ok(norm.includes('_'), `${name} 归一成了 ${norm}`)
  }
})

// ─────────────────────────────────────────────────────────────
// 注入块剥离
// ─────────────────────────────────────────────────────────────

test('剥注入块 · context hook 注入的 <hca-context> 不该出现在用户气泡里', () => {
  const real = '请对 600519 做基本面研判。\n\n<hca-context>\n当前时间：2026-09-22 08:00（上海）\n交易时段：盘中\n</hca-context>'
  assert.equal(stripInjected(real), '请对 600519 做基本面研判。')
})

test('剥注入块 · BFF 注入的画像与技能指令一并剥掉，没有注入时原样返回', () => {
  const t = '问题正文\n\n<hunter-profile>\n偏好：稳健\n</hunter-profile>\n\n<hunter-skill>\n用 uzi\n</hunter-skill>'
  assert.equal(stripInjected(t), '问题正文')
  assert.equal(stripInjected('干净的一句话'), '干净的一句话')
  assert.equal(stripInjected(''), '')
})

// ─────────────────────────────────────────────────────────────
// 事件投影 · 用 M2 的 14 份真实 /live 流逐个跑
// ─────────────────────────────────────────────────────────────

test('事件投影 · 每份真实流的正文都能逐字还原', () => {
  const files = liveFixtures()
  assert.ok(files.length >= 10, `期望至少 10 份真实流，实际 ${files.length}`)
  for (const { name, events } of files) {
    const expected = events
      .filter((e) => e?.type === 'text')
      .map((e) => String(e.content ?? ''))
      .join('')
    const { p } = replay('sess-1', events)
    // warning 事件会往正文里插引用块，所以只在没有 warning 的流上比逐字相等
    const hasWarning = events.some((e) => e?.type === 'warning' && String(e.message || '').trim())
    if (hasWarning) {
      assert.ok(p.assistantText.includes(expected.slice(0, 200)), `${name}: 正文开头对不上`)
    } else {
      assert.equal(p.assistantText, expected, `${name}: 正文没有逐字还原`)
    }
  }
})

test('事件投影 · 工具调用一次不少，名字归一、输出原样', () => {
  for (const { name, events } of liveFixtures()) {
    const starts = events.filter((e) => e?.type === 'tool_start')
    const { out } = replay('sess-1', events)
    const toolParts = out.filter((e) => e.type === 'message.part.updated' && e.properties.part.type === 'tool')
    const ids = new Set(toolParts.map((e) => e.properties.part.callID))
    assert.equal(ids.size, starts.length, `${name}: 工具卡片数量对不上`)

    for (const r of events.filter((e) => e?.type === 'tool_result')) {
      const done = toolParts.find(
        (e) => e.properties.part.callID === r.id && e.properties.part.state.status !== 'running',
      )
      assert.ok(done, `${name}: ${r.id} 没有终态卡片`)
      assert.equal(done!.properties.part.tool, normalizeToolName(r.name))
      assert.equal(done!.properties.part.state.output, r.output)
      assert.equal(done!.properties.part.state.status, r.success === false ? 'error' : 'completed')
    }
  }
})

test('事件投影 · delta 之前一定先建过同 id 的 part（前端会把孤儿 delta 直接丢掉）', () => {
  for (const { name, events } of liveFixtures()) {
    const { out } = replay('sess-1', events)
    const known = new Set<string>()
    for (const e of out) {
      if (e.type === 'message.part.updated') known.add(e.properties.part.id)
      if (e.type === 'message.part.delta') {
        assert.ok(known.has(e.properties.partID), `${name}: ${e.properties.partID} 的 delta 早于它的 part`)
      }
    }
  }
})

test('事件投影 · 工具调用之后另起 text part（否则调用前的「正在查…」会被算进最终报告）', () => {
  const events = parseSse(readFileSync(join(LIVE_DIR, 'q1-fundamental-atomcode-r1.sse'), 'utf-8'))
  assert.ok(events.some((e) => e.type === 'tool_start'), '这份夹具应该有工具调用')
  const { out } = replay('sess-1', events)

  // 复刻前端 reportDetect.getAssistantText 的口径：最后一个 tool part 之后的 text
  const parts: any[] = []
  for (const e of out) {
    if (e.type !== 'message.part.updated') continue
    const idx = parts.findIndex((p) => p.id === e.properties.part.id)
    if (idx >= 0) parts[idx] = e.properties.part
    else parts.push(e.properties.part)
  }
  for (const e of out) {
    if (e.type !== 'message.part.delta') continue
    const p = parts.find((x) => x.id === e.properties.partID)
    if (p) p[e.properties.field] = (p[e.properties.field] || '') + e.properties.delta
  }
  const lastTool = parts.map((p, i) => (p.type === 'tool' ? i : -1)).filter((i) => i >= 0).pop()!
  const finalText = parts.slice(lastTool + 1).filter((p) => p.type === 'text').map((p) => p.text).join('\n')

  const firstText = String(events.find((e) => e.type === 'text')?.content || '')
  assert.ok(firstText.length > 0)
  assert.ok(!finalText.includes(firstText), '工具调用之前那段话不该进最终报告')
  assert.ok(finalText.length > 200, '最终报告正文不该是空的')
})

test('事件投影 · 正常收尾只发一条终态、不挂错误卡', () => {
  const events = parseSse(readFileSync(join(LIVE_DIR, 'q1-fundamental-atomcode-r1.sse'), 'utf-8'))
  const final = [...events].reverse().find((e) => e.type === 'state' && e.running === false)
  assert.equal(final.stop_reason, 'stopped')

  const { out, p } = replay('sess-1', events)
  assert.equal(p.finished, true)
  assert.equal(p.stopReason, 'stopped')
  const finals = out.filter((e) => e.type === 'message.updated' && e.properties.info.role === 'assistant' && e.properties.info.time?.updated)
  assert.equal(finals.length, 1)
  assert.equal(finals[0].properties.info.error, undefined)
  assert.equal(p.finish().length, 0, 'finish() 必须幂等')
})

test('事件投影 · provider 报错要变成前端认得的 error 形状', () => {
  const p = new TurnProjector({ sessionId: 's', startedAt: 1 })
  p.begin('x')
  p.project({ type: 'error', message: '账户余额不足（HTTP 402）' })
  const out = p.project({ type: 'state', running: false, stop_reason: 'provider_error' })
  const err = out[0].properties.info.error
  assert.equal(err.name, 'APIError')
  assert.equal(err.data.message, '账户余额不足（HTTP 402）')
})

test('事件投影 · 用户主动停止不画错误卡（modelError 对 MessageAbortedError 返回 null）', () => {
  const p = new TurnProjector({ sessionId: 's', startedAt: 1 })
  p.begin('x')
  p.markAborted()
  const out = p.project({ type: 'state', running: false, stop_reason: 'cancelled' })
  assert.equal(out[0].properties.info.error.name, 'MessageAbortedError')
})

test('事件投影 · 非正常终止要如实标出来，stopped 不猜', () => {
  const mk = (reason: string) => {
    const p = new TurnProjector({ sessionId: 's', startedAt: 1 })
    p.begin('x')
    return p.project({ type: 'state', running: false, stop_reason: reason })[0].properties.info.error
  }
  assert.equal(mk('stopped'), undefined)
  assert.ok(String(mk('max_rounds').data.message).includes('max_rounds'))
})

test('事件投影 · 用户那条气泡用的是用户原话，不是 /live 回显的带注入版本', () => {
  const p = new TurnProjector({ sessionId: 's', startedAt: 1 })
  const out = p.begin('茅台今天怎么样')
  const user = out.find((e) => e.properties.info?.role === 'user')!
  assert.equal(user.properties.info.parts[0].text, '茅台今天怎么样')
})

// ─────────────────────────────────────────────────────────────
// artifact 三联（只在 /chat 上出现过）
// ─────────────────────────────────────────────────────────────

test('artifact · M0 的 /chat 真实流里那组三联是正文副本，必须去重不重复显示', () => {
  const events = parseSse(readFileSync(join(REPO, 'docs', 'evidence', 'M0', 't2-bash.sse'), 'utf-8'))
  assert.ok(events.some((e) => e.type === 'artifact_start'), '这份夹具应该有 artifact 三联')
  // 这一组 artifact 的内容（"2\n"）正是 bash 的输出，正文里也有
  const { p } = replay('sess-1', events)
  const body = events.filter((e) => e.type === 'artifact_content').map((e) => e.content).join('').trim()
  const occurrences = p.assistantText.split(body).length - 1
  assert.ok(occurrences <= 1, `artifact 内容在正文里出现了 ${occurrences} 次`)
})

test('artifact · 内容与正文不重复时，html 类型要包成 ```html 围栏（ArtifactPanel 靠它识别）', () => {
  const p = new TurnProjector({ sessionId: 's', startedAt: 1 })
  p.begin('x')
  p.project({ type: 'text', content: '报告如下：' })
  p.project({ type: 'artifact_start', id: 'a1', artifact_type: 'html', language: '', title: null })
  p.project({ type: 'artifact_content', id: 'a1', content: '<html><body>图</body></html>' })
  p.project({ type: 'artifact_end', id: 'a1' })
  assert.match(p.assistantText, /```html\n<html><body>图<\/body><\/html>\n```/)
})

// ─────────────────────────────────────────────────────────────
// 历史投影
// ─────────────────────────────────────────────────────────────

test('历史投影 · 真实会话的 7 条 daemon 消息 → 2 条前端消息', () => {
  const raw = JSON.parse(readFileSync(join(REPO, 'docs', 'evidence', 'M3', 'session-detail-e06d2d59.json'), 'utf-8'))
  const msgs = projectHistory(raw.id, raw.messages)
  assert.equal(msgs.length, 2)
  assert.equal(msgs[0].role, 'user')
  assert.equal(msgs[1].role, 'assistant')
})

test('历史投影 · system 提示不进界面，用户消息剥掉 hook 注入的上下文', () => {
  const raw = JSON.parse(readFileSync(join(REPO, 'docs', 'evidence', 'M3', 'session-detail-e06d2d59.json'), 'utf-8'))
  const user = raw.messages.find((m: any) => m.role === 'user')
  assert.ok(String(user.content).includes('<hca-context>'), '夹具里应该有注入块')

  const msgs = projectHistory(raw.id, raw.messages)
  const text = msgs[0].parts[0].text
  assert.ok(!text.includes('<hca-context>'))
  assert.ok(!text.includes('You are AtomCode'))
  assert.ok(text.startsWith('我想用「最近一期 ROE'))
})

test('历史投影 · tool_calls 变成工具卡片，输出取 content 全文而不是被截断的 summary', () => {
  const raw = JSON.parse(readFileSync(join(REPO, 'docs', 'evidence', 'M3', 'session-detail-e06d2d59.json'), 'utf-8'))
  const call = raw.messages.find((m: any) => m.tool_calls)!.tool_calls[0]
  const toolMsg = raw.messages.find((m: any) => m.role === 'tool')!
  assert.ok(String(toolMsg.tool_result.summary).endsWith('...'), '夹具里的 summary 应该是截断的')

  const msgs = projectHistory(raw.id, raw.messages)
  const part = msgs[1].parts.find((p: any) => p.type === 'tool')!
  assert.equal(part.callID, call.id)
  assert.equal(part.tool, 'screener_market_screen')
  assert.equal(part.state.status, 'completed')
  assert.equal(part.state.output, toolMsg.content)
  assert.equal(part.state.input.market, 'a')
})

test('历史投影 · 助手正文排在工具卡片之后（最终报告口径要和实时一致）', () => {
  const raw = JSON.parse(readFileSync(join(REPO, 'docs', 'evidence', 'M3', 'session-detail-e06d2d59.json'), 'utf-8'))
  const msgs = projectHistory(raw.id, raw.messages)
  const types = msgs[1].parts.map((p: any) => p.type)
  assert.deepEqual(types, ['text', 'tool', 'text'])
  const last = msgs[1].parts[2].text
  assert.ok(last.includes('313 只'), '最终报告正文应该是模型的作答')
})

test('历史投影 · 空会话与脏输入不会抛', () => {
  assert.deepEqual(projectHistory('s', []), [])
  assert.deepEqual(projectHistory('s', null), [])
  assert.deepEqual(projectHistory('s', undefined), [])
  assert.deepEqual(projectHistory('s', [{ role: 'system', content: 'x' }]), [])
  // 只有工具调用、还没作答的半截回合不往界面上发空消息
  assert.deepEqual(projectHistory('s', [{ role: 'assistant', content: '' }]), [])
})

// ── permission_request 的处理（设计文档 §8.2）────────────────────────────────
//
// 这条路径在本发行版里是**残余情形**：`.mcp.json` 给 9 个 server 全配了
// autoApprove（M1/M2 实测 25 次 MCP 调用 0 次弹窗），工作区外的动作又被
// guard.py 先 deny 掉了。M3 的 10 步 Playwright 跑完也一次没触发。
// 所以它只能靠单测把「回给 daemon 什么 / 给用户看什么」钉住。

test('审批事件 · 一律回 deny，并且带上 tool_name', () => {
  const { body } = permissionDenyPlan({
    type: 'permission_request', tool_name: 'bash', call_id: 'c1',
    reason: '要在工作区外写文件',
  })
  assert.equal(body.decision, 'deny')
  assert.equal(body.tool_name, 'bash')
})

test('审批事件 · 用户看到的是「哪个工具被拒了、为什么」，不是一个卡住的界面', () => {
  const plan = permissionDenyPlan({
    tool_name: 'bash', call_id: 'c1', reason: '要在工作区外写文件',
  })
  assert.match(plan.output, /已自动拒绝：bash/)
  assert.match(plan.output, /原因：要在工作区外写文件/)
  assert.equal(plan.callId, 'c1')
})

test('审批事件 · 回 daemon 失败要如实写进提示，不假装拒绝成功了', () => {
  const ok = permissionDenyPlan({ tool_name: 'bash' }, 200)
  assert.ok(!/回复 daemon 失败/.test(ok.output))
  const bad = permissionDenyPlan({ tool_name: 'bash' }, 503)
  assert.match(bad.output, /回复 daemon 失败：HTTP 503/)
})

test('审批事件 · 没给 tool_name 也不能崩，也不要往 body 里塞 undefined', () => {
  const { body, tool } = permissionDenyPlan({})
  assert.equal(tool, '未知工具')
  assert.equal(body.decision, 'deny')
  assert.ok(!('tool_name' in body))
})

// ── 出口语言守卫（待办池 P1-21）──────────────────────────────────────────
//
// 判据与翻译在 api 那一侧（有反误伤用例），这里只钉住 BFF 这一跳的三件事：
//   1. 替换用的是 `message.part.updated` 同 id 重发（前端零改动的关键）
//   2. 替换之后 `assistantText` 也得跟着变，否则 POST 的返回与界面对不上
//   3. api 挂了 / 超时 / 回空 → **放行原文**，不许把回答吞掉

test('replaceTextPart 用同 id 重发 part.updated，并同步 assistantText', () => {
  const p = new TurnProjector({ sessionId: 's1', startedAt: 1 })
  p.begin('问一句')
  p.project({ type: 'text', content: 'Let us first analyze the balance sheet.' })
  p.project({ type: 'tool_start', id: 'c1', name: 'mcp__watchlist__stock_quickview', arguments: {} })
  p.project({ type: 'text', content: '结论：估值处于近五年 30% 分位。' })
  p.finish('stopped')

  const parts = p.textParts
  assert.equal(parts.length, 2)
  assert.equal(parts[0].text, 'Let us first analyze the balance sheet.')
  assert.ok(p.assistantText.includes('Let us first'))

  const ev = p.replaceTextPart(parts[0].id, '先看资产负债表。')
  assert.equal(ev.type, 'message.part.updated')
  assert.equal(ev.properties.part.id, parts[0].id)     // 同 id = 改写而不是新增
  assert.equal(ev.properties.part.type, 'text')
  assert.equal(ev.properties.part.text, '先看资产负债表。')
  assert.equal(p.textParts[0].text, '先看资产负债表。')
  assert.ok(!p.assistantText.includes('Let us first'))
  assert.ok(p.assistantText.includes('先看资产负债表。'))
  assert.ok(p.assistantText.includes('结论：估值处于近五年 30% 分位。'))  // 另一段没被动
})

test('出口守卫：api 不可用时放行原文，不吞回答', async () => {
  const { guardText } = await import('../app/lib/atomcode/lang.ts')
  const long = 'This is a fairly long English sentence that would normally be translated. '.repeat(3)
  const orig = globalThis.fetch
  try {
    globalThis.fetch = (async () => { throw new Error('ECONNREFUSED') }) as any
    const r = await guardText(long)
    assert.equal(r.changed, false)
    assert.equal(r.text, long)
  } finally {
    globalThis.fetch = orig
  }
})

test('出口守卫：判定命中但翻译回空串时也保留原文', async () => {
  const { guardText } = await import('../app/lib/atomcode/lang.ts')
  const long = 'Another long English paragraph that the guard would flag as prose. '.repeat(3)
  const orig = globalThis.fetch
  try {
    globalThis.fetch = (async () => new Response(JSON.stringify({ changed: true, text: '' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } })) as any
    const r = await guardText(long)
    assert.equal(r.changed, false)
    assert.equal(r.text, long)     // 抹成空白比留着更糟：用户会以为回答丢了
  } finally {
    globalThis.fetch = orig
  }
})

test('出口守卫：短文本不送检（省掉绝大多数无谓调用）', async () => {
  const { guardText } = await import('../app/lib/atomcode/lang.ts')
  let called = 0
  const orig = globalThis.fetch
  try {
    globalThis.fetch = (async () => { called++; return new Response('{}', { status: 200 }) }) as any
    const r = await guardText('OK.')
    assert.equal(called, 0)
    assert.equal(r.text, 'OK.')
  } finally {
    globalThis.fetch = orig
  }
})

test('出口守卫：改写过的正文进缓存，历史投影按缓存复用', async () => {
  const { guardText, cachedFix } = await import('../app/lib/atomcode/lang.ts')
  const en = 'The company reported solid revenue growth in the latest quarter overall. '.repeat(2)
  const orig = globalThis.fetch
  try {
    globalThis.fetch = (async () => new Response(JSON.stringify({ changed: true, text: '公司最新一季营收稳健增长。' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } })) as any
    const r = await guardText(en)
    assert.equal(r.changed, true)
    assert.equal(cachedFix(en), '公司最新一季营收稳健增长。')
  } finally {
    globalThis.fetch = orig
  }
  assert.equal(cachedFix('从来没送检过的一段话'), null)

  const hist = projectHistory('s9', [
    { role: 'user', content: '问', created_at: 1 },
    { role: 'assistant', content: en, created_at: 2 },
  ])
  assert.equal(hist[1].parts[0].text, '公司最新一季营收稳健增长。')
})
