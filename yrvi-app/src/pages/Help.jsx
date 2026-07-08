import { useState, useEffect } from 'react'
import axios from 'axios'
import { Activity, BookOpen, MessageSquare, CheckCircle, AlertTriangle, XCircle, RefreshCw, ExternalLink, Send, SlidersHorizontal, ChevronDown, RotateCcw, Monitor, Heart } from 'lucide-react'

const FAQ_URL = 'https://github.com/controllinghand/you_rock_fund/blob/main/FAQ.md'

function Section({ icon: Icon, title, children }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 space-y-4">
      <div className="text-gray-900 dark:text-white font-semibold text-sm flex items-center gap-2">
        <Icon size={15} className="text-blue-500" />
        {title}
      </div>
      {children}
    </div>
  )
}

function StatusIcon({ status }) {
  if (status === 'ok')   return <CheckCircle  size={15} className="text-green-500 shrink-0" />
  if (status === 'warn') return <AlertTriangle size={15} className="text-amber-500 shrink-0" />
  if (status === 'error') return <XCircle     size={15} className="text-red-500  shrink-0" />
  return <div className="w-[15px] h-[15px] rounded-full bg-blue-400 shrink-0" />
}

function CheckRow({ c }) {
  const hasSnippet = Array.isArray(c.log_snippet)
  const [expanded,     setExpanded]     = useState(false)
  const [resetting,    setResetting]    = useState(false)
  const [resetMsg,     setResetMsg]     = useState(null)
  const [resetError,   setResetError]   = useState(null)

  const doReset = async () => {
    setResetting(true)
    setResetMsg(null)
    setResetError(null)
    try {
      const res = await axios.post('/api/gateway/reset-installation')
      setResetMsg(res.data.message)
    } catch (err) {
      setResetError(err.response?.data?.detail ?? err.message ?? 'Reset failed')
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="px-4 py-3 bg-white dark:bg-gray-900">
      <div className="flex items-center gap-3">
        <StatusIcon status={c.status} />
        <div className="min-w-[130px] text-sm font-medium text-gray-700 dark:text-gray-300 shrink-0">
          {c.name}
        </div>
        <div className="text-sm text-gray-500 dark:text-gray-500 flex-1">
          {c.detail}
        </div>
        {c.reset_available && !resetMsg && (
          <button
            onClick={doReset}
            disabled={resetting}
            className="flex items-center gap-1.5 px-3 py-1 bg-amber-600 hover:bg-amber-500 disabled:opacity-60 disabled:cursor-wait text-white text-xs font-medium rounded-md transition-colors shrink-0"
          >
            <RotateCcw size={11} className={resetting ? 'animate-spin' : ''} />
            {resetting ? 'Resetting…' : 'Reset Installation'}
          </button>
        )}
        {hasSnippet && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 shrink-0 transition-colors"
            title="Show log lines"
          >
            <ChevronDown size={13} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
            logs
          </button>
        )}
      </div>

      {/* Reset feedback */}
      {resetMsg && (
        <div className="mt-2 ml-[27px] flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          <RotateCcw size={11} />
          {resetMsg}
        </div>
      )}
      {resetError && (
        <div className="mt-2 ml-[27px] text-xs text-red-500">{resetError}</div>
      )}

      {/* Log snippet */}
      {hasSnippet && expanded && (
        <div className="mt-2 ml-[27px] font-mono text-xs text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-800/60 rounded-md px-3 py-2 space-y-0.5 overflow-x-auto">
          {c.log_snippet.length > 0
            ? c.log_snippet.map((line, i) => <div key={i} className="whitespace-nowrap">{line}</div>)
            : <div className="italic">No log lines captured yet</div>
          }
        </div>
      )}
    </div>
  )
}

