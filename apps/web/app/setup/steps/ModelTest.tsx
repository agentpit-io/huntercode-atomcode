'use client'
// 第 3 步 · 填 key 当场测(设计方案 4.5)
//
// 三项检测由 **api 容器**发出(和 opencode 实际调用走同一条网络路径),
// 每项显示真实耗时。三项全过才给 test_token,没有 test_token 保存按钮不可用。
//
// ⚠️ 保存按钮的禁用条件是 `!testToken`,**不是** `!result?.ok` ——
//    token 会过期(10 分钟),过期后 result 还挂在界面上却已经存不进去了。
import { useState } from 'react'
import { Loader2, KeyRound, CheckCircle2, XCircle, AlertTriangle, ExternalLink } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { testLlm, saveLlm, isFail, type TestResult } from '../lib/setupClient'
import { Box, Tag, btn } from '../lib/ui'
import { Field, type Draft } from './ModelPick'

/** 只露头尾,中间打码 —— 界面上永远不回显完整 key。 */
function maskKey(k: string): string {
  const v = (k || '').trim()
  if (v.length <= 12) return v ? `${v.slice(0, 2)}****${v.slice(-2)}` : ''
  return `${v.slice(0, 11)}****${v.slice(-4)}`
}

export default function ModelTest({
  draft, onChange, testToken, onTested, onSaved, onBack,
}: {
  draft: Draft
  onChange: (d: Draft) => void
  testToken: string
  onTested: (t: string) => void
  onSaved: () => void | Promise<void>
  onBack: () => void
}) {
  const [result, setResult] = useState<TestResult | null>(null)
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const builtin = !!draft.builtin
  // 「验证过」= 三项全过且凭证还在。凭证过期时 testToken 会被清掉,
  // 界面自动退回输入状态 —— 那时候确实需要重测,不能再说"已验证"。
  const verified = !!(result?.ok && testToken)

  const canTest = !!(draft.base_url.trim() && draft.model.trim() && draft.api_key.trim()) && !testing

  const runTest = async () => {
    if (!canTest) return
    setTesting(true); setErr(''); setResult(null); onTested('')
    const r = await testLlm(draft.base_url.trim(), draft.api_key.trim(), draft.model.trim())
    setTesting(false)
    if (isFail(r)) { setErr(r.message); return }
    setResult(r)
    onTested(r.test_token || '')
    // 清洗开关由检测结果决定,不让用户猜(设计方案 4.5)
    if (r.sanitize_suggest) onChange({ ...draft, sanitize: r.sanitize_suggest })
  }

  const save = async () => {
    if (!testToken || saving) return
    setSaving(true); setErr('')
    const r = await saveLlm({
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim(),
      model: draft.model.trim(),
      sanitize: draft.sanitize || 'auto',
      test_token: testToken,
      builtin,
    })
    setSaving(false)
    if (isFail(r)) {
      setErr(r.message)
      // 凭证过期 / 配置被改过 —— 让用户重测,而不是让他对着一个存不进去的按钮点
      if (r.data?.reason) onTested('')
      return
    }
    setSaved(true)
    await onSaved()
  }

  return (
    <Box icon={<KeyRound size={16} />}
         title={builtin ? '第 3 步 · 填一把 Hunter 平台 key,当场检测' : '第 3 步 · 填 key,当场检测'}>
      {builtin ? (
        // 内置额度:地址与模型名是固定的,**只读展示**不给输入框 ——
        // 用户在这一步唯一要做的事就是粘一把 key。
        <div style={{
          padding: '9px 11px', borderRadius: HUNTER.R_SM,
          background: HUNTER.BRAND_PALE, fontSize: 13, color: HUNTER.COPPER3, lineHeight: 1.8,
        }}>
          接口地址 <code>{draft.base_url}</code><br />
          模型名 <code>{draft.model}</code>
          {draft.preset?.deep_model ? <>(深度分析 <code>{draft.preset.deep_model}</code>)</> : null}
          <br />schema 清洗 <code>0</code> —— 清洗在网关做,本地不用再过一遍。
        </div>
      ) : (
        <>
          <Field label="接口地址" value={draft.base_url} onChange={(v) => { onChange({ ...draft, base_url: v }); onTested('') }} />
          <Field label="模型名" value={draft.model} onChange={(v) => { onChange({ ...draft, model: v }); onTested('') }} />
        </>
      )}
      {verified ? (
        // 检测通过后**不再显示输入框** —— 密码框回显成一片空白的圆点,用户会以为
        // 自己填的东西没存上,于是重填一遍、再测一遍。这里换成一句「这把 key 已验证」
        // 加一个明确的「换一把」入口,想换的人点一下就能回到输入状态。
        <div style={{
          marginTop: 10, padding: '10px 12px', borderRadius: HUNTER.R_SM,
          border: `1px solid ${HUNTER.SUCCESS}`, background: HUNTER.PAPER,
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
        }}>
          <CheckCircle2 size={15} color={HUNTER.SUCCESS} />
          <span style={{ fontSize: 13.5, color: HUNTER.INK }}>
            {builtin ? 'Hunter 平台 key' : 'API key'} 已验证:<code>{maskKey(draft.api_key)}</code>
          </span>
          <button onClick={() => { onChange({ ...draft, api_key: '' }); onTested(''); setResult(null) }}
                  style={{
                    marginLeft: 'auto', border: 'none', background: 'none', padding: 0,
                    color: HUNTER.THEME, fontSize: 12.5, cursor: 'pointer', textDecoration: 'underline',
                  }}>换一把 key</button>
        </div>
      ) : (
        <Field label={builtin ? 'Hunter 平台 key(hunt_tools_ 开头)' : 'API key'}
               type="password" placeholder={builtin ? 'hunt_tools_…' : '粘贴你的 key'}
               value={draft.api_key} onChange={(v) => { onChange({ ...draft, api_key: v }); onTested('') }} />
      )}
      {builtin && !verified && draft.preset?.apply_url && (
        <div style={{ marginTop: 6, fontSize: 12.5 }}>
          还没有?<a href={draft.preset.apply_url} target="_blank" rel="noreferrer"
                    style={{ color: HUNTER.THEME, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            免费申请(约 30 秒) <ExternalLink size={12} />
          </a>
          <span style={{ color: HUNTER.INK_F }}> · 这把 key 同时管工具与数据源,不需要第二把</span>
        </div>
      )}
      <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 6 }}>
        key 会用 AES-256-GCM 加密后存进这台实例自己的数据库,界面上只回显末 4 位,
        日志里一个字都不打。它只发给{builtin ? '上面这个 HunterCode 网关地址' : '你填的这个地址'}。
        {builtin && '网关只记 token 数与模型名,不记任何对话内容。'}
        {builtin && draft.preset?.terms_url && (
          <>
            {' '}使用前请读一遍{' '}
            <a href={draft.preset.terms_url} target="_blank" rel="noreferrer"
               style={{ color: HUNTER.INK_S, textDecoration: 'underline' }}>
              服务条款与可接受使用政策
            </a>
            (禁止转售、禁止当通用 API 用;额度与服务可能调整或下线)。
          </>
        )}
      </div>

      {!verified && (
      <button onClick={() => void runTest()} disabled={!canTest} style={btn('primary', !canTest)}>
        {testing ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Loader2 size={14} style={{ animation: 'hspin 1s linear infinite' }} /> 检测中…(最长约 85 秒)
        </span> : '开始检测'}
      </button>
      )}

      {err && <div style={{ marginTop: 12, color: HUNTER.UP, fontSize: 13.5 }}>{err}</div>}

      {result && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Tag tone={result.ok ? 'ok' : 'fail'}>{result.ok ? '三项全部通过' : '未通过'}</Tag>
            <span style={{ fontSize: 12.5, color: HUNTER.INK_F }}>
              总耗时 {(result.elapsed_ms / 1000).toFixed(2)} 秒
            </span>
          </div>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {result.checks.map((c, i) => (
              <div key={i} style={{
                border: `1px solid ${HUNTER.LINE}`, borderRadius: HUNTER.R_SM,
                padding: '9px 11px', background: c.ok ? HUNTER.PAPER : HUNTER.PAPER3,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  {c.ok ? <CheckCircle2 size={15} color={HUNTER.SUCCESS} />
                        : <XCircle size={15} color={HUNTER.UP} />}
                  <strong style={{ fontSize: 13.5, color: HUNTER.INK }}>{i + 1}. {c.name}</strong>
                  <span style={{ fontSize: 12.5, color: HUNTER.INK_F }}>
                    {(c.elapsed_ms / 1000).toFixed(2)} 秒
                  </span>
                </div>
                <div style={{ fontSize: 13, color: HUNTER.INK_S, marginTop: 4 }}>{c.message}</div>
                {c.warn && (
                  <div style={{
                    fontSize: 12.5, color: HUNTER.TAG_WARN_FG, marginTop: 5,
                    display: 'flex', gap: 5, alignItems: 'flex-start',
                  }}>
                    <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} /> {c.warn}
                  </div>
                )}
              </div>
            ))}
          </div>
          {result.ok && builtin && (
            <div style={{ marginTop: 10, fontSize: 13, color: HUNTER.INK_S }}>
              这三项测的就是内置额度网关本身 —— 过了就说明 key 有效、今天还有额度。
              保存时会顺带把深度分析的模型指向 <strong>hunter-deep</strong>,
              并用同一把 key 解锁数据供给(第 4 步会告诉你结果)。
              检测结果 {Math.round(result.test_token_ttl / 60)} 分钟内有效,超时要重测。
            </div>
          )}
          {result.ok && !builtin && (
            <div style={{ marginTop: 10, fontSize: 13, color: HUNTER.INK_S }}>
              schema 清洗开关将保存为 <strong>{draft.sanitize || 'auto'}</strong>
              {draft.sanitize === '1' && '(这个模型不接受完整 JSON Schema,必须开)'}
              {draft.sanitize === 'auto' && '(未清洗即通过,按模型名自动决定走不走 llm-shim)'}
              。检测结果 {Math.round(result.test_token_ttl / 60)} 分钟内有效,超时要重测。
            </div>
          )}
        </div>
      )}

      {verified && (
        // 红字。用户反馈:检测通过之后不确定"到底存没存",容易重填重测。
        // 这句话要把状态说死:测过了、还没存、下一步点哪个按钮。
        <div style={{
          marginTop: 14, padding: '10px 12px', borderRadius: HUNTER.R_SM,
          border: `1px solid ${HUNTER.UP}`, background: HUNTER.PAPER,
          color: HUNTER.UP, fontSize: 13.5, fontWeight: 600, lineHeight: 1.7,
        }}>
          检测成功,这把 key 可用 —— 不用再输入一次。
          <span style={{ fontWeight: 400 }}>
            {' '}现在点下面的「保存并继续」才算存进这台实例
            {saved ? '(已保存)' : '(尚未保存)'}。
          </span>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
        <button onClick={onBack} style={btn('ghost')}>上一步</button>
        <button onClick={() => void save()} disabled={!testToken || saving}
                style={btn('primary', !testToken || saving)}>
          {saving ? '保存中…' : '保存并继续'}
        </button>
      </div>
      {!testToken && (
        <div style={{ marginTop: 8, fontSize: 12.5, color: HUNTER.INK_F }}>
          三项检测全部通过之后才能保存 —— 存一份没测过的配置,问题会推迟到你发第一条
          消息时才暴露,那时候看到的只是"没有回复"。
        </div>
      )}
    </Box>
  )
}
