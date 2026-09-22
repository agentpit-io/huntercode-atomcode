# 端到端脚本（M3 起）

两套都**在测试机上跑**，对着真部署打，不 mock。

| 文件 | 验什么 |
|---|---|
| `m3-web.mjs` | Playwright（chromium）真浏览器 10 步验收：登录 → 新建对话 → 问一个会调 MCP 的问题 → 工具卡片 → 通用卡片进 ArtifactPanel → 触发技能 → Kronos 图 → 刷新恢复 → 中止生成 → 断网重连 |
| `m3-sse-reconnect.mjs` | 只断 SSE（不断那条挂着的 `POST /message`）的重连测试。浏览器断网会把两件事一起断掉，混在一起看不清是谁的问题 |

## 怎么跑

前置：测试机 `~/hca/pw` 里装过 `playwright@1.63.0` + chromium（`npx playwright install chromium --with-deps`）。
**脚本要从 `~/hca/pw` 跑** —— `node_modules` 在那儿，从仓库目录跑会 `ERR_MODULE_NOT_FOUND`。

```bash
# 开发机：先把仓库同步过去
bash tools/eval/sync-to-test.sh

# 测试机
cd ~/hca/pw
cp ~/hca/repo/tools/e2e/m3-web.mjs .
node ./m3-web.mjs --base http://localhost:3200 --secrets ~/hca/secrets --out ~/hca/pw/out
node ~/hca/repo/tools/e2e/m3-sse-reconnect.mjs --base http://localhost:3200 --secrets ~/hca/secrets
```

`m3-web.mjs` 支持 `--only <用例名,…>` 只跑某几步（调试时省 token），用例名照抄汇总里打的那串。

跑完 `--out` 目录里是每步一张截图 + `result.json`（脚本直接写的原始结论，报告里的数字都从这儿来）。

## 想完整走一遍「首次登录」

管理员账号第一次登录会连弹两层全屏遮罩（合规声明、偏好引导），acked 之后就不再弹。要重新走一遍，把那两个标记复位：

```bash
EMAIL=$(grep '^email:' ~/hca/secrets/admin.txt | cut -d' ' -f2)
docker exec hca-postgres psql -U hunter -d hunter -tAc \
  "update users set compliance_ack_at=null, compliance_ack_version=null where email_lower=lower('$EMAIL')"
docker exec hca-postgres psql -U hunter -d hunter -tAc \
  "update user_profile set onboarded=false where user_id in (select id::text from users where email_lower=lower('$EMAIL'))"
```

## 写新用例时的一条规矩

**断言只认被测系统产出的东西，不认用户输进去的东西。**

M3 这三处都栽在这上面（都已改正，理由写在脚本注释里）：

- 「问一个会调 MCP 的问题」原来断言 `/stock_quickview/` —— 那四个字在用户发的那句话里就有，工具没调也命中，于是把一次真实的「模型绕过 MCP 层」判成了过；
- 「触发一个技能」原来断言 `/风险|保守/` —— 同样出自用户输入；
- 「工具卡片进 ArtifactPanel」原来是「点一下就算过」，前后两张截图 `md5sum` 一模一样。
