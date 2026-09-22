/**
 * LiveHub —— AtomCode `/live` 与网页之间的那一层。
 *
 * 为什么必须有（设计文档 §3.2）：
 *   1. 一条 daemon 只有**一个** `/live` 运行时，绑定**一个**会话；多标签页直接各连各的
 *      会拿到同一条流、还会互相把会话切走。
 *   2. `POST /live/message` **立刻返回**，而前端 `sendMessage` 是 await 到整轮结束才
 *      解锁输入框；不挂住 POST 的话，用户点完发送立刻就能再点，停止按钮也会消失。
 *   3. AtomCode 没有 message / part 这层模型，事件要投影（events.ts）。
 *
 * 切会话**不用 `POST /live/switch_session`**：那条路（`resume_session_with_lease`）
 * 不等 MCP 就绪，正是待办池 P0-10 的根因；`GET /live?session_id=X` 走
 * `ensure_headless_runtime` → `bind_after_mcp_ready`，会等。见设计文档 §2.3 第 4 条。
 */

import { daemonFetch, mcpAllConnected, mcpStatus, openLiveStream } from './daemon.ts'
import { TurnProjector, type OcEvent } from './events.ts'
import { guardText, langGuardEnabled } from './lang.ts'

interface Subscriber {
  /** 这个浏览器连接能看哪些会话；null = 不限（单用户部署） */
  owned: Set<string> | null
  push: (ev: OcEvent) => void
}

interface HubState {
  bound: string | null
  abort: AbortController | null
  reader: Promise<void> | null
  subs: Set<Subscriber>
  projector: TurnProjector | null
  turnChain: Promise<unknown>
  snapshotWaiters: Array<() => void>
  mcpWarned: boolean
}

// Next 在 dev 下会重载模块；挂 globalThis 保证进程里只有一个 hub。
const g = globalThis as any
const state: HubState = g.__hcaLiveHub || (g.__hcaLiveHub = {
  bound: null,
  abort: null,
  reader: null,
  subs: new Set<Subscriber>(),
  projector: null,
  turnChain: Promise.resolve(),
  snapshotWaiters: [],
  mcpWarned: false,
} as HubState)

// ── 订阅 ────────────────────────────────────────────────────

/**
 * 订阅事件。**按归属过滤，不按当前会话过滤** —— 前端 `useSSE` 自己会按
 * `properties.sessionID` 挑出当前会话的那些，这样就不必改前端去传 `session_id`。
 *
 * @param owned 这个用户拥有的会话 id 集合；null 表示不限（单用户部署）
 */
export function subscribe(owned: Set<string> | null, push: (ev: OcEvent) => void): () => void {
  const sub: Subscriber = { owned, push }
  state.subs.add(sub)
  return () => { state.subs.delete(sub) }
}

function fanout(sessionId: string, events: OcEvent[]): void {
  if (events.length === 0) return
  for (const sub of state.subs) {
    // 归属不明的连接一条都不给 —— 宁可界面不动，也不把别人的对话推过去
    if (sub.owned && !sub.owned.has(sessionId)) continue
    for (const ev of events) {
      try {
        sub.push(ev)
      } catch {
        // 浏览器那头已经断了 —— 下一次 push 会被 route 层清掉，这里不打断其他订阅者
      }
    }
  }
}

/** 往当前会话的订阅者插一条可见提示（不是模型说的话，用引用块标出来）。 */
function notice(sessionId: string, text: string): void {
  const p = state.projector
  if (p && p.sessionId === sessionId && !p.finished) {
    fanout(sessionId, p.project({ type: 'warning', message: text }))
  }
}

// ── 上游 `/live` 连接 ───────────────────────────────────────

function closeUpstream(): void {
  if (state.abort) {
    state.abort.abort()
    state.abort = null
  }
  state.bound = null
  state.reader = null
}

