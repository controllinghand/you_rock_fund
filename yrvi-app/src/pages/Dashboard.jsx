import { useEffect, useState, useCallback } from 'react'
import axios from 'axios'
import { Clock, DollarSign, TrendingUp, RefreshCw } from 'lucide-react'
import PositionCard from '../components/PositionCard.jsx'
import YTDChart from '../components/YTDChart.jsx'

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

function StatCard({ label, value, sub, accent = 'text-gray-900 dark:text-white' }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
      <div className="text-gray-500 text-xs mb-2">{label}</div>
      <div className={`text-2xl font-bold ${accent}`}>{value}</div>
      {sub && <div className="text-gray-500 dark:text-gray-600 text-xs mt-1">{sub}</div>}
    </div>
  )
}

// ── Portfolio helpers ─────────────────────────────────────────

function fmtExpiry(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length < 8) return yyyymmdd || '—'
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  const m = parseInt(yyyymmdd.slice(4, 6), 10) - 1
  const d = yyyymmdd.slice(6, 8)
  return `${months[m]}${d}`
}

function fmtInstrument(item) {
  if (item.secType !== 'OPT') return `${item.symbol} Stock`
  return `${item.symbol} ${fmtExpiry(item.expiry)} ${item.strike}${item.right}`
}

function fmtPnl(n) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : '−'
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function pnlColor(n) {
  if (n == null) return 'text-gray-500'
  return n >= 0 ? 'text-green-400' : 'text-red-400'
}

function fmtMktVal(n) {
  if (n == null) return '—'
  const abs = Math.round(Math.abs(n)).toLocaleString()
  return n < 0 ? `-$${abs}` : `$${abs}`
}

