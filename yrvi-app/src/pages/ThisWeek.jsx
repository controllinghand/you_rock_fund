import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { Clock, TrendingUp, AlertCircle, Play } from 'lucide-react'

function useCountdown(isoStr) {
  const [label, setLabel] = useState('')
  useEffect(() => {
    if (!isoStr) return
    const update = () => {
      const diff = new Date(isoStr) - Date.now()
      if (diff <= 0) { setLabel('Executing now!'); return }
      const d = Math.floor(diff / 86400000)
      const h = Math.floor((diff % 86400000) / 3600000)
      const m = Math.floor((diff % 3600000) / 60000)
      const s = Math.floor((diff % 60000) / 1000)
      setLabel(d > 0 ? `${d}d ${h}h ${m}m` : `${h}h ${m}m ${s}s`)
    }
    update()
    const t = setInterval(update, 1000)
    return () => clearInterval(t)
  }, [isoStr])
  return label
}

function fmtDate(s) {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' })
  } catch { return s }
}

// Ticks once per second while `active`, resetting to 0 each time it turns on.
// Drives the dry-run progress display so a 1–2 min preview never looks frozen.
function useElapsedSeconds(active) {
  const [secs, setSecs] = useState(0)
  useEffect(() => {
    if (!active) { setSecs(0); return }
    const start = Date.now()
    const id = setInterval(() => setSecs(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(id)
  }, [active])
  return secs
}

// Approximate phases of the /api/screener dry-run, in real execution order.
// Thresholds are rough (the wheel check dominates and scales with wheel count);
// the live elapsed counter is the true "still working" signal.
const PREVIEW_STAGES = [
  { at: 0,  text: 'Connecting to IBKR…' },
  { at: 3,  text: 'Pricing covered calls on your wheel holdings (the slow step)…' },
  { at: 35, text: 'Fetching CSP candidates and sizing positions…' },
]
function previewStage(secs) {
  let stage = PREVIEW_STAGES[0].text
  for (const s of PREVIEW_STAGES) if (secs >= s.at) stage = s.text
  return stage
}

export default function ThisWeek() {
  const [screener, setScreener]       = useState(null)
  const [status, setStatus]           = useState(null)
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState(null)
  const [runAt, setRunAt]             = useState(null)
  const [manualRunning, setManualRunning] = useState(false)
  const [manualMsg, setManualMsg]     = useState(null)
  const [runStatus, setRunStatus]     = useState(null)
  const elapsed = useElapsedSeconds(loading)

  useEffect(() => {
    axios.get('/api/status').then(r => setStatus(r.data)).catch(() => {})
    axios.get('/api/run-status').then(r => setRunStatus(r.data)).catch(() => {})
  }, [])

  // Always poll run-status every 5s so we catch both manual and scheduled runs
  useEffect(() => {
    const t = setInterval(() => {
      axios.get('/api/run-status').then(r => {
        const wasExecuting = runStatus?.executing
        setRunStatus(r.data)
        // Run just finished — show result
        if (wasExecuting && !r.data.executing) {
          if (r.data.result) {
            const { fills, premium, cc_premium, freed_capital } = r.data.result
            let text = `✅ Run complete — ${fills} CSP fill(s), $${(premium ?? 0).toLocaleString()} CSP premium`
            if (cc_premium) text += `, $${cc_premium.toLocaleString()} CC premium`
            if (freed_capital) text += `, $${freed_capital.toLocaleString()} freed`
            setManualMsg({ ok: true, text })
          } else if (r.data.error) {
            setManualMsg({ ok: false, text: `Run failed: ${r.data.error}` })
          }
        }
      }).catch(() => {})
    }, 5000)
    return () => clearInterval(t)
  }, [runStatus?.executing])

  const countdown = useCountdown(status?.next_execution)

  const execLabel = (() => {
    if (!status?.next_execution) return 'Monday 10:00 AM PST (1:00 PM ET)'
    const d = new Date(status.next_execution)
    const day = d.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'America/Los_Angeles' })
    const pst = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'America/Los_Angeles' })
    const et  = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'America/New_York' })
    return `${day} ${pst} PST (${et} ET)`
  })()

  const isExecuting = runStatus?.executing || manualRunning

  const triggerManualRun = useCallback(async () => {
    // Context-aware warning: Run Now executes the FULL Monday sequence live —
    // it will sell shares, write covered calls, and open CSPs immediately.
    const ptParts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Los_Angeles', weekday: 'short', hour: 'numeric',
      minute: 'numeric', hour12: false,
    }).formatToParts(new Date())
    const wd  = ptParts.find(p => p.type === 'weekday')?.value
    const hh  = parseInt(ptParts.find(p => p.type === 'hour')?.value ?? '0', 10)
    const mm  = parseInt(ptParts.find(p => p.type === 'minute')?.value ?? '0', 10)
    const mins = hh * 60 + mm
    const inMondayWindow = wd === 'Mon' && mins >= 9 * 60 + 45 && mins <= 10 * 60 + 15

    let msg = 'Run the FULL Monday sequence now?\n\n'
      + 'This places REAL orders in your IBKR account immediately:\n'
      + '  • Wheel check — sells shares (dropped screener / stop-loss / no viable CC)\n'
      + '    and writes covered calls on remaining holdings\n'
      + '  • CSP pipeline — opens new cash-secured puts\n\n'
      + 'Tip: click "Run Screener" first to preview exactly what will execute.'
    if (inMondayWindow) {
      msg += '\n\n⚠️ It is currently the Monday 9:55/10:00 AM PT window. The scheduled '
        + 'run may also fire — running now can DOUBLE-EXECUTE (duplicate CCs and CSPs).'
    }
    if (!window.confirm(msg)) return
    setManualRunning(true)
    setManualMsg(null)
    try {
      await axios.post('/api/manual-run', {}, { timeout: 15000 })
      setRunStatus({ executing: true, started_at: new Date().toISOString(), result: null, error: null })
      setManualMsg(null)
    } catch (err) {
      setManualMsg({ ok: false, text: err.response?.data?.detail ?? err.message })
    } finally {
      setManualRunning(false)
    }
  }, [])

  const runScreener = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Backend dry-run connects to IBKR, queries option chains for every wheel
      // holding, AND hits the Render screener API (which can cold-start ~60s on
      // its own). With multiple wheels active this routinely exceeds 60s, so give
      // it generous headroom rather than aborting a run that's still working.
      const res = await axios.get('/api/screener', { timeout: 150000 })
      setScreener(res.data)
      setRunAt(new Date())
    } catch (err) {
      setError(err.response?.data?.detail ?? err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const positions = screener?.positions ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1">This Week</h1>
        <div className="text-gray-500 text-sm">Preview or run Monday's full sequence — wheel check + CSPs</div>
      </div>

      {/* Next execution */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-gray-500 text-sm mb-1">Next Execution</div>
            <div className="text-3xl font-bold text-gray-900 dark:text-white font-mono">{countdown || '—'}</div>
            <div className="text-gray-500 dark:text-gray-600 text-sm mt-1">{execLabel}</div>
          </div>
          <div className="flex flex-col items-end gap-3">
            <Clock size={40} className="text-blue-600/30" />
            <div className="flex gap-2">
              <div className="flex flex-col items-end gap-1">
                <button
                  onClick={triggerManualRun}
                  disabled={isExecuting}
                  className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-500 disabled:opacity-60 disabled:cursor-wait text-white text-sm font-medium rounded-lg transition-colors"
                  title="Run the full CSP pipeline now and place orders"
                >
                  <Play size={14} className={isExecuting ? 'animate-pulse' : ''} />
                  {isExecuting ? 'Executing...' : 'Run Now'}
                </button>
                <div className="text-xs text-gray-400 dark:text-gray-600 text-right">Only if schedule failed or mid-week re-run</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Executing banner */}
      {isExecuting && (
        <div className="rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4 space-y-3">
          {/* Header */}
          <div className="flex items-center gap-3">
            <div className="animate-spin rounded-full h-4 w-4 border-2 border-blue-500 border-t-transparent flex-shrink-0" />
            <div className="text-sm font-medium text-blue-700 dark:text-blue-400">
              {runStatus?.current_ticker
                ? <>Working on <span className="font-bold">{runStatus.current_ticker}</span>
                    {runStatus.current_stage ? <span className="font-normal opacity-75"> — {runStatus.current_stage}</span> : ''}</>
                : 'Pipeline executing — connecting to IBKR...'}
            </div>
          </div>

          {/* Per-ticker results so far */}
          {runStatus?.ticker_results?.length > 0 && (
            <div className="space-y-1">
              {runStatus.ticker_results.map((r, i) => {
                const s = r.status || ''
                let emoji = '❌'
                let detail = s.replace(/_/g, ' ')
                if (s === 'filled' || s === 'partial_fill' || s === 'dry_run') {
                  emoji = '✅'
                  detail = `filled @ $${r.fill_price?.toFixed(2)}`
                    + (r.order_type ? ` via ${r.order_type.replace(/_/g, ' ')}` : '')
                    + (r.premium_collected != null ? ` — $${r.premium_collected.toFixed(0)}` : '')
                } else if (s === 'cc_opened') {
                  emoji = '✅'
                  detail = `CC $${r.cc_strike}${r.cc_delta != null ? ` (δ${r.cc_delta})` : ''}`
                    + (r.cc_premium != null ? ` — $${r.cc_premium.toFixed(0)}` : '')
                } else if (s === 'skipped_excluded') {
                  emoji = '🚫'; detail = 'excluded — left alone'
                } else if (s === 'cc_deferred') {
                  emoji = '⏳'; detail = 'CC deferred (no data) — shares kept'
                } else if (s === 'cc_already_open' || s === 'held_covered') {
                  emoji = '♻️'; detail = 'already covered'
                } else if (s.startsWith('sold_')) {
                  emoji = '💰'
                  detail = s.replace(/_/g, ' ') + (r.proceeds != null ? ` — $${r.proceeds.toFixed(0)}` : '')
                } else if (s.startsWith('skipped')) {
                  emoji = '⚠️'
                }
                return (
                  <div key={i} className="text-xs font-mono text-blue-800 dark:text-blue-300 flex gap-2">
                    <span>{emoji}</span>
                    <span className="font-bold">{r.ticker}</span>
                    <span className="opacity-75">{detail}</span>
                  </div>
                )
              })}
            </div>
          )}

          {/* Footer note */}
          <div className="text-xs text-blue-600 dark:text-blue-500 opacity-75">
            Check the <span className="font-medium">Dashboard</span> for live holdings and final results.
          </div>
        </div>
      )}

      {/* Manual run feedback */}
      {manualMsg && (
        <div className={`rounded-xl px-4 py-3 text-sm font-medium ${manualMsg.ok ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'}`}>
          {manualMsg.ok ? '✅' : '❌'} {manualMsg.text}
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-8 text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto mb-4" />
          <div className="text-gray-600 dark:text-gray-400">{previewStage(elapsed)}</div>
          <div className="text-gray-500 dark:text-gray-600 text-sm mt-1">
            Previewing Monday — wheel check + screener + sizer · {elapsed}s elapsed · usually 1–2 min
          </div>
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div className="bg-red-900/20 border border-red-800 rounded-xl p-4 flex gap-3">
          <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
          <div>
            <div className="text-red-400 font-medium text-sm">Screener error</div>
            <div className="text-red-400/70 text-sm mt-1">{error}</div>
          </div>
        </div>
      )}

      {/* Results */}
      {screener && !loading && (
        <>
          {/* Capital allocation (shown when wheel holdings are active) */}
          {(screener.active_wheel_count ?? 0) > 0 && (() => {
            const slotsTotal   = screener.num_positions ?? ((screener.active_wheel_count ?? 0) + positions.length)
            const noCspSlots    = (screener.target_fills ?? 0) <= 0
            const idleCash      = screener.cash_park?.idle ?? screener.cash_park?.buying_power ?? null
            return (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
              <div className="text-gray-900 dark:text-white font-semibold text-sm mb-3">Capital Allocation</div>
              {noCspSlots ? (
                /* All slots taken by wheels → no CSP slots open. Show the real reason,
                   not a misleading "net liq − reserved = $0 available" cash-shortage view. */
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Slots</span>
                    <span className="text-gray-900 dark:text-white font-mono">{screener.active_wheel_count} wheel · 0 CSP of {slotsTotal} total</span>
                  </div>
                  <div className="text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 leading-relaxed">
                    All {slotsTotal} position slot{slotsTotal !== 1 ? 's are' : ' is'} held by wheel holdings, so no CSP slots are open this week — there are no new CSPs regardless of cash. This is <span className="font-semibold">not</span> a cash shortage.
                    {idleCash != null && idleCash > 0 && <> Your idle cash (${Math.round(idleCash).toLocaleString()}) is free and available for the cash sweep.</>}
                    {' '}Raise “# Positions” in Settings to open more CSP slots.
                  </div>
                </div>
              ) : (
              <div className="space-y-2 text-sm">
                {screener.cash_account ? (
                  <>
                    <div className="flex justify-between">
                      <span className="text-gray-500">Buying Power (cash)</span>
                      <span className="text-gray-900 dark:text-white font-mono">${(screener.buying_power ?? screener.budget ?? 0).toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-xs text-gray-500 dark:text-gray-600">
                      <span>
                        Wheel stock ({screener.active_wheel_count} holding{screener.active_wheel_count !== 1 ? 's' : ''})
                        {(screener.wheel_holdings ?? []).filter(h => h.shares > 0).map(h => ` · ${h.ticker}`).join('')} — cash already spent, not re-subtracted
                      </span>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="flex justify-between">
                      <span className="text-gray-500">
                        {screener.compound_enabled ? 'Effective Budget (net liq)' : 'Fund Budget'}
                      </span>
                      <span className="text-gray-900 dark:text-white font-mono">${(screener.total_budget ?? 0).toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-red-400">
                      <span>
                        Reserved ({screener.active_wheel_count} wheel holding{screener.active_wheel_count !== 1 ? 's' : ''})
                        {(screener.wheel_holdings ?? []).filter(h => h.shares > 0).map(h => ` · ${h.ticker}`).join('')}
                      </span>
                      <span className="font-mono">− ${(screener.reserved_capital ?? 0).toLocaleString()}</span>
                    </div>
                  </>
                )}
                <div className="border-t border-gray-200 dark:border-gray-800 pt-2 flex justify-between font-semibold">
                  <span className="text-gray-900 dark:text-white">Available for CSPs</span>
                  <span className="text-green-400 font-mono">${(screener.budget ?? 0).toLocaleString()}</span>
                </div>
                <div className="flex justify-between text-xs text-gray-500 dark:text-gray-600 pt-0.5">
                  <span>Slots used</span>
                  <span>{screener.active_wheel_count ?? 0} wheel + {positions.length} CSP of {slotsTotal} total</span>
                </div>
              </div>
              )}
            </div>
            )
          })()}

          {/* Wheel holdings table */}
          {(screener.wheel_holdings ?? []).some(h => h.shares > 0) && (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2">
                <div className="text-gray-900 dark:text-white font-semibold text-sm">🔄 Wheel Holdings</div>
                <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 px-2 py-0.5 rounded-full">
                  {screener.wheel_holdings.filter(h => h.shares > 0).length}
                </span>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                    {['Ticker', 'Shares', 'Avg Cost', 'Capital Tied', 'CC Status', 'CC Strike', 'Expiry'].map(h => (
                      <th key={h} className={`${h === 'Ticker' ? 'text-left' : 'text-right'} px-4 py-3`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {screener.wheel_holdings.filter(h => h.shares > 0).map(h => {
                    const capitalTied = h.shares * (h.assigned_strike ?? 0)
                    return (
                      <tr key={h.ticker} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                        <td className="px-4 py-3 text-gray-900 dark:text-white font-semibold">{h.ticker}</td>
                        <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">{h.shares}</td>
                        <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">${h.assigned_strike}</td>
                        <td className="px-4 py-3 text-right text-red-400 font-medium font-mono">${capitalTied.toLocaleString()}</td>
                        <td className="px-4 py-3 text-right">
                          <span className={`text-xs px-2 py-0.5 rounded-full border capitalize ${
                            h.cc_status === 'open'    ? 'bg-green-100 text-green-700 border-green-300 dark:bg-green-900/40 dark:text-green-400 dark:border-green-800'
                            : h.cc_status === 'pending' ? 'bg-yellow-100 text-yellow-800 border-yellow-300 dark:bg-yellow-900/40 dark:text-yellow-400 dark:border-yellow-800'
                            : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700'
                          }`}>{h.cc_status ?? '—'}</span>
                        </td>
                        <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400">{h.current_cc_strike ? `$${h.current_cc_strike}` : '—'}</td>
                        <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400 text-xs">{h.current_cc_expiry ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Monday wheel plan (preview of wheel-check decisions) */}
          {(screener.wheel_plan ?? []).length > 0 && (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
                <div className="text-gray-900 dark:text-white font-semibold text-sm">🗓️ Monday Wheel Plan</div>
                <div className="text-gray-500 dark:text-gray-600 text-xs">
                  CC ${(screener.wheel_cc_premium ?? 0).toLocaleString()} · Freed ${(screener.wheel_freed_capital ?? 0).toLocaleString()}
                </div>
              </div>
              <div className="divide-y divide-gray-100 dark:divide-gray-800/50">
                {screener.wheel_plan.map((a, i) => {
                  const isCC       = a.action === 'cc_opened'
                  const isFailed   = a.action === 'cc_failed'
                  const isDeferred = a.action === 'cc_deferred'
                  const isAlready  = a.action === 'cc_already_open' || a.action === 'held_covered'
                  const isSold     = typeof a.action === 'string' && a.action.startsWith('sold')
                  const isSellFail = a.action === 'sell_failed'
                  const emoji      = isCC ? '✅' : isDeferred ? '⏳' : isAlready ? '♻️' : isSold ? '📤' : isSellFail ? '🛑' : '⚠️'
                  let detail
                  if (isSellFail) {
                    detail = `Sell (${(a.reason ?? '').replace(/_/g, ' ')}) couldn't be priced — ${a.shares ?? ''} sh kept; will sell live at market`
                  } else if (isCC) {
                    detail = `Write CC @ $${a.cc_strike} · δ${(a.cc_delta ?? 0).toFixed(2)} · ~$${(a.cc_premium ?? 0).toLocaleString()} premium · exp ${a.cc_expiry}`
                  } else if (isDeferred) {
                    // "kept (no sale)" alone reads as a decision. When the stop
                    // loss couldn't be evaluated (no price — the same gap that
                    // blocks CC pricing), Monday may well SELL this holding once
                    // a price exists, so the preview must not imply otherwise.
                    detail = a.stop_loss_undetermined
                      ? `⚠️ Stop loss NOT evaluated — no price available. ${a.shares ?? ''} sh kept for now; both the stop-loss check and the CC re-run at Monday's open, and this may become a SALE.`
                      : `CC priced Monday at open — market closed, ${a.shares ?? ''} sh kept (no sale)`
                  } else if (isAlready) {
                    detail = `Already covered by open CC${a.cc_expiry ? ` (exp ${a.cc_expiry}${a.contracts ? `, ${a.contracts}x` : ''})` : ''} — skip (recovery-safe)`
                  } else if (isSold) {
                    const reason = a.action.replace(/^sold_?/, '').replace(/_/g, ' ') || 'sold'
                    detail = `Sell ${a.shares ?? ''} sh (${reason}) · ~$${(a.proceeds ?? 0).toLocaleString()} proceeds · P&L $${(a.realized_pnl ?? 0).toLocaleString()}`
                  } else if (isFailed) {
                    detail = `CC could not be priced @ $${a.cc_strike}`
                  } else {
                    detail = a.action
                  }
                  return (
                    <div key={i} className="px-5 py-2.5 flex items-start gap-2 text-sm">
                      <span>{emoji}</span>
                      <span className="font-semibold text-gray-900 dark:text-white w-16">{a.ticker}</span>
                      <span className="text-gray-600 dark:text-gray-400 text-xs leading-relaxed">{detail}</span>
                    </div>
                  )
                })}
              </div>
              <div className="px-5 py-2 text-xs text-gray-400 dark:text-gray-600 border-t border-gray-100 dark:border-gray-800/50">
                Covered-call strikes/deltas come from IBKR option-chain queries (delayed-frozen on a closed market, i.e. Friday's close) — this mirrors Monday's wheel check. ⏳ rows are holdings whose CC can't be priced until Monday's open; shares are kept, not sold.
              </div>
            </div>
          )}

          {/* Cash Sweep decision (preview of the QQQ/SGOV park) */}
          {screener.cash_park && (() => {
            const cp = screener.cash_park
            const emoji = cp.status === 'bought' || cp.status === 'dry_run' ? '🅿️'
                        : cp.status === 'skipped_no_cash' ? '💤'
                        : cp.status === 'skipped_slots_unfilled' ? '⏸️'
                        : cp.status === 'skipped_existing_open' ? '♻️' : '⚠️'
            const usd = v => `$${Math.round(v ?? 0).toLocaleString()}`
            return (
              <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
                <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
                  <div className="text-gray-900 dark:text-white font-semibold text-sm">🅿️ Cash Sweep</div>
                  <div className="text-gray-500 dark:text-gray-600 text-xs">{cp.instrument}</div>
                </div>
                <div className="px-5 py-3 space-y-2">
                  <div className="flex items-start gap-2 text-sm">
                    <span>{emoji}</span>
                    <span className="text-gray-700 dark:text-gray-300">{cp.message}</span>
                  </div>
                  {cp.base != null && (
                    <div className="text-xs text-gray-500 dark:text-gray-500 pl-6">
                      Remainder {usd(cp.remainder ?? cp.base)} · Settled cash {cp.settled_cash == null ? '—' : usd(cp.settled_cash)}
                      {' · '}{cp.all_slots_filled ? 'no 10% cap (all slots filled)' : `10% net-liq cap ${usd(cp.netliq_cap)}`}
                      {' · '}Would park {usd(cp.buy_amount)}
                    </div>
                  )}
                </div>
                <div className="px-5 py-2 text-xs text-gray-400 dark:text-gray-600 border-t border-gray-100 dark:border-gray-800/50">
                  Buys real settled cash only (never margin). When all option slots are filled it parks the full idle amount; the 10% net-liq cap is a safety that only applies if some slots went unfilled.
                </div>
              </div>
            )
          })()}

          {/* Recovery note — CSPs already open in IBKR that a re-run skips */}
          {(screener.already_open_put_tickers ?? []).length > 0 && (
            <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl px-5 py-3 text-xs text-amber-800 dark:text-amber-300 leading-relaxed">
              ♻️ <span className="font-semibold">Recovery:</span> {screener.already_open_put_tickers.join(', ')} already
              {' '}{screener.already_open_put_tickers.length === 1 ? 'has' : 'have'} an open CSP — skipped to avoid duplicates.
              {' '}Filling {screener.target_fills} remaining slot{screener.target_fills === 1 ? '' : 's'} with available cash.
            </div>
          )}

          {/* CSP targets table */}
          {positions.length > 0 ? (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
                <div className="text-gray-900 dark:text-white font-semibold text-sm">CSP Targets</div>
                <div className="text-gray-500 dark:text-gray-600 text-xs">Budget: ${(screener.budget ?? 0).toLocaleString()}</div>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                    {['#', 'Ticker', 'Strike', 'Contracts', 'Capital', 'Premium', 'Yield', 'Buffer', 'Delta', 'Expiry'].map(h => (
                      <th key={h} className={`${h === '#' || h === 'Ticker' ? 'text-left' : 'text-right'} px-4 py-3`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p, i) => {
                    const bufPct = p.buffer_pct ?? 0
                    return (
                      <tr key={p.ticker} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                        <td className="px-4 py-3 text-gray-500 dark:text-gray-600 text-xs">{i + 1}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <span className="text-gray-900 dark:text-white font-semibold">{p.ticker}</span>
                            {p.buyzone && (
                              <span className="text-xs text-blue-400 border border-blue-800 bg-blue-900/30 px-1.5 py-0.5 rounded">bz</span>
                            )}
                          </div>
                          <div className="text-gray-500 dark:text-gray-600 text-xs">{p.sector}</div>
                        </td>
                        <td className="px-4 py-3 text-right text-gray-900 dark:text-white">${p.strike}</td>
                        <td className="px-4 py-3 text-right text-gray-900 dark:text-white">{p.contracts}</td>
                        <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">${(p.capital_used ?? 0).toLocaleString()}</td>
                        <td className="px-4 py-3 text-right text-green-400 font-medium">${(p.premium_total ?? 0).toLocaleString()}</td>
                        <td className="px-4 py-3 text-right text-green-400">{(p.yield_pct ?? 0).toFixed(2)}%</td>
                        <td className={`px-4 py-3 text-right font-medium ${
                          bufPct >= 10 ? 'text-green-400' : bufPct >= 5 ? 'text-yellow-400' : 'text-red-400'
                        }`}>
                          {bufPct.toFixed(1)}%
                        </td>
                        <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400">{p.delta?.toFixed(3)}</td>
                        <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400 text-xs">{fmtDate(p.expiry)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-10 text-center">
              <TrendingUp size={32} className="text-gray-300 dark:text-gray-700 mx-auto mb-3" />
              <div className="text-gray-500">No positions sized — screener returned 0 qualified targets</div>
            </div>
          )}

          {/* Combined CSP + CC summary — placed BELOW both the Wheel Plan and CSP
              Targets blocks so it reads as the whole-plan grand total, not CSP-only
              (mirrors the Discord plan layout). */}
          {(() => {
            const cspCap  = screener.total_capital ?? 0
            const cspPrem = screener.total_premium ?? 0
            const ccCap   = screener.wheel_cc_capital ?? 0
            const ccPrem  = screener.wheel_cc_premium ?? 0
            const hasCC   = ccPrem > 0 || ccCap > 0
            const capital = screener.combined_capital ?? (cspCap + ccCap)
            const premium = screener.combined_premium ?? (cspPrem + ccPrem)
            const yield_  = screener.combined_yield ?? 0
            const split   = (csp, cc) => `CSP $${Math.round(csp).toLocaleString()} + CC $${Math.round(cc).toLocaleString()}`
            const cp = screener.cash_park
            const cards = [
              { label: hasCC ? 'Capital Deployed (CSP + CC)' : 'Capital Deployed', value: `$${Math.round(capital).toLocaleString()}`, sub: hasCC ? split(cspCap, ccCap) : null },
              { label: hasCC ? 'Est. Premium (CSP + CC)'     : 'Est. Premium',     value: `$${Math.round(premium).toLocaleString()}`, accent: 'text-green-400', sub: hasCC ? split(cspPrem, ccPrem) : null },
              { label: 'Blended Yield',    value: `${yield_.toFixed(2)}%`,                     accent: 'text-blue-400',  sub: hasCC ? 'CSP + CC combined' : null },
            ]
            if (cp) cards.push({
              label: '🅿️ Cash Sweep Remainder',
              value: `$${Math.round(cp.remainder ?? 0).toLocaleString()}`,
              accent: 'text-purple-500 dark:text-purple-400',
              sub: cp.buy_amount != null ? `would park $${Math.round(cp.buy_amount).toLocaleString()}${cp.instrument ? ' ' + cp.instrument : ''}` : null,
            })
            return (
              <div className={`grid gap-4 ${cp ? 'grid-cols-2 md:grid-cols-4' : 'grid-cols-3'}`}>
                {cards.map(({ label, value, accent = 'text-gray-900 dark:text-white', sub }) => (
                  <div key={label} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
                    <div className="text-gray-500 text-xs mb-2">{label}</div>
                    <div className={`text-2xl font-bold ${accent}`}>{value}</div>
                    {sub && <div className="text-gray-400 dark:text-gray-600 text-[11px] mt-1">{sub}</div>}
                  </div>
                ))}
              </div>
            )
          })()}

          <div className="bg-blue-100 dark:bg-blue-950/30 border border-blue-300 dark:border-blue-900/40 rounded-xl px-5 py-3.5 text-xs text-blue-800 dark:text-blue-300/80 leading-relaxed">
            <span className="font-semibold text-blue-900 dark:text-blue-300">Dry-run preview of Monday — no orders placed.</span>
            {' '}The <span className="font-medium">Wheel Plan</span> (sells + covered calls) is computed from live IBKR option chains, so it mirrors Monday&apos;s wheel check closely.
            {' '}<span className="font-medium">CSP targets</span> are screener estimates; exact strikes/premiums settle against the live chain at execution.
            {' '}Running this again, or clicking <span className="font-medium">Run Now</span>, executes this same plan for real.
          </div>

          {runAt && (
            <div className="text-gray-400 dark:text-gray-700 text-xs text-right">
              Screener run at {runAt.toLocaleTimeString()}
            </div>
          )}
        </>
      )}

      {/* Idle state */}
      {!screener && !loading && !error && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-12 text-center">
          <TrendingUp size={40} className="text-gray-300 dark:text-gray-700 mx-auto mb-4" />
          <div className="text-gray-500 text-lg mb-2">No screener results</div>
          <div className="text-gray-500 dark:text-gray-600 text-sm mb-6">
            Click "Run Screener" to fetch and size this week's targets
          </div>
          <button
            onClick={runScreener}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            Run Screener Now
          </button>
        </div>
      )}
    </div>
  )
}
