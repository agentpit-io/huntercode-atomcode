#!/usr/bin/env node
/**
 * `app/chat/lib/modelError.ts` 的回归检查 —— 不起浏览器、不连网。
 *
 *     cd apps/web && node scripts/model-error-check.mjs
 *
 * ## 为什么值得单独有一个
 *
 * 这个文件是「模型调用失败时用户看到什么」的唯一出口。它坏掉的表现是
 * **界面一片空白或者一句看不懂的英文**,而 `tsc` 与 `next build` 全绿 ——
 * 和仓内那条「前端验证不能只靠 HTTP 200」是同一类问题。
 *
 * 断言里的两个 fixture 都是**真实抓到的**报错对象(不是编的):
 *   · builtinQuota  2026-09-19 测试服务器,把内置额度 key 的 quota_daily 调到 2000
 *                   之后发一条对话,opencode 记在 assistant 消息 info.error 上的原文
 *   · deepseek402   2026-09-17 本地,DeepSeek 账户余额 0
 *
 * 用 `tsc` 把那个 .ts 单独转一份出来再 import —— 不另抄一份实现
 * (抄一份迟早会漂,而漂了的表现正是这个脚本要挡的那件事)。
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '..', 'app', 'chat', 'lib', 'modelError.ts')
const out = mkdtempSync(join(tmpdir(), 'model-error-'))
execFileSync('npx', ['tsc', src, '--outDir', out, '--module', 'esnext',
                     '--target', 'es2020', '--moduleResolution', 'bundler'],
             { cwd: resolve(here, '..'), stdio: 'inherit' })
const { describeModelError } = await import(join(out, 'modelError.js'))

// ── fixture ───────────────────────────────────────────────────────
const QUOTA_MSG =
  'HunterCode 内置模型额度今天用完了（已用 154,686 / 上限 2,000 token）。' +
  '额度每天北京时间 0 点重置，下次重置在 2026-09-20 00:00（约 532 分钟后）。' +
  '现在就要继续用的话有两条路：① 在设置里改用你自己的大模型 key；② 联系我们把额度调上去。'

const builtinQuota = {
  name: 'APIError',
  data: {
    message: QUOTA_MSG,
    statusCode: 402,
    isRetryable: false,
    responseBody: JSON.stringify({
      error: {
        message: QUOTA_MSG, type: 'insufficient_quota',
        code: 'daily_quota_exceeded', param: null,
        used_today: 154686, limit: 2000,
        reset_at: '2026-09-20T00:00:00+08:00', retry_after: 31934,
      },
    }),
  },
}

const deepseek402 = {
  name: 'APIError',
  data: { message: 'Insufficient Balance', statusCode: 402 },
}

// ── P3(2026-09-19)· 对外开放之后的两道全局闸门 ──────────────────────
// 两段 message 都是**从 hermes 生产网关真实抓下来的**(curl 到
// https://hunter.agentpit.io/api/saas/llm/v1/chat/completions,
// 一次把全局熔断线调到 1000、一次把 HUNTER_BUILTIN_LLM_ENABLED 设成 0),
// 不是照着代码抄的。
const BREAKER_MSG =
  '今天 HunterCode 内置模型额度的**整体**用量已经到顶了（全平台合计 88,306 / 上限 1,000 token），' +
  '为了不把服务和账单拖垮，现在新的请求要先排到明天。额度每天北京时间 0 点重置，' +
  '下次重置在 2026-09-20 00:00。这跟你个人的额度没关系 —— 你自己的额度多半还剩着。' +
  '现在就要继续用的话，请在「设置 → 大模型」里改用你自己的大模型 key' +
  '（任何 OpenAI 兼容服务都行，填地址 + key + 模型名即可），内置额度恢复后随时可以切回来。给你添麻烦了。'

const DISABLED_MSG =
  'HunterCode 内置模型额度当前已暂停提供服务。这不是你的配置或 key 出了问题 —— ' +
  '你的 Hunter 平台 key 依然有效，行情、K 线、财报、深度分析这些工具与数据供给都不受影响，照常可用。' +
  '现在要继续对话，请在「设置 → 大模型」里改用你自己的大模型 key：任何 OpenAI 兼容服务都行' +
  '（填地址 + key + 模型名三项即可），也可以指向本机跑的 Ollama / vLLM。' +
  '内置额度恢复后随时可以切回来。最新状态见服务条款页面。'

const globalBreaker = {
  name: 'APIError',
  data: {
    message: BREAKER_MSG, statusCode: 402, isRetryable: false,
    responseBody: JSON.stringify({
      error: {
        message: BREAKER_MSG, type: 'insufficient_quota',
        code: 'global_quota_exceeded', param: null,
        global_used_today: 88306, global_limit: 1000,
        reset_at: '2026-09-20T00:00:00+08:00',
      },
    }),
  },
}

const serviceDisabled = {
  name: 'APIError',
  data: {
    message: DISABLED_MSG, statusCode: 403, isRetryable: false,
    responseBody: JSON.stringify({
      error: {
        message: DISABLED_MSG, type: 'api_error',
        code: 'service_disabled', param: null,
      },
    }),
  },
}

const cases = []
const t = (name, fn) => cases.push([name, fn])
const ok = (cond, msg) => { if (!cond) throw new Error(msg) }

t('内置额度用完 · 标题说人话、正文就是网关那段中文引导', () => {
  const v = describeModelError(builtinQuota)
  ok(v, '应该画错误卡')
  ok(v.title.includes('额度用完'), `标题不对:${v.title}`)
  ok(v.hint === QUOTA_MSG, '正文必须是网关原话,不能套我们自己的模板')
  ok(!v.raw, '中文引导已经在正文里了,不该再折叠一份原始信息')
  ok(!/请求太频繁|稍等一会儿/.test(v.hint), '不能说成限流 —— 等多久都没用')
})

t('内置额度用完 · 只有 data.message 没有 responseBody 时也认得出', () => {
  const v = describeModelError({ name: 'APIError', data: { message: QUOTA_MSG, statusCode: 402 } })
  ok(v.hint === QUOTA_MSG, '上游给了中文就原样用')
})

t('限流 · 用网关的话,不用模板', () => {
  const m = '请求太频繁了：每分钟最多 20 次，这一分钟已经第 21 次。等一分钟再试即可。'
  const v = describeModelError({
    name: 'APIError',
    data: { statusCode: 429, message: m,
            responseBody: JSON.stringify({ error: { code: 'rate_limited', message: m } }) },
  })
  ok(v.title.includes('太频繁'), v.title)
  ok(v.hint === m, v.hint)
})

t('DeepSeek 余额 0 · 老行为一字不变', () => {
  const v = describeModelError(deepseek402)
  ok(v.title.includes('余额不足'), v.title)
  ok(v.raw === 'Insufficient Balance', '英文原话要折叠展示,不翻译不改写')
})

t('全局熔断 · 标题说的是「整体用满」而不是「你的余额不足」', () => {
  const v = describeModelError(globalBreaker)
  ok(v, '应该画错误卡')
  ok(v.title.includes('整体用满'), `标题不对:${v.title}`)
  ok(v.hint === BREAKER_MSG, '正文必须是网关原话')
  // 402 会落进老的「模型账户余额不足」分支 —— 那句话会让用户去充值,
  // 而真相是全平台到顶了、跟他一分钱关系都没有。必须被 code 分支先截住。
  ok(!/余额不足|充值/.test(v.title + v.hint), '不能说成余额不足')
})

t('整体停用 · 403 不能被说成「模型密钥无效」', () => {
  const v = describeModelError(serviceDisabled)
  ok(v, '应该画错误卡')
  ok(v.title.includes('暂停服务'), `标题不对:${v.title}`)
  ok(v.hint === DISABLED_MSG, '正文必须是网关原话')
  // 老分支里 401/403 一律写「模型密钥无效,请管理员检查 LLM_API_KEY」——
  // 用户会去换一把完全没问题的 key。必须被 code 分支先截住。
  ok(!/密钥无效|填错|过期|撤销/.test(v.title + v.hint), '不能说成 key 有问题')
})

t('两道闸门的状态码都必须是不可重试的', () => {
  // AI SDK 的 isRetryable = 408|409|429|>=500。落进这些里就会无限转圈,
  // 而这两段中文一个字都到不了界面 —— P2 用 17 分钟买到的教训。
  for (const [name, err] of [['熔断', globalBreaker], ['停用', serviceDisabled]]) {
    const sc = err.data.statusCode
    ok(![408, 409, 429].includes(sc) && sc < 500, `${name}用了可重试的状态码 ${sc}`)
  }
})

t('用户点了停止 · 不画错误卡', () => {
  ok(describeModelError({ name: 'MessageAbortedError' }) === null, '停止不是故障')
})

t('认不出的失败 · 如实写,不猜原因', () => {
  const v = describeModelError({ name: 'APIError', data: { statusCode: 418, message: 'teapot' } })
  ok(v.title.includes('模型调用失败'), v.title)
  ok(v.raw === 'teapot', '上游原话要带上')
})

t('坏掉的 responseBody 不许把整张卡打崩', () => {
  for (const body of ['{不是 JSON', '', null, 42, { error: 'not an object' }]) {
    const v = describeModelError({ name: 'APIError', data: { statusCode: 500, responseBody: body } })
    ok(v && v.title, `responseBody=${JSON.stringify(body)} 时没给出错误卡`)
  }
})

let bad = 0
for (const [name, fn] of cases) {
  try { fn(); console.log('✅', name) } catch (e) { bad++; console.error('❌', name, '·', e.message) }
}
console.log(bad ? `\n${bad}/${cases.length} 条不通过` : `\n${cases.length} 条全部通过`)
process.exit(bad ? 1 : 0)
