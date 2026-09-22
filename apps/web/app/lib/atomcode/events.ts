/**
 * AtomCode `/live` 事件 → opencode 事件形状（前端 `reduceEvents` 认得的那三种）。
 *
 * 这个文件**故意不 import 任何东西**：它是纯函数 + 一个状态机，
 * 既要能被 Next 的 BFF 用，也要能被 `node --test` 直接跑（见 apps/web/tests/）。
 * 单测的夹具是 M0 / M2 抓到的**真实 SSE 原文**，不是手写的假事件。
 *
 * 映射依据见 docs/web-adapter-design.md §3.5。要点三条：
 *
 *   1. **先建 part 再发 delta**。前端 `reduceEvents` 对 `message.part.delta` 的处理是
 *      「按 partID 找不到就 continue」，所以第一段文字必须先发一条带空 text 的
 *      `message.part.updated` 把 part 建出来。
 *   2. **每次工具调用之后另起一个 text part**。`reportDetect.getAssistantText()` 取的是
 *      「最后一个 tool part 之后」的文本；把整轮文字拼进同一个 part 的话，
 *      工具调用前那句「正在查行情…」会被算进最终报告里。
 *   3. **工具名要归一**。AtomCode 是 `mcp__{server}__{tool}`，前端富卡片认的是
 *      `{server}_{tool}`（opencode 的命名）。不归一的话 7 张富卡片全部退化成通用卡。
 */

export type LiveEvent = Record<string, any>

export interface OcEvent {
  type: string
  properties: Record<string, any>
}

/** `mcp__watchlist__stock_quickview` → `watchlist_stock_quickview`；其余原样。 */
export function normalizeToolName(name: string): string {
  const m = /^mcp__([^_].*?)__(.+)$/.exec(name || '')
  return m ? `${m[1]}_${m[2]}` : (name || '')
}

/**
 * 剥掉注入块，还原用户真正打的字。
 *
 * 三个来源：
 *   · `<hca-context>` —— `context.sh`（UserPromptSubmit hook）注入的日期 / 交易时段 / 资产清单
 *   · `<hunter-profile>` —— BFF 注入的用户画像（opencode 侧走 `body.system`，
 *      而 `/live/message` **没有 system 字段**，只能进正文，见设计文档 §5）
 *   · `<hunter-skill>` —— BFF 注入的「本轮必须用这个技能」
 *
 * 不剥的话，用户刷新页面看到的自己那句话后面会拖着一大段系统文本。
 */
export function stripInjected(text: string): string {
  if (!text) return ''
  let out = text
  for (const tag of ['hca-context', 'hunter-profile', 'hunter-skill']) {
    out = out.replace(new RegExp(`\\n*<${tag}>[\\s\\S]*?</${tag}>\\n*`, 'g'), '')
  }
  return out.trim()
}

function safeParseArgs(raw: any): any {
  if (raw == null) return {}
  if (typeof raw === 'object') return raw
  if (typeof raw !== 'string') return raw
  try {
    return JSON.parse(raw)
  } catch {
    // 解析不了就把原文给前端 —— `fmtArgs` 对字符串也能显示，
    // 硬塞一个 {} 会让用户看到「参数：{}」这种假信息
    return raw
  }
}

export interface TurnIds {
  sessionId: string
  /** 回合起点毫秒，用来生成同一回合内稳定的 message / part id */
  startedAt: number
}

interface ToolSlot {
  partId: string
  tool: string
  input: any
  start: number
}

interface ArtifactSlot {
  kind: string
  language: string
  buf: string
}

/**
 * 一个回合的投影状态机。**一个回合一个实例**，不要跨回合复用。
 */
export class TurnProjector {
  readonly sessionId: string
  readonly assistantMsgId: string
  readonly userMsgId: string

  private textSeq = 0
  private openTextPartId: string | null = null
  private text = ''
  private tools = new Map<string, ToolSlot>()
  private artifacts = new Map<string, ArtifactSlot>()
  private done = false
  private stop: string | null = null
  private failed: { name: string; message: string } | null = null
  private textPartTexts: Array<{ id: string; text: string }> = []
  private toolNames: string[] = []
  private aborted = false

  constructor(ids: TurnIds) {
    this.sessionId = ids.sessionId
    this.assistantMsgId = `asst_${ids.sessionId}_${ids.startedAt}`
    this.userMsgId = `user_${ids.sessionId}_${ids.startedAt}`
  }

  get finished(): boolean { return this.done }
  get stopReason(): string | null { return this.stop }
  get assistantText(): string { return this.text }
  get error(): { name: string; message: string } | null { return this.failed }

  /** 标记「这一轮是用户主动停的」—— 终态就不画错误卡（见 chat/lib/modelError.ts）。 */
  markAborted(): void { this.aborted = true }

  // ── 事件构造 ────────────────────────────────────────────────

  private msgUpdated(info: Record<string, any>): OcEvent {
    return { type: 'message.updated', properties: { sessionID: this.sessionId, info } }
  }

