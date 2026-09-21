#!/usr/bin/env node
// 筛选器官方示例跨层用例的第 2 步:用**真实的** screener.html 代码把后端解析结果回写成脚本。
// 用法见 apps/api/tests/test_screen_xlayer_official.py 文件头。
//   node xlayer_build.js <后端解析结果.json> <输出.json>
// 输出每个示例两份脚本:原样回写(buildScript(),与运行扫描 / 官方示例判定同一口径)、停用第一条之后的回写
'use strict'
const fs = require('fs')
const path = require('path')
const vm = require('vm')

const DIR = __dirname
const [, , inPath, outPath] = process.argv
if (!inPath || !outPath) { console.error('用法: node xlayer_build.js <in.json> <out.json>'); process.exit(2) }

function makeEl() {
  const el = {
    style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild: (c) => c, removeChild() {}, remove() {}, setAttribute() {}, getAttribute: () => null,
    addEventListener() {}, removeEventListener() {}, querySelector: () => makeEl(), querySelectorAll: () => [],
    closest: () => null, focus() {}, select() {}, setSelectionRange() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
    innerHTML: '', textContent: '', value: '', children: [],
  }
  return el
}
const store = {}
const doc = {
  body: makeEl(), documentElement: makeEl(), head: makeEl(), title: '', readyState: 'complete',
  getElementById: () => makeEl(), querySelector: () => makeEl(), querySelectorAll: () => [],
  createElement: () => makeEl(), createTextNode: () => makeEl(), addEventListener() {},
  location: { href: 'http://localhost/strategies/screener.html', search: '', pathname: '/strategies/screener.html' },
}
const ctx = {
  document: doc, console: { log() {}, warn() {}, error() {} },
  fetch: () => Promise.reject(new Error('xlayer: 不联网')),
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v) }, removeItem: (k) => { delete store[k] } },
  location: doc.location, history: { pushState() {}, replaceState() {} },
  navigator: { userAgent: 'xlayer', clipboard: { writeText: () => Promise.resolve() } },
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval, requestAnimationFrame: (f) => setTimeout(f, 0),
  URL, URLSearchParams, Blob: function () {}, FormData: function () {},
  alert() {}, confirm: () => true, prompt: () => null, addEventListener() {}, removeEventListener() {},
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  echarts: { init: () => ({ setOption() {}, resize() {}, dispose() {}, on() {} }), getInstanceByDom: () => null },
}
ctx.window = ctx
ctx.globalThis = ctx
process.on('unhandledRejection', () => {})
vm.createContext(ctx)
vm.runInContext(fs.readFileSync(path.join(DIR, 'app.js'), 'utf8'), ctx, { filename: 'app.js' })
const html = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi
let m
while ((m = re.exec(html))) {
  if (/\bsrc\s*=/.test(m[1])) continue
  vm.runInContext(m[2], ctx, { filename: 'screener.html' })
}

const items = JSON.parse(fs.readFileSync(inPath, 'utf8'))
const out = []
for (const it of items) {
  ctx.__P = it.parsed
  const res = vm.runInContext(`
    (function () {
      applyParsed(__P)
      var script = buildScript()
      var rows = condRows()
      var off = ''
      if (rows.length > 1 && S.combine !== 'custom') { rows[0].c.enabled = false; off = buildScript() }
      return { script: script, script_off: off }
    })()
  `, ctx)
  out.push({ key: it.key, market: it.market, script: res.script, script_off: res.script_off })
}
fs.writeFileSync(outPath, JSON.stringify(out, null, 1))
console.log('xlayer_build · ' + out.length + ' 个示例 → ' + outPath)
setImmediate(() => process.exit(0))
