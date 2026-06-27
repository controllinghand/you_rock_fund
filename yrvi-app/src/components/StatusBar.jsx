import { useEffect, useState, useRef } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'
import axios from 'axios'
import { useThemeContext } from '../ThemeProvider.jsx'
import AlertsBell from './AlertsBell.jsx'

function Indicator({ ok, label }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-2 h-2 rounded-full ${ok ? 'bg-green-400' : 'bg-red-500'}`} />
      <span className={`text-xs ${ok ? 'text-gray-700 dark:text-gray-300' : 'text-red-400'}`}>{label}</span>
    </div>
  )
}

function fmt(n) {
  if (n == null) return '—'
  return '$' + Math.round(n).toLocaleString()
}

const THEME_CYCLE = { dark: 'light', light: 'system', system: 'dark' }
const THEME_ICONS = {
  dark:   <Moon size={14} />,
  light:  <Sun size={14} />,
  system: <Monitor size={14} />,
}

// How long the gateway must be continuously unreachable before the StatusBar
// offers a one-click restart. A gateway that just restarted (upgrade, nightly
// restart, soft restart) is normally disconnected for a minute or two while it
// logs back in — that's healthy startup, not a wedge. Waiting ~2 min (the
// watchdog itself waits 10) means the button only appears when it's actually
// stuck, so nobody is tempted to restart a gateway that's already recovering
// (which on live would fire a needless IB Key 2FA push).
const RESTART_BTN_GRACE_MS = 120000

// True when the gateway looks like it needs a manual restart: port down, or port
// up but the API handshake is dead — but NOT a credential problem (locked/failed),
// where a restart just risks a deeper lockout.
function gatewayNeedsRecovery(s) {
  return !!s
    && s.gateway_login_status !== 'failed'
    && s.gateway_login_status !== 'locked'
    && (!s.gateway_running || !s.ibkr_connected)
}

function versionDiff(current, latest) {
  const parse = v => (v || '').replace(/^v/, '').split('.').map(n => parseInt(n, 10) || 0)
  const [cM, cN, cP] = parse(current)
  const [lM, lN, lP] = parse(latest)
  if (lM > cM) return 'major'
  if (lN > cN) return 'minor'
  if (lP > cP) return 'patch'
  return 'same'
}

export default function StatusBar() {
  const [status, setStatus]       = useState(null)
  const [pidFlash, setPidFlash]   = useState(false)
  const prevPid                   = useRef(null)
  const { theme, setTheme }       = useThemeContext()

  const [gwRestarting, setGwRestarting]   = useState(false)
  const [gwRestartFlash, setGwRestartFlash] = useState(null)  // {msg, color} | null
  const [ibkrDownSince, setIbkrDownSince] = useState(null)    // ms timestamp | null

  const [versionInfo, setVersionInfo]     = useState(null)
  const [vChecking, setVChecking]         = useState(false)
  const [vFlash, setVFlash]               = useState(null)   // {msg, color} | null
  const [showConfirm, setShowConfirm]     = useState(false)
  const [upgradePhase, setUpgradePhase]   = useState(null)   // null|waiting_up|done|error
  const [upgradeOutput, setUpgradeOutput] = useState('')
  const [buildLog, setBuildLog]           = useState('')
  const [elapsedSecs, setElapsedSecs]     = useState(0)
  const pollRef      = useRef(null)
  const timerRef     = useRef(null)
  const logBoxRef    = useRef(null)
  const startTimeRef = useRef(null)

  // Auto-scroll build log to bottom as new lines arrive
  useEffect(() => {
    if (logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
    }
  }, [buildLog])

  // ── Status polling every 30s ─────────────────────────────────
  useEffect(() => {
    const fetch = () => axios.get('/api/status').then(r => {
      setStatus(r.data)
      const newPid = r.data?.scheduler_pid
      if (prevPid.current != null && newPid != null && newPid !== prevPid.current) {
        setPidFlash(true)
        setTimeout(() => setPidFlash(false), 2000)
      }
      prevPid.current = newPid
      // Stamp when the gateway first went unreachable, so the restart button can
      // wait out the normal login window before appearing. Cleared on recovery.
      setIbkrDownSince(prev =>
        gatewayNeedsRecovery(r.data) ? (prev ?? Date.now()) : null
      )
    }).catch(() => {})
    fetch()
    const t = setInterval(fetch, 30000)
    return () => clearInterval(t)
  }, [])

  // ── Version polling every 5 minutes ──────────────────────────
  useEffect(() => {
    const check = () =>
      axios.get('/api/version/check')
        .then(r => setVersionInfo(r.data))
        .catch(() => setVersionInfo(prev =>
          prev
            ? { ...prev, latest: null, up_to_date: null, error: 'unavailable' }
            : { current: 'unknown', latest: null, up_to_date: null, error: 'unavailable' }
        ))
    check()
    const t = setInterval(check, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  const flash = (msg, color) => {
    setVFlash({ msg, color })
    setTimeout(() => setVFlash(null), 2500)
  }

  // ── Wedged-gateway recovery (one-click full restart) ─────────
  const restartGateway = async () => {
    if (gwRestarting) return
    if (!window.confirm(
      'Restart the IB Gateway?\n\n' +
      'This recovers a wedged gateway by re-running login. On a LIVE account you ' +
      'must approve an IB Key 2FA push on your phone; paper logs in automatically. ' +
      'Takes ~1–2 minutes.'
    )) return
    setGwRestarting(true)
    setGwRestartFlash(null)
    try {
      await axios.post('/api/gateway/restart')
      setGwRestartFlash({ msg: 'Restart sent — recovering…', color: 'text-amber-400' })
      // Restart the grace clock: the gateway is now re-running login, so fall back
      // to the calm "connecting…" state instead of immediately re-offering the button.
      setIbkrDownSince(Date.now())
    } catch (err) {
      setGwRestartFlash({
        msg: err.response?.data?.detail ?? 'Restart failed',
        color: 'text-red-400',
      })
    } finally {
      setGwRestarting(false)
      setTimeout(() => setGwRestartFlash(null), 6000)
    }
  }

  const checkVersionNow = () => {
    if (vChecking) return
    setVChecking(true)
    axios.get('/api/version/check')
      .then(r => {
        setVersionInfo(r.data)
        if (r.data?.up_to_date) {
          flash('✓ Up to date', 'text-green-400')
        }
        // if behind, the Upgrade button appearing is feedback enough
      })
      .catch(() => flash('Unable to reach GitHub', 'text-gray-400'))
      .finally(() => setVChecking(false))
  }

  // Cleanup on unmount
  useEffect(() => () => {
    if (pollRef.current)  clearInterval(pollRef.current)
    if (timerRef.current) clearInterval(timerRef.current)
  }, [])

  // ── Reconnect polling helpers ─────────────────────────────────
  function stopPoll() {
    if (pollRef.current)  { clearInterval(pollRef.current);  pollRef.current  = null }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }

  // Poll /api/version/check until the running version matches expectedVersion.
  // Also polls /api/upgrade/log every tick to stream live build output.
  function startReconnectPolling(baseOutput, expectedVersion) {
    setUpgradePhase('waiting_up')
    setBuildLog('')
    setElapsedSecs(0)
    startTimeRef.current = Date.now()

    // Elapsed-time ticker (every second)
    timerRef.current = setInterval(() => {
      setElapsedSecs(Math.floor((Date.now() - startTimeRef.current) / 1000))
    }, 1000)

    pollRef.current = setInterval(() => {
      const elapsed = Date.now() - startTimeRef.current
      if (elapsed > 300000) {
        stopPoll()
        setUpgradePhase('error')
        setBuildLog(prev => prev + '\n\n⚠️  Still running after 5 minutes — check Docker logs:\n  docker compose --env-file .env.compose logs --tail=50 api')
        return
      }

      // Version check — detects when upgrade is complete
      axios.get('/api/version/check', { timeout: 2000 })
        .then(r => {
          if (r.data?.current && r.data.current === expectedVersion) {
            stopPoll()
            setUpgradePhase('done')
            setTimeout(() => window.location.reload(), 2000)
          }
        })
        .catch(() => {})

      // Log poll — streams live build output
      axios.get('/api/upgrade/log', { timeout: 2000 })
        .then(r => { if (r.data?.content) setBuildLog(r.data.content) })
        .catch(() => {})
    }, 3000)
  }

  // ── Upgrade: call API endpoint, then poll for reconnect ──────
  async function handleUpgrade() {
    const expectedVersion = versionInfo?.latest
    setShowConfirm(false)
    setUpgradePhase('waiting_up')
    setUpgradeOutput('Pulling latest code and rebuilding containers…')
    try {
      const res = await axios.post('/api/version/upgrade', {}, { timeout: 90000 })
      const { success, output } = res.data
      setUpgradeOutput(output || '')
      if (success) {
        startReconnectPolling(output || '', expectedVersion)
      } else {
        setUpgradePhase('error')
      }
    } catch (err) {
      // API going dark mid-request means containers are already rebuilding — poll for restart
      if (!err.response) {
        startReconnectPolling('', expectedVersion)
      } else {
        setUpgradePhase('error')
        setUpgradeOutput(err.response?.data?.detail ?? err.message ?? 'Upgrade request failed')
      }
    }
  }

  function closeUpgrade() {
    stopPoll()
    setUpgradePhase(null)
    setUpgradeOutput('')
    setBuildLog('')
    setElapsedSecs(0)
  }

  // Gateway needs recovery (raw signal). Split into two UI states by how long it's
  // been down: within the grace window it's probably just logging in → show a calm
  // "connecting…" hint; past the grace window it's likely wedged → offer the button.
  const gwUnhealthy = gatewayNeedsRecovery(status)
  const gwDownMs    = gwUnhealthy && ibkrDownSince ? Date.now() - ibkrDownSince : 0
  const gwConnecting  = gwUnhealthy && gwDownMs <  RESTART_BTN_GRACE_MS
  const gwShowRestart = gwUnhealthy && gwDownMs >= RESTART_BTN_GRACE_MS

  // ── Derived version state ─────────────────────────────────────
  const isLive    = status?.trading_mode === 'live'
  const vUp       = versionInfo && !versionInfo.error && versionInfo.up_to_date === true
  const vBehind   = versionInfo && !versionInfo.error && versionInfo.up_to_date === false
  const vUnknown  = !versionInfo || !!versionInfo.error || versionInfo.up_to_date === null
  const diff      = vBehind ? versionDiff(versionInfo.current, versionInfo.latest) : null
  const pillColor = vUp ? 'green' : vBehind && diff === 'patch' ? 'yellow' : vBehind ? 'red' : 'gray'

  const canCancel = upgradePhase === 'waiting_up'

  const upgradeModalPhaseLabel = {
    waiting_up: 'Building & restarting — this takes 1–2 minutes…',
    done:       '✅ Back online! Refreshing...',
    error:      '⚠️ Taking longer than expected',
  }

  return (
    <>
      {/* ── Top bar ────────────────────────────────────────────── */}
      <div className="h-12 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 flex items-center px-6 gap-6 shrink-0">
        {/* Status pills */}
        <div className="flex items-center gap-4">
          <Indicator
            ok={status?.gateway_running && status?.gateway_login_status !== 'failed' && status?.gateway_login_status !== 'locked'}
            label={
              status?.gateway_login_status === 'locked' ? 'Gateway · locked out' :
              status?.gateway_login_status === 'failed' ? 'Gateway · login failed' :
              'Gateway'
            }
          />

          {/* Logging in — probably just a normal restart/reconnect; let it resolve */}
          {gwConnecting && !gwRestarting && (
            <span className="flex items-center gap-1.5 text-xs text-amber-500" title="Gateway is logging in — this usually clears on its own in a minute or two">
              <svg className="animate-spin w-2.5 h-2.5" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Gateway connecting…
            </span>
          )}

          {/* Still down past the grace window → likely wedged, offer one-click recovery */}
          {(gwShowRestart || gwRestarting) && (
            <button
              onClick={restartGateway}
              disabled={gwRestarting}
              title="Gateway still unreachable after a couple minutes — restart it to recover (live needs IB Key 2FA)"
              className="text-xs px-2 py-0.5 rounded border border-red-700 text-red-400 hover:bg-red-900/30 disabled:opacity-60 disabled:cursor-wait font-medium transition-colors"
            >
              {gwRestarting ? 'Restarting…' : 'Restart Gateway'}
            </button>
          )}
          {gwRestartFlash && (
            <span className={`text-xs font-medium ${gwRestartFlash.color}`}>
              {gwRestartFlash.msg}
            </span>
          )}

          {/* Scheduler with PID-change flash */}
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full transition-colors duration-500 ${
              pidFlash                         ? 'bg-green-300 shadow-[0_0_6px_2px_rgba(74,222,128,0.6)]' :
              status?.scheduler_pid != null    ? 'bg-green-400' : 'bg-red-500'
            }`} />
            <span className={`text-xs transition-colors duration-500 ${
              pidFlash                         ? 'text-green-400 font-semibold' :
              status?.scheduler_pid != null    ? 'text-gray-700 dark:text-gray-300' : 'text-red-400'
            }`}>Scheduler</span>
          </div>

          <Indicator ok={status?.ibkr_connected} label="IBKR" />

          {/* Version pill — click to check for updates */}
          {versionInfo && (
            <div className="flex items-center gap-2">
              <button
                onClick={checkVersionNow}
                disabled={vChecking}
                title={vChecking ? 'Checking…' : 'Click to check for updates'}
                className="flex items-center gap-1.5 hover:opacity-70 transition-opacity disabled:cursor-wait"
              >
                {vChecking
                  ? <svg className="animate-spin w-2 h-2 text-gray-400" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  : <div className={`w-2 h-2 rounded-full ${
                      pillColor === 'green'  ? 'bg-green-400' :
                      pillColor === 'yellow' ? 'bg-yellow-400' :
                      pillColor === 'red'    ? 'bg-red-500' : 'bg-gray-400'
                    }`} />
                }
                <span className={`text-xs ${
                  pillColor === 'green'  ? 'text-gray-700 dark:text-gray-300' :
                  pillColor === 'yellow' ? 'text-yellow-400' :
                  pillColor === 'red'    ? 'text-red-400' : 'text-gray-500'
                }`}>
                  {vChecking
                    ? `v${versionInfo.current}`
                    : vUnknown
                    ? 'version unknown'
                    : vUp
                    ? `v${versionInfo.current}`
                    : `v${versionInfo.current} → v${versionInfo.latest}`}
                </span>
                {vFlash && (
                  <span className={`text-xs font-medium ${vFlash.color}`}>
                    {vFlash.msg}
                  </span>
                )}
              </button>
              {vBehind && (
                <button
                  onClick={() => setShowConfirm(true)}
                  className={`text-xs px-2 py-0.5 rounded border font-medium transition-colors ${
                    pillColor === 'yellow'
                      ? 'border-yellow-600 text-yellow-400 hover:bg-yellow-900/30'
                      : 'border-red-700 text-red-400 hover:bg-red-900/30'
                  }`}
                >
                  Upgrade
                </button>
              )}
            </div>
          )}
        </div>

        <div className="w-px h-5 bg-gray-200 dark:bg-gray-800" />

        {/* Account info */}
        <div className="flex items-center gap-4 text-xs">
          <span className="text-gray-500">Account</span>
          <span className="text-gray-900 dark:text-white font-medium font-mono">{fmt(status?.account_value)}</span>
          {status?.unrealized_pnl != null && status.unrealized_pnl !== 0 && (
            <>
              <span className="text-gray-500">Unrealized</span>
              <span className={`font-medium font-mono ${status.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {status.unrealized_pnl >= 0 ? '+' : '−'}${Math.round(Math.abs(status.unrealized_pnl)).toLocaleString()}
              </span>
            </>
          )}
          <span className="text-gray-500">Buying Power</span>
          <span className="text-gray-900 dark:text-white font-medium font-mono">{fmt(status?.buying_power)}</span>
          {(status?.wheel_count ?? 0) > 0 && (
            <span className="text-yellow-600 dark:text-yellow-400 font-medium">🔄 {status.wheel_count} wheel{status.wheel_count !== 1 ? 's' : ''}</span>
          )}
        </div>

        <div className="flex-1" />

        {/* Mode badge */}
        <span className={`text-xs font-bold px-3 py-1 rounded-full border ${
          isLive
            ? 'bg-green-100 text-green-700 border-green-300 dark:bg-green-900/50 dark:text-green-400 dark:border-green-700 animate-pulse'
            : 'bg-blue-100 text-blue-700 border-blue-300 dark:bg-blue-900/40 dark:text-blue-400 dark:border-blue-800'
        }`}>
          {isLive ? '🟢 LIVE' : '📄 PAPER'}
        </span>

        {status?.account && (
          <span className="text-xs text-gray-500 dark:text-gray-600">{status.account}</span>
        )}

        {/* Alerts bell */}
        <AlertsBell />

        {/* Theme toggle */}
        <button
          onClick={() => setTheme(THEME_CYCLE[theme])}
          title={`Theme: ${theme} (click to cycle)`}
          className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          {THEME_ICONS[theme]}
        </button>
      </div>

      {/* ── Confirmation modal ─────────────────────────────────── */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-gray-900 rounded-xl p-6 max-w-md w-full mx-4 shadow-2xl border border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-2">Upgrade YRVI?</h2>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
              This will pull the latest code and rebuild the containers.
              The dashboard will automatically reconnect when complete. Continue?
            </p>
            <p className="text-sm font-mono text-gray-400 dark:text-gray-500 mb-6">
              v{versionInfo?.current} → v{versionInfo?.latest}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpgrade}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors font-medium"
              >
                Upgrade
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Upgrade full-screen overlay ────────────────────────── */}
      {upgradePhase && (
        <div className="fixed inset-0 bg-black/90 flex flex-col items-center justify-center z-[100] px-6">
          {upgradePhase === 'done' ? (
            <>
              <svg className="w-12 h-12 text-green-400 mb-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              <h2 className="text-white text-2xl font-bold mb-1">Upgrade complete</h2>
              <p className="text-gray-400 text-sm">Reloading dashboard…</p>
            </>
          ) : upgradePhase === 'error' ? (
            <div className="w-full max-w-2xl">
              <div className="flex items-center gap-3 mb-5">
                <span className="text-2xl">⚠️</span>
                <h2 className="text-white text-xl font-bold">Build taking longer than expected</h2>
              </div>
              {(upgradeOutput || buildLog) && (
                <pre className="bg-gray-950 text-green-300 text-xs font-mono p-4 rounded-lg overflow-auto max-h-72 mb-5 whitespace-pre-wrap break-all">
                  {upgradeOutput}{buildLog ? '\n\n' + buildLog : ''}
                </pre>
              )}
              <p className="text-yellow-400 text-sm mb-5">
                The git pull succeeded — containers may still be building. Run manually if needed:{' '}
                <code className="font-mono bg-gray-800 px-1.5 py-0.5 rounded text-yellow-300">
                  bash scripts/yrvi-build.sh all --paper
                </code>
              </p>
              <button
                onClick={closeUpgrade}
                className="px-5 py-2 rounded-lg border border-gray-600 text-gray-300 hover:bg-gray-800 text-sm font-medium transition-colors"
              >
                Close
              </button>
            </div>
          ) : (
            <div className="w-full max-w-2xl flex flex-col items-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-400 mb-5" />
              <h2 className="text-white text-xl font-semibold mb-1">Upgrading YRVI…</h2>
              <p className="text-gray-400 text-sm mb-1">Rebuilding containers — dashboard will reload automatically</p>
              <p className="text-gray-600 text-xs mb-6 font-mono">{elapsedSecs}s elapsed · typically 1–2 min</p>
              {(upgradeOutput || buildLog) && (
                <pre
                  ref={logBoxRef}
                  className="w-full bg-gray-950 text-green-300 text-xs font-mono p-4 rounded-lg overflow-auto max-h-72 mb-5 whitespace-pre-wrap break-all"
                >
                  {upgradeOutput}{buildLog ? '\n\n' + buildLog : ''}
                </pre>
              )}
              {canCancel && (
                <button
                  onClick={closeUpgrade}
                  className="px-5 py-2 rounded-lg border border-gray-700 text-gray-400 hover:bg-gray-800 text-sm transition-colors"
                >
                  Cancel
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </>
  )
}
