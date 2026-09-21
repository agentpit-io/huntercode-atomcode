// 首访合规声明弹层(§3.D.3.3)
// 挂载:layout.tsx 里与 AuthGuard 并列
// 时机:登录后 3s 查 /api/auth/compliance-status · acked=false 弹层
// 行为:确认 → POST /auth/compliance-ack → 关弹层 · 拒绝 → 清 token + 跳登录
// 合规版本升级:后端 COMPLIANCE_CURRENT_VERSION 递增 · 老用户自动重弹
'use client'
import { useEffect, useState } from 'react'

const CURRENT_VERSION = 'v1.0'

export default function ComplianceAckModal() {
  const [need, setNeed] = useState(false)
  const [checked, setChecked] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  // 登录页 / 注册页 / 首启向导 / 法务页 不弹
  const muted = (path: string) =>
    path === '/login' || path === '/register' ||
    path.startsWith('/setup') || path.startsWith('/legal/')

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (muted(window.location.pathname)) return

    // 延迟 2.5s · 等 AuthGuard 拿到 token
    const timer = setTimeout(async () => {
      try {
        const token = localStorage.getItem('hunter_token')
        if (!token) return
        const r = await fetch('/api/auth/compliance-status', {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!r.ok) return
        const d = await r.json()
        // ⚠️ **落地时再判一次路径**。上面那次判的是挂载时的地址,而全新安装的用户
        // 落在 `/`、由首页在客户端 replace 到 `/setup` —— 2.5 秒之后这个回调
        // 才跑完,那时人已经在向导里了,弹层却照弹,而且是 `fixed inset-0 z-50`
        // 的全屏遮罩,把「下一步」整个挡住(M4 实测:playwright 重试 60 次全部被
        // intercept pointer events 拦掉;真人要先勾选再确认才能继续)。
        // 开箱第一屏应该是向导,不是合规弹层。
        if (muted(window.location.pathname)) return
        if (!d.acked) setNeed(true)
      } catch { /* 静默失败 · 不打扰 */ }
    }, 2500)
    return () => clearTimeout(timer)
  }, [])

  const onConfirm = async () => {
    if (!checked) return
    setSubmitting(true)
    try {
      const token = localStorage.getItem('hunter_token')
      const r = await fetch('/api/auth/compliance-ack', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: CURRENT_VERSION }),
      })
      if (r.ok) setNeed(false)
    } catch { /* keep modal · 让用户重试 */ }
    setSubmitting(false)
  }

  const onReject = () => {
    try {
      localStorage.removeItem('hunter_token')
      localStorage.removeItem('hunter_refresh')
    } catch { /* ignore */ }
    window.location.replace('/login')
  }

  if (!need) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.55)' }}>
      <div className="max-w-lg w-full rounded-xl overflow-hidden shadow-2xl"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
        <div className="px-5 py-4 border-b flex items-center gap-2"
             style={{ borderColor: 'var(--border)' }}>
          <span className="text-xl">⚠️</span>
          <h2 className="text-base font-semibold">投资研究工具 · 合规声明</h2>
        </div>
        <div className="px-5 py-4 text-sm space-y-3 leading-relaxed"
             style={{ color: 'var(--text)' }}>
          <p>
            Hunter Community 是开源投资研究工具 · 提供数据聚合、AI 分析、走势预测、
            策略回测能力 · <b>仅供个人学习和研究使用</b>。
          </p>
          <div className="text-xs space-y-1.5 pl-3 py-2 rounded"
               style={{ background: 'var(--bg)', borderLeft: '3px solid #f59e0b' }}>
            <div>① 本工具所有输出<b>不构成任何形式的投资建议</b></div>
            <div>② 历史业绩与回测结果不代表未来收益</div>
            <div>③ 市场有风险 · 投资需谨慎 · 决策由使用者自行承担</div>
            <div>④ 本工具不代客交易 · 不提供投资顾问服务</div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            数据来源:公开行情数据 · AI 模型输出为统计推断 · 可能存在错误或偏差。
            关键决策前请以交易所官方数据为准。
          </p>
          <p className="text-xs">
            <a href="/legal/disclaimer" target="_blank" rel="noopener noreferrer"
               style={{ color: '#f59e0b' }} className="hover:underline">
              查看完整免责声明 →
            </a>
          </p>
        </div>
        <div className="px-5 py-3 border-t space-y-3" style={{ borderColor: 'var(--border)' }}>
          <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
            <input type="checkbox" checked={checked}
                   onChange={e => setChecked(e.target.checked)}
                   className="w-4 h-4" />
            <span>我已阅读并知悉上述内容</span>
          </label>
          <div className="flex items-center gap-3">
            <button onClick={onConfirm} disabled={!checked || submitting}
                    className="flex-1 py-2 rounded font-medium text-sm transition-opacity"
                    style={{
                      background: '#f59e0b', color: '#000',
                      opacity: (!checked || submitting) ? 0.4 : 1,
                    }}>
              {submitting ? '提交中…' : '确认继续'}
            </button>
            <button onClick={onReject}
                    className="px-4 py-2 rounded text-sm border hover:opacity-70"
                    style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
              拒绝并退出
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