async function pumpLive(res: Response, signal: AbortSignal): Promise<void> {
  const reader = res.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  let buf = ''
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).trimEnd()
        buf = buf.slice(idx + 1)
        if (!line.startsWith('data:')) continue     // `: ping` / `: bye` 心跳
        const raw = line.slice(5).trim()
        if (!raw) continue
        let ev: any
        try { ev = JSON.parse(raw) } catch { continue }
        handleLiveEvent(ev)
      }
    }
  } catch (e: any) {
    if (!signal.aborted) console.error('[atomcode] /live 流中断：', String(e?.cause || e))
  } finally {
    try { reader.releaseLock() } catch { /* 已经释放过 */ }
  }
}

function handleLiveEvent(ev: any): void {
  const t = ev?.type

  if (t === 'snapshot') {
    const waiters = state.snapshotWaiters
    state.snapshotWaiters = []
    for (const w of waiters) w()
    return
  }

  // 审批类事件由 hub 自己应答，不进投影（设计文档 §8）
  if (t === 'permission_request') { void answerPermission(ev); return }
  if (t === 'user_input_request') { void answerUserInput(ev); return }
  if (t === 'policy_intervention') { void answerPolicy(ev); return }

  const p = state.projector
  if (!p) return
  const sid = String(ev?.session_id || p.sessionId)
  if (sid !== p.sessionId) return
  fanout(p.sessionId, p.project(ev))
}

// ── 审批：一律自动拒绝 + 可见提示（设计文档 §8.2）──────────────

/**
 * 从一条 `permission_request` 算出「回给 daemon 什么」和「给用户看什么」。
 *
 * 抽成纯函数只为一件事：这条路径**在本发行版里是残余情形**
 * （`.mcp.json` 全配了 autoApprove，工作区外的动作 guard.py 先 deny 了），
 * 端到端很难稳定触发，所以它得能单测。见设计文档 §8.2。
 */
export function permissionDenyPlan(ev: any, httpStatus?: number) {
  const tool = String(ev?.tool_name || '未知工具')
  const reason = String(ev?.reason || '').trim()
  const body: Record<string, any> = { decision: 'deny' }
  if (ev?.tool_name) body.tool_name = ev.tool_name
  const failed = httpStatus != null && !(httpStatus >= 200 && httpStatus < 300)
  return {
    body,
    tool,
    callId: String(ev?.call_id || ''),
    output:
      `已自动拒绝：${tool}\n` +
      (reason ? `原因：${reason}\n` : '') +
      '研究会话默认不执行需要人工批准的动作。要取数请用数据工具；' +
      '确实需要这个操作的话，请在本机部署里放开它。' +
      (failed ? `\n（回复 daemon 失败：HTTP ${httpStatus}）` : ''),
  }
}

async function answerPermission(ev: any): Promise<void> {
  const pre = permissionDenyPlan(ev)
  const r = await daemonFetch('POST', '/live/permission', pre.body, 15_000)
  const plan = permissionDenyPlan(ev, r.status)
  const p = state.projector
  if (!p || p.finished) return
  const callId = plan.callId || `perm_${Date.now()}`
  // 画成一张 error 状态的工具卡：用户看得见「哪个工具被拒了、为什么」
  fanout(p.sessionId, [
    ...p.project({ type: 'tool_start', id: callId, name: plan.tool, arguments: ev?.arguments }),
    ...p.project({
      type: 'tool_result',
      id: callId,
      name: plan.tool,
      success: false,
      output: plan.output,
    }),
  ])
}

async function answerUserInput(ev: any): Promise<void> {
  // 本发行版把这个工具关掉了（ATOMCODE_REQUEST_USER_INPUT=0，M2 §2.4），
  // 走到这里说明上游换了实现。回一个空响应，别把回合挂住。
  await daemonFetch('POST', '/live/user-input', {
    request_id: ev?.request_id,
    session_id: ev?.session_id,
    response: {},
  }, 15_000)
  const p = state.projector
  if (p && !p.finished) notice(p.sessionId, '模型发起了一次结构化提问，本发行版不支持，已跳过。')
}

