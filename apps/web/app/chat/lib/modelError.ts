/**
 * 把 opencode 记在 assistant 消息 `info.error` 上的失败对象翻成给用户看的中文说明。
 *
 * ## 为什么需要(2026-09-17 本地实测)
 *
 * 模型上游返回 402 Insufficient Balance(DeepSeek 账户余额 0)时,opencode 会把
 * `{name:'APIError', data:{statusCode:402, message:'Insufficient Balance', ...}}`
 * 写进那条 assistant 消息的 `error` 字段,parts 为空。前端原来不读这个字段,
 * 于是界面只剩头像和一句「深度思考完成」,下面空白 —— 用户以为 AI 卡住了,
 * 不知道是额度问题,也不知道该找谁。
 *
 * ## 口径
 *
 * - 用户主动点停止(`MessageAbortedError`)不是故障,返回 null,不画错误卡。
 * - 按状态码归类给出「原因 + 下一步」;认不出的类别如实写「模型调用失败」,
 *   附上游原话(外部原始数据,不翻译不改写),不去猜原因。
 * - **上游已经给了中文引导就原样用它,不要再套我们自己的模板**(2026-09-19 · P2)。
 *   内置额度网关(`/api/saas/llm/*`)在额度用完 / 限流 / 并发超限时返回的是
 *   OpenAI 兼容错误体,`message` 是一段写清「用完了、几点重置、现在能怎么办」的中文。
 *   按状态码套模板的话,429 会被写成「模型请求太频繁,稍等一会儿再重发」——
 *   而真相是今天的额度用完了,等多久都没用,那条中文引导反倒被折叠进「原始信息」里。
 */
export interface ModelErrorView {
  title: string
  hint: string
  /** 上游原话,可能是英文 —— 作为原始数据折叠展示 */
  raw?: string
}

/** OpenAI 兼容错误体里的 `error.code`。不同网关字段位置不一,几处都找一遍。 */
function upstreamError(data: any, raw?: string): { code: string; message: string } {
  const pick = (o: any) =>
    (o && typeof o === 'object' && o.error && typeof o.error === 'object') ? o.error : null
  let e = pick(data)
  // opencode 有时把上游正文原样放在 responseBody 里
  for (const k of ['responseBody', 'body', 'response']) {
    if (e) break
    const v = data?.[k]
    if (typeof v === 'string' && v.trim().startsWith('{')) {
      try { e = pick(JSON.parse(v)) } catch { /* 不是 JSON 就算了,不猜 */ }
    } else if (v && typeof v === 'object') {
      e = pick(v)
    }
  }
  // 最后一手:message 本身就是那段 JSON
  if (!e && raw && raw.trim().startsWith('{')) {
    try { e = pick(JSON.parse(raw)) } catch { /* 同上 */ }
  }
  return {
    code: String(e?.code || '').trim(),
    message: String(e?.message || '').trim(),
  }
}

/** 内置额度网关自己会把话说清楚的那几种。见 hermes `app/services/llm_quota.py`。 */
const GATEWAY_CODES: Record<string, string> = {
  daily_quota_exceeded: '今天的内置额度用完了',
  rate_limited: '请求太频繁,被网关限流了',
  too_many_concurrent: '同时在跑的请求太多了',
  input_too_large: '这次发过去的内容太长了',
  hunter_key_required: '这台实例还没填 Hunter 平台 key',
  model_not_allowed: '内置额度不支持这个模型名',
  // ── P3(2026-09-19)· 对外开放之后的两道全局闸门 ──
  // 两者的状态码都**不是** 5xx,否则 AI SDK 会判为可重试、无限转圈,
  // 这段中文又一个字都到不了界面(P2 踩过,见文件头)。
  //   service_disabled      403 · 我们把内置额度整体关了(运维开关)
  //   global_quota_exceeded 402 · 全平台当日总量熔断,跟你个人额度无关
  service_disabled: '内置额度当前已暂停服务',
  global_quota_exceeded: '内置额度今天整体用满了',
}

export function describeModelError(err: any): ModelErrorView | null {
  if (!err || typeof err !== 'object') return null
  const name = String(err.name || '')
  if (name === 'MessageAbortedError') return null

  const data = err.data && typeof err.data === 'object' ? err.data : {}
  const status = Number(data.statusCode)
  const raw = String(data.message || err.message || '').trim() || undefined
  const low = (raw || '').toLowerCase()

  // ① 上游是我们自己的内置额度网关 —— 它给的中文引导比任何模板都准,原样用。
  const up = upstreamError(data, raw)
  if (up.code && GATEWAY_CODES[up.code] && up.message) {
    return { title: `${GATEWAY_CODES[up.code]},这次没有生成内容`, hint: up.message }
  }
  // ② 认不出 code,但已经拿到一段中文且足够长 —— 同样直接用,别套模板。
  //    判据只看「有没有中文」,不猜是哪家网关:能写中文引导的上游就该让用户看到原话。
  //    `up.message` 为空时退回 `raw` —— 有的客户端只把上游的 error.message 放进
  //    `data.message`,不带 responseBody,那时整段中文引导全在 raw 里。
  const zh = up.message || (raw && !raw.trim().startsWith('{') ? raw : '')
  if (zh && /[\u4e00-\u9fa5]/.test(zh) && zh.length >= 12) {
    const title = up.code && GATEWAY_CODES[up.code]
      ? `${GATEWAY_CODES[up.code]},这次没有生成内容`
      : '模型调用失败,这次没有生成内容'
    return { title, hint: zh }
  }

  if (status === 402 || /insufficient.?balance|quota|余额/.test(low)) {
    return {
      title: '模型账户余额不足,这次没有生成内容',
      hint: '上游模型服务拒绝了请求(额度用完)。请管理员充值,或在部署的 .env 里换一个有余额的 LLM_API_KEY 后重发。',
      raw,
    }
  }
  if (status === 401 || status === 403 || name === 'ProviderAuthError') {
    return {
      title: '模型密钥无效,这次没有生成内容',
      hint: '上游模型服务不认这个 key(可能填错、过期或被撤销)。请管理员检查 LLM_API_KEY 后重发。',
      raw,
    }
  }
  if (status === 429) {
    return {
      title: '模型请求太频繁,被上游限流',
      hint: '稍等一会儿再重发;如果一直这样,说明账户的并发或速率额度不够。',
      raw,
    }
  }
  if (name === 'MessageOutputLengthError') {
    return {
      title: '回答超出长度上限,被截断',
      hint: '可以让它分几次回答,或把问题拆小一点。',
      raw,
    }
  }
  if (Number.isFinite(status) && status >= 500) {
    return {
      title: '模型服务暂时不可用',
      hint: '上游返回了服务端错误,通常过一会儿自己恢复,稍后重发即可。',
      raw,
    }
  }
  return {
    title: '模型调用失败,这次没有生成内容',
    hint: '可以重发一次;反复出现请把下面的原始信息发给管理员。',
    raw,
  }
}
