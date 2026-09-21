'use client'
// 向导共用的小组件与样式。
//
// **为什么单独一个文件**:Next.js 的 app router 只允许 page.tsx 导出 default 与
// 几个约定字段(metadata / revalidate 之类),多导出一个 `td` 就会构建失败:
//   Type error: Page "app/setup/page.tsx" does not match the required types
//   of a Next.js Page. "td" is not a valid Page export field.
import type { CSSProperties, ReactNode } from 'react'
import { HUNTER } from '../../lib/hunter-theme'

export function Box({
  tone = 'plain', icon, title, children,
}: {
  tone?: 'plain' | 'ok' | 'warn' | 'fail'
  icon?: ReactNode
  title?: string
  children: ReactNode
}) {
  const accent = tone === 'fail' ? HUNTER.UP : tone === 'warn' ? '#9B571F'
    : tone === 'ok' ? HUNTER.SUCCESS : HUNTER.LINE
  return (
    <div style={{
      background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}`,
      borderTop: `3px solid ${accent}`, borderRadius: HUNTER.R_LG, padding: 18,
      color: HUNTER.INK_S, fontSize: 14, lineHeight: 1.7,
    }}>
      {title && (
        <div style={{
          fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, color: HUNTER.INK,
          marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8,
        }}>{icon}{title}</div>
      )}
      {children}
    </div>
  )
}

export function Tag({ tone, children }: { tone: 'ok' | 'warn' | 'fail' | 'plain'; children: ReactNode }) {
  const map = {
    ok: [HUNTER.TAG_OK_BG, HUNTER.TAG_OK_FG],
    warn: [HUNTER.TAG_WARN_BG, HUNTER.TAG_WARN_FG],
    fail: ['#FBEBEA', HUNTER.UP],
    plain: [HUNTER.PAPER2, HUNTER.INK_F],
  } as const
  const [bg, fg] = map[tone]
  return (
    <span style={{
      background: bg, color: fg, borderRadius: 6, padding: '2px 8px',
      fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
    }}>{children}</span>
  )
}

export function btn(kind: 'primary' | 'ghost' = 'primary', disabled = false): CSSProperties {
  return {
    marginTop: 14, padding: '10px 18px', borderRadius: HUNTER.R_MD,
    fontSize: 14, fontWeight: 600, cursor: disabled ? 'not-allowed' : 'pointer',
    border: kind === 'ghost' ? `1.5px solid ${HUNTER.LINE}` : 'none',
    background: kind === 'ghost' ? 'transparent' : disabled ? '#C9B9A5' : HUNTER.THEME,
    color: kind === 'ghost' ? HUNTER.INK_S : '#fff',
  }
}

export function tbl(): CSSProperties {
  return { width: '100%', borderCollapse: 'collapse', marginTop: 12, fontSize: 13 }
}

export function td(head = false): CSSProperties {
  return {
    padding: '7px 8px', borderBottom: `1px solid ${HUNTER.LINE}`,
    color: head ? HUNTER.INK : HUNTER.INK_S, fontWeight: head ? 600 : 400,
    wordBreak: 'break-all',
  }
}