async function answerPolicy(ev: any): Promise<void> {
  await daemonFetch('POST', '/live/policy-intervention', {
    intervention_id: ev?.intervention_id,
    decision: 'deny',
  }, 15_000)
  const p = state.projector
  if (p && !p.finished) {
    notice(p.sessionId, `安全策略介入（${String(ev?.code || '未知')}），已拒绝。`)
  }
}

// ── 绑定会话 ────────────────────────────────────────────────

async function waitSnapshot(ms: number): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      resolve(false)
    }, ms)
    state.snapshotWaiters.push(() => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(true)
    })
  })
}

/** MCP 第二道保险：上游 `wait_mcp_ready` 有 30 秒上限，2 核机器高负载时可能没等满。 */
async function ensureMcp(sessionId: string): Promise<void> {
  let st = await mcpStatus()
  if (mcpAllConnected(st)) return
  for (let round = 0; round < 3; round++) {
    await daemonFetch('POST', '/mcp/reload', {}, 30_000)
    const deadline = Date.now() + 60_000
    while (Date.now() < deadline) {
      st = await mcpStatus()
      if (!(st.servers || []).some((s) => s.status === 'connecting')) break
      await new Promise((r) => setTimeout(r, 2000))
    }
    if (mcpAllConnected(st)) return
  }
  const bad = (st.servers || []).filter((s) => s.status !== 'connected').map((s) => s.name)
  // **不谎报健康**：数据源少了就说少了，让用户知道这一轮的答案可能缺数据
  console.warn('[atomcode] MCP 未全部连上：', bad.join(', '))
  notice(sessionId, `数据源未全部就绪（${bad.join('、') || '未知'}），这一轮可能取不到部分数据。`)
}

async function bind(sessionId: string): Promise<void> {
  if (state.bound === sessionId && state.abort) return
  closeUpstream()
  const abort = new AbortController()
  state.abort = abort
  const snapshot = waitSnapshot(60_000)
  let res: Response
  // ── 「cannot replace an active live runtime」要能恢复（M4 实测撞到）──────────
  //
  // 上游 `native_live.rs:394-401`：换绑 session 时，如果**当前那个 runtime**
  // 处于 InTurn / WaitingApproval / Reconfiguring，就整个拒绝，
  // HTTP 404 + body `{"error":"cannot replace an active live runtime"}`。
  //
  // 两种情况会撞上：
  //   · 真的有一轮在跑（我们自己这个进程里的 —— state.projector 还没 finished）；
  //   · **孤儿 runtime**：上一个消费者（调试脚本、被 kill 的进程、崩掉的容器）
  //     把 daemon 留在 InTurn 上就走了。这时整个网页会一直报
  //     `upstream_unreachable`，谁也用不了 —— M4 回归里就是这么被卡住的。
  //
  // 处理：自己这边确实在跑 → 明说在忙（决策 10 的语义）；否则判定是孤儿，
  // `POST /live/stop` 收掉它再重试。单工作区形态下（已拍板决策 5）网页是唯一
  // 合法消费者，所以"孤儿"这个判断是站得住的。
  for (let attempt = 0; ; attempt += 1) {
    try {
      res = await openLiveStream(sessionId, abort.signal)
    } catch (e: any) {
      state.abort = null
      throw new Error(`连不上 daemon 的 /live：${String(e?.cause || e)}`)
    }
    if (res.ok && res.body) break
    const body = await res.text().catch(() => '')
    const occupied = res.status === 404 && /cannot replace an active live runtime/.test(body)
    if (!occupied || attempt >= 3) {
      state.abort = null
      if (occupied) {
        throw new Error('daemon 的 /live 被另一个还在生成的会话占着，收不回来（试了 4 次）。'
          + '本发行版同一时刻只跑一个回合；等它结束，或在那个会话里点停止。')
      }
      throw new Error(`daemon /live 返回 HTTP ${res.status}${body ? `：${body.slice(0, 200)}` : ''}`)
    }
    const mine = !!state.projector && !state.projector.finished
    if (mine) {
      state.abort = null
      throw new Error('另一个会话正在生成，暂时切不过去 —— 等它结束再试。')
    }
    console.warn('[atomcode] /live 被一个孤儿 runtime 占着（没人在等它），POST /live/stop 收掉重试')
    await daemonFetch('POST', '/live/stop', undefined, 15_000)
    await new Promise((r) => setTimeout(r, 800 * (attempt + 1)))
  }
  state.reader = pumpLive(res, abort.signal)
  if (!(await snapshot)) {
    closeUpstream()
    throw new Error('60 秒内没收到 /live 的 snapshot，会话没绑上')
  }
  state.bound = sessionId
  await ensureMcp(sessionId)
}