export default function Dashboard() {
  const [positions, setPositions]     = useState(null)
  const [status, setStatus]           = useState(null)
  const [performance, setPerformance] = useState(null)
  const [loading, setLoading]         = useState(true)
  const [lastRefresh, setLastRefresh] = useState(null)

  const fetchAll = useCallback(async () => {
    try {
      const [pos, stat, perf] = await Promise.all([
        axios.get('/api/positions'),
        axios.get('/api/status'),
        axios.get('/api/performance'),
      ])
      setPositions(pos.data)
      setStatus(stat.data)
      setPerformance(perf.data)
      setLastRefresh(new Date())
    } catch (err) {
      console.error('[Dashboard] fetch error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const t = setInterval(fetchAll, 30000)
    return () => clearInterval(t)
  }, [fetchAll])

  const toggleExclude = useCallback(async (ticker, excluded) => {
    // Optimistic update so the checkbox flips immediately; refetch reconciles.
    setPositions(prev => {
      if (!prev) return prev
      const cur = new Set((prev.excluded_tickers ?? []).map(t => t.toUpperCase()))
      if (excluded) cur.add(ticker.toUpperCase())
      else cur.delete(ticker.toUpperCase())
      return { ...prev, excluded_tickers: [...cur].sort() }
    })
    try {
      await axios.post('/api/excluded-tickers', { ticker, excluded })
    } catch (err) {
      console.error('[Dashboard] toggleExclude error:', err)
    } finally {
      fetchAll()
    }
  }, [fetchAll])

  const countdown = useCountdown(status?.next_execution)

  const execLabel = (() => {
    if (!status?.next_execution) return 'Monday 10:00 AM PST (1:00 PM ET)'
    const d = new Date(status.next_execution)
    const day = d.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'America/Los_Angeles' })
    const pst = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'America/Los_Angeles' })
    const et  = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'America/New_York' })
    return `${day} ${pst} PST (${et} ET)`
  })()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    )
  }

  const pnl         = positions?.weekly_pnl ?? {}
  const ytdTotal    = performance?.total_premium ?? 0
  const ytdTarget   = performance?.annual_target ?? 100_000
  const progressPct = Math.min(100, (ytdTotal / ytdTarget) * 100)

  const runDate = positions?.run_date
    ? new Date(positions.run_date).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
    : null

  const excludedSet = new Set((positions?.excluded_tickers ?? []).map(t => t.toUpperCase()))

  const openPositions = (positions?.positions ?? []).filter(
    p => ['filled', 'dry_run', 'partial_fill'].includes(p.status)
  )
  const failedPositions = (positions?.positions ?? []).filter(
    p => !['filled', 'dry_run', 'partial_fill'].includes(p.status)
  )

  return (
    <div className="space-y-6">
      {/* Header row: countdown + refresh */}
      <div className="flex items-start gap-4">
        <div className="flex-1 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-gray-500 text-sm mb-1">Next Execution</div>
              <div className="text-4xl font-bold text-gray-900 dark:text-white font-mono tracking-tight">{countdown}</div>
              <div className="text-gray-500 dark:text-gray-600 text-sm mt-1.5">{execLabel}</div>
            </div>
            <Clock size={52} className="text-blue-600/30" />
          </div>
        </div>

        <button
          onClick={fetchAll}
          title="Refresh"
          className="mt-2 p-3 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl text-gray-500 hover:text-gray-900 dark:hover:text-white hover:border-gray-300 dark:hover:border-gray-700 transition-colors"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {/* Live Portfolio */}
      {positions?.account_summary && (
        <div className="space-y-4">
          {/* Account summary cards */}
          <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
            {[
              { label: '💰 Net Liq',      value: positions.account_summary.net_liquidation },
              { label: '💵 Cash',          value: positions.account_summary.settled_cash },
              { label: '📈 Unrealized',    value: positions.account_summary.unrealized_pnl,   pnl: true, liveOnly: true },
              { label: '✅ Realized',      value: positions.account_summary.realized_pnl,     pnl: true, liveOnly: true },
              { label: '🛡️ Margin',        value: positions.account_summary.maintenance_margin },
              { label: '⚡ Buying Power',  value: positions.account_summary.buying_power },
            ].map(({ label, value, pnl, liveOnly }) => (
              <div key={label} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4">
                <div className="text-gray-500 text-xs mb-1.5">{label}</div>
                {liveOnly && value == null ? (
                  <div className="text-xs text-gray-400 dark:text-gray-600 italic leading-tight">Live account only</div>
                ) : (
                  <div className={`text-lg font-bold font-mono ${pnl ? pnlColor(value) : 'text-gray-900 dark:text-white'}`}>
                    {pnl ? fmtPnl(value) : (value != null ? `$${Math.round(value).toLocaleString()}` : '—')}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Live holdings table */}
          {(positions.portfolio?.length ?? 0) > 0 && (
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2">
                <div className="text-gray-900 dark:text-white font-semibold text-sm">IBKR Holdings</div>
                <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 px-2 py-0.5 rounded-full">
                  {positions.portfolio.length}
                </span>
                <span className="text-gray-400 dark:text-gray-600 text-xs ml-auto">live market prices</span>
              </div>
              <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[900px]">
                <thead>
                  <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                    {['Instrument', 'Position', 'Market Value', 'Avg Price', 'Price', 'Unrealized P&L', 'Entry δ', 'Entry IV', 'Buffer %', 'Prem/Contract', 'Total Premium'].map(h => (
                      <th key={h} className={`${h === 'Instrument' ? 'text-left' : 'text-right'} px-4 py-3`}>{h}</th>
                    ))}
                    <th className="text-center px-4 py-3" title="Exclude from the wheel — no CSPs, no covered calls, never sold by the app">Exclude</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.portfolio.map((item, i) => (
                    <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                      <td className="px-4 py-3 text-gray-900 dark:text-white font-medium">{fmtInstrument(item)}</td>
                      <td className={`px-4 py-3 text-right font-mono font-semibold ${item.position > 0 ? 'text-green-400' : item.position < 0 ? 'text-orange-400' : 'text-gray-500'}`}>
                        {item.position > 0 ? `+${item.position}` : item.position}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300 font-mono">
                        {fmtMktVal(item.marketValue)}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400 font-mono">
                        {item.avgCost != null ? `$${item.avgCost.toFixed(2)}` : '—'}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300 font-mono">
                        {item.marketPrice != null ? `$${item.marketPrice.toFixed(2)}` : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className={`px-4 py-3 text-right font-mono font-semibold ${pnlColor(item.unrealizedPNL)}`}>
                        {fmtPnl(item.unrealizedPNL)}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-gray-400 dark:text-gray-500">
                        {item.delta_at_entry != null ? item.delta_at_entry.toFixed(2) : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-gray-400 dark:text-gray-500">
                        {item.iv_at_entry != null ? `${(item.iv_at_entry * 100).toFixed(1)}%` : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-gray-400 dark:text-gray-500">
                        {item.buffer_pct_at_entry != null ? `${item.buffer_pct_at_entry.toFixed(1)}%` : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-gray-400 dark:text-gray-500">
                        {item.premium_per_contract != null ? `$${item.premium_per_contract.toFixed(2)}` : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-gray-400 dark:text-gray-500">
                        {item.total_premium != null ? `$${Math.round(item.total_premium).toLocaleString()}` : <span className="text-gray-300 dark:text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {item.secType === 'STK' ? (
                          <input
                            type="checkbox"
                            className="h-4 w-4 cursor-pointer accent-blue-600"
                            checked={excludedSet.has((item.symbol || '').toUpperCase())}
                            onChange={e => toggleExclude(item.symbol, e.target.checked)}
                            title={`Exclude ${item.symbol} from the wheel`}
                          />
                        ) : (
                          <span className="text-gray-300 dark:text-gray-600">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* This week P&L */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="CSP Premium"    value={`$${(pnl.csp_premium ?? 0).toLocaleString()}`}    accent="text-green-400" />
        <StatCard label="CC Premium"     value={`$${(pnl.cc_premium ?? 0).toLocaleString()}`}     accent="text-blue-400" />
        <StatCard label="Total Realized" value={`$${(pnl.total_realized ?? 0).toLocaleString()}`} />
        <StatCard
          label="YTD Premium"
          value={`$${ytdTotal.toLocaleString()}`}
          sub={`${progressPct.toFixed(1)}% of $${ytdTarget.toLocaleString()} goal`}
        />
      </div>

      {/* YTD progress bar */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="text-gray-900 dark:text-white font-semibold text-sm">Annual Goal Progress</div>
          <div className="text-gray-500 text-xs">
            ${ytdTotal.toLocaleString()} / ${ytdTarget.toLocaleString()}
          </div>
        </div>
        <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2.5">
          <div
            className="bg-gradient-to-r from-blue-600 to-green-500 h-2.5 rounded-full transition-all duration-700"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <div className="flex justify-between mt-2 text-xs text-gray-500 dark:text-gray-600">
          <span>{progressPct.toFixed(1)}%</span>
          <span>${(ytdTarget - ytdTotal).toLocaleString()} to go</span>
        </div>
      </div>

      {/* YTD chart */}
      {(performance?.weeks?.length ?? 0) > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
          <div className="text-gray-900 dark:text-white font-semibold text-sm mb-4">Weekly Premium</div>
          <YTDChart weeks={performance.weeks} />
        </div>
      )}

      {/* Open positions */}
      {openPositions.length > 0 && (
        <div>
          <div className="flex items-center gap-3 mb-3">
            <h2 className="text-gray-900 dark:text-white font-semibold">Open Positions</h2>
            {runDate && <span className="text-gray-500 dark:text-gray-600 text-xs">week of {runDate}</span>}
            <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 px-2 py-0.5 rounded-full">
              {openPositions.length}
            </span>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {openPositions.map(p => <PositionCard key={p.ticker} position={p} />)}
          </div>
        </div>
      )}

      {/* Wheel holdings — shown before failed/skipped */}
      {(positions?.wheel_holdings?.filter(h => h.shares > 0).length ?? 0) > 0 && (
        <div>
          <div className="flex items-center gap-3 mb-3">
            <h2 className="text-gray-900 dark:text-white font-semibold">🔄 Wheel Holdings</h2>
            <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 px-2 py-0.5 rounded-full">
              {positions.wheel_holdings.filter(h => h.shares > 0).length}
            </span>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {positions.wheel_holdings.filter(h => h.shares > 0).map(h => {
              const upnl = h.current_price != null
                ? (h.current_price - h.assigned_strike) * h.shares
                : null
              return (
                <div key={h.ticker} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <div className="text-xl font-bold text-gray-900 dark:text-white">{h.ticker}</div>
                      <div className="text-gray-500 text-sm">
                        {h.shares} shares @ ${h.assigned_strike} assigned
                        {h.current_price != null && (
                          <span className="ml-2 text-gray-400 dark:text-gray-600">· now ${h.current_price}</span>
                        )}
                      </div>
                    </div>
                    <span className={`text-xs px-2.5 py-1 rounded-full border font-medium capitalize ${
                      h.cc_status === 'open'
                        ? 'bg-green-900/40 text-green-400 border-green-800'
                        : h.cc_status === 'pending'
                        ? 'bg-yellow-900/40 text-yellow-400 border-yellow-800'
                        : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700'
                    }`}>
                      CC: {h.cc_status ?? '—'}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-sm">
                    {[
                      { label: 'CC Strike',       value: h.current_cc_strike ? `$${h.current_cc_strike}` : '—' },
                      { label: 'CC Premium',      value: h.current_cc_premium ? `$${h.current_cc_premium.toLocaleString()}` : '—', accent: 'text-green-400' },
                      { label: 'CC Expiry',       value: h.current_cc_expiry ?? '—' },
                      {
                        label: 'Unrealized P&L',
                        value: upnl != null
                          ? `${upnl >= 0 ? '+' : ''}$${Math.round(Math.abs(upnl)).toLocaleString()}`
                          : '—',
                        accent: upnl == null ? 'text-gray-900 dark:text-white'
                               : upnl >= 0   ? 'text-green-400'
                               :               'text-red-400',
                      },
                      { label: 'Stop Loss',       value: h.assigned_strike ? `$${(h.assigned_strike * 0.9).toFixed(2)}` : '—', accent: 'text-red-400' },
                      { label: 'Week #',          value: h.weeks_held ?? 1 },
                    ].map(({ label, value, accent = 'text-gray-900 dark:text-white' }) => (
                      <div key={label}>
                        <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">{label}</div>
                        <div className={`font-semibold ${accent}`}>{value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Failed/skipped */}
      {failedPositions.length > 0 && (
        <div>
          <h2 className="text-gray-500 font-semibold text-sm mb-3">Failed / Skipped</h2>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {failedPositions.map(p => <PositionCard key={p.ticker} position={p} />)}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!positions?.positions?.length && !positions?.wheel_holdings?.length && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-16 text-center">
          <DollarSign size={40} className="text-gray-300 dark:text-gray-700 mx-auto mb-3" />
          <div className="text-gray-500 text-lg">No open positions this week</div>
          <div className="text-gray-400 dark:text-gray-700 text-sm mt-1">Positions appear after Monday 10AM execution</div>
        </div>
      )}

      {lastRefresh && (
        <div className="text-gray-400 dark:text-gray-700 text-xs text-right">
          Last updated {lastRefresh.toLocaleTimeString()} · auto-refreshes every 30s
        </div>
      )}
    </div>
  )
}
