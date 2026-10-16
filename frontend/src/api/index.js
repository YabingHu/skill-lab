import { ElMessage } from 'element-plus'

async function request(method, url, body, opts = {}) {
  const init = { method, headers: {} }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch('/api' + url, init)
  } catch (e) {
    if (!opts.silent) ElMessage.error('网络错误：' + e.message)
    throw e
  }
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `${res.status} ${res.statusText}`
    if (!opts.silent) ElMessage.error(msg)
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return data
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, opts),
  post: (url, body, opts) => request('POST', url, body ?? {}, opts),
  patch: (url, body, opts) => request('PATCH', url, body, opts),
  del: (url, opts) => request('DELETE', url, undefined, opts),
}

export function qs(params) {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v) })
  const s = p.toString()
  return s ? '?' + s : ''
}

/** 轮询后台任务直到结束，onUpdate(job) 每次拿到新日志时回调 */
export async function pollJob(jobId, onUpdate, interval = 700) {
  let offset = 0
  const logs = []
  for (;;) {
    const j = await api.get(`/jobs/${jobId}?offset=${offset}`, { silent: true })
    logs.push(...j.logs)
    offset = j.offset
    onUpdate && onUpdate({ ...j, logs: [...logs] })
    if (j.status !== 'running') return { ...j, logs }
    await new Promise(r => setTimeout(r, interval))
  }
}

/** 下载文件：失败时提示后端返回的原因，成功时按 Content-Disposition 的文件名保存 */
export async function download(url, fallbackName = 'download') {
  const res = await fetch('/api' + url)
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try { msg = (await res.json()).detail || msg } catch { /* ignore */ }
    ElMessage.error(msg)
    return false
  }
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i)
  const name = m ? decodeURIComponent(m[1]) : fallbackName
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 10000)
  return true
}

export function downloadConversation(conv) {
  return download(`/conversations/${encodeURIComponent(conv)}/download`, `llm-calls-${conv}.jsonl`)
}
