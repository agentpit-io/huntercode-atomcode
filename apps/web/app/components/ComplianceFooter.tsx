// 全站合规底栏(§3.D.3.4)· 每个页面底部常驻
// 显示:仅供研究 · Apache 2.0 · 免责/隐私链接 · 开源地址(GitCode 主仓 + GitHub 镜像)
// 不使用 next/link · 避免与 auth guard 状态冲突
'use client'

/** 本发行版的主仓在 GitCode（总控规则：GitCode 是主仓，GitHub 是镜像）。 */
const GITCODE_REPO = 'https://gitcode.com/agentpit-io/huntercode-atomcode'
const GITHUB_MIRROR = 'https://github.com/agentpit-io/huntercode-atomcode'

export default function ComplianceFooter() {
  return (
    <footer className="fixed bottom-0 left-0 right-0 z-30 pointer-events-none">
      <div className="max-w-6xl mx-auto px-3 py-1.5 flex items-center justify-center gap-3
                      text-[10px] select-none"
           style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
        <span className="pointer-events-auto">
          Hunter-AtomCode · 开源(Apache 2.0)
        </span>
        <span>·</span>
        <a href="/legal/disclaimer" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>免责声明</a>
        <span>·</span>
        <a href="/legal/privacy" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>隐私</a>
        <span>·</span>
        {/* 开源地址 · 主仓在 GitCode(AtomGit 生态)，所以挂 AtomGit 的标。
            原图 244×78，按高度 20px 等比缩到 63×20 —— 底栏整行才 10px 字号，
            再大就把这一行撑起来、挡住对话框底边。 */}
        <a href={GITCODE_REPO} target="_blank" rel="noopener noreferrer"
           title="开源地址(GitCode 主仓)"
           className="pointer-events-auto hover:underline inline-flex items-center gap-1.5"
           style={{ color: 'inherit' }}>
          <img src="/brand/atomgit-logo.png" alt="AtomGit"
               width={63} height={20}
               style={{ width: 63, height: 20, display: 'block', opacity: 0.9 }} />
          <span>开源地址</span>
        </a>
        <span>·</span>
        <a href={GITHUB_MIRROR} target="_blank"
           rel="noopener noreferrer" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>GitHub 镜像</a>
        <span>·</span>
        <span className="pointer-events-auto">仅供研究 · 非投资建议</span>
      </div>
    </footer>
  )
}
