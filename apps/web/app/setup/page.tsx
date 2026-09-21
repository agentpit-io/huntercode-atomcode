'use client'
// 首启向导 · 容器(设计方案 4.1)
//
// 六步:0 口令 / 1 环境自检 / 2 选大模型 / 3 填 key 当场测 / 4 数据供给 / 5 完成。
// 第 0 步只在"需要口令"时出现,本机开箱即用的路径直接从第 1 步开始。
//
// 三条约定:
//  1. **状态由后端说了算**。进门与否看 `/api/setup/status` 的 need_unlock,
//     不在前端自己猜"我是不是本机"——那是可以被伪造的判断,而且前端根本看不到来源。
//  2. **环境变量锁定时不进向导**。演示站这类 .env 里配好大模型的部署,
//     `llm.source === 'env'`,向导只读展示并说明为什么改不了。
//  3. 任何一步失败都**如实显示原因**,不自动跳过、不假装成功。
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, AlertTriangle, Lock } from 'lucide-react'
import { HUNTER } from '../lib/hunter-theme'
import {
  getStatus, isFail, type SetupStatus, type LlmStatus,
} from './lib/setupClient'
import { Box, Tag, btn, tbl, td } from './lib/ui'
import Unlock from './steps/Unlock'
import EnvCheck from './steps/EnvCheck'
import ModelPick, { type Draft } from './steps/ModelPick'
import ModelTest from './steps/ModelTest'
import DataSupply from './steps/DataSupply'
import Done from './steps/Done'

const STEPS = ['环境自检', '选大模型', '填 key 当场测', '数据供给', '完成'] as const

export default function SetupPage() {
  const router = useRouter()
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [step, setStep] = useState(1)          // 1..5,0 是口令页(单独渲染)
  const [draft, setDraft] = useState<Draft>({ base_url: '', api_key: '', model: '', sanitize: '' })
  const [testToken, setTestToken] = useState('')

  const refresh = useCallback(async () => {
    const s = await getStatus()
    if (isFail(s)) { setLoadErr(s.message); return }
    setLoadErr('')
    setStatus(s)
    return s
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  if (loadErr) {
    return (
      <Shell>
        <Box tone="fail" icon={<AlertTriangle size={16} />} title="打不开向导">
          <div>{loadErr}</div>
          <div style={{ marginTop: 10, fontSize: 13, color: HUNTER.INK_F }}>
            这一步只调了 <code>/api/setup/status</code>,它连 api 容器都没用上;
            打不开通常是 web 容器自己有问题。看一眼:
            <code style={{ display: 'block', marginTop: 6 }}>docker compose logs web --tail 50</code>
          </div>
          <button onClick={() => void refresh()} style={btn()}>重试</button>
        </Box>
      </Shell>
    )
  }

  if (!status) {
    return (
      <Shell>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: HUNTER.INK_F }}>
          <Loader2 size={16} style={{ animation: 'hspin 1s linear infinite' }} /> 正在读取这台实例的状态…
        </div>
      </Shell>
    )
  }

  // ── 第 0 步 · 口令 ────────────────────────────────────────────
  if (status.need_unlock) {
    return (
      <Shell>
        <Unlock status={status} onDone={() => void refresh()} />
      </Shell>
    )
  }

  const llm = status.llm as LlmStatus

  // ── 环境变量锁定 · 不进向导 ───────────────────────────────────
  if (llm.env_locked) {
    return (
      <Shell>
        <Box tone="ok" icon={<Lock size={16} />} title="这台实例的大模型配置已锁定">
          <div>
            配置写在环境变量里,向导改不了它 —— 这是有意的:锁定的实例(比如演示站)
            不该被任何打开网页的人换掉模型。
          </div>
          <table style={tbl()}>
            <tbody>
              {([['base_url', 'LLM_BASE_URL', llm.base_url],
                 ['model', 'LLM_DEFAULT_MODEL', llm.model],
                 ['api_key', 'LLM_API_KEY', llm.api_key_masked]] as const).map(([k, env, v]) => (
                <tr key={k}>
                  <td style={td(true)}>{env}</td>
                  <td style={td()}>{v || <span style={{ color: HUNTER.INK_F }}>—</span>}</td>
                  <td style={td()}>
                    <Tag tone={llm.locked_items[k] === 'env' ? 'warn' : 'ok'}>
                      {llm.locked_items[k] === 'env' ? '已锁定(环境变量)'
                        : llm.locked_items[k] === 'db' ? '数据库' : '未配置'}
                    </Tag>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ marginTop: 12, fontSize: 13, color: HUNTER.INK_F }}>
            要换模型:改部署目录的 <code>.env</code>,然后 <code>docker compose up -d</code>
            (<strong>不是 restart</strong> —— restart 不会重新读 .env)。
          </div>
          <button onClick={() => router.replace('/chat')} style={btn()}>去对话</button>
        </Box>
      </Shell>
    )
  }

  const go = (n: number) => setStep(Math.max(1, Math.min(5, n)))

  return (
    <Shell>
      <Steps current={step} />
      <div style={{ marginTop: 18 }}>
        {step === 1 && <EnvCheck onNext={() => go(2)} />}
        {step === 2 && (
          <ModelPick
            llm={llm}
            draft={draft}
            onChange={setDraft}
            onNext={() => { setTestToken(''); go(3) }}
            onBack={() => go(1)}
          />
        )}
        {step === 3 && (
          <ModelTest
            draft={draft}
            onChange={setDraft}
            testToken={testToken}
            onTested={setTestToken}
            onSaved={async () => { await refresh(); go(4) }}
            onBack={() => go(2)}
          />
        )}
        {step === 4 && (
          <DataSupply
            status={status}
            builtin={!!draft.builtin}
            onNext={() => go(5)}
            onBack={() => go(3)}
            onRefresh={refresh}
          />
        )}
        {step === 5 && <Done status={status} onBack={() => go(4)} />}
      </div>
    </Shell>
  )
}

// ── 外壳与小组件 ────────────────────────────────────────────────
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS,
      padding: '28px 16px 60px',
    }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
          <span style={{ fontSize: 26, lineHeight: 1 }}>🦌</span>
          <div>
            <div style={{ fontFamily: HUNTER.SERIF, fontSize: 20, fontWeight: 700, color: HUNTER.INK }}>
              猎鹿人 · 首次启动设置
            </div>
            <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 2 }}>
              配好大模型就能开始用。全程不用改任何文件。
            </div>
          </div>
        </div>
        {children}
      </div>
      <style>{`@keyframes hspin{from{transform:rotate(0)}to{transform:rotate(360deg)}}`}</style>
    </div>
  )
}

function Steps({ current }: { current: number }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {STEPS.map((s, i) => {
        const n = i + 1
        const done = n < current
        const now = n === current
        return (
          <div key={s} style={{
            flex: '1 1 120px', padding: '8px 10px', borderRadius: HUNTER.R_SM,
            background: now ? HUNTER.THEME : done ? HUNTER.BRAND_PALE : HUNTER.PAPER,
            color: now ? '#fff' : done ? HUNTER.COPPER3 : HUNTER.INK_F,
            border: `1px solid ${now ? HUNTER.THEME : HUNTER.LINE}`,
            fontSize: 12.5, fontWeight: now ? 700 : 500, whiteSpace: 'nowrap',
          }}>
            {n}. {s}
          </div>
        )
      })}
    </div>
  )
}
