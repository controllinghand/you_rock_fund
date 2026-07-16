import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Bell, Trash2 } from 'lucide-react'
import axios from 'axios'

const LAST_SEEN_KEY = 'yrvi_alerts_last_seen'

// Strip the Discord markdown the alert strings carry (**bold**, `code`) for a
// cleaner in-app rendering. Newlines are preserved (rendered pre-wrap).
function clean(msg) {
  return (msg || '').replace(/\*\*/g, '').replace(/`/g, '')
}

function relTime(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (s < 60)      return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60)      return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24)      return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

// Absolute wall-clock time. Shows just the time for today's alerts; prepends the
// date for older ones so an "11h ago" can't be mistaken for the wrong day.
function absTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (d.toDateString() === new Date().toDateString()) return time
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
}

// Full date+time for the hover tooltip (exact, unambiguous).
function fullTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

const DOT = {
  critical: 'bg-red-500',
  warning:  'bg-yellow-400',
  resolved: 'bg-green-400',
  info:     'bg-gray-400',
}

const SEV_RANK = { critical: 3, warning: 2, resolved: 1, info: 1 }

export default function AlertsBell() {
  const [alerts, setAlerts]   = useState([])
  const [latestId, setLatest] = useState(0)
  const [open, setOpen]       = useState(false)
  const [lastSeen, setLastSeen] = useState(() => {
    const v = parseInt(localStorage.getItem(LAST_SEEN_KEY) || '0', 10)
    return Number.isNaN(v) ? 0 : v
  })
  const [pos, setPos]         = useState(null)
  const wrapRef  = useRef(null)
  const panelRef = useRef(null)

  // ── Poll the feed every 30s (matches the rest of the StatusBar) ──
  useEffect(() => {
    const fetch = () => axios.get('/api/alerts', { params: { limit: 50 } })
      .then(r => {
        setAlerts(r.data?.alerts || [])
        setLatest(r.data?.latest_id || 0)
      })
      .catch(() => {})
    fetch()
    const t = setInterval(fetch, 30000)
    return () => clearInterval(t)
  }, [])

  // ── Placement ──
  // The panel is portalled to <body> and fixed-positioned rather than absolutely
  // positioned inside the bell. The StatusBar sets overflow-x on itself at lg+,
  // and a container with overflow-x:auto computes overflow-y to auto as well —
  // an absolute panel would be trapped inside the 48px bar behind a scrollbar.
  // Fixed + portal keeps it clear of any ancestor's overflow for good.
  useLayoutEffect(() => {
    if (!open) return
    const GAP = 8, EDGE = 8, IDEAL_W = 384  // 24rem
    const place = () => {
      const r = wrapRef.current?.getBoundingClientRect()
      if (!r) return
      // Right-align to the bell, but clamp so neither edge leaves the viewport.
      // On a phone the bell is not the rightmost item, so an unclamped
      // right-anchor hangs the panel off the left side.
      const width = Math.min(IDEAL_W, window.innerWidth - EDGE * 2)
      const right = Math.min(
        Math.max(EDGE, window.innerWidth - r.right),
        window.innerWidth - width - EDGE
      )
      setPos({
        top:  r.bottom + GAP,
        right,
        width,
        maxH: Math.max(160, window.innerHeight - r.bottom - GAP - EDGE),
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)  // capture: any scrolling ancestor
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])

  // Close on outside click (the panel lives outside wrapRef, so check both) or Esc
  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (wrapRef.current?.contains(e.target)) return
      if (panelRef.current?.contains(e.target)) return
      setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const unread = alerts.filter(a => a.id > lastSeen)
  const unreadCount = unread.length
  const topSev = unread.reduce(
    (acc, a) => (SEV_RANK[a.severity] > SEV_RANK[acc] ? a.severity : acc),
    'info'
  )
  const badgeColor = unreadCount === 0 ? '' :
    topSev === 'critical' ? 'bg-red-500' :
    topSev === 'warning'  ? 'bg-yellow-400 text-gray-900' : 'bg-blue-500'

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && latestId > lastSeen) {
      // Opening the panel marks everything currently in the feed as seen.
      setLastSeen(latestId)
      localStorage.setItem(LAST_SEEN_KEY, String(latestId))
    }
  }

  function clearAll() {
    axios.delete('/api/alerts')
      .then(() => { setAlerts([]); setLatest(0) })
      .catch(() => {})
  }

  return (
    <div className="relative" ref={wrapRef}>
      <button
        onClick={toggle}
        title="Alerts"
        className="relative p-1.5 rounded-lg text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
      >
        <Bell size={14} />
        {unreadCount > 0 && (
          <span className={`absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 rounded-full text-[10px] font-bold leading-[15px] text-white text-center ${badgeColor}`}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && pos && createPortal(
        <div
          ref={panelRef}
          style={{
            top:       pos.top,
            right:     pos.right,
            width:     pos.width,
            maxHeight: Math.min(pos.maxH, 448),   // 28rem, or less when the viewport is short
          }}
          className="fixed flex flex-col bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl shadow-2xl z-50 overflow-hidden"
        >
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 dark:border-gray-800">
            <span className="text-sm font-semibold text-gray-900 dark:text-white">
              Alerts
              {alerts.length > 0 && (
                <span className="ml-1.5 text-xs font-normal text-gray-500">{alerts.length}</span>
              )}
            </span>
            {alerts.length > 0 && (
              <button
                onClick={clearAll}
                title="Clear all"
                className="flex items-center gap-1 text-xs text-gray-500 hover:text-red-400 transition-colors"
              >
                <Trash2 size={12} /> Clear
              </button>
            )}
          </div>

          <div className="overflow-y-auto">
            {alerts.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-gray-400 dark:text-gray-600">
                No alerts — all quiet.
              </div>
            ) : (
              alerts.map(a => (
                <div
                  key={a.id}
                  className={`flex gap-2.5 px-4 py-2.5 border-b border-gray-100 dark:border-gray-800/60 ${
                    a.id > lastSeen ? 'bg-blue-50/50 dark:bg-blue-950/20' : ''
                  }`}
                >
                  <div className={`mt-1 w-2 h-2 rounded-full shrink-0 ${DOT[a.severity] || DOT.info}`} />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-words leading-snug">
                      {clean(a.message)}
                    </p>
                    <p className="mt-1 text-[10px] text-gray-400 dark:text-gray-600" title={fullTime(a.ts)}>
                      {absTime(a.ts)} · {relTime(a.ts)}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}
