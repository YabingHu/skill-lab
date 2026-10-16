export function fmtTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return '-'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`
  const m = Math.floor(ms / 60000)
  return `${m}m${Math.round((ms % 60000) / 1000)}s`
}

export function fmtNum(n) {
  if (n === null || n === undefined) return '-'
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (n >= 1e4) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}

export function pretty(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') {
    const s = v.trim()
    if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
      try { return JSON.stringify(JSON.parse(s), null, 2) } catch { /* ignore */ }
    }
    return v
  }
  return JSON.stringify(v, null, 2)
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    return true
  }
}

export const STATUS_META = {
  done: { label: '已执行', type: 'success', color: '#16a34a' },
  out_of_order: { label: '顺序不符', type: 'warning', color: '#d97706' },
  missing: { label: '未执行', type: 'danger', color: '#dc2626' },
  partial: { label: '部分执行', type: 'warning', color: '#d97706' },
  skipped: { label: '跳过', type: 'danger', color: '#dc2626' },
  deviated: { label: '偏离', type: 'danger', color: '#dc2626' },
}

// 调用类型配色：取自经过色盲校验的分类色板（固定顺序），状态色另行保留
export const TYPE_COLORS = {
  LLM: '#2a78d6', CLI: '#eb6834', API: '#1baf7a', FILE: '#eda100', TOOL: '#8a8a86',
}

export function fmtSize(n) {
  if (n === null || n === undefined) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
