/**
 * AtomCode 会话详情 → 前端 Message[]（刷新页面后恢复历史）。
 *
 * 同样**不 import 任何东西**，方便 `node --test` 直接跑；夹具是测试机上一条
 * 真实会话的 JSON 原文（apps/web/tests/fixtures/session-detail.json）。
 *
 * 源结构（`GET /projects/{hash}/sessions/{id}`，本轮实测，见设计文档 §2.4）：
 *
 *   messages: [
 *     {role:'system',    content}                                  ← 丢弃
 *     {role:'user',      content, created_at}                      ← 一条 user 消息
 *     {role:'assistant', content, tool_calls:[{id,name,arguments}], created_at}
 *     {role:'tool',      content, tool_result:{call_id,success}, created_at}  ← 回填上面那条
 *     {role:'assistant', content, created_at}                      ← 追加到同一条 assistant
 *   ]
 *
 * 一个「问 → 调工具 → 答」的回合投影成 **2 条消息**（1 user + 1 assistant），与 opencode 一致。
 */

import { normalizeToolName, stripInjected } from './events.ts'
import { cachedFix } from './lang.ts'

export interface HistoryMessage {
  id: string
  sessionID: string
  role: 'user' | 'assistant'
  parts: any[]
  time: { created: number; updated?: number }
}

interface RawMsg {
  role?: string
  content?: any
  created_at?: number
  tool_calls?: Array<{ id?: string; name?: string; arguments?: any }>
  tool_result?: { call_id?: string; success?: boolean }
}

function asText(content: any): string {
  if (typeof content === 'string') return content
  if (content == null) return ''
  // 上游偶尔把 content 写成分段数组（多模态），取其中的文本段
  if (Array.isArray(content)) {
    return content
      .map((c) => (typeof c === 'string' ? c : (c?.text ?? '')))
      .filter(Boolean)
      .join('')
  }
  return String(content)
}

function parseArgs(raw: any): any {
  if (raw == null) return {}
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(String(raw))
  } catch {
    return raw
  }
}

/**
 * @param sessionId 会话 id
 * @param messages  daemon 返回的 `messages` 数组
 */
export function projectHistory(sessionId: string, messages: RawMsg[] | null | undefined): HistoryMessage[] {
  const out: HistoryMessage[] = []
  if (!Array.isArray(messages)) return out

  let current: HistoryMessage | null = null       // 当前正在拼的 assistant 消息
  let textSeq = 0
  let seq = 0
  const byCall = new Map<string, any>()           // callID → tool part（供 role:'tool' 回填）

  const newId = (role: string, ts: number) => `hist_${role}_${sessionId}_${ts}_${seq++}`

  for (const m of messages) {
    const role = String(m?.role || '')
    const ts = Number(m?.created_at) || (out.length ? (out[out.length - 1].time.created + 1) : Date.now())

    if (role === 'system') continue

    if (role === 'user') {
      current = null
      byCall.clear()
      const text = stripInjected(asText(m.content))
      if (!text) continue
      const id = newId('user', ts)
      out.push({
        id,
        sessionID: sessionId,
        role: 'user',
        time: { created: ts },
        parts: [{ id: `${id}:t0`, type: 'text', text, messageID: id, sessionID: sessionId }],
      })
      continue
    }

    if (role === 'assistant') {
      if (!current) {
        const id = newId('asst', ts)
        current = { id, sessionID: sessionId, role: 'assistant', time: { created: ts }, parts: [] }
        textSeq = 0
        out.push(current)
      }
      // 出口语言守卫在直播那一轮改写过的段落，刷新时也要拿到同一份
      // （待办池 P1-21）。这里**只查缓存不发请求** —— 历史投影是同步的，
      // 而且刷一次页面就对每段正文调一次 api 不划算。web 进程重启后缓存没了，
      // 历史会显示未修正的原文，这是已知限制（docs/web-adapter-design.md）。
      const raw = asText(m.content)
      const text = raw ? (cachedFix(raw) ?? raw) : raw
      if (text) {
        current.parts.push({
          id: `${current.id}:t${textSeq++}`,
          type: 'text',
          text,
          messageID: current.id,
          sessionID: sessionId,
        })
      }
      for (const call of m.tool_calls || []) {
        const callID = String(call?.id || '')
        if (!callID) continue
        const part = {
          id: `${current.id}:call:${callID}`,
          type: 'tool',
          tool: normalizeToolName(String(call?.name || '')),
          callID,
          messageID: current.id,
          sessionID: sessionId,
          // 历史里没有起止时刻，**不编**：`ToolCallCard` 取不到 time 就不显示耗时
          state: { status: 'completed', input: parseArgs(call?.arguments) },
        }
        current.parts.push(part)
        byCall.set(callID, part)
        // 工具调用之后另起 text part（与实时投影同一条规矩，见 events.ts 文件头第 2 点）
        textSeq += 1
      }
      continue
    }

    if (role === 'tool') {
      const callID = String(m.tool_result?.call_id || '')
      const part = byCall.get(callID)
      if (!part) continue
      const ok = m.tool_result?.success !== false
      part.state = {
        ...part.state,
        status: ok ? 'completed' : 'error',
        // `tool_result.summary` 是被截断的，完整输出在这条消息的 content 里
        output: asText(m.content),
      }
      continue
    }
  }

  // 一条 part 都没有的空消息不往界面上发（模型只发了工具调用、还没作答的半截回合）
  return out.filter((m) => m.parts.length > 0)
}
