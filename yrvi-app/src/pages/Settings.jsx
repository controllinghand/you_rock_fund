import { useEffect, useState, useCallback, useRef } from 'react'
import axios from 'axios'
import { Save, AlertTriangle, CheckCircle, Send, Sun, Moon, Monitor, RefreshCw, Power, RotateCcw, Upload, Download, RotateCw, ExternalLink, KeyRound, Pause, Play } from 'lucide-react'
import { useThemeContext } from '../ThemeProvider.jsx'

// Earliest allowed execution is 07:00 PST. The wheel check runs 5 min before
// execution and must land after the 6:30 open (to price CCs) yet before the CSP
// pipeline. 7:00 → wheel check at 6:55 (~25 min post-open), safe on live AND on
// paper's 15-min-delayed feed. The scheduler enforces the same floor server-side.
const MIN_EXEC_TIME = '07:00'

const PRESET_TIMES = [
  { value: '07:00', et: '10:00 AM ET', note: '30 min after open — earliest' },
  { value: '07:30', et: '10:30 AM ET', note: '1 hr after open' },
  { value: '08:00', et: '11:00 AM ET', note: '' },
  { value: '09:00', et: '12:00 PM ET', note: 'Noon ET' },
  { value: '10:00', et: '1:00 PM ET',  note: 'Recommended ★' },
  { value: '11:00', et: '2:00 PM ET',  note: '' },
]

function fmtExecTime(val) {
  if (!val) return ''
  const [hStr, mStr] = val.split(':')
  const h = parseInt(hStr, 10)
  const m = parseInt(mStr, 10)
  if (isNaN(h) || isNaN(m)) return val
  const pad = n => String(n).padStart(2, '0')
  const pst12 = h % 12 || 12
  const pstAP  = h >= 12 ? 'PM' : 'AM'
  const etH    = (h + 3) % 24
  const et12   = etH % 12 || 12
  const etAP   = etH >= 12 ? 'PM' : 'AM'
  return `${pst12}:${pad(m)} ${pstAP} PST (${et12}:${pad(m)} ${etAP} ET)`
}

function isPreset(val) {
  return PRESET_TIMES.some(p => p.value === val)
}

function SliderRow({ label, value, min, max, step = 1, format = v => v, onChange, description }) {
  const fillPct = max > min ? Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100)) : 0
  return (
    <div>
      <div className="flex items-center gap-4">
        <div className="w-40 shrink-0">
          <div className="text-gray-700 dark:text-gray-300 text-sm">{label}</div>
          <div className="text-blue-500 dark:text-blue-400 font-medium text-sm">{format(value)}</div>
        </div>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={e => onChange(Number(e.target.value))}
          className="yrvi-range flex-1"
          style={{ '--fill': `${fillPct}%` }}
        />
        <div className="flex gap-1 text-xs text-gray-400 dark:text-gray-600 w-28 shrink-0 justify-end">
          <span>{format(min)}</span>
          <span>–</span>
          <span>{format(max)}</span>
        </div>
      </div>
      {description && (
        <div className="ml-40 mt-1 text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
          {description}
        </div>
      )}
    </div>
  )
}

function Section({ title, emoji, children }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 space-y-4">
      <div className="text-gray-900 dark:text-white font-semibold text-sm flex items-center gap-2">
        <span>{emoji}</span>
        {title}
      </div>
      {children}
    </div>
  )
}

function Toggle({ label, sub, checked, onChange }) {
  return (
    <label className="flex items-center justify-between cursor-pointer select-none">
      <div>
        <div className="text-gray-700 dark:text-gray-300 text-sm">{label}</div>
        {sub && <div className="text-gray-500 dark:text-gray-600 text-xs mt-0.5">{sub}</div>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 shrink-0 ml-3 items-center rounded-full transition-colors ${
          checked ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'
        }`}
      >
        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-1'
        }`} />
      </button>
    </label>
  )
}

function TickerExcludeInput({ value, onChange }) {
  const [draft, setDraft] = useState('')
  const tickers = Array.isArray(value) ? value : []

  const add = () => {
    const sym = draft.trim().toUpperCase()
    if (sym && !tickers.includes(sym)) onChange([...tickers, sym].sort())
    setDraft('')
  }
  const remove = (sym) => onChange(tickers.filter(t => t !== sym))

  return (
    <div>
      <div className="text-gray-700 dark:text-gray-300 text-sm">Excluded Tickers</div>
      <div className="text-gray-500 dark:text-gray-600 text-xs mt-0.5 mb-2">
        Never traded by the wheel — no CSPs, no covered calls, never sold. Use for long-term holds.
      </div>
      <div className="flex flex-wrap gap-2 mb-2">
        {tickers.length === 0 && (
          <span className="text-xs text-gray-400 dark:text-gray-600 italic">None excluded</span>
        )}
        {tickers.map(sym => (
          <span key={sym} className="inline-flex items-center gap-1 text-xs font-mono bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded-full px-2.5 py-1">
            {sym}
            <button type="button" onClick={() => remove(sym)} className="text-gray-400 hover:text-red-500" title={`Remove ${sym}`}>×</button>
          </span>
        ))}
      </div>
      <input
        type="text"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add() } }}
        onBlur={add}
        placeholder="Add ticker (e.g. AAPL) — Enter to add"
        className="w-full text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-600 focus:outline-none focus:border-blue-500"
      />
    </div>
  )
}

const THEME_OPTIONS = [
  { value: 'system', label: 'System', icon: Monitor },
  { value: 'light',  label: 'Light',  icon: Sun },
  { value: 'dark',   label: 'Dark',   icon: Moon },
]

const TIMEZONES = [
  { value: 'America/Los_Angeles', label: 'Pacific  — America/Los_Angeles' },
  { value: 'America/Denver',      label: 'Mountain — America/Denver' },
  { value: 'America/Chicago',     label: 'Central  — America/Chicago' },
  { value: 'America/New_York',    label: 'Eastern  — America/New_York' },
  { value: 'America/Anchorage',   label: 'Alaska   — America/Anchorage' },
  { value: 'Pacific/Honolulu',    label: 'Hawaii   — Pacific/Honolulu' },
]