  private partUpdated(part: Record<string, any>): OcEvent {
    return {
      type: 'message.part.updated',
      properties: {
        sessionID: this.sessionId,
        messageID: this.assistantMsgId,
        part: { ...part, messageID: this.assistantMsgId, sessionID: this.sessionId },
      },
    }
  }

  private delta(partID: string, field: string, delta: string): OcEvent {
    return {
      type: 'message.part.delta',
      properties: {
        sessionID: this.sessionId,
        messageID: this.assistantMsgId,
        partID,
        field,
        delta,
      },
    }
  }

  // ── 文本 part 的开合 ────────────────────────────────────────

  private ensureTextPart(out: OcEvent[]): string {
    if (this.openTextPartId) return this.openTextPartId
    const id = `${this.assistantMsgId}:t${this.textSeq}`
    this.openTextPartId = id
    this.textPartTexts.push({ id, text: '' })
    out.push(this.partUpdated({ id, type: 'text', text: '' }))
    return id
  }

  private closeTextPart(): void {
    if (this.openTextPartId) {
      this.openTextPartId = null
      this.textSeq += 1
    }
  }

  private appendText(out: OcEvent[], chunk: string): void {
    if (!chunk) return
    const id = this.ensureTextPart(out)
    this.text += chunk
    const slot = this.textPartTexts[this.textPartTexts.length - 1]
    if (slot && slot.id === id) slot.text += chunk
    out.push(this.delta(id, 'text', chunk))
  }

  // ── 出口语言守卫要用的两个口子（待办池 P1-21）────────────────

  /** 这一轮调过的工具**原始名**（`mcp__a__b` 未归一，按出现顺序含重复）。P0-10 的判据要用。 */
  get toolsUsed(): string[] {
    return this.toolNames
  }

  /** 这一轮发出去的所有文本 part 及其终态正文（按出现顺序）。 */
  get textParts(): ReadonlyArray<{ id: string; text: string }> {
    return this.textPartTexts
  }

  /**
   * 把某个文本 part 的正文整段换掉，返回给前端的替换事件。
   *
   * 前端按 part id 做 upsert（`message.part.updated`），所以重发同一个 id
   * 就是"改写这一段"，**不需要前端改一行**。`delta` 是追加语义、不能用来改写。
   */
  replaceTextPart(id: string, text: string): OcEvent {
    const slot = this.textPartTexts.find((p) => p.id === id)
    if (slot) {
      this.text = this.text.replace(slot.text, text)
      slot.text = text
    }
    return this.partUpdated({ id, type: 'text', text })
  }

  // ── 对外 ────────────────────────────────────────────────────

  /**
   * 回合开始：把用户那条真消息与一个空的助手气泡发出去。
   *
   * `userText` 必须是**用户实际打的字**（不含任何注入块）——前端靠内容逐字匹配
   * 来清掉乐观占位气泡 `tmp_user_*`，差一个字就会出现两条一样的用户消息。
   */
  begin(userText: string): OcEvent[] {
    const now = Date.now()
    return [
      this.msgUpdated({
        id: this.userMsgId,
        sessionID: this.sessionId,
        role: 'user',
        time: { created: now },
        parts: [{
          id: `${this.userMsgId}:t0`,
          type: 'text',
          text: userText,
          messageID: this.userMsgId,
          sessionID: this.sessionId,
        }],
      }),
      this.msgUpdated({
        id: this.assistantMsgId,
        sessionID: this.sessionId,
        role: 'assistant',
        time: { created: now + 1 },
        parts: [],
      }),
    ]
  }

