'use client'
// 第 2 步 · 选大模型(设计方案 4.4)
//
// 预设来自 `data/llm-presets.json`(随 api 镜像分发)。卡片上的命中率与耗时
// **全是实测值**,抄自 docs/model-testing/model-compat-matrix.md ——
// 读不到预设文件时如实显示"读不到",不在前端内置一份兜底(两处数值迟早不一致)。
import { useEffect, useState } from 'react'
import { Loader2, Cpu, ExternalLink } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { getPresets, isFail, type Preset, type PresetDoc, type LlmStatus } from '../lib/setupClient'
import { Box, Tag, btn } from '../lib/ui'

export interface Draft {
  base_url: string
  api_key: string
  model: string
  sanitize: string
  preset?: Preset
  /** 选中的是「HunterCode 内置额度」那张卡。保存时会一路传到后端。 */
  builtin?: boolean
}

export default function ModelPick({
  llm, draft, onChange, onNext, onBack,
}: {
  llm: LlmStatus
  draft: Draft
  onChange: (d: Draft) => void
  onNext: () => void
  onBack: () => void
}) {
  const [doc, setDoc] = useState<PresetDoc | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    void (async () => {
      const r = await getPresets()
      if (isFail(r)) { setErr(r.message); return }
      setDoc(r)
      // 已经配过的话,默认停在当前配置上(换模型的场景),不强迫重选。
      //
      // ⚠️ **必须把对应的那张卡也选中**,不能只填地址和模型名。
      // 只填字段的话 `draft.preset` 是空的、`draft.builtin` 也是 false,而
      // 「下一步」按钮只看地址与模型名非空 —— 于是「重新运行初始化向导」的用户
      // 一路点到底、保存,后端收到 `builtin:false`,**把那批指向 hunter-deep 的
      // 模型名清掉了**:对话照常、深度分析悄悄坏掉(正是 P1 踩过的那个组合)。
      if (!draft.base_url && llm.configured) {
        const cur = (llm.base_url || '').replace(/\/+$/, '')
        const hit = (r.presets || []).find(
          (p) => p.base_url.replace(/\/+$/, '') === cur && p.model === llm.model)
        if (hit) {
          pick(hit)
        } else {
          onChange({
            ...draft, base_url: llm.base_url, model: llm.model, sanitize: llm.sanitize,
            // 预设里没有对得上的(用户手填过地址),也要把标记带过来,
            // 否则一次「什么都没改的重跑」会把内置额度关掉。
            builtin: !!llm.builtin,
          })
        }
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pick = (p: Preset) => {
    onChange({
      base_url: p.base_url,
      model: p.model,
      sanitize: p.sanitize,
      api_key: draft.api_key,
      preset: p,
      builtin: !!p.builtin,
    })
  }

  const chosen = draft.preset?.id
  // 必须**选过一张卡**才能往下走。只看「地址和模型名非空」的话,已配过的实例上
  // 用户什么都不点就能点下一步,而那时 `draft.builtin` 是 false —— 见上面 useEffect
  // 里的说明。自定义卡允许字段为空(下一步再填)。
  const canNext = chosen === 'custom'
    || (!!chosen && !!draft.base_url.trim() && !!draft.model.trim())

  return (
    <Box icon={<Cpu size={16} />} title="第 2 步 · 选大模型">
      <div style={{ color: HUNTER.INK_F, fontSize: 13.5 }}>
        猎鹿人自己不训练模型。<strong style={{ color: HUNTER.INK }}>第一张卡是推荐路径</strong>:
        用 HunterCode 的内置额度,不用自己去各家申请大模型 key;
        下面几张是自带 key 的高级路径,它们的命中率和耗时是我们实测出来的
        {doc?.updated_at ? `(${doc.updated_at},7 个 golden case)` : ''},
        不是厂商宣传值。两条路随时可以互相切换。
      </div>

      {err && <div style={{ marginTop: 12, color: HUNTER.UP, fontSize: 13.5 }}>{err}</div>}
      {doc?.error && <div style={{ marginTop: 12, color: HUNTER.UP, fontSize: 13.5 }}>{doc.error}</div>}

      {!doc && !err && (
        <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 8, color: HUNTER.INK_F }}>
          <Loader2 size={15} style={{ animation: 'hspin 1s linear infinite' }} /> 正在读取预设…
        </div>
      )}

      {llm.configured && (
        <div style={{
          marginTop: 12, padding: '9px 11px', borderRadius: HUNTER.R_SM,
          background: HUNTER.BRAND_PALE, fontSize: 13, color: HUNTER.COPPER3,
        }}>
          当前在用:<strong>{llm.model}</strong> @ {llm.base_url}
          （key {llm.api_key_masked || '未配置'}）
        </div>
      )}

      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {(doc?.presets || []).map((p) => {
          const on = chosen === p.id
          return (
            <button key={p.id} onClick={() => pick(p)} data-preset={p.id} style={{
              textAlign: 'left', cursor: 'pointer', padding: 13,
              borderRadius: HUNTER.R_MD, background: on ? HUNTER.BRAND_PALE : HUNTER.PAPER,
              // 内置额度是推荐路径 —— 没选中时也比别的卡片重一点,免得它看起来只是「第一条」
              border: `${p.builtin ? 2 : 1.5}px solid ${
                on ? HUNTER.THEME : p.builtin ? HUNTER.COPPER3 : HUNTER.LINE}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 14.5, color: HUNTER.INK }}>{p.title}</strong>
                {p.tags.map((t) => (
                  <Tag key={t} tone={t === '不推荐' ? 'fail' : t === '高级' ? 'plain' : 'ok'}>{t}</Tag>
                ))}
              </div>
              <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 3 }}>{p.vendor}</div>
              {p.builtin && (
                <div style={{ fontSize: 12.5, color: HUNTER.COPPER3, marginTop: 6 }}>
                  地址与模型名自动填好(<code>{p.model}</code>
                  {p.deep_model ? <>,深度分析走 <code>{p.deep_model}</code></> : null}),
                  <strong>你只需要填一把 Hunter 平台 key</strong>。
                </div>
              )}
              {(p.tool_hit || p.avg_latency_s != null) && (
                <div style={{ fontSize: 12.5, color: HUNTER.INK_S, marginTop: 6 }}>
                  工具调用命中 <strong>{p.tool_hit || '—'}</strong>
                  {p.avg_latency_s != null && <> · 单次平均 <strong>{p.avg_latency_s} 秒</strong></>}
                  {p.tested_at && <> · 实测于 {p.tested_at}</>}
                </div>
              )}
              {p.notes?.length > 0 && (
                <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12.5, color: HUNTER.INK_F }}>
                  {p.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
              {p.apply_url && (
                <div style={{ marginTop: 6, fontSize: 12.5 }}>
                  <a href={p.apply_url} target="_blank" rel="noreferrer"
                     onClick={(e) => e.stopPropagation()}
                     style={{ color: HUNTER.THEME, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    去申请 key <ExternalLink size={12} />
                  </a>
                  {p.key_hint && <span style={{ color: HUNTER.INK_F }}> · {p.key_hint}</span>}
                </div>
              )}
              {/* 用的是我们的算力,点之前得让人能读到边界 —— 不是走完向导才发现 */}
              {p.terms_url && (
                <div style={{ marginTop: 4, fontSize: 12.5 }}>
                  <a href={p.terms_url} target="_blank" rel="noreferrer"
                     onClick={(e) => e.stopPropagation()}
                     style={{ color: HUNTER.INK_S, textDecoration: 'underline',
                              display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    服务条款与可接受使用政策 <ExternalLink size={12} />
                  </a>
                </div>
              )}
            </button>
          )
        })}
      </div>

      {chosen && (
        <div style={{ marginTop: 14 }}>
          <Field label="接口地址(OpenAI 兼容)" value={draft.base_url}
                 placeholder="https://your-gateway/v1" disabled={!!draft.builtin}
                 onChange={(v) => onChange({ ...draft, base_url: v })} />
          <Field label="模型名" value={draft.model}
                 placeholder="例如 deepseek-v4-pro" disabled={!!draft.builtin}
                 onChange={(v) => onChange({ ...draft, model: v })} />
          <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 6 }}>
            {draft.builtin
              ? '内置额度的地址与模型名是固定的,改了就不是内置额度了 —— 想自己填请选下面的高级卡片。下一步只要粘一把 key。'
              : '这两项可以在这里改。key 在下一步填,填完当场检测三项。'}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={onBack} style={btn('ghost')}>上一步</button>
        <button onClick={onNext} disabled={!canNext} style={btn('primary', !canNext)}>
          下一步 · 填 key 当场测
        </button>
      </div>
    </Box>
  )
}

export function Field({
  label, value, onChange, placeholder, type, disabled,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: string
  disabled?: boolean
}) {
  return (
    <label style={{ display: 'block', marginTop: 10 }}>
      <div style={{ fontSize: 12.5, color: HUNTER.INK_S, marginBottom: 4 }}>{label}</div>
      <input
        type={type || 'text'}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: '100%', padding: '10px 11px', fontSize: 14,
          borderRadius: HUNTER.R_MD, border: `1px solid ${HUNTER.LINE}`,
          background: disabled ? HUNTER.PAPER2 : '#fff', color: HUNTER.INK,
        }}
      />
    </label>
  )
}
