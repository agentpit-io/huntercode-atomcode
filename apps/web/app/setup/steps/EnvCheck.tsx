'use client'
// 第 1 步 · 环境自检(设计方案 4.3 + M1 交接第 7 条)
//
// 六个服务 / 迁移账本 / 密钥来源与强度 / 卷可写 / api↔opencode 双向 / 访问方式。
//
// **有 fail 也放行下一步**:卷不可写只影响装 SKILL,大模型照样能配;
// 把用户堵在这里,他既修不了也用不了。标红 + 写清怎么修,然后让他继续。
import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Stethoscope } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { getEnvCheck, isFail, type EnvCheck as EnvCheckData, type CheckState } from '../lib/setupClient'
import { Box, Tag, btn } from '../lib/ui'

const TONE: Record<CheckState, 'ok' | 'warn' | 'fail' | 'plain'> = {
  ok: 'ok', warn: 'warn', fail: 'fail', unknown: 'plain',
}
const LABEL: Record<CheckState, string> = {
  ok: '正常', warn: '注意', fail: '有问题', unknown: '未测出',
}

export default function EnvCheck({ onNext }: { onNext: () => void }) {
  const [data, setData] = useState<EnvCheckData | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    const r = await getEnvCheck()
    setBusy(false)
    if (isFail(r)) { setErr(r.message); setData(null); return }
    setErr(''); setData(r)
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <Box icon={<Stethoscope size={16} />} title="第 1 步 · 环境自检">
      <div style={{ color: HUNTER.INK_F, fontSize: 13.5 }}>
        逐项<strong>真探测</strong>,不读配置猜结论。有红色项也可以继续 —— 它们只影响对应的功能,
        大模型照样能配。
      </div>

      {err && <div style={{ marginTop: 12, color: HUNTER.UP, fontSize: 13.5 }}>{err}</div>}

      {busy && !data && (
        <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 8, color: HUNTER.INK_F }}>
          <Loader2 size={15} style={{ animation: 'hspin 1s linear infinite' }} /> 正在探测…
        </div>
      )}

      {data && (
        <>
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Tag tone="ok">正常 {data.summary.ok}</Tag>
            {data.summary.warn > 0 && <Tag tone="warn">注意 {data.summary.warn}</Tag>}
            {data.summary.fail > 0 && <Tag tone="fail">有问题 {data.summary.fail}</Tag>}
            {data.summary.unknown > 0 && <Tag tone="plain">未测出 {data.summary.unknown}</Tag>}
          </div>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.items.map((it) => (
              <div key={it.key} style={{
                border: `1px solid ${HUNTER.LINE}`, borderRadius: HUNTER.R_SM,
                padding: '9px 11px', background: it.state === 'ok' ? HUNTER.PAPER : HUNTER.PAPER3,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Tag tone={TONE[it.state]}>{LABEL[it.state]}</Tag>
                  <strong style={{ fontSize: 13.5, color: HUNTER.INK }}>{it.label}</strong>
                </div>
                <div style={{ fontSize: 13, color: HUNTER.INK_S, marginTop: 4, wordBreak: 'break-all' }}>
                  {it.detail}
                </div>
                {it.hint && (
                  <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 4 }}>
                    → {it.hint}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={() => void load()} disabled={busy} style={{ ...btn('ghost', busy), display: 'flex', alignItems: 'center', gap: 6 }}>
          <RefreshCw size={14} /> 重新检查
        </button>
        <button onClick={onNext} style={btn()}>下一步 · 选大模型</button>
      </div>
    </Box>
  )
}
