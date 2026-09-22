/**
 * M3 · SSE 断线重连（HTTP 层，对着部署好的 web BFF 打）。
 *
 *     node tools/e2e/m3-sse-reconnect.mjs --base http://localhost:3200 --secrets ~/hca/secrets
 *
 * 为什么单独做一个：浏览器里把网断掉，断的是 SSE **和** 那条挂着的 `POST
 * /session/{id}/message`，两件事混在一起看不清是谁的问题。这里只断 SSE，
 * 让发消息那条请求照常挂着，单独验 LiveHub 的扇出在订阅者来回进出时对不对。
 *
 * 量三件事，都是实测：
 *   1. 第二条连接能不能连上、还能不能收到**同一轮**后续的事件；
 *   2. 断开那段时间里的事件有没有补发（现在的实现：**没有**，事件是即发即弃）；
 *   3. 断线这一轮的内容，刷新（走 `GET /session/{id}/message`）能不能拿全。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const argv = process.argv.slice(2)
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d }
const BASE = arg('--base', 'http://localhost:3200').replace(/\/$/, '')
const SECRETS = arg('--secrets', `${process.env.HOME}/hca/secrets`)
const QUESTION = arg('--q', '用 stock_quickview 看一下 000001 现在的行情，一句话说结论')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function admin() {
  const txt = readFileSync(join(SECRETS, 'admin.txt'), 'utf8')
  const get = (k) => (txt.split('\n').find((l) => l.startsWith(k + ':')) || '').split(':').slice(1).join(':').trim()
  return { email: get('email'), password: get('password') }
}

const { email, password } = admin()
const lr = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password }),
})
if (!lr.ok) throw new Error(`登录失败 HTTP ${lr.status}`)
const token = (await lr.json()).access_token
console.log(`登录成功（token 长度 ${token.length}，不打印内容）`)

const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

const cr = await fetch(`${BASE}/api/opencode/session`, {
  method: 'POST', headers: H, body: JSON.stringify({ title: 'M3 SSE 断线重连' }),
})
if (!cr.ok) throw new Error(`建会话失败 HTTP ${cr.status}：${(await cr.text()).slice(0, 200)}`)
const sid = (await cr.json()).id
console.log(`会话 ${sid}`)

/** 开一条 SSE，把收到的事件塞进 sink；返回 {stop()}。 */
async function openSse(name, sink) {
  const ac = new AbortController()
  const r = await fetch(`${BASE}/api/opencode/event?token=${encodeURIComponent(token)}`, {
    headers: { Accept: 'text/event-stream' }, signal: ac.signal,
  })
  if (!r.ok) throw new Error(`${name} 连不上 HTTP ${r.status}`)
  console.log(`  ${name} 已连上（HTTP ${r.status}）`)
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  ;(async () => {
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let i
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trimEnd(); buf = buf.slice(i + 1)
          if (!line.startsWith('data:')) continue
          try { sink.push(JSON.parse(line.slice(5).trim())) } catch { /* 心跳等 */ }
        }
      }
    } catch { /* abort */ }
  })()
  return { stop: () => ac.abort() }
}

const conn1 = [], conn2 = []
const s1 = await openSse('conn1', conn1)
await sleep(500)

// 发消息 —— 这条请求会一直挂到整轮结束（设计文档 §3.4），不 await
const t0 = Date.now()
const post = fetch(`${BASE}/api/opencode/session/${sid}/message`, {
  method: 'POST', headers: H,
  body: JSON.stringify({ parts: [{ type: 'text', text: QUESTION }] }),
}).then(async (r) => ({ status: r.status, body: (await r.text()).slice(0, 300) }))
  .catch((e) => ({ status: 0, body: String(e.message) }))

// 等到真的开始出事件再断，否则断的是一段空流，测不出东西
for (let i = 0; i < 120 && conn1.length < 3; i += 1) await sleep(500)
console.log(`\n第 1 条连接在断开前收到 ${conn1.length} 条事件（${((Date.now() - t0) / 1000).toFixed(1)}s）`)
if (conn1.length === 0) throw new Error('第 1 条连接一条事件都没收到，后面的对照没有意义')

s1.stop()
const gapStart = Date.now()
console.log('  ✂ conn1 已断开，空窗 6 秒')
await sleep(6000)

const s2 = await openSse('conn2', conn2)
const gapMs = Date.now() - gapStart

// 等整轮结束
const postResult = await Promise.race([post, sleep(300000).then(() => ({ status: -1, body: '超过 300s 没返回' }))])
await sleep(1500)
s2.stop()
const totalMs = Date.now() - t0

const kinds = (arr) => {
  const m = {}
  for (const e of arr) m[e.type] = (m[e.type] || 0) + 1
  return m
}
const finalOnConn2 = conn2.filter((e) => e.type === 'message.updated'
  && e.properties?.info?.role === 'assistant' && e.properties?.info?.time?.updated)

// 刷新口径：历史里这一轮拿不拿得全
const hr = await fetch(`${BASE}/api/opencode/session/${sid}/message`, { headers: H })
const hist = hr.ok ? await hr.json() : []
const asst = (Array.isArray(hist) ? hist : []).filter((m) => (m.info?.role || m.role) === 'assistant')
const histText = JSON.stringify(asst)

console.log(`\n══════ 结果 ══════`)
console.log(`  整轮耗时              ${(totalMs / 1000).toFixed(1)}s`)
console.log(`  POST /message 返回     HTTP ${postResult.status}`)
console.log(`  conn1（断开前）        ${conn1.length} 条  ${JSON.stringify(kinds(conn1))}`)
console.log(`  空窗                  ${(gapMs / 1000).toFixed(1)}s`)
console.log(`  conn2（重连后）        ${conn2.length} 条  ${JSON.stringify(kinds(conn2))}`)
console.log(`  conn2 收到本轮终态     ${finalOnConn2.length > 0 ? '是' : '否'}`)
// `server.connected` 每条连接都会收到一条，内容一样，不算补发 —— 比对时要排掉，
// 否则永远报「疑似有补发」（第一版就是这么误报的）。
const bodyOf = (e) => JSON.stringify(e)
const c1set = new Set(conn1.filter((e) => e.type !== 'server.connected').map(bodyOf))
const replayed = conn2.filter((e) => e.type !== 'server.connected' && c1set.has(bodyOf(e)))
console.log(`  空窗期事件有没有补发    ${replayed.length ? `有，${replayed.length} 条` : '没有（即发即弃，重连前的事件不补）'}`)
console.log(`  刷新（GET /message）    ${asst.length} 条 assistant 消息，${histText.length} 字符`)

const ok = conn2.length > 0 && finalOnConn2.length > 0 && postResult.status === 200 && asst.length > 0
console.log(`\n${ok ? '✅ 通过' : '❌ 不通过'}：重连后的连接${conn2.length > 0 ? '能' : '不能'}继续收到同一轮的事件，`
  + `本轮终态${finalOnConn2.length ? '收到了' : '没收到'}，刷新${asst.length ? '能' : '不能'}把这一轮拿全`)
process.exit(ok ? 0 : 1)