// ── 回合 ────────────────────────────────────────────────────


/**
 * 出口语言守卫（待办池 P1-21）——回合结束、正文终态已定时跑一遍。
 *
 * 为什么放在这里而不是 hook：AtomCode 的 8 个 hook 事件里没有「助手正文写完」
 * 这个点（`Stop` 拿不到正文），而 BFF 本来就坐在 SSE 流上。
 *
 * 逐个文本 part 送检而不是整段送检：一轮里文本被工具调用切成好几段，
 * 整段送检再整段替换会把「文字—工具卡—文字」的版式压成一段。
 *
 * **任何失败都不影响这一轮**：guardText 自己吞异常返回原文，这里再包一层。
 */
async function applyLangGuard(sessionId: string, p: TurnProjector): Promise<void> {
  if (!langGuardEnabled()) return
  try {
    for (const part of [...p.textParts]) {
      const r = await guardText(part.text)
      if (r.changed) {
        fanout(sessionId, [p.replaceTextPart(part.id, r.text)])
        console.warn(`[hca-lang] 出口守卫改写了 ${part.id}（${part.text.length} → ${r.text.length} 字）`)
      }
    }
  } catch (e) {
    console.warn('[hca-lang] 出口守卫整体失败，原文照常：', (e as Error)?.message)
  }
}

export interface TurnResult {
  ok: boolean
  messageId: string
  stopReason: string | null
  text: string
  error?: string
}

const TURN_TIMEOUT_MS = Number(process.env.ATOMCODE_TURN_TIMEOUT_MS || 570_000)

/**
 * 跑一个回合，**等到整轮结束才 resolve**（与 opencode 的 `POST /session/{id}/message` 对齐）。
 * 同一时刻只跑一个回合 —— `/live` 是 daemon 全局单例（设计文档 §9 限制 1）。
 */