export default function SettingsPage() {
  const [settings, setSettings]           = useState(null)
  const [original, setOriginal]           = useState(null)
  const [saving, setSaving]               = useState(false)
  const [testing, setTesting]             = useState(false)
  const [msg, setMsg]                     = useState(null)
  const [showModal, setShowModal]         = useState(false)
  const [confirm, setConfirm]             = useState('')
  const [switching, setSwitching]         = useState(false)
  const [liveReady, setLiveReady]         = useState(null)
  const [liveMissing, setLiveMissing]     = useState([])
  const [liveChecking, setLiveChecking]   = useState(false)
  const [accountMasked, setAccountMasked] = useState('')
  const [restarting, setRestarting]         = useState(false)
  const [restartResult, setRestartResult]   = useState(null)
  const [patching, setPatching]             = useState(false)
  const [patchResult, setPatchResult]       = useState(null)
  const [resettingGw, setResettingGw]       = useState(false)
  const [resetGwResult, setResetGwResult]   = useState(null)
  const [tokenStatus, setTokenStatus]       = useState(null)
  const [refreshingToken, setRefreshingToken] = useState(false)
  const [refreshTokenResult, setRefreshTokenResult] = useState(null)
  const [timezone, setTimezone]                 = useState('')
  const [timezoneOriginal, setTimezoneOriginal] = useState('')
  const [tzSaving, setTzSaving]                 = useState(false)
  // What the running scheduler is actually using. Tracked separately from
  // `original` (the saved-to-disk baseline) because persisting a new time does
  // NOT live-reschedule the scheduler — only a restart does. Gating the "restart
  // scheduler" prompt on these (not on the dirty/saved state) keeps the button
  // visible after a plain Save, until the scheduler has actually been restarted
  // onto the new value. Initialized at load assuming disk and scheduler agree.
  const [appliedExecTime, setAppliedExecTime]   = useState(null)
  const [appliedTimezone, setAppliedTimezone]   = useState('')
  const [confirmReset, setConfirmReset]           = useState(false)
  const [showShutdownModal, setShowShutdownModal] = useState(false)
  const [shuttingDown, setShuttingDown]           = useState(false)
  const [systemOffline, setSystemOffline]         = useState(false)
  // System Control — Pause/Resume trading (A) + Restart All (B)
  const [pausing, setPausing]                     = useState(false)
  const [pauseResult, setPauseResult]             = useState(null)
  const [showRestartAllModal, setShowRestartAllModal] = useState(false)
  const [restartingAll, setRestartingAll]         = useState(false)

  // Reconciler
  const [reconXml, setReconXml]               = useState('')
  const [reconDateFrom, setReconDateFrom]     = useState('')
  const [reconDateTo, setReconDateTo]         = useState('')
  const [reconPreview, setReconPreview]       = useState(null)
  const [reconRunning, setReconRunning]       = useState(false)
  const [reconCommitting, setReconCommitting] = useState(false)
  const [reconMsg, setReconMsg]               = useState(null)
  const [reconMode, setReconMode]             = useState('upload') // 'upload' | 'flex'
  const [ytdWeeks, setYtdWeeks]               = useState(null)
  const [manualWeekStart, setManualWeekStart] = useState('')
  const [manualPremium, setManualPremium]     = useState('')
  const [manualSaving, setManualSaving]       = useState(false)
  const [showManual, setShowManual]           = useState(false)
  const [confirmDelete, setConfirmDelete]     = useState(null) // week_start pending delete confirm
  const [deleting, setDeleting]               = useState(false)
  const fileInputRef                          = useRef(null)

  const { theme, setTheme } = useThemeContext()

  useEffect(() => {
    axios.get('/api/settings').then(r => {
      setSettings(r.data)
      setOriginal(r.data)
      setAppliedExecTime(r.data?.execution_time ?? '10:00')
      const tz = r.data?.timezone || 'America/Los_Angeles'
      setTimezone(tz)
      setTimezoneOriginal(tz)
      setAppliedTimezone(tz)
    })
  }, [])

  // Poll /api/status for the weekly IB Key token state (drives the status line
  // and the Refresh Weekly Token button). 20s cadence catches the post-2FA
  // transition without hammering the API.
  useEffect(() => {
    let alive = true
    const pull = () => axios.get('/api/status', { timeout: 4000 })
      .then(r => { if (alive) setTokenStatus(r.data) })
      .catch(() => {})
    pull()
    const id = setInterval(pull, 20000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Once the token re-establishes, clear the "Gateway restarting — check your
  // phone" notice so it doesn't linger next to the now-active status.
  useEffect(() => {
    if (tokenStatus?.weekly_token_active) setRefreshTokenResult(null)
  }, [tokenStatus?.weekly_token_active])

  const set = useCallback((key, val) => {
    setSettings(prev => ({ ...prev, [key]: val }))
  }, [])

  const showMsg = (type, text) => {
    setMsg({ type, text })
    setTimeout(() => setMsg(null), 4000)
  }

  const save = async () => {
    setSaving(true)
    try {
      const res = await axios.post('/api/settings', settings)
      setSettings(res.data)
      setOriginal(res.data)
      showMsg('success', 'Settings saved')
    } catch (err) {
      showMsg('error', err.response?.data?.detail ?? err.message)
    } finally {
      setSaving(false)
    }
  }

  const testDiscord = async () => {
    setTesting(true)
    try {
      await axios.post('/api/discord-test')
      showMsg('success', 'Test notification sent to Discord')
    } catch (err) {
      showMsg('error', err.response?.data?.detail ?? err.message)
    } finally {
      setTesting(false)
    }
  }

  const confirmShutdown = async () => {
    setShuttingDown(true)
    setShowShutdownModal(false)
    try {
      await axios.post('/api/shutdown', { confirm: 'shutdown' })
    } catch (err) {
      // The api kills itself shortly after responding, so a network error here
      // means the shutdown is in progress — treat as success.
      const status = err?.response?.status
      if (status === 400 || status === 501) {
        showMsg('error', err.response?.data?.detail ?? 'Shutdown rejected')
        setShuttingDown(false)
        return
      }
    }
    // Poll until the API stops responding, then show the offline screen.
    const checkOffline = async () => {
      try {
        await axios.get('/api/status', { timeout: 2000 })
        setTimeout(checkOffline, 1500)
      } catch {
        setSystemOffline(true)
      }
    }
    setTimeout(checkOffline, 2000)
  }

  // Whether the operator has intentionally paused trading (System Control marker,
  // surfaced by /api/status). Drives the Pause ↔ Resume toggle. Distinct from a
  // real gateway outage — a genuine outage leaves trading_paused false so the
  // watchdog (not this button) handles recovery.
  const tradingPaused = tokenStatus?.trading_paused === true

  // Option A — pause / resume trading. api + web stay up, so this page never
  // goes offline; we just flip the button and surface the result inline.
  const toggleTrading = async () => {
    const resuming = tradingPaused
    setPausing(true)
    setPauseResult(null)
    try {
      const res = await axios.post(resuming ? '/api/trading/start' : '/api/trading/stop')
      setPauseResult({ ok: res.data.success, text: res.data.message })
      // Refresh status so the Pause ↔ Resume button flips immediately rather
      // than waiting for the 20s status poll.
      axios.get('/api/status', { timeout: 4000 }).then(r => setTokenStatus(r.data)).catch(() => {})
    } catch (err) {
      setPauseResult({ ok: false, text: err.response?.data?.detail ?? 'Action failed — check logs' })
    } finally {
      setPausing(false)
    }
  }

  // Option B — restart every container (clean reboot, no rebuild). api restarts
  // itself last, so we bounce to the offline overlay and poll until it's back.
  const confirmRestartAll = async () => {
    setRestartingAll(true)
    setShowRestartAllModal(false)
    try {
      await axios.post('/api/restart-all')
    } catch (err) {
      const status = err?.response?.status
      if (status === 501) {
        showMsg('error', err.response?.data?.detail ?? 'Restart rejected')
        setRestartingAll(false)
        return
      }
      // api killing itself mid-request looks like a network error — expected.
    }
    // Wait for the API to drop, then come back, then reload the app fresh.
    let sawDown = false
    const poll = async () => {
      try {
        await axios.get('/api/status', { timeout: 2000 })
        if (sawDown) { window.location.reload(); return }
        setTimeout(poll, 1500)
      } catch {
        sawDown = true
        setTimeout(poll, 1500)
      }
    }
    setTimeout(poll, 2500)
  }

  const saveTimezone = async () => {
    setTzSaving(true)
    try {
      const res = await axios.post('/api/settings/timezone', { timezone })
      setTimezone(res.data.timezone)
      setTimezoneOriginal(res.data.timezone)
      showMsg('success', 'Timezone saved — restart the scheduler for changes to take effect')
    } catch (err) {
      showMsg('error', err.response?.data?.detail ?? err.message)
    } finally {
      setTzSaving(false)
    }
  }

  const restartScheduler = async () => {
    setRestarting(true)
    setRestartResult(null)
    try {
      // Persist any pending schedule/timezone edits BEFORE restarting, so the
      // scheduler reads the new values on startup. This is what makes the button
      // a true one-click "apply" — no separate Save step required.
      const saved = await axios.post('/api/settings', settings)
      setSettings(saved.data)
      setOriginal(saved.data)
      if (timezone !== appliedTimezone) {
        const tzRes = await axios.post('/api/settings/timezone', { timezone })
        setTimezone(tzRes.data.timezone)
        setTimezoneOriginal(tzRes.data.timezone)
      }
      const res = await axios.post('/api/restart-scheduler')
      const detail = res.data.container ?? (res.data.pid ? `PID ${res.data.pid}` : 'success')
      // Scheduler is now running on the persisted values — mark them applied so
      // the prompt clears (it only clears here, never on a plain Save).
      setAppliedExecTime(saved.data?.execution_time ?? settings.execution_time)
      setAppliedTimezone(timezone)
      setRestartResult({ ok: true, text: `Scheduler restarted (${detail})` })
    } catch (err) {
      setRestartResult({ ok: false, text: err.response?.data?.detail ?? 'Restart failed — check logs' })
    } finally {
      setRestarting(false)
    }
  }

  const openModal = async () => {
    setShowModal(true)
    setConfirm('')
    if (settings.trading_mode !== 'live') {
      setLiveReady(null)
      setLiveMissing([])
      setAccountMasked('')
      setLiveChecking(true)
      try {
        const res = await axios.get('/api/live-ready')
        setLiveReady(res.data.ready)
        setLiveMissing(res.data.missing)
        setAccountMasked(res.data.account_masked)
      } catch {
        setLiveReady(false)
        setLiveMissing(['Error checking live credentials — is the API running?'])
      } finally {
        setLiveChecking(false)
      }
    }
  }

  const switchMode = async () => {
    if (confirm !== 'CONFIRM') return
    const target = settings.trading_mode === 'live' ? 'paper' : 'live'
    setSwitching(true)
    try {
      await axios.post('/api/trading-mode', { mode: target, confirmation: 'CONFIRM' })
      setSettings(prev => ({ ...prev, trading_mode: target, ibkr_port: target === 'live' ? 4003 : 4004 }))
      setOriginal(prev => ({ ...prev, trading_mode: target }))
      setShowModal(false)
      setConfirm('')
      showMsg('success', `Switched to ${target.toUpperCase()} mode`)
    } catch (err) {
      showMsg('error', err.response?.data?.detail ?? err.message)
    } finally {
      setSwitching(false)
    }
  }

  const patchGateway = async () => {
    setPatching(true)
    setPatchResult(null)
    try {
      // Save settings first so the watchdog window moves to the new time too
      await axios.post('/api/settings', settings)
      setOriginal(s => ({ ...s, auto_restart_time: settings.auto_restart_time, auto_restart_suppress_mins: settings.auto_restart_suppress_mins }))
      const res = await axios.post('/api/gateway/patch-restart-time', {
        auto_restart_time: settings.auto_restart_time ?? '11:59 PM',
      })
      setPatchResult({ ok: res.data.restarting,
        text: res.data.restarting
          ? `Gateway restarting now — will use ${settings.auto_restart_time} from tonight onwards (~30–60s to come back up)`
          : `Failed: ${res.data.detail}`
      })
    } catch (err) {
      setPatchResult({ ok: false, text: err.response?.data?.detail ?? 'Patch failed' })
    } finally {
      setPatching(false)
    }
  }

  const resetGateway = async () => {
    if (!window.confirm('Reset IB Gateway installation? This wipes the settings volume and reinstalls (~2 min). Gateway will be unavailable during reset.')) return
    setResettingGw(true)
    setResetGwResult(null)
    try {
      const res = await axios.post('/api/gateway/reset-installation')
      setResetGwResult({ ok: true, text: res.data.message })
    } catch (err) {
      setResetGwResult({ ok: false, text: err.response?.data?.detail ?? 'Reset failed' })
    } finally {
      setResettingGw(false)
    }
  }

  const refreshWeeklyToken = async () => {
    if (!window.confirm('Restart IB Gateway to refresh the weekly IB Key token? You will get an IB Key approval push on your phone — approve it to complete the restart (~30–60s).')) return
    setRefreshingToken(true)
    setRefreshTokenResult(null)
    try {
      const res = await axios.post('/api/gateway/refresh-token')
      setRefreshTokenResult({ ok: true, text: res.data.message })
    } catch (err) {
      setRefreshTokenResult({ ok: false, text: err.response?.data?.detail ?? 'Refresh failed' })
    } finally {
      setRefreshingToken(false)
    }
  }

  // Format an ISO timestamp like "Sun, Jun 1 at 9:30 PM" in the viewer's local
  // timezone. Used for both the established time and the next reset — the reset
  // ISO carries ET offset but renders in local time, matching the established line.
  const fmtTokenTime = (iso) => {
    if (!iso) return ''
    try {
      const d = new Date(iso)
      const day  = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
      const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
      return `${day} at ${time}`
    } catch { return iso }
  }

  const isDirty = JSON.stringify(settings) !== JSON.stringify(original)

  const DEFAULTS = {
    fund_budget: 250000, goal_pct: 0.24, num_positions: 5, min_position_size: 10000,
    max_position_size: 70000, max_delta: 0.21, min_buffer_pct: 0.05,
    earnings_filter_days: 7, wheel_cc_ignore_earnings_filter: true,
    wheel_retention_market_cap_min: 5000000000,
    wheel_sell_when_cc_below_assigned: false, wheel_cover_all_shares: true,
    wheel_stop_loss_enabled: false, stop_loss_pct: 0.10, compound_enabled: true, cash_account: false,
    max_spread_pct: 0.20, min_bid_yield_pct: 0.01, max_spread_hard_cap: 0.50,
    min_oi_notional: 1000000, excluded_tickers: [],
    dry_run: false, discord_webhook_enabled: true, execution_time: '10:00',
    auto_restart_time: '11:59 PM', auto_restart_suppress_mins: 30,
    auto_update_enabled: false,
  }

  const resetToDefaults = () => {
    setSettings(prev => ({ ...prev, ...DEFAULTS }))
    setConfirmReset(false)
    showMsg('success', 'Reset to defaults — save to apply')
  }

  if (!settings) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  )

  const isLive = settings.trading_mode === 'live'

  return (
    <div className="max-w-2xl space-y-5">
      {/* Hidden file input pinned to the viewport origin. Fixed positioning
          (no clip / negative margin) means the browser's "scroll focused
          element into view" is a no-op — the page never jumps when the
          native file dialog opens. */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".xml,text/xml"
        tabIndex={-1}
        aria-hidden="true"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 1,
          height: 1,
          opacity: 0,
          pointerEvents: 'none',
        }}
        onChange={e => {
          const file = e.target.files?.[0]
          if (!file) return
          const reader = new FileReader()
          reader.onload = ev => setReconXml(ev.target.result)
          reader.readAsText(file)
          e.target.value = ''
        }}
      />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1">Settings</h1>
          <div className="text-gray-500 text-sm">Hot-reloads on every API call — no restart needed</div>
        </div>
        <div className="flex items-center gap-2">
          {confirmReset ? (
            <>
              <span className="text-xs text-gray-500 dark:text-gray-400 hidden sm:inline">Reset all to defaults?</span>
              <button
                onClick={() => setConfirmReset(false)}
                className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white border border-gray-200 dark:border-gray-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={resetToDefaults}
                className="flex items-center gap-2 px-3 py-2 bg-red-600 hover:bg-red-500 text-white text-sm font-medium rounded-lg transition-colors"
              >
                <RotateCcw size={13} />
                Confirm Reset
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirmReset(true)}
              className="flex items-center gap-2 px-3 py-2 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 border border-gray-200 dark:border-gray-700 rounded-lg transition-colors"
            >
              <RotateCcw size={13} />
              Reset
            </button>
          )}
          <button
            onClick={save}
            disabled={saving || !isDirty}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
          >
            <Save size={14} />
            {saving ? 'Saving...' : isDirty ? 'Save Changes' : 'Saved'}
          </button>
        </div>
      </div>

      {/* Toast message */}
      {msg && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm ${
          msg.type === 'success'
            ? 'bg-green-900/30 border border-green-800 text-green-400'
            : 'bg-red-900/30 border border-red-800 text-red-400'
        }`}>
          {msg.type === 'success' ? <CheckCircle size={15} /> : <AlertTriangle size={15} />}
          {msg.text}
        </div>
      )}

      {/* Fund Settings */}
      <Section title="Fund Settings" emoji="💰">
        <SliderRow label="Initial Fund Budget"  value={settings.fund_budget}      min={10000}  max={2000000} step={10000} format={v => `$${v.toLocaleString()}`} onChange={v => set('fund_budget', v)} />
        <SliderRow
          label="Annual Goal %"
          value={settings.goal_pct ?? 0.24}
          min={0.06} max={0.60} step={0.02}
          format={v => `${(v * 100).toFixed(0)}%`}
          onChange={v => set('goal_pct', v)}
          description={`Target annual return as % of fund budget. Drives both goal bars: premium goal = $${Math.round((settings.fund_budget ?? 250000) * (settings.goal_pct ?? 0.24)).toLocaleString()}, account-value target = $${Math.round((settings.fund_budget ?? 250000) * (1 + (settings.goal_pct ?? 0.24))).toLocaleString()}. Default 24% ≈ 2%/month.`}
        />
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle
            label="Compound Weekly"
            sub="Use IBKR net liquidation as the deployment budget each Monday — grows with realized gains"
            checked={settings.compound_enabled !== false}
            onChange={v => set('compound_enabled', v)}
          />
          {settings.compound_enabled === false && (
            <p className="mt-2 text-xs text-gray-500 dark:text-gray-600">
              Fixed budget — always deploying ${(settings.fund_budget ?? 250000).toLocaleString()} regardless of account growth.
            </p>
          )}
        </div>
        {settings.compound_enabled !== false && (
          <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
            <Toggle
              label="Cash Account (no margin)"
              sub="Deploy IBKR Buying Power directly as the CSP budget. Only enable on a true cash account — Buying Power is real settled cash and already excludes capital tied up in wheel stock, so reserved capital is not subtracted again."
              checked={settings.cash_account === true}
              onChange={v => set('cash_account', v)}
            />
            {settings.cash_account === true && (
              <p className="mt-2 text-xs text-amber-500 dark:text-amber-400">
                Reserved wheel capital is NOT subtracted from the budget in this mode. Do not enable on a margin account — you'd deploy leverage.
              </p>
            )}
          </div>
        )}
        <SliderRow label="# Positions"  value={settings.num_positions}    min={1}      max={10}                  format={v => `${v} positions`}           onChange={v => set('num_positions', v)} />
        <SliderRow label="Min Position" value={settings.min_position_size} min={5000}  max={100000}  step={5000}  format={v => `$${v.toLocaleString()}`} onChange={v => set('min_position_size', v)} />
        <SliderRow label="Max Position" value={settings.max_position_size} min={10000} max={200000}  step={5000}  format={v => `$${v.toLocaleString()}`} onChange={v => set('max_position_size', v)} />
        {settings.compound_enabled !== false && (
          <p className="mt-1 text-xs text-amber-500 dark:text-amber-400">Max Position ignored in compound mode — each slot is sized by net balance ÷ # positions.</p>
        )}
      </Section>

      {/* Screener Filters */}
      <Section title="Screener Filters" emoji="📐">
        <SliderRow label="Max Delta"      value={settings.max_delta}            min={0.10} max={0.30} step={0.01} format={v => v.toFixed(2)}                onChange={v => set('max_delta', v)} />
        <SliderRow label="Min Buffer %"   value={settings.min_buffer_pct}       min={0.03} max={0.20} step={0.01} format={v => `${(v * 100).toFixed(0)}%`} onChange={v => set('min_buffer_pct', v)} />
        <SliderRow label="Earnings Window" value={settings.earnings_filter_days} min={0}    max={30}              format={v => `${v} days`}                  onChange={v => set('earnings_filter_days', v)} />
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <TickerExcludeInput value={settings.excluded_tickers ?? []} onChange={v => set('excluded_tickers', v)} />
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle
            label="Ignore Earnings for Wheel CCs"
            sub="ON (default): keep held positions through earnings and write the CC. OFF: sell shares before earnings to dodge the gap. No effect on new CSP entries."
            checked={!!settings.wheel_cc_ignore_earnings_filter}
            onChange={v => set('wheel_cc_ignore_earnings_filter', v)}
          />
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <SliderRow
            label="Wheel Retention Mkt Cap"
            value={settings.wheel_retention_market_cap_min ?? 5000000000}
            min={1000000000} max={10000000000} step={500000000}
            format={v => `$${(v / 1e9).toFixed(1)}B`}
            onChange={v => set('wheel_retention_market_cap_min', v)}
            description="Keep wheeling a held name down to this market cap, even if below the 10B entry floor — sell only if it falls further"
          />
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle
            label="Sell Shares Instead of Below-Cost CC"
            sub="Default OFF: an underwater holding with no CC at/above cost writes a 20-delta CC below cost (keeps shares + premium). Turn ON to force-sell those shares at market instead, like the old behavior."
            checked={!!settings.wheel_sell_when_cc_below_assigned}
            onChange={v => set('wheel_sell_when_cc_below_assigned', v)}
          />
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle
            label="Cover All Owned Shares"
            sub="Default ON: when a holding is only partially covered (e.g. a covered-call order that only partly filled), automatically write the shortfall at the existing strike/expiry so every owned share carries a CC. Turn OFF to leave partial holdings as-is."
            checked={!!settings.wheel_cover_all_shares}
            onChange={v => set('wheel_cover_all_shares', v)}
          />
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle
            label="Stop Loss on Wheel Holdings"
            sub="Sell a holding on Monday if price has fallen below assigned strike by this %"
            checked={!!settings.wheel_stop_loss_enabled}
            onChange={v => set('wheel_stop_loss_enabled', v)}
          />
          {settings.wheel_stop_loss_enabled && (
            <div className="mt-3">
              <SliderRow
                label="Stop Loss %"
                value={settings.stop_loss_pct ?? 0.10}
                min={0} max={0.50} step={0.01}
                format={v => `${(v * 100).toFixed(0)}%`}
                onChange={v => set('stop_loss_pct', v)}
                description={`Sell if price falls more than ${((settings.stop_loss_pct ?? 0.10) * 100).toFixed(0)}% below assigned strike`}
              />
            </div>
          )}
        </div>
      </Section>

      {/* Liquidity Filters */}
      <Section title="Liquidity Filters" emoji="💧">
        <SliderRow
          label="Max Spread %"
          value={settings.max_spread_pct ?? 0.20}
          min={0.05} max={0.50} step={0.05}
          format={v => `${(v * 100).toFixed(0)}%`}
          onChange={v => set('max_spread_pct', v)}
          description="Skip if bid/ask spread exceeds this % of mid price"
        />
        <SliderRow
          label="Min Bid Yield %"
          value={settings.min_bid_yield_pct ?? 0.01}
          min={0.005} max={0.03} step={0.0025}
          format={v => `${(v * 100).toFixed(2)}%`}
          onChange={v => set('min_bid_yield_pct', v)}
          description="Override spread filter if bid yield meets this threshold"
        />
        <SliderRow
          label="Max Spread Hard Cap %"
          value={settings.max_spread_hard_cap ?? 0.50}
          min={0.25} max={1.00} step={0.05}
          format={v => `${(v * 100).toFixed(0)}%`}
          onChange={v => set('max_spread_hard_cap', v)}
          description="Always skip regardless of yield if spread exceeds this %"
        />
        <SliderRow
          label="Min OI Notional"
          value={settings.min_oi_notional ?? 1000000}
          min={250000} max={5000000} step={250000}
          format={v => `$${(v / 1e6).toFixed(2)}M`}
          onChange={v => set('min_oi_notional', v)}
          description="Skip if open-interest notional (OI × strike × 100) is below this — price-neutral liquidity floor, fairer to high-strike names than a flat contract count"
        />
      </Section>

      {/* Execution */}
      <Section title="Execution" emoji="⚙️">
        {/* Monday execution time */}
        <div>
          <div className="text-gray-700 dark:text-gray-300 text-sm mb-2">⏰ Monday Execution Time (PST)</div>
          <select
            value={isPreset(settings.execution_time) ? settings.execution_time : 'custom'}
            onChange={e => {
              if (e.target.value !== 'custom') set('execution_time', e.target.value)
              // switching to custom: keep current value in the text input below
            }}
            className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          >
            {PRESET_TIMES.map(({ value, et, note }) => (
              <option key={value} value={value}>
                {value} PST ({et}){note ? ` — ${note}` : ''}
              </option>
            ))}
            <option value="custom">Custom (HH:MM PST)</option>
          </select>

          {!isPreset(settings.execution_time) && (
            <input
              type="text"
              placeholder="HH:MM (24-hr PST, e.g. 09:30)"
              value={settings.execution_time ?? ''}
              onChange={e => set('execution_time', e.target.value)}
              className="mt-2 w-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
          )}

          {settings.execution_time && (
            <div className="mt-1.5 text-blue-500 dark:text-blue-400 text-xs font-medium">
              {fmtExecTime(settings.execution_time)}
            </div>
          )}

          <div className="mt-2 text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
            Earlier = less liquidity and wider spreads.
            10:00 AM PST (1:00 PM ET) recommended for best fill prices.
            {/^\d{2}:\d{2}$/.test(settings.execution_time || '') && settings.execution_time < MIN_EXEC_TIME && (
              <span className="block mt-1 text-amber-600 dark:text-amber-500">
                ⚠ Earliest allowed is 7:00 AM PST. The wheel check runs 5 min before execution and must price covered calls after the open — anything earlier has no option data. The scheduler will use 7:00 AM if you save an earlier time.
              </span>
            )}
          </div>
          {settings.execution_time !== appliedExecTime && (
            <div className="mt-2 flex items-center gap-3 flex-wrap">
              <span className="text-xs text-amber-600 dark:text-amber-500">
                ⚠ Restart scheduler for the new time to take effect (saves automatically).
              </span>
              <button
                onClick={restartScheduler}
                disabled={restarting}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-600 text-amber-600 dark:text-amber-500 dark:border-amber-500 hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-60 disabled:cursor-wait transition-colors"
              >
                <RefreshCw size={11} className={restarting ? 'animate-spin' : ''} />
                {restarting ? 'Restarting…' : 'Restart Scheduler'}
              </button>
            </div>
          )}
          {restartResult && (
            <div className={`mt-1.5 text-xs font-medium ${restartResult.ok ? 'text-green-500' : 'text-red-400'}`}>
              {restartResult.ok ? '✅' : '❌'} {restartResult.text}
            </div>
          )}
        </div>

        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <Toggle label="Dry Run" sub="Simulate orders — no real trades placed" checked={settings.dry_run} onChange={v => set('dry_run', v)} />
          {isLive && !settings.dry_run && (
            <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
              You're in live trading — enable Dry Run above if you want to test without placing real orders.
            </p>
          )}
        </div>
      </Section>

      {/* IB Gateway */}
      <Section title="IB Gateway" emoji="🔌">
        {/* Weekly IB Key 2FA token */}
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-gray-700 dark:text-gray-300 text-sm">
            <KeyRound size={14} /> Weekly IB Key Token
          </div>
          {!isLive ? (
            <div className="text-xs leading-relaxed text-gray-500 dark:text-gray-600">
              📄 Paper trading — IB Key 2FA isn't required, so there's no weekly token to manage.
              This applies only to live accounts.
            </div>
          ) : (
            <>
              {tokenStatus?.weekly_token_active ? (
                <div className="text-xs leading-relaxed text-green-600 dark:text-green-400">
                  ✅ Weekly token active{tokenStatus.weekly_token_established
                    ? <> — established {fmtTokenTime(tokenStatus.weekly_token_established)}</>
                    : null}
                  <span className="text-gray-500 dark:text-gray-600">
                    {' '}· Next reset: ~{fmtTokenTime(tokenStatus.weekly_token_next_reset)}
                  </span>
                </div>
              ) : (
                <div className="text-xs leading-relaxed text-amber-600 dark:text-amber-400">
                  🔑 No weekly token yet — the next gateway restart will require an IB Key approval on your phone.
                  {tokenStatus?.weekly_token_next_reset && (
                    <span className="text-gray-500 dark:text-gray-600">
                      {' '}Next scheduled reset: ~{fmtTokenTime(tokenStatus.weekly_token_next_reset)}.
                    </span>
                  )}
                </div>
              )}
              <div className="text-gray-500 dark:text-gray-600 text-xs leading-relaxed">
                Restarts IB Gateway to trigger your IB Key approval. Check your phone after clicking.
              </div>
              <button
                onClick={refreshWeeklyToken}
                disabled={refreshingToken || !tokenStatus?.weekly_token_refresh_enabled}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-blue-600 text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <KeyRound size={11} className={refreshingToken ? 'animate-pulse' : ''} />
                {refreshingToken ? 'Waiting for IB Key approval…' : '🔑 Refresh Weekly Token'}
              </button>
              {!tokenStatus?.weekly_token_refresh_enabled && !refreshingToken && (
                <div className="text-xs text-gray-500 dark:text-gray-600">
                  Disabled while this week's token is active — no approval needed until the next reset.
                </div>
              )}
              {refreshTokenResult && (
                <div className={`text-xs font-medium ${refreshTokenResult.ok ? 'text-green-500' : 'text-red-400'}`}>
                  {refreshTokenResult.ok ? '✅' : '❌'} {refreshTokenResult.text}
                </div>
              )}
            </>
          )}
        </div>

        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <div className="text-gray-700 dark:text-gray-300 text-sm mb-2">⏰ Daily Auto-Restart Time</div>
          <select
            value={settings.auto_restart_time ?? '11:59 PM'}
            onChange={e => { set('auto_restart_time', e.target.value); setPatchResult(null) }}
            className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          >
            {[
              '07:00 PM','07:30 PM','08:00 PM','08:30 PM',
              '09:00 PM','09:30 PM','10:00 PM','10:30 PM',
              '11:00 PM','11:30 PM','11:59 PM',
              '12:00 AM','12:30 AM','01:00 AM','01:30 AM','02:00 AM',
            ].map(t => (
              <option key={t} value={t}>{t}{t === '11:59 PM' ? ' (default)' : ''}</option>
            ))}
          </select>
          <div className="mt-2 text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
            IB Gateway restarts at this time each night to keep the session fresh. Choose a time with no trading activity.
          </div>
        </div>

        <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
          <SliderRow
            label="Restart Window"
            value={settings.auto_restart_suppress_mins ?? 30}
            min={10} max={60} step={5}
            format={v => `${v} min`}
            onChange={v => set('auto_restart_suppress_mins', v)}
            description="How long after the restart time to treat alerts as restart-related — alerts in this window say 'likely the daily restart' instead of 'manual restart required'"
          />
        </div>

        <div className="border-t border-gray-200 dark:border-gray-800 pt-3 space-y-2">
          <div className="text-gray-500 dark:text-gray-600 text-xs leading-relaxed">
            Alerts always fire, but within the restart window they say <em>"likely the daily restart — recovery message will follow"</em> instead of <em>"manual restart required."</em>{' '}
            <strong className="text-gray-700 dark:text-gray-400">Apply to Gateway</strong> saves the new time and restarts the gateway container immediately — it will come back up using the new restart time tonight and on every future restart.
          </div>
          <button
            onClick={patchGateway}
            disabled={patching}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-blue-600 text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-60 disabled:cursor-wait transition-colors"
          >
            <RefreshCw size={11} className={patching ? 'animate-spin' : ''} />
            {patching ? 'Restarting…' : 'Apply to Gateway'}
          </button>
          {patchResult && (
            <div className={`text-xs font-medium ${patchResult.ok ? 'text-green-500' : 'text-red-400'}`}>
              {patchResult.ok ? '✅' : '❌'} {patchResult.text}
            </div>
          )}
        </div>

        <div className="border-t border-gray-200 dark:border-gray-800 pt-3 space-y-2">
          <div className="text-gray-500 dark:text-gray-600 text-xs leading-relaxed">
            <strong className="text-gray-700 dark:text-gray-400">Reset Installation</strong> wipes the Gateway settings volume and reinstalls from scratch. Use this if Gateway is stuck or failing to connect after a version mismatch. Gateway will be unavailable for ~2 minutes.
          </div>
          {isLive && (
            <div className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-600 dark:text-amber-400 rounded-lg border border-amber-300 dark:border-amber-700/50 bg-amber-50 dark:bg-amber-900/15 px-2.5 py-2">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <span>Reset Installation also wipes the weekly auth token. A new IB Key 2FA approval will be required on your phone at the next restart.</span>
            </div>
          )}
          <button
            onClick={resetGateway}
            disabled={resettingGw}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-600 text-amber-500 hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-60 disabled:cursor-wait transition-colors"
          >
            <RefreshCw size={11} className={resettingGw ? 'animate-spin' : ''} />
            {resettingGw ? 'Resetting…' : 'Reset Installation'}
          </button>
          {resetGwResult && (
            <div className={`text-xs font-medium ${resetGwResult.ok ? 'text-green-500' : 'text-red-400'}`}>
              {resetGwResult.ok ? '✅' : '❌'} {resetGwResult.text}
            </div>
          )}
        </div>
      </Section>

      {/* Timezone */}
      <Section title="Timezone" emoji="🌐">
        <div>
          <div className="text-gray-700 dark:text-gray-300 text-sm mb-2">Scheduler timezone</div>
          <select
            value={timezone}
            onChange={e => setTimezone(e.target.value)}
            className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          >
            {TIMEZONES.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>

          <div className="mt-2 text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
            Cron jobs (Monday execution, weekly preview, daily monitor) fire at the local wall-clock time in this zone.
          </div>

          <div className="mt-3 flex items-center gap-3 flex-wrap">
            <button
              onClick={saveTimezone}
              disabled={tzSaving || timezone === timezoneOriginal}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Save size={11} />
              {tzSaving ? 'Saving…' : 'Save Timezone'}
            </button>
            {timezone !== appliedTimezone && (
              <span className="text-xs text-amber-600 dark:text-amber-500">
                ⚠ Restart scheduler for the change to take effect (saves automatically).
              </span>
            )}
            <button
              onClick={restartScheduler}
              disabled={restarting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-600 text-amber-600 dark:text-amber-500 dark:border-amber-500 hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-60 disabled:cursor-wait transition-colors"
            >
              <RefreshCw size={11} className={restarting ? 'animate-spin' : ''} />
              {restarting ? 'Restarting…' : 'Restart Scheduler'}
            </button>
          </div>
        </div>
      </Section>

      {/* Appearance */}
      <Section title="Appearance" emoji="🎨">
        <div>
          <div className="text-gray-700 dark:text-gray-300 text-sm mb-3">Theme</div>
          <div className="flex gap-2">
            {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                onClick={() => setTheme(value)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
                  theme === value
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 hover:text-gray-900 dark:hover:text-white'
                }`}
              >
                <Icon size={14} />
                {label}
              </button>
            ))}
          </div>
        </div>
      </Section>

      {/* Trading Mode — prominent */}
      <div className={`border-2 rounded-xl p-5 space-y-4 ${
        isLive ? 'border-green-700 bg-green-900/10' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900'
      }`}>
        <div className="text-gray-900 dark:text-white font-semibold text-sm flex items-center gap-2">
          <span>🔄</span> Trading Mode
        </div>

        <div className="flex items-center justify-between">
          <div>
            <div className="text-gray-500 text-sm mb-1">Current mode</div>
            <span className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-bold border ${
              isLive
                ? 'bg-green-900/50 text-green-400 border-green-700 animate-pulse'
                : 'bg-blue-900/40 text-blue-400 border-blue-800'
            }`}>
              {isLive ? '🟢 LIVE TRADING' : '📄 PAPER TRADING'}
            </span>
            <div className="text-gray-500 dark:text-gray-600 text-xs mt-2">
              IBKR port: {settings.ibkr_port} ({isLive ? '4003 = live' : '4004 = paper'})
            </div>
          </div>
          <button
            onClick={openModal}
            className={`px-4 py-2 text-sm font-medium rounded-lg border transition-colors ${
              isLive
                ? 'border-blue-700 text-blue-400 hover:bg-blue-900/30'
                : 'border-red-700 text-red-400 hover:bg-red-900/30'
            }`}
          >
            Switch to {isLive ? 'Paper' : 'Live'}
          </button>
        </div>

        {isLive && (
          <div className="flex items-center gap-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-400 dark:border-amber-700 rounded-lg px-3 py-2">
            <AlertTriangle size={14} className="text-amber-600 dark:text-amber-400 shrink-0" />
            <span className="text-amber-700 dark:text-amber-400 text-xs font-medium">
              Live mode active — all trades use real money
            </span>
          </div>
        )}
      </div>

      {/* Notifications */}
      <Section title="Notifications" emoji="🔔">
        <Toggle
          label="Discord Webhook"
          sub="Post trade results and alerts to Discord"
          checked={settings.discord_webhook_enabled}
          onChange={v => set('discord_webhook_enabled', v)}
        />
        <button
          onClick={testDiscord}
          disabled={testing}
          className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 px-3 py-2 rounded-lg transition-colors w-full"
        >
          <Send size={13} />
          {testing ? 'Sending...' : 'Send test notification'}
        </button>
      </Section>

      {/* Software Updates */}
      <Section title="Software Updates" emoji="⬆️">
        <Toggle
          label="Auto-Update"
          sub="Automatically apply updates Wed–Fri at 3 AM — keeps bug fixes rolling out without manual action"
          checked={!!settings.auto_update_enabled}
          onChange={v => set('auto_update_enabled', v)}
        />
        {settings.auto_update_enabled && (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-600 leading-relaxed">
            Updates run at 3:00 AM Wednesday through Friday — safe even on holiday weeks when
            Tuesday becomes the execution day. A Discord alert fires when an update is applied.
          </p>
        )}
        {isLive && (
          <div className="mt-2 flex items-start gap-1.5 text-xs leading-relaxed text-amber-600 dark:text-amber-400 rounded-lg border border-amber-300 dark:border-amber-700/50 bg-amber-50 dark:bg-amber-900/15 px-2.5 py-2">
            <AlertTriangle size={13} className="shrink-0 mt-0.5" />
            <span>
              Every update restarts IB Gateway, which on a <strong>live</strong> account requires
              a fresh IB Key 2FA approval on your phone. With Auto-Update on, that prompt fires
              unattended at 3 AM — if no one approves it, the gateway stays logged out and trading
              is paused until you do. Leave Auto-Update off unless you'll be available to approve,
              or plan to apply updates manually while you're at the computer.
            </span>
          </div>
        )}
      </Section>

      {/* System Control */}
      <Section title="System Control" emoji="⛔">
        <div className="space-y-4">

          {/* A — Pause / Resume Trading */}
          <div className="flex items-start justify-between gap-4 pb-4 border-b border-gray-100 dark:border-gray-800">
            <div className="flex-1">
              <div className="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-2">
                {tradingPaused
                  ? <><span className="h-2 w-2 rounded-full bg-yellow-400 inline-block" /> Trading is paused</>
                  : <><span className="h-2 w-2 rounded-full bg-green-500 inline-block" /> Trading is active</>}
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-600 leading-relaxed mt-1">
                Stops just the IB Gateway + scheduler. The dashboard stays up, so you can resume right here — no desktop icon needed.
              </div>
              {pauseResult && (
                <div className={`mt-1.5 text-xs font-medium ${pauseResult.ok ? 'text-green-500' : 'text-red-400'}`}>
                  {pauseResult.ok ? '✅' : '❌'} {pauseResult.text}
                </div>
              )}
            </div>
            <button
              onClick={toggleTrading}
              disabled={pausing}
              className={`flex items-center gap-2 px-4 py-2 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-60 ${
                tradingPaused ? 'bg-green-600 hover:bg-green-500' : 'bg-yellow-600 hover:bg-yellow-500'}`}
            >
              {tradingPaused ? <Play size={14} /> : <Pause size={14} />}
              {pausing ? 'Working…' : (tradingPaused ? 'Resume Trading' : 'Pause Trading')}
            </button>
          </div>

          {/* B — Restart All */}
          <div className="flex items-start justify-between gap-4 pb-4 border-b border-gray-100 dark:border-gray-800">
            <div className="flex-1">
              <div className="text-sm font-medium text-gray-900 dark:text-white">Restart all containers</div>
              <div className="text-xs text-gray-500 dark:text-gray-600 leading-relaxed mt-1">
                A clean reboot of every container (no update/rebuild). This page will briefly disconnect and reload automatically (~30–60s).
              </div>
            </div>
            <button
              onClick={() => setShowRestartAllModal(true)}
              disabled={restartingAll}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-60"
            >
              <RotateCw size={14} className={restartingAll ? 'animate-spin' : ''} />
              Restart All
            </button>
          </div>

          {/* C — Full Shutdown */}
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <div className="text-sm font-medium text-gray-900 dark:text-white">Full shutdown</div>
              <div className="text-xs text-gray-500 dark:text-gray-600 leading-relaxed mt-1">
                Stops <span className="font-medium">everything</span>, including this dashboard. To bring it back, use the YRVI icon on the desktop.
              </div>
            </div>
            <button
              onClick={() => setShowShutdownModal(true)}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-sm font-medium rounded-lg transition-colors"
            >
              <Power size={14} />
              Shut Down
            </button>
          </div>

        </div>
      </Section>

      {/* Reconciler */}
      <Section title="History Reconciler" emoji="🔄">
        <div className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
          Rebuild the weekly premium history from IBKR Flex XML — use this to recover weeks that
          were traded outside YRVI or lost during a volume wipe.
          Option sell proceeds are summed by week; stock P&amp;L is not included.{' '}
          <a
            href="https://github.com/controllinghand/you_rock_fund/blob/main/FAQ.md#q-how-do-i-export-a-flex-xml-from-ibkr-to-use-with-the-history-reconciler"
            target="_blank"
            rel="noreferrer"
            className="text-blue-500 hover:underline inline-flex items-center gap-0.5"
          >
            How-to guide <ExternalLink size={11} />
          </a>
        </div>

        {/* Mode tabs */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => { setReconMode('upload'); setReconPreview(null); setReconMsg(null) }}
            className={`text-xs px-3 py-1.5 rounded-md border font-medium inline-flex items-center gap-1.5 ${
              reconMode === 'upload'
                ? 'bg-blue-600 text-white border-blue-600'
                : 'border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
            }`}
          >
            <Upload size={13} /> Paste / Upload XML
          </button>
          <button
            type="button"
            onClick={() => { setReconMode('flex'); setReconPreview(null); setReconMsg(null) }}
            className={`text-xs px-3 py-1.5 rounded-md border font-medium inline-flex items-center gap-1.5 ${
              reconMode === 'flex'
                ? 'bg-blue-600 text-white border-blue-600'
                : 'border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
            }`}
          >
            <Download size={13} /> Fetch from IBKR
          </button>
        </div>

        {/* Optional date range */}
        <div className="flex gap-3 items-center">
          <div className="flex flex-col gap-0.5">
            <label className="text-xs text-gray-500">From (YYYY-MM-DD)</label>
            <input
              type="text"
              placeholder="2026-01-01"
              value={reconDateFrom}
              onChange={e => setReconDateFrom(e.target.value)}
              className="bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-md text-xs px-2.5 py-1.5 text-gray-900 dark:text-white font-mono w-36 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="flex flex-col gap-0.5">
            <label className="text-xs text-gray-500">To (YYYY-MM-DD)</label>
            <input
              type="text"
              placeholder="2026-12-31"
              value={reconDateTo}
              onChange={e => setReconDateTo(e.target.value)}
              className="bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-md text-xs px-2.5 py-1.5 text-gray-900 dark:text-white font-mono w-36 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="text-xs text-gray-400 dark:text-gray-600 mt-4">Leave blank to include all dates</div>
        </div>

        {/* XML textarea — upload mode only */}
        {reconMode === 'upload' && (
          <div>
            <label className="text-xs text-gray-500 block mb-1">Paste Flex XML</label>
            <textarea
              rows={6}
              placeholder={'<?xml version="1.0" ?>\n<FlexQueryResponse …>…</FlexQueryResponse>'}
              value={reconXml}
              onChange={e => setReconXml(e.target.value)}
              className="w-full bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-md text-xs px-3 py-2 text-gray-900 dark:text-white font-mono resize-y focus:outline-none focus:border-blue-500"
            />
            <button
              type="button"
              onClick={() => {
                // Safety net: if the browser still nudges the <main> scroll
                // when the native dialog opens/closes, snap it back. Reads
                // main.scrollTop (the real scroll container) — not window.
                const main = fileInputRef.current?.closest('main')
                const y = main ? main.scrollTop : 0
                const restore = () => {
                  if (main) main.scrollTop = y
                  window.removeEventListener('focus', restore)
                }
                window.addEventListener('focus', restore)
                fileInputRef.current?.click()
              }}
              className="mt-1.5 flex items-center gap-1.5 text-xs text-blue-500 hover:text-blue-400"
            >
              <Upload size={12} /> Or click to select a .xml file
            </button>
          </div>
        )}

        {reconMode === 'flex' && (
          <div className="text-xs text-gray-500 dark:text-gray-400 bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900/50 rounded-lg px-3 py-2.5 leading-relaxed space-y-1.5">
            <div>
              Requires <strong>IBKR Flex Token</strong> and <strong>Flex Query ID</strong> to be set in the{' '}
              <a href="/secrets" className="text-blue-500 hover:underline">Secrets</a> page.
              Your query must include the <strong>Executions</strong> sub-type under Trades, XML format.
            </div>
            <div>
              <a
                href="https://github.com/controllinghand/you_rock_fund/blob/main/FAQ.md#q-how-do-i-set-up-automatic-reconciliation-via-the-ibkr-flex-web-service"
                target="_blank"
                rel="noreferrer"
                className="text-blue-500 hover:underline inline-flex items-center gap-0.5"
              >
                Step-by-step setup guide <ExternalLink size={11} />
              </a>
            </div>
          </div>
        )}

        {/* Preview result */}
        {reconPreview && (
          <div className="bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-gray-900 dark:text-white">Preview</div>
              <div className="text-xs text-gray-500">{reconPreview.fills_found} fills · {reconPreview.weeks_found} weeks · ${reconPreview.total_premium?.toLocaleString()}</div>
            </div>
            {reconPreview.weeks?.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left pb-1.5">Week of</th>
                    <th className="text-right pb-1.5">Premium</th>
                  </tr>
                </thead>
                <tbody>
                  {reconPreview.weeks.map(w => (
                    <tr key={w.week_start} className="border-b border-gray-100 dark:border-gray-800/60">
                      <td className="py-1 font-mono text-gray-700 dark:text-gray-300">{w.week_start}</td>
                      <td className="py-1 text-right font-mono text-green-600 dark:text-green-400">
                        ${w.premium_collected?.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-xs text-gray-500">No option sell fills found in this XML / date range.</div>
            )}
          </div>
        )}

        {/* Status message */}
        {reconMsg && (
          <div className={`text-xs px-3 py-2 rounded-lg flex items-center gap-2 ${
            reconMsg.type === 'success'
              ? 'bg-green-900/20 border border-green-800 text-green-400'
              : 'bg-red-900/20 border border-red-800 text-red-400'
          }`}>
            {reconMsg.type === 'success' ? <CheckCircle size={13} /> : <AlertTriangle size={13} />}
            {reconMsg.text}
          </div>
        )}

        {/* Current weeks + manual entry */}
        <div className="border-t border-gray-200 dark:border-gray-800 pt-3 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-xs font-medium text-gray-700 dark:text-gray-300">Current ytd_tracker weeks</div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={async () => {
                  try {
                    const r = await axios.get('/api/performance')
                    setYtdWeeks(r.data.weeks || [])
                  } catch { setYtdWeeks([]) }
                }}
                className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 inline-flex items-center gap-1"
              >
                <RefreshCw size={11} /> Load
              </button>
              <button
                type="button"
                onClick={() => setShowManual(s => !s)}
                className="text-xs px-2 py-1 rounded border border-blue-400 text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 inline-flex items-center gap-1"
              >
                + Add week manually
              </button>
            </div>
          </div>

          {ytdWeeks !== null && (
            ytdWeeks.length === 0 ? (
              <div className="text-xs text-gray-400">No weeks in ytd_tracker.json</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left pb-1">Week of</th>
                    <th className="text-right pb-1">Premium</th>
                    <th className="pb-1" />
                  </tr>
                </thead>
                <tbody>
                  {ytdWeeks.map(w => (
                    <tr key={w.week_start} className="border-b border-gray-100 dark:border-gray-800/60">
                      <td className="py-1 font-mono text-gray-700 dark:text-gray-300">{w.week_start}</td>
                      <td className="py-1 text-right font-mono text-green-600 dark:text-green-400">
                        ${(w.premium_collected ?? w.realized ?? 0).toLocaleString()}
                      </td>
                      <td className="py-1 text-right">
                        {confirmDelete === w.week_start ? (
                          <span className="inline-flex items-center gap-1.5">
                            <span className="text-[10px] text-gray-500">Remove?</span>
                            <button
                              type="button"
                              disabled={deleting}
                              onClick={async () => {
                                setDeleting(true)
                                try {
                                  await axios.delete(`/api/ytd/weeks/${w.week_start}`)
                                  const r = await axios.get('/api/performance')
                                  setYtdWeeks(r.data.weeks || [])
                                  setReconMsg({ type: 'success', text: `Removed week ${w.week_start}` })
                                } catch (e) {
                                  setReconMsg({ type: 'error', text: e?.response?.data?.detail || 'Delete failed' })
                                } finally {
                                  setDeleting(false)
                                  setConfirmDelete(null)
                                }
                              }}
                              className="text-[10px] px-1.5 py-0.5 rounded bg-red-600 hover:bg-red-500 text-white disabled:opacity-50 disabled:cursor-wait"
                            >
                              {deleting ? '…' : 'yes'}
                            </button>
                            <button
                              type="button"
                              disabled={deleting}
                              onClick={() => setConfirmDelete(null)}
                              className="text-[10px] px-1.5 py-0.5 rounded border border-gray-300 dark:border-gray-700 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
                            >
                              no
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setConfirmDelete(w.week_start)}
                            className="text-red-400 hover:text-red-600 text-[10px] px-1.5 py-0.5 rounded hover:bg-red-50 dark:hover:bg-red-950/30"
                          >
                            remove
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}

          {showManual && (
            <div className="bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg p-3 space-y-2">
              <div className="text-xs font-medium text-gray-700 dark:text-gray-300">Add / update a week</div>
              <div className="flex gap-2 items-end flex-wrap">
                <div className="flex flex-col gap-0.5">
                  <label className="text-[10px] text-gray-500">Week start (YYYY-MM-DD)</label>
                  <input
                    type="text"
                    placeholder="2026-04-20"
                    value={manualWeekStart}
                    onChange={e => setManualWeekStart(e.target.value)}
                    className="bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded text-xs px-2 py-1.5 font-mono w-36 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div className="flex flex-col gap-0.5">
                  <label className="text-[10px] text-gray-500">Premium ($)</label>
                  <input
                    type="number"
                    placeholder="2498"
                    value={manualPremium}
                    onChange={e => setManualPremium(e.target.value)}
                    className="bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded text-xs px-2 py-1.5 font-mono w-28 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <button
                  type="button"
                  disabled={manualSaving || !manualWeekStart || !manualPremium}
                  onClick={async () => {
                    setManualSaving(true)
                    setReconMsg(null)
                    try {
                      await axios.post('/api/ytd/weeks', {
                        week_start: manualWeekStart,
                        premium_collected: parseFloat(manualPremium),
                      })
                      setManualWeekStart('')
                      setManualPremium('')
                      setReconMsg({ type: 'success', text: `Week ${manualWeekStart} saved` })
                      const r = await axios.get('/api/performance')
                      setYtdWeeks(r.data.weeks || [])
                    } catch (e) {
                      setReconMsg({ type: 'error', text: e?.response?.data?.detail || 'Save failed' })
                    } finally {
                      setManualSaving(false)
                    }
                  }}
                  className="text-xs px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 text-white font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {manualSaving ? 'Saving…' : 'Save week'}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 flex-wrap">
          <button
            type="button"
            disabled={reconRunning || (reconMode === 'upload' && !reconXml.trim())}
            onClick={async () => {
              setReconRunning(true)
              setReconPreview(null)
              setReconMsg(null)
              try {
                const body = { dry_run: true, date_from: reconDateFrom || undefined, date_to: reconDateTo || undefined }
                const url = reconMode === 'upload' ? '/api/reconcile/upload' : '/api/reconcile/flex'
                if (reconMode === 'upload') body.xml = reconXml
                const r = await axios.post(url, body)
                setReconPreview(r.data)
              } catch (e) {
                setReconMsg({ type: 'error', text: e?.response?.data?.detail || e.message || 'Preview failed' })
              } finally {
                setReconRunning(false)
              }
            }}
            className="text-xs px-3 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 font-medium inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <RotateCw size={13} className={reconRunning ? 'animate-spin' : ''} />
            {reconRunning ? 'Parsing…' : 'Preview'}
          </button>

          {reconPreview && reconPreview.weeks_found > 0 && (
            <button
              type="button"
              disabled={reconCommitting}
              onClick={async () => {
                setReconCommitting(true)
                setReconMsg(null)
                try {
                  const r = await axios.post('/api/reconcile/commit', { weeks: reconPreview.weeks })
                  setReconMsg({ type: 'success', text: `Committed — ${r.data.weeks_found} weeks, $${r.data.total_premium?.toLocaleString()} total premium written to ytd_tracker.json` })
                  setReconPreview(null)
                } catch (e) {
                  setReconMsg({ type: 'error', text: e?.response?.data?.detail || e.message || 'Commit failed' })
                } finally {
                  setReconCommitting(false)
                }
              }}
              className="text-xs px-3 py-1.5 rounded-md bg-green-600 hover:bg-green-700 text-white font-medium inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <CheckCircle size={13} />
              {reconCommitting ? 'Saving…' : `Commit ${reconPreview.weeks_found} weeks`}
            </button>
          )}
        </div>
      </Section>

      {/* Shutdown overlay — covers page while stopping, then confirms offline */}
      {shuttingDown && (
        <div className="fixed inset-0 bg-black/90 flex flex-col items-center justify-center z-[100]">
          {systemOffline ? (
            <>
              <Power size={48} className="text-red-400 mb-5" />
              <h2 className="text-white text-2xl font-bold mb-2">YRVI is offline</h2>
              <p className="text-gray-400 text-sm">Restart from the YRVI icon on your desktop.</p>
            </>
          ) : (
            <>
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-red-400 mb-5" />
              <h2 className="text-white text-xl font-semibold mb-1">Shutting down YRVI…</h2>
              <p className="text-gray-500 text-sm">Stopping all containers</p>
            </>
          )}
        </div>
      )}

      {/* Restart-All overlay — covers page while all containers bounce, then reloads */}
      {restartingAll && (
        <div className="fixed inset-0 bg-black/90 flex flex-col items-center justify-center z-[100]">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-400 mb-5" />
          <h2 className="text-white text-xl font-semibold mb-1">Restarting YRVI…</h2>
          <p className="text-gray-500 text-sm">Bouncing all containers — this page will reload when it's back (~30–60s)</p>
        </div>
      )}

      {/* Restart-All Modal */}
      {showRestartAllModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={e => e.target === e.currentTarget && !restartingAll && setShowRestartAllModal(false)}
        >
          <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl p-6 max-w-md w-full shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <RotateCw size={22} className="text-blue-400" />
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Restart all containers</h3>
            </div>
            <p className="text-gray-600 dark:text-gray-300 text-sm mb-5">
              This bounces every container (a clean reboot — no update). The dashboard will disconnect briefly and reload itself when it's back.
              {tokenStatus?.trading_mode === 'live' && ' On live, the Gateway restart will trigger an IB Key approval on your phone.'}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowRestartAllModal(false)}
                disabled={restartingAll}
                className="flex-1 px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={confirmRestartAll}
                disabled={restartingAll}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60 disabled:cursor-wait"
              >
                {restartingAll ? 'Restarting…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Shutdown Modal */}
      {showShutdownModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={e => e.target === e.currentTarget && !shuttingDown && setShowShutdownModal(false)}
        >
          <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl p-6 max-w-md w-full shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <Power size={22} className="text-red-400" />
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Shut Down YRVI</h3>
            </div>
            <p className="text-gray-600 dark:text-gray-300 text-sm mb-5">
              This will stop all YRVI containers. To restart, click the YRVI icon on your desktop.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowShutdownModal(false)}
                disabled={shuttingDown}
                className="flex-1 px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={confirmShutdown}
                disabled={shuttingDown}
                className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60 disabled:cursor-wait"
              >
                {shuttingDown ? 'Shutting down…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Trading Mode Modal */}
      {showModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={e => e.target === e.currentTarget && setShowModal(false)}
        >
          <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl p-6 max-w-md w-full shadow-2xl">

            {/* ── Switch to Live ── */}
            {!isLive ? (
              <>
                <div className="flex items-center gap-3 mb-4">
                  <AlertTriangle size={22} className="text-red-400" />
                  <h3 className="text-lg font-bold text-gray-900 dark:text-white">Switch to Live Trading</h3>
                </div>

                {liveChecking && (
                  <div className="flex items-center gap-3 text-gray-500 text-sm py-6">
                    <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-500 shrink-0" />
                    Checking live credentials…
                  </div>
                )}

                {!liveChecking && liveReady === false && (
                  <>
                    <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 mb-5">
                      <div className="text-red-400 font-medium text-sm mb-2">
                        ⚠️ Live credentials not configured
                      </div>
                      <div className="text-red-300 text-sm mb-3">
                        Add these to your .env file before switching:
                      </div>
                      <ul className="space-y-1 mb-3">
                        {liveMissing.map(v => (
                          <li key={v} className="text-red-300 text-sm font-mono">• {v}</li>
                        ))}
                      </ul>
                      <div className="text-red-400 text-xs">Then restart YRVI and try again.</div>
                    </div>
                    <button
                      onClick={() => setShowModal(false)}
                      className="w-full px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors"
                    >
                      Close
                    </button>
                  </>
                )}

                {!liveChecking && liveReady === true && (
                  <>
                    <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 mb-5">
                      <div className="text-red-400 font-medium text-sm mb-1">⚠️ Switching to LIVE trading</div>
                      <div className="text-red-300 text-sm">
                        Account: <span className="font-mono font-bold">{accountMasked}</span>
                        <br />Real money will be used!
                      </div>
                    </div>
                    <div className="mb-5">
                      <label className="text-gray-600 dark:text-gray-400 text-sm block mb-2">
                        Type <code className="text-yellow-500 dark:text-yellow-400 bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">CONFIRM</code> to proceed:
                      </label>
                      <input
                        autoFocus
                        type="text"
                        value={confirm}
                        onChange={e => setConfirm(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && confirm === 'CONFIRM' && switchMode()}
                        placeholder="CONFIRM"
                        className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-gray-900 dark:text-white px-3 py-2.5 rounded-lg text-sm focus:outline-none focus:border-blue-600"
                      />
                    </div>
                    <div className="flex gap-3">
                      <button
                        onClick={() => { setShowModal(false); setConfirm('') }}
                        className="flex-1 px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={switchMode}
                        disabled={confirm !== 'CONFIRM' || switching}
                        className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {switching ? 'Switching…' : 'Switch to Live'}
                      </button>
                    </div>
                  </>
                )}
              </>
            ) : (
              /* ── Switch back to Paper ── */
              <>
                <div className="flex items-center gap-3 mb-4">
                  <AlertTriangle size={22} className="text-yellow-400" />
                  <h3 className="text-lg font-bold text-gray-900 dark:text-white">Switch to Paper Trading</h3>
                </div>
                <p className="text-gray-600 dark:text-gray-300 text-sm mb-5">
                  This will switch back to IBKR port 4004 (paper gateway). No real trades will be placed.
                </p>
                <div className="mb-5">
                  <label className="text-gray-600 dark:text-gray-400 text-sm block mb-2">
                    Type <code className="text-yellow-500 dark:text-yellow-400 bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">CONFIRM</code> to proceed:
                  </label>
                  <input
                    autoFocus
                    type="text"
                    value={confirm}
                    onChange={e => setConfirm(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && confirm === 'CONFIRM' && switchMode()}
                    placeholder="CONFIRM"
                    className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-gray-900 dark:text-white px-3 py-2.5 rounded-lg text-sm focus:outline-none focus:border-blue-600"
                  />
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={() => { setShowModal(false); setConfirm('') }}
                    className="flex-1 px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={switchMode}
                    disabled={confirm !== 'CONFIRM' || switching}
                    className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {switching ? 'Switching…' : 'Switch to Paper'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
