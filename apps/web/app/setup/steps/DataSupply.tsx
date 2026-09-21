'use client'
// 第 4 步 · 数据供给三选一(设计方案 4.6)
//
// 平台 key 走**现成的** PUT /api/hunter/unlock —— 它本来就是"先校验再保存",
// 不另写一套校验(两套校验对同一把 key 给出不同结论是最难查的一类问题)。
import { useState } from 'react'
import { Database, ExternalLink } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { savePlatformKey, isFail, type SetupStatus } from '../lib/setupClient'
import { Box, Tag, btn } from '../lib/ui'
import { Field } from './ModelPick'

type Choice = 'free' | 'platform' | 'mcp'

export default function DataSupply({
  status, builtin, onNext, onBack, onRefresh,
}: {
  status: SetupStatus
  /** 上一步选的是「HunterCode 内置额度」—— 那把 key 同时管数据供给,这一步通常已经配好了。 */
  builtin: boolean
  onNext: () => void
  onBack: () => void
  onRefresh: () => Promise<any>
}) {
  const ds = status.data_supply
  const [choice, setChoice] = useState<Choice>(ds?.configured ? 'platform' : 'free')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [okMsg, setOkMsg] = useState('')

  const saveKey = async () => {
    if (!key.trim() || busy) return
    setBusy(true); setErr(''); setOkMsg('')
    const r = await savePlatformKey(key.trim())
    setBusy(false)
    if (isFail(r)) {
      setErr(r.status === 401
        ? 'key 没保存成功:这台实例开了多用户模式,保存平台 key 需要先有一个账号。'
          + '可以先跳过这一步,在最后一步创建管理员账号之后,到左下角「解锁全部工具」里再填。'
        : r.message)
      return
    }
    setKey('')
    setOkMsg('平台 key 已校验通过并保存。')
    await onRefresh()
  }

  return (
    <Box icon={<Database size={16} />} title="第 4 步 · 数据从哪来">
      <div style={{ color: HUNTER.INK_F, fontSize: 13.5 }}>
        大模型负责思考,行情和财务数据要另外有来源。这一步可以跳过,之后随时能改。
      </div>

      {/* 内置额度路径:同一把 key 已经在上一步解锁了数据供给,别让用户以为还要再填一次。
          ds.configured 为假时(环境变量锁定 / 上游当时连不上)照实说还要填,不假装已解锁。 */}
      {builtin && (
        <div style={{
          marginTop: 12, padding: '10px 12px', borderRadius: HUNTER.R_SM,
          background: ds?.configured ? HUNTER.TAG_OK_BG : HUNTER.PAPER2,
          border: `1px solid ${ds?.configured ? HUNTER.SUCCESS : HUNTER.LINE}`,
          fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.75,
        }}>
          {ds?.configured ? (
            <>
              <strong style={{ color: HUNTER.INK }}>同一把 key 已解锁数据供给</strong>
              （{ds.masked}）—— 你在上一步填的那把 <code>hunt_tools_</code> key
              同时管大模型额度、工具、SKILL 与数据源,<strong>这一步不用再填一遍</strong>,
              直接「下一步 · 完成」即可。
            </>
          ) : (
            <>
              你走的是内置额度,那把 <code>hunt_tools_</code> key 本来也能解锁数据供给,
              但这台实例现在还没记上（{ds?.env_locked
                ? '数据源 key 写在 .env 的 HUNTER_API_KEY 里,向导改不了它'
                : '保存时没能向 Hunter 服务器确认这把 key —— 多半是网络不通'}）。
              在下面「平台数据管道」里把同一把 key 再粘一次即可。
            </>
          )}
        </div>
      )}

      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* ⚠️ 这一项**不写任何配置**,选它等于「先不配数据源」。文案必须说实话:
            后端的 DATA_SOURCE_PROVIDER 留空时默认走 hunter 网关(那是有意的 ——
            宁可如实报 hunter_key_required,也不悄悄回落到容器里经常连不通的 AKShare),
            所以这里没有 key 时**实时行情会明确报错**,而 K 线 / 财务 / 新闻 /
            研报 / 龙虎榜 / 十大股东 / 治理这些照常能取,深度分析实测能跑完。
            M4 实测:原文案写「装好就能用」,用户选完第一条对话就顶出一张红色
            「无法拉取 行情 · hunter_key_required」,与承诺矛盾。 */}
        <Option on={choice === 'free'} onClick={() => setChoice('free')} title="先不配数据源"
                tag={<Tag tone="plain">不用填任何东西</Tag>}>
          现在就能用的:K 线、财务、新闻、研报、龙虎榜、十大股东、治理 —— 深度分析跑得完。
          <div style={{ marginTop: 4, color: HUNTER.INK_F }}>
            现在还不能用的:<b>实时行情</b>会明确提示「未配置 Hunter Key」,不会编一个价格给你。
            随时可以回到这一步补上面那把免费 key;想完全自给自足,
            也可以在「数据源与 MCP」页把数据源换成 AKShare 等公开源
            (免 key,但容器直连这些站点在部分网络环境下不稳定,且港美股覆盖不全)。
          </div>
        </Option>

        <Option on={choice === 'platform'} onClick={() => setChoice('platform')} title="平台数据管道"
                tag={ds?.configured ? <Tag tone="ok">已配置 {ds.masked}</Tag> : <Tag tone="plain">需要一把免费 key</Tag>}>
          一把 key 打开 Kronos 预测、港美股数据、全部工具与 SKILL。
          {ds?.env_locked ? (
            <div style={{ marginTop: 6, color: HUNTER.TAG_WARN_FG }}>
              这台实例的 key 来自 .env(HUNTER_API_KEY),这里改不了。
            </div>
          ) : (
            <>
              <Field label="平台 key" type="password" placeholder="hunt_tools_…"
                     value={key} onChange={setKey} />
              <button onClick={() => void saveKey()} disabled={!key.trim() || busy}
                      style={{ ...btn('primary', !key.trim() || busy), marginTop: 10 }}>
                {busy ? '校验中…' : '校验并保存'}
              </button>
              {ds?.apply_url && (
                <div style={{ marginTop: 8, fontSize: 12.5 }}>
                  <a href={ds.apply_url} target="_blank" rel="noreferrer"
                     onClick={(e) => e.stopPropagation()}
                     style={{ color: HUNTER.THEME, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    免费申请(约 30 秒) <ExternalLink size={12} />
                  </a>
                </div>
              )}
            </>
          )}
        </Option>

        <Option on={choice === 'mcp'} onClick={() => setChoice('mcp')} title="自己接数据源 / MCP"
                tag={<Tag tone="plain">进阶</Tag>}>
          有 Tushare、聚宽这类账号,或者自己写了 MCP 工具,可以直接接进来。
          <div style={{ marginTop: 6, fontSize: 12.5 }}>
            <a href="/mcp-config" style={{ color: HUNTER.THEME }}>去「数据源与 MCP」页配置 →</a>
            <span style={{ color: HUNTER.INK_F }}>（也可以先跳过,配好向导再回来）</span>
          </div>
        </Option>
      </div>

      {err && <div style={{ marginTop: 12, color: HUNTER.UP, fontSize: 13.5 }}>{err}</div>}
      {okMsg && <div style={{ marginTop: 12, color: HUNTER.SUCCESS, fontSize: 13.5 }}>{okMsg}</div>}

      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={onBack} style={btn('ghost')}>上一步</button>
        <button onClick={onNext} style={btn()}>下一步 · 完成</button>
      </div>
    </Box>
  )
}

function Option({
  on, onClick, title, tag, children,
}: {
  on: boolean
  onClick: () => void
  title: string
  tag?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div onClick={onClick} style={{
      cursor: 'pointer', padding: 13, borderRadius: HUNTER.R_MD,
      background: on ? HUNTER.BRAND_PALE : HUNTER.PAPER,
      border: `1.5px solid ${on ? HUNTER.THEME : HUNTER.LINE}`,
      fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.7,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        <strong style={{ fontSize: 14.5, color: HUNTER.INK }}>{title}</strong>
        {tag}
      </div>
      {children}
    </div>
  )
}