export function runTurn(sessionId: string, promptText: string, displayText: string): Promise<TurnResult> {
  // ── 已拍板决策 10（总控规则，M3 U-11 的答复）：接受「同一时刻只能一个人在生成」，
  //    但**界面在忙时要给出明确提示**。
  //
  // 回合是串在 state.turnChain 上的，所以第二个人发消息时原来的表现是「什么都不动」——
  // 用户不知道自己在排队，只会以为坏了。这里在入队的那一刻就把 projector 建好、
  // 先把用户消息和一条排队提示推给他，等前一轮结束再真正开跑。
  //
  // projector 提到 run 外面是为了**共用同一个 assistant 消息 id**：
  // 要是排队提示用一个新 projector，界面上会出现两条助手消息。
  const startedAt = Date.now()
  const projector = new TurnProjector({ sessionId, startedAt })
  const busyElsewhere = !!state.projector && !state.projector.finished
  let begun = false
  if (busyElsewhere) {
    const other = state.projector!.sessionId === sessionId ? '这个对话' : '另一个对话'
    fanout(sessionId, projector.begin(displayText))
    fanout(sessionId, projector.project({
      type: 'warning',
      message: `${other}正在生成 —— 本发行版同一时刻只跑一个回合（daemon 的 /live 是全局单例）。` +
               `你这条已排队，前一轮结束后会自动开始，不用重发。`,
    }))
    begun = true
  }

  const run = async (): Promise<TurnResult> => {
    state.projector = projector
    try {
      await bind(sessionId)
    } catch (e: any) {
      state.projector = null
      return { ok: false, messageId: projector.assistantMsgId, stopReason: null, text: '', error: String(e?.message || e) }
    }

    if (!begun) fanout(sessionId, projector.begin(displayText))

    const sent = await daemonFetch<{ accepted?: boolean; disposition?: string }>(
      'POST', '/live/message', { message: promptText, session_id: sessionId }, 60_000,
    )
    if (!sent.ok || sent.data?.accepted === false) {
      const why = sent.ok ? `daemon 拒绝了这条消息：${sent.text.slice(0, 200)}` : `HTTP ${sent.status}：${sent.text.slice(0, 200)}`
      fanout(sessionId, projector.project({ type: 'error', message: why }))
      fanout(sessionId, projector.finish('rejected'))
      state.projector = null
      return { ok: false, messageId: projector.assistantMsgId, stopReason: 'rejected', text: '', error: why }
    }

    // ⚠️ 超时要从**真正开跑**算起，不是从入队算起（决策 10 把 startedAt 提前到了入队那一刻，
    // 直接用它会让排在后面的回合把超时预算耗在等待上）。
    const deadline = Date.now() + TURN_TIMEOUT_MS
    while (!projector.finished && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 120))
    }
    if (!projector.finished) {
      // 超时：把界面从生成态放出来，并如实说明。不假装它结束了。
      await daemonFetch('POST', '/live/stop', undefined, 15_000)
      fanout(sessionId, projector.project({
        type: 'warning',
        message: `本轮超过 ${Math.round(TURN_TIMEOUT_MS / 1000)} 秒仍未结束，已请求停止。`,
      }))
      fanout(sessionId, projector.finish('timeout'))
      state.projector = null
      return { ok: false, messageId: projector.assistantMsgId, stopReason: 'timeout', text: projector.assistantText, error: 'turn_timeout' }
    }

    state.projector = null
    // 正文终态已定 → 出口语言守卫（待办池 P1-21）。放在 state.projector 清掉之后，
    // 这样守卫万一慢一点也不会把「正在生成」的状态多挂几秒。
    await applyLangGuard(sessionId, projector)
    return {
      ok: !projector.error,
      messageId: projector.assistantMsgId,
      stopReason: projector.stopReason,
      text: projector.assistantText,
      error: projector.error?.message,
    }
  }

  const queued = state.turnChain.then(run, run)
  // 串成链，但不让一次失败把后面的回合全带崩
  state.turnChain = queued.then(() => undefined, () => undefined)
  return queued
}

/** 停止生成。只停「当前绑定的那个会话」的那一轮 —— 回合是串行的，不会误停别人。 */
export async function stopTurn(sessionId: string): Promise<{ ok: boolean; accepted: boolean }> {
  const p = state.projector
  if (!p || p.sessionId !== sessionId) return { ok: true, accepted: false }
  p.markAborted()
  const r = await daemonFetch<{ accepted?: boolean }>('POST', '/live/stop', undefined, 15_000)
  return { ok: r.ok, accepted: !!r.data?.accepted }
}

/** 这个会话现在是不是正在生成（给 route 层判断「能不能删 / 能不能切」用）。 */
export function isBusy(sessionId?: string): boolean {
  const p = state.projector
  if (!p || p.finished) return false
  return sessionId ? p.sessionId === sessionId : true
}

/** 会话被删掉时把绑定放掉，否则下次 bind 会连到一个已经不存在的会话上。 */
export function forgetSession(sessionId: string): void {
  if (state.bound === sessionId) closeUpstream()
}
