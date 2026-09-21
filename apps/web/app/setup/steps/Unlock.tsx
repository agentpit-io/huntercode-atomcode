'use client'
// 第 0 步 · 初始化口令(设计方案 4.2)
//
// 三种进不来的原因,文案完全不同 —— 用户的下一步动作也完全不同:
//   token_required  已设口令 → 输进去
//   no_token_public 公网来访但没设口令 → 去平台设 HUNTER_SETUP_TOKEN 再重启 api
//   locked          连错 5 次 → 等 15 分钟
import { useState } from 'react'
import { Lock, AlertTriangle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { unlock, setSession, isFail, type SetupStatus } from '../lib/setupClient'
import { Box, btn } from '../lib/ui'

export default function Unlock({ status, onDone }: { status: SetupStatus; onDone: () => void }) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [lockedFor, setLockedFor] = useState(status.locked_for || 0)

  if (status.unlock_reason === 'no_token_public') {
    return (
      <Box tone="fail" icon={<AlertTriangle size={16} />} title="这台实例还没有设置初始化口令">
        <div>{status.message}</div>
        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600, color: HUNTER.INK }}>怎么做</div>
          <ol style={{ paddingLeft: 20, marginTop: 6 }}>
            <li>在部署平台的环境变量面板(或部署目录的 <code>.env</code>)里加一行
              <code style={{ display: 'block', margin: '6px 0' }}>HUNTER_SETUP_TOKEN=一串足够随机的口令</code>
            </li>
            <li>重启 api 容器:<code>docker compose up -d api</code></li>
            <li>回到这个页面,把口令填进来</li>
          </ol>
          <div style={{ fontSize: 13, color: HUNTER.INK_F, marginTop: 8 }}>
            为什么非要口令:这台实例现在就在公网上。没有口令的话,谁先打开这个页面
            谁就能配置大模型、看到这台实例的运行状态。
          </div>
        </div>
      </Box>
    )
  }

  const locked = lockedFor > 0

  const submit = async () => {
    if (!token.trim() || busy) return
    setBusy(true); setErr('')
    const r = await unlock(token.trim())
    setBusy(false)
    if (isFail(r)) {
      setErr(r.message)
      const lf = r.data?.locked_for
      if (typeof lf === 'number' && lf > 0) setLockedFor(lf)
      return
    }
    setSession(r.session)
    setToken('')
    onDone()
  }

  return (
    <Box tone={locked ? 'fail' : 'plain'} icon={<Lock size={16} />} title="请输入初始化口令">
      <div style={{ color: HUNTER.INK_F, fontSize: 13.5 }}>
        口令是部署时设置的 <code>HUNTER_SETUP_TOKEN</code>。
        云平台一键部署的话,它在平台的「环境变量」面板里;
        本机部署的话,在部署目录的 <code>.env</code> 里。
      </div>
      <input
        type="password"
        value={token}
        disabled={locked || busy}
        onChange={(e) => setToken(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
        placeholder="HUNTER_SETUP_TOKEN"
        style={{
          width: '100%', marginTop: 14, padding: '11px 12px', fontSize: 14,
          borderRadius: HUNTER.R_MD, border: `1px solid ${HUNTER.LINE}`,
          background: locked ? HUNTER.PAPER2 : '#fff', color: HUNTER.INK,
        }}
      />
      {err && (
        <div style={{ marginTop: 10, color: HUNTER.UP, fontSize: 13.5 }}>{err}</div>
      )}
      {locked && (
        <div style={{ marginTop: 8, fontSize: 13, color: HUNTER.INK_F }}>
          锁定还剩约 {Math.ceil(lockedFor / 60)} 分钟。锁定是<strong>整台实例</strong>的,
          换个网络重试也没用 —— 这台机器只有一个管理员。
        </div>
      )}
      <button onClick={() => void submit()} disabled={locked || busy || !token.trim()}
              style={btn('primary', locked || busy || !token.trim())}>
        {busy ? '校验中…' : '进入向导'}
      </button>
      <div style={{ marginTop: 10, fontSize: 12.5, color: HUNTER.INK_F }}>
        连续输错 5 次会锁定 15 分钟。
      </div>
    </Box>
  )
}