function OverallBadge({ overall }) {
  const styles = {
    ok:    'bg-green-50 border-green-300 text-green-700 dark:bg-green-900/30 dark:border-green-800 dark:text-green-400',
    warn:  'bg-amber-50 border-amber-400 text-amber-800 dark:bg-amber-900/30 dark:border-amber-800 dark:text-amber-400',
    error: 'bg-red-50 border-red-300 text-red-700 dark:bg-red-900/30 dark:border-red-800 dark:text-red-400',
  }
  const labels = { ok: 'All systems go', warn: 'Needs attention', error: 'Action required' }
  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium ${styles[overall] ?? styles.ok}`}>
      <StatusIcon status={overall} />
      {labels[overall] ?? overall}
    </div>
  )
}

const SETTINGS_GROUPS = [
  {
    title: 'Fund Settings',
    items: [
      { label: 'Initial Fund Budget', default: '$250,000', range: '$10K – $2M',   description: 'Starting capital for CSP deployment. When Compound Weekly is off, this is always the deployment base.' },
      { label: '# Positions',         default: '5',        range: '1 – 10',        description: 'Target number of CSP positions to fill each Monday.' },
      { label: 'Min Position',        default: '$10,000',  range: '$5K – $100K',   description: 'Minimum capital allocated to any single CSP position.' },
      { label: 'Max Position',        default: '$90,000',  range: '$10K – $200K',  description: 'Maximum capital for any single position. The last position absorbs remaining budget up to this cap.' },
      { label: 'Compound Weekly',     default: 'On',       range: 'On / Off',      description: 'When on, uses your IBKR net liquidation as the Monday deployment budget so the fund grows as premiums accumulate. When off, always deploys the fixed initial budget.' },
    ],
  },
  {
    title: 'Screener Filters',
    items: [
      { label: 'Max Delta',                          default: '0.21',  range: '0.10 – 0.30', description: 'Maximum absolute delta for CSPs sold. Higher = more aggressive strike selection and more premium, but more assignment risk.' },
      { label: 'Min Buffer %',                       default: '5%',    range: '3% – 20%',    description: 'The strike must be at least this far below the current stock price. Higher = more downside cushion.' },
      { label: 'Earnings Filter',                    default: '7 days', range: '0 – 30 days', description: 'Skip tickers with earnings within this many days. Protects against earnings-driven moves.' },
      { label: 'Ignore Earnings Filter for Wheel CCs', default: 'Off', range: 'On / Off',    description: 'When on, covered calls are still sold on held positions even during earnings weeks. Has no effect on new CSP entries.' },
      { label: 'Stop Loss on Wheel Holdings',        default: 'Off',   range: 'On / Off',    description: 'When on, a holding is sold on Monday if its price has fallen more than the Stop Loss % below its assigned strike. The screener exit (dropping off the IV screener) is the primary exit — this is an optional additional layer.' },
      { label: 'Stop Loss %',                        default: '10%',   range: '0% – 50%',    description: 'How far below the assigned strike triggers a stop loss sale. Only applies when Stop Loss on Wheel Holdings is enabled.' },
    ],
  },
  {
    title: 'Liquidity Filters',
    items: [
      { label: 'Max Spread %',         default: '20%', range: '5% – 50%',    description: 'Skip a CSP if the bid/ask spread exceeds this percentage of the mid price. Protects against poor fills on illiquid options.' },
      { label: 'Min Bid Yield %',      default: '1%',  range: '0.5% – 3%',   description: 'Override the spread filter if the bid yield meets this threshold — useful when wide spreads are justified by high premium.' },
      { label: 'Max Spread Hard Cap %', default: '50%', range: '25% – 100%', description: 'Always skip regardless of yield if spread exceeds this. An absolute ceiling that cannot be overridden by bid yield.' },
    ],
  },
  {
    title: 'Execution',
    items: [
      { label: 'Monday Execution Time', default: '10:00 AM PST', range: 'Any time',  description: 'When the CSP pipeline fires each Monday. 10:00 AM PST (1:00 PM ET) is recommended for best liquidity and tighter spreads. Requires a scheduler restart to take effect.' },
      { label: 'Dry Run',               default: 'Off',          range: 'On / Off',  description: 'Simulate all orders without placing real trades. Fills are logged as dry_run. Useful for testing the pipeline or verifying a new configuration. When in live trading, enable this for extra protection before committing real money.' },
    ],
  },
]

function SettingsGroup({ group }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-800/50 hover:bg-gray-100 dark:hover:bg-gray-800 text-sm font-medium text-gray-700 dark:text-gray-300 transition-colors"
      >
        {group.title}
        <ChevronDown size={14} className={`text-gray-400 transition-transform ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && (
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {group.items.map(item => (
            <div key={item.label} className="px-4 py-3 space-y-0.5">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{item.label}</span>
                <span className="text-xs text-gray-400 dark:text-gray-500 shrink-0">
                  Default: <span className="font-mono">{item.default}</span>
                  {item.range && <> &middot; {item.range}</>}
                </span>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-500 leading-relaxed">{item.description}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function WordsOfEncouragement() {
  const [verse, setVerse] = useState(null)

  useEffect(() => {
    let alive = true
    axios.get('/api/verse-of-the-day')
      .then(res => { if (alive) setVerse(res.data) })
      .catch(() => { /* endpoint always returns a fallback; ignore */ })
    return () => { alive = false }
  }, [])

  return (
    <div className="bg-gradient-to-br from-rose-50 to-indigo-50 dark:from-rose-950/30 dark:to-indigo-950/30 border border-rose-200 dark:border-rose-900/50 rounded-xl p-5 space-y-3">
      <div className="text-gray-900 dark:text-white font-semibold text-sm flex items-center gap-2">
        <Heart size={15} className="text-rose-500" />
        Words of Encouragement
      </div>
      {verse ? (
        <figure className="space-y-2">
          <blockquote className="text-gray-700 dark:text-gray-200 text-base leading-relaxed italic">
            &ldquo;{verse.text}&rdquo;
          </blockquote>
          {verse.reference && (
            <figcaption className="text-sm font-medium text-rose-600 dark:text-rose-400">
              —{' '}
              {verse.source_url ? (
                <a
                  href={verse.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Verify at Blue Letter Bible"
                  className="inline-flex items-center gap-1 hover:underline"
                >
                  {verse.reference}
                  <ExternalLink size={11} className="opacity-70" />
                </a>
              ) : (
                verse.reference
              )}
            </figcaption>
          )}
        </figure>
      ) : (
        <div className="text-sm text-gray-400 dark:text-gray-500 italic">Loading today's verse…</div>
      )}
      <div className="text-[11px] text-gray-400 dark:text-gray-600 pt-1">
        World English Bible (public domain)
      </div>
    </div>
  )
}

export default function Help() {
  const [running, setRunning]     = useState(false)
  const [results, setResults]     = useState(null)
  const [error, setError]         = useState(null)

  const [fbType, setFbType]       = useState('bug')
  const [fbMessage, setFbMessage] = useState('')
  const [fbSending, setFbSending] = useState(false)
  const [fbResult, setFbResult]   = useState(null)  // {ok, text}

  const [gwRestarting, setGwRestarting] = useState(false)
  const [gwRestartMsg, setGwRestartMsg] = useState(null)  // {ok, text}

  const [viewStarting, setViewStarting] = useState(false)
  const [viewMsg, setViewMsg]           = useState(null)  // {ok, text}

  const openViewGateway = async () => {
    setViewStarting(true)
    setViewMsg(null)
    // Open the tab synchronously within the click gesture so popup blockers don't
    // eat it; we redirect it once the viewer is up, or close it on failure.
    const tab = window.open('', '_blank')
    try {
      const res  = await axios.post('/api/view-gateway/start')
      const port = res.data.port ?? '6080'
      const url  = `${window.location.protocol}//${window.location.hostname}:${port}`
      if (tab) tab.location = url
      else window.open(url, '_blank', 'noopener')
      setViewMsg({ ok: true, text: res.data.message })
    } catch (err) {
      if (tab) tab.close()
      setViewMsg({ ok: false, text: err.response?.data?.detail ?? err.message ?? 'Could not start View Gateway' })
    } finally {
      setViewStarting(false)
    }
  }

  const restartGateway = async () => {
    if (!window.confirm(
      'Restart the IB Gateway?\n\n' +
      'This recovers a wedged gateway (port open but API not responding) by ' +
      're-running login. On a LIVE account you must approve an IB Key 2FA push ' +
      'on your phone. Paper logs in automatically. Takes ~1–2 minutes.'
    )) return
    setGwRestarting(true)
    setGwRestartMsg(null)
    try {
      const res = await axios.post('/api/gateway/restart')
      setGwRestartMsg({ ok: true, text: res.data.message })
    } catch (err) {
      setGwRestartMsg({ ok: false, text: err.response?.data?.detail ?? err.message ?? 'Restart failed' })
    } finally {
      setGwRestarting(false)
    }
  }

  const submitFeedback = async () => {
    if (!fbMessage.trim()) return
    setFbSending(true)
    setFbResult(null)
    try {
      await axios.post('/api/feedback', { type: fbType, message: fbMessage.trim() })
      setFbResult({ ok: true, text: 'Sent — thanks for the feedback!' })
      setFbMessage('')
    } catch (err) {
      setFbResult({ ok: false, text: err.response?.data?.detail ?? 'Failed to send — try again' })
    } finally {
      setFbSending(false)
    }
  }

  const runDiag = async () => {
    setRunning(true)
    setResults(null)
    setError(null)
    try {
      const res = await axios.get('/api/diag')
      setResults(res.data)
    } catch (err) {
      setError(err.response?.data?.detail ?? err.message ?? 'Request failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="max-w-2xl space-y-5">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1">Help</h1>
        <div className="text-gray-500 text-sm">Diagnostics, documentation, and support</div>
      </div>

      {/* ── Words of Encouragement ──────────────────────────── */}
      <WordsOfEncouragement />

      {/* ── System Diagnostics ──────────────────────────────── */}
      <Section icon={Activity} title="System Diagnostics">
        <div className="text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
          Checks scheduler health, IB Gateway connectivity, last run times, market status,
          and live SPY market data (stock price + options bid/ask/delta).
          No trades are placed — read-only. Takes ~10–40 seconds when the gateway is running
          (it waits for the delayed options feed to populate, like the trader does).
          <span className="block mt-1"><strong>View Gateway</strong> opens a browser window
          showing the live IB Gateway screen (view-only) so you can see a login, 2FA prompt,
          or error dialog directly.</span>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={runDiag}
            disabled={running}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-60 disabled:cursor-wait text-white text-sm font-medium rounded-lg transition-colors"
          >
            <RefreshCw size={13} className={running ? 'animate-spin' : ''} />
            {running ? 'Running…' : results ? 'Run Again' : 'Run Diagnostics'}
          </button>

          <button
            onClick={restartGateway}
            disabled={gwRestarting}
            title="Recover a wedged gateway by restarting it (re-runs login; live needs IB Key 2FA)"
            className="flex items-center gap-2 px-4 py-2 border border-amber-600 text-amber-700 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-900/30 disabled:opacity-60 disabled:cursor-wait text-sm font-medium rounded-lg transition-colors"
          >
            <RotateCcw size={13} className={gwRestarting ? 'animate-spin' : ''} />
            {gwRestarting ? 'Restarting…' : 'Restart Gateway'}
          </button>

          <button
            onClick={openViewGateway}
            disabled={viewStarting}
            title="Open a browser window showing the live IB Gateway screen (view-only)"
            className="flex items-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-60 disabled:cursor-wait text-sm font-medium rounded-lg transition-colors"
          >
            <Monitor size={13} className={viewStarting ? 'animate-pulse' : ''} />
            {viewStarting ? 'Starting…' : 'View Gateway'}
          </button>
        </div>

        {gwRestartMsg && (
          <div className={`flex items-center gap-2 px-3 py-2 text-sm rounded-lg border ${
            gwRestartMsg.ok
              ? 'bg-amber-50 border-amber-300 text-amber-700 dark:bg-amber-900/30 dark:border-amber-800 dark:text-amber-400'
              : 'bg-red-900/30 border-red-800 text-red-400'
          }`}>
            <RotateCcw size={14} className="shrink-0" />
            {gwRestartMsg.text}
          </div>
        )}

        {viewMsg && (
          <div className={`flex items-center gap-2 px-3 py-2 text-sm rounded-lg border ${
            viewMsg.ok
              ? 'bg-blue-50 border-blue-300 text-blue-700 dark:bg-blue-900/30 dark:border-blue-800 dark:text-blue-400'
              : 'bg-red-900/30 border-red-800 text-red-400'
          }`}>
            <Monitor size={14} className="shrink-0" />
            {viewMsg.text}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-3 py-2 bg-red-900/30 border border-red-800 text-red-400 text-sm rounded-lg">
            <XCircle size={14} className="shrink-0" />
            {error}
          </div>
        )}

        {results && (
          <div className="space-y-3">
            <OverallBadge overall={results.overall} />

            <div className="divide-y divide-gray-100 dark:divide-gray-800 border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
              {results.checks.map((c) => (
                <CheckRow key={c.name} c={c} />
              ))}
            </div>

            <div className="text-xs text-gray-400 dark:text-gray-600">
              Run at {new Date(results.timestamp).toLocaleTimeString()}
            </div>
          </div>
        )}
      </Section>

      {/* ── FAQ & Troubleshooting ────────────────────────────── */}
      <Section icon={BookOpen} title="FAQ & Troubleshooting">
        <div className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
          Step-by-step answers for common setup issues — Docker failures, IB Gateway
          dialogs, market data errors, order fills, and more.
        </div>
        <a
          href={FAQ_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white text-sm font-medium rounded-lg transition-colors"
        >
          <ExternalLink size={13} />
          View FAQ on GitHub
        </a>
      </Section>

      {/* ── Settings Reference ──────────────────────────────── */}
      <Section icon={SlidersHorizontal} title="Settings Reference">
        <div className="text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
          All settings hot-reload — changes take effect immediately without restarting. Exception: Monday Execution Time requires a scheduler restart.
        </div>
        <div className="space-y-2">
          {SETTINGS_GROUPS.map(group => (
            <SettingsGroup key={group.title} group={group} />
          ))}
        </div>
      </Section>

      {/* ── Report a Bug / Feature Request ──────────────────── */}
      <Section icon={MessageSquare} title="Report a Bug or Request a Feature">
        <div className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
          Describe what happened or what you'd like to see — this goes straight to the
          YRVI team on Discord. No account needed.
        </div>

        <div className="space-y-3">
          {/* Type selector */}
          <div className="flex gap-2">
            {[
              { value: 'bug',     label: '🐛 Bug Report' },
              { value: 'feature', label: '💡 Feature Request' },
            ].map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setFbType(value)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                  fbType === value
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Message */}
          <textarea
            rows={4}
            placeholder={fbType === 'bug'
              ? 'What happened? What did you expect? Which page or feature?'
              : 'What would you like to see? What problem would it solve?'}
            value={fbMessage}
            onChange={e => { setFbMessage(e.target.value); setFbResult(null) }}
            className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 resize-none"
          />

          {/* Submit */}
          <div className="flex items-center gap-3">
            <button
              onClick={submitFeedback}
              disabled={fbSending || !fbMessage.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              <Send size={13} />
              {fbSending ? 'Sending…' : 'Send'}
            </button>

            {fbResult && (
              <span className={`text-sm font-medium flex items-center gap-1.5 ${fbResult.ok ? 'text-green-500' : 'text-red-400'}`}>
                {fbResult.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
                {fbResult.text}
              </span>
            )}
          </div>
        </div>
      </Section>
    </div>
  )
}
