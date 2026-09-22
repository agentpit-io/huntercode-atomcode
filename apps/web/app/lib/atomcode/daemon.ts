/**
 * AtomCode daemon 的 HTTP 客户端（只在 Next 服务端用）。
 *
 * 鉴权：daemon 强制 Bearer token（M0 §1.2 实测，无 token 一律 401）。token 由
 * daemon 的 entrypoint 抄到共享卷 `/run/hca/daemon-token`，web 容器只读挂载。
 * 走文件而不是环境变量，是因为 token 是 daemon **自己启动时生成**的，
 * compose 写不进去（见 deploy/Dockerfile.daemon 的 entrypoint）。
 */

import { Agent } from 'undici'

const DAEMON_URL = (process.env.ATOMCODE_DAEMON_URL || 'http://daemon:13456').replace(/\/$/, '')
const TOKEN_FILE = process.env.ATOMCODE_TOKEN_FILE || '/run/hca/daemon-token'
const TOKEN_ENV = (process.env.ATOMCODE_DAEMON_TOKEN || '').trim()

/** 与现有 opencode 分支同样的 10 分钟预算（单条消息可能跑很久）。 */
export const DAEMON_DISPATCHER = new Agent({
  headersTimeout: 600_000,
  bodyTimeout: 600_000,
  connectTimeout: 30_000,
})

let cachedToken = ''
let cachedAt = 0

/** token 文件可能在 daemon 重启后换一份，所以缓存 30 秒而不是永久。 */
export async function daemonToken(): Promise<string> {
  if (TOKEN_ENV) return TOKEN_ENV
  const now = Date.now()
  if (cachedToken && now - cachedAt < 30_000) return cachedToken
  try {
    const { readFile } = await import('node:fs/promises')
    const raw = (await readFile(TOKEN_FILE, 'utf-8')).trim()
    if (raw) {
      cachedToken = raw
      cachedAt = now
    }
  } catch (e) {
    console.error('[atomcode] 读不到 daemon token：', TOKEN_FILE, (e as Error).message)
  }
  return cachedToken
}

export interface DaemonResult<T = any> {
  ok: boolean
  status: number
  data: T | null
  text: string
}

export async function daemonFetch<T = any>(
  method: string,
  path: string,
  body?: unknown,
  timeoutMs = 60_000,
): Promise<DaemonResult<T>> {
  const token = await daemonToken()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${DAEMON_URL}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: controller.signal,
      dispatcher: DAEMON_DISPATCHER,
    } as any)
    const text = await res.text()
    let data: any = null
    if (text) {
      try { data = JSON.parse(text) } catch { data = null }
    }
    return { ok: res.ok, status: res.status, data, text }
  } catch (e: any) {
    const aborted = e?.name === 'AbortError'
    console.error(`[atomcode] ${method} ${path} ${aborted ? '超时' : '失败'}：`, String(e?.cause || e))
    return { ok: false, status: aborted ? 504 : 502, data: null, text: String(e?.cause || e) }
  } finally {
    clearTimeout(timer)
  }
}

/** 打开 `/live` 的 SSE 连接。返回原始 Response，读流的事在 LiveHub 里做。 */
export async function openLiveStream(sessionId: string | null, signal: AbortSignal): Promise<Response> {
  const token = await daemonToken()
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return fetch(`${DAEMON_URL}/live${qs}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    cache: 'no-store',
    signal,
    dispatcher: DAEMON_DISPATCHER,
  } as any)
}

// ── 工作目录 / project_hash ────────────────────────────────────
//
// 会话的增删改查全部挂在 `/projects/{hash}/sessions/...` 下，而 hash 由 daemon
// 按当前工作目录算。单工作区形态（总控已拍板决策 5）下它是常量，缓存 5 分钟即可。

let cachedHash = ''
let hashAt = 0

export async function projectHash(): Promise<string> {
  const now = Date.now()
  if (cachedHash && now - hashAt < 300_000) return cachedHash
  const r = await daemonFetch<{ project_hash?: string }>('GET', '/project', undefined, 15_000)
  const h = r.data?.project_hash || ''
  if (h) {
    cachedHash = h
    hashAt = now
  }
  return h
}

export interface McpStatus {
  servers: Array<{ name?: string; status?: string; tool_count?: number }>
  trusted?: boolean
  blocked?: string[]
}

export async function mcpStatus(): Promise<McpStatus> {
  const r = await daemonFetch<McpStatus>('GET', '/mcp/status', undefined, 15_000)
  return r.data || { servers: [] }
}

/** 全部 connected 才算齐。`servers` 为空也算不齐 —— 空数组通常意味着项目没被信任。 */
export function mcpAllConnected(st: McpStatus): boolean {
  const list = st.servers || []
  return list.length > 0 && list.every((s) => s.status === 'connected')
}
