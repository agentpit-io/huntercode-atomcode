'use client'
// 第 5 步 · 完成(设计方案 4.7)
//
// 1. POST /setup/apply —— 热推给 opencode,**不重启容器**(R0 实测端到端 7.4~7.9 秒)
// 2. 轮询 /setup/engine-ready,进度条上限 60 秒
// 3. 超时不假装成功:显示降级提示与排错入口
// 4. 3 个示例问题 → /chat?q=…&send=0(**填进输入框,不自动发送**)
// 5. 写 setup.completed_at;多用户模式引导创建管理员账号
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, PartyPopper, AlertTriangle, UserPlus } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { applyLlm, engineReady, complete, isFail, type SetupStatus, type LlmStatus } from '../lib/setupClient'
import { Box, Tag, btn } from '../lib/ui'

const MAX_WAIT_MS = 60_000
const POLL_MS = 2_000

const EXAMPLES = [
  '贵州茅台现在多少钱?',
  '帮我看看 600519 最近的走势',
  '今天 A 股有什么值得注意的?',
]

type Phase = 'idle' | 'applying' | 'waiting' | 'ready' | 'timeout' | 'failed'

export default function Done({ status, onBack }: { status: SetupStatus; onBack: () => void }) {
  const router = useRouter()
  const llm = status.llm as LlmStatus
  const [phase, setPhase] = useState<Phase>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [reason, setReason] = useState('')
  const [adminExists, setAdminExists] = useState<boolean | null>(null)
  const stop = useRef(false)

  useEffect(() => () => { stop.current = true }, [])

  // 多用户模式要知道有没有管理员账号 —— 没有的话最后一步引导注册
  useEffect(() => {
    if (status.single_user) return
    void (async () => {
      try {
        const r = await fetch('/api/auth/status', { cache: 'no-store' })
        const d = await r.json()
        setAdminExists(d?.admin_exists === true)
      } catch { setAdminExists(null) }
    })()
  }, [status.single_user])

  const run = useCallback(async () => {
    stop.current = false
    setPhase('applying'); setReason(''); setElapsed(0)

    const a = await applyLlm()
    if (isFail(a)) { setPhase('failed'); setReason(a.message); return }
    if (!a.ok) { setPhase('failed'); setReason(a.reason || '热生效失败'); return }

    setPhase('waiting')
    const t0 = Date.now()
    // 轮询到就绪或超时。**不在事件循环里死等** —— 每轮之间让出去,
    // 用户随时能看到已经等了多久。
    while (!stop.current) {
      const waited = Date.now() - t0
      setElapsed(waited)
      if (waited > MAX_WAIT_MS) { setPhase('timeout'); return }
      const r = await engineReady()
      if (!isFail(r) && r.ready) {
        // 把刚配好的模型设成对话页当前选中的那个。
        //
        // **不能指望对话页自己发现**:热生效走 opencode 的 `updateGlobal`(mergeDeep),
        // 旧模型名会一直留在 /config/providers 的清单里,而对话页的
        // `resolveModelKey` 判「还在清单里就算有效」—— 于是浏览器继续拿着
        // `hunter-unconfigured` 发消息,用户看到的是「向导说配好了、第一条消息却回
        // 大模型尚未配置」。2026-09-18 M2 实测踩到,两边一起修
        // (这里主动写、resolveModelKey 排掉占位名兜底)。
        if (r.current) {
          try { localStorage.setItem('hunter_chat_model', r.current) } catch { /* 隐私模式 */ }
        }
        setPhase('ready')
        await complete(false)
        return
      }
      if (!isFail(r)) setReason(r.reason || '')
      await new Promise((res) => setTimeout(res, POLL_MS))
    }
  }, [])

  useEffect(() => { void run() }, [run])

  const toChat = (q?: string) => {
    // send=0 = 只填进输入框,不自动发送(chat/page.tsx 认这个参数)
    router.replace(q ? `/chat?q=${encodeURIComponent(q)}&send=0` : '/chat')
  }

  const needAdmin = !status.single_user && adminExists === false

  return (
    <Box
      tone={phase === 'ready' ? 'ok' : phase === 'failed' ? 'fail' : phase === 'timeout' ? 'warn' : 'plain'}
      icon={phase === 'ready' ? <PartyPopper size={16} />
        : phase === 'failed' || phase === 'timeout' ? <AlertTriangle size={16} />
        : <Loader2 size={16} style={{ animation: 'hspin 1s linear infinite' }} />}
      title="第 5 步 · 让配置生效"
    >
      <div style={{ fontSize: 13.5, color: HUNTER.INK_S }}>
        正在把 <strong>{llm.model || '新配置'}</strong> 热推给对话引擎。
        <span style={{ color: HUNTER.INK_F }}>
          （<strong>不会重启任何容器</strong>,已有会话不受影响。实测端到端约 7.5 秒。）
        </span>
      </div>

      {(phase === 'applying' || phase === 'waiting') && (
        <div style={{ marginTop: 12 }}>
          <div style={{ height: 6, borderRadius: 3, background: HUNTER.PAPER2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${Math.min(100, (elapsed / MAX_WAIT_MS) * 100)}%`,
              background: HUNTER.THEME, transition: 'width .3s linear',
            }} />
          </div>
          <div style={{ marginTop: 6, fontSize: 12.5, color: HUNTER.INK_F }}>
            已等待 {(elapsed / 1000).toFixed(0)} 秒 / 上限 {MAX_WAIT_MS / 1000} 秒
            {reason && ` · ${reason}`}
          </div>
        </div>
      )}

      {phase === 'ready' && (
        <div style={{ marginTop: 12 }}>
          <Tag tone="ok">已生效 · 用时 {(elapsed / 1000).toFixed(0)} 秒</Tag>
          <div style={{ marginTop: 10, fontSize: 13.5 }}>
            对话引擎当前用的就是 <strong>{llm.model}</strong>。试试这几个问题(点了会填进输入框,
            你可以改完再发):
          </div>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {EXAMPLES.map((q) => (
              <button key={q} onClick={() => toChat(q)} style={{
                textAlign: 'left', cursor: 'pointer', padding: '10px 12px',
                borderRadius: HUNTER.R_MD, background: HUNTER.PAPER,
                border: `1px solid ${HUNTER.LINE}`, fontSize: 13.5, color: HUNTER.INK_S,
              }}>{q}</button>
            ))}
          </div>
        </div>
      )}

      {phase === 'timeout' && (
        <div style={{ marginTop: 12 }}>
          <Tag tone="warn">等了 {MAX_WAIT_MS / 1000} 秒还没就绪</Tag>
          <div style={{ marginTop: 8, fontSize: 13.5 }}>
            配置<strong>已经存下来了</strong>,但对话引擎还没报告生效{reason ? `(${reason})` : ''}。
            这通常是 opencode 正在重连 MCP,再等一会儿多半就好了。
            还不行的话看一眼:
            <code style={{ display: 'block', marginTop: 6 }}>docker compose logs opencode --tail 80</code>
            实在不行重启它:<code>docker compose restart opencode</code>(约 50 秒)。
          </div>
        </div>
      )}

      {phase === 'failed' && (
        <div style={{ marginTop: 12, fontSize: 13.5 }}>
          <Tag tone="fail">热生效失败</Tag>
          <div style={{ marginTop: 8 }}>{reason}</div>
          <div style={{ marginTop: 6, color: HUNTER.INK_F }}>
            配置已经存进数据库了,重启 opencode 容器同样能生效:
            <code style={{ display: 'block', marginTop: 6 }}>docker compose restart opencode</code>
          </div>
        </div>
      )}

      {needAdmin && (
        <div style={{
          marginTop: 14, padding: 12, borderRadius: HUNTER.R_MD,
          background: HUNTER.PAPER3, border: `1px solid ${HUNTER.LINE}`,
        }}>
          <div style={{
            fontWeight: 700, color: HUNTER.INK, display: 'flex', alignItems: 'center', gap: 7,
          }}>
            <UserPlus size={15} /> 还差一步:创建管理员账号
          </div>
          <div style={{ fontSize: 13, color: HUNTER.INK_S, marginTop: 6 }}>
            这台实例是<strong>多用户模式</strong>(HUNTER_SINGLE_USER=0),现在还一个账号都没有。
            <strong>第一个注册的账号就是管理员</strong> —— 先把它注册掉,别让别人抢走。
          </div>
          <button onClick={() => router.replace('/register')} style={btn()}>去创建管理员账号</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button onClick={onBack} style={btn('ghost')}>上一步</button>
        {(phase === 'timeout' || phase === 'failed') && (
          <button onClick={() => void run()} style={btn('ghost')}>重试</button>
        )}
        <button
          onClick={async () => {
            if (phase !== 'ready') await complete(true)
            toChat()
          }}
          style={btn()}
        >
          {phase === 'ready' ? '开始使用' : '先进对话页(稍后再说)'}
        </button>
      </div>
    </Box>
  )
}