  /** 投影一条 `/live` 事件。返回 0..n 条前端事件。 */
  project(ev: LiveEvent): OcEvent[] {
    const out: OcEvent[] = []
    if (!ev || typeof ev !== 'object') return out
    switch (ev.type) {
      case 'text':
        this.appendText(out, String(ev.content ?? ev.text ?? ''))
        break

      case 'reasoning':
        // 接 hunter 网关时恒为空（M0 §11.6）。有值也不画 —— 前端没有思考过程 UI，
        // 混进正文会被当成模型的回答。
        break

      case 'tool_start': {
        this.closeTextPart()
        const callId = String(ev.id ?? '')
        const rawName = String(ev.name ?? '')
        // **记原始名**（`mcp__<服务>__<工具>`）而不是归一后的：P0-10 的判据要认
        // `mcp__` 前缀，而 normalizeToolName 正好把这个前缀剥掉了。
        this.toolNames.push(rawName)
        const tool = normalizeToolName(rawName)
        const input = safeParseArgs(ev.arguments)
        const partId = `${this.assistantMsgId}:call:${callId}`
        const start = Date.now()
        this.tools.set(callId, { partId, tool, input, start })
        out.push(this.partUpdated({
          id: partId,
          type: 'tool',
          tool,
          callID: callId,
          state: { status: 'running', input, time: { start } },
        }))
        break
      }

      case 'tool_output':
      case 'tool_progress': {
        // `/live` 上本轮一次没抓到过，按 `/chat` 的字段实现（见设计文档 §3.5）。
        const callId = String(ev.id ?? '')
        const slot = this.tools.get(callId)
        const chunk = String(ev.chunk ?? ev.progress ?? '')
        if (slot && chunk) out.push(this.delta(slot.partId, 'output', chunk))
        break
      }

      case 'tool_result': {
        const callId = String(ev.id ?? '')
        const slot = this.tools.get(callId)
        const tool = slot?.tool ?? normalizeToolName(String(ev.name ?? ''))
        const partId = slot?.partId ?? `${this.assistantMsgId}:call:${callId}`
        const ok = ev.success !== false
        const end = Date.now()
        const start = slot?.start ?? (typeof ev.duration_ms === 'number' ? end - ev.duration_ms : end)
        out.push(this.partUpdated({
          id: partId,
          type: 'tool',
          tool,
          callID: callId,
          state: {
            status: ok ? 'completed' : 'error',
            input: slot?.input ?? {},
            output: typeof ev.output === 'string' ? ev.output : JSON.stringify(ev.output ?? null),
            time: { start, end },
          },
        }))
        break
      }

      case 'artifact_start':
        this.artifacts.set(String(ev.id ?? ''), {
          kind: String(ev.artifact_type ?? 'code'),
          language: String(ev.language ?? ''),
          buf: '',
        })
        break

      case 'artifact_content': {
        const slot = this.artifacts.get(String(ev.id ?? ''))
        if (slot) slot.buf += String(ev.content ?? '')
        break
      }

      case 'artifact_end': {
        const key = String(ev.id ?? '')
        const slot = this.artifacts.get(key)
        this.artifacts.delete(key)
        if (!slot) break
        const body = slot.buf.trim()
        if (!body) break
        // artifact 在 `/chat` 上常常是正文或工具输出的**副本**（M0 t2-bash.sse 就是）。
        // 已经逐字出现过就跳过，否则用户会看到同一段内容出现两次。
        if (this.text.includes(body)) break
        this.closeTextPart()
        const fence = slot.kind === 'markdown'
          ? ''
          : '```' + (slot.kind === 'html' ? 'html' : slot.language || '')
        const block = fence ? `\n\n${fence}\n${body}\n\`\`\`\n` : `\n\n${body}\n`
        this.appendText(out, block)
        this.closeTextPart()
        break
      }

      case 'warning': {
        const msg = String(ev.message ?? '').trim()
        if (msg) {
          this.closeTextPart()
          this.appendText(out, `\n\n> ⚠️ ${msg}\n\n`)
          this.closeTextPart()
        }
        break
      }

      case 'error': {
        const msg = String(ev.message ?? '').trim() || '模型调用失败'
        this.failed = { name: 'APIError', message: msg }
        break
      }

      case 'tokens':
        out.push({
          type: 'session.updated',
          properties: {
            sessionID: this.sessionId,
            info: {
              id: this.sessionId,
              tokens: {
                input: Number(ev.prompt ?? 0),
                output: Number(ev.completion ?? 0),
                reasoning: 0,
                cache: { read: 0, write: 0 },
              },
            },
          },
        })
        break

      case 'state':
        if (ev.running === false) {
          this.stop = ev.stop_reason == null ? null : String(ev.stop_reason)
          out.push(...this.finish())
        }
        break

      // snapshot / user / mode / reasoning_effort / steered / session_switched /
      // session_renamed / persistence_warning / rate_limited：前端没有对应 UI，丢弃。
      // permission_request / user_input_request / policy_intervention 由 LiveHub
      // 自己应答（见设计文档 §8），不走投影。
      default:
        break
    }
    return out
  }

  /**
   * 收尾。正常路径由 `state{running:false}` 触发；超时 / 上游断连时由 LiveHub 强制调用。
   * 幂等——重复调用只产出一次终态。
   */
  finish(reason?: string): OcEvent[] {
    if (this.done) return []
    this.done = true
    if (reason && !this.stop) this.stop = reason
    this.closeTextPart()
    const info: Record<string, any> = {
      id: this.assistantMsgId,
      sessionID: this.sessionId,
      role: 'assistant',
      time: { updated: Date.now() },
    }
    if (this.aborted) {
      // 用户点了停止 —— 不是故障。modelError.describeModelError 对这个 name 返回 null，
      // 界面上不画错误卡。
      info.error = { name: 'MessageAbortedError', data: {} }
    } else if (this.failed) {
      info.error = { name: this.failed.name, data: { message: this.failed.message } }
    } else if (this.stop && this.stop !== 'stopped') {
      // `stop_reason` 分不出「做完了」和「做了一半」（待办池 P0-7），所以这里
      // **只对明确的非正常终止**挂错误，不去猜 `stopped` 到底完没完。
      info.error = {
        name: 'APIError',
        data: { message: `本轮以 ${this.stop} 结束，内容可能不完整。` },
      }
    }
    return [this.msgUpdated(info)]
  }
}
