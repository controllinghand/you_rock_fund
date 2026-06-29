import { useEffect, useState } from 'react'
import axios from 'axios'

const STATUS_CLASS = {
  filled:             'text-green-400',
  dry_run:            'text-green-400',
  partial_fill:       'text-yellow-400',
  failed:             'text-red-400',
  failed_qualify:     'text-red-400',
  failed_market_data: 'text-red-400',
  unfilled:           'text-red-400',
  skipped_liquidity:  'text-yellow-400',
}

function fmtDate(s) {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
  } catch { return s }
}

function fmtTime(s) {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleString('en-US', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  } catch { return s }
}

// Covered-call / wheel actions, with a human label and a color class.
const WHEEL_ACTION = {
  cc_opened:               { label: 'CC opened',         cls: 'text-green-400' },
  cc_failed:               { label: 'CC failed',         cls: 'text-red-400' },
  cc_deferred:             { label: 'CC deferred',       cls: 'text-yellow-400' },
  cc_already_open:         { label: 'CC already open',   cls: 'text-gray-500 dark:text-gray-400' },
  held_covered:            { label: 'Held (covered)',    cls: 'text-gray-500 dark:text-gray-400' },
  sold_dropped_screener:   { label: 'Sold (off screener)', cls: 'text-red-400' },
  sold_stop_loss:          { label: 'Sold (stop loss)',  cls: 'text-red-400' },
  sold_earnings_this_week: { label: 'Sold (earnings)',   cls: 'text-red-400' },
  sold_no_viable_cc:       { label: 'Sold (no CC)',      cls: 'text-red-400' },
  skipped_excluded:        { label: 'Skipped (excluded)', cls: 'text-gray-500 dark:text-gray-400' },
}

function fmtExpiry(s) {
  // wheel expiries are YYYYMMDD strings
  if (!s || s.length !== 8) return s ?? '—'
  return `${s.slice(4, 6)}/${s.slice(6, 8)}`
}

export default function TradeHistory() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    axios.get('/api/trade-history')
      .then(r => setData(r.data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  )
  if (error) return (
    <div className="bg-red-900/20 border border-red-800 rounded-xl p-6 text-red-400">{error}</div>
  )

  const { current_week = {}, weekly_summaries = [], total_premium = 0 } = data ?? {}
  const executions    = current_week.executions ?? []
  const wheelActivity = current_week.wheel_activity ?? []
  const pnl           = current_week.weekly_pnl ?? {}
  const weekStart  = current_week.run_date
    ? new Date(current_week.run_date).toISOString().slice(0, 10)
    : null

  const slippages = executions
    .filter(ex => ex.fill_price != null && ex.screener_premium != null)
    .map(ex => ex.fill_price - ex.screener_premium)

  const avgSlippage = slippages.length
    ? slippages.reduce((a, b) => a + b, 0) / slippages.length
    : null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1">Trade History</h1>
        <div className="text-gray-500 text-sm">Execution details and weekly summaries</div>
      </div>

      {/* Current week */}
      {executions.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800">
            <div className="text-gray-900 dark:text-white font-semibold text-sm">
              Current Week{weekStart && ` — ${fmtDate(weekStart)}`}
            </div>
            {avgSlippage != null && (
              <div className={`text-xs mt-0.5 ${avgSlippage >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                Avg fill vs screener: {avgSlippage >= 0 ? '+' : ''}${avgSlippage.toFixed(2)}/contract
              </div>
            )}
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                {['Ticker', 'Status', 'Contracts', 'Strike', 'Fill', 'Screener', 'Slippage', 'Premium', 'Time'].map(h => (
                  <th key={h} className={`${h === 'Ticker' || h === 'Status' ? 'text-left' : 'text-right'} px-4 py-3`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {executions.map((ex, i) => {
                const slip = ex.fill_price != null && ex.screener_premium != null
                  ? ex.fill_price - ex.screener_premium : null
                return (
                  <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-3 font-semibold text-gray-900 dark:text-white">{ex.ticker}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs capitalize ${STATUS_CLASS[ex.status] ?? 'text-gray-600 dark:text-gray-400'}`}>
                        {(ex.status ?? '').replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">{ex.contracts ?? '—'}</td>
                    <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">
                      {ex.strike != null ? `$${ex.strike}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900 dark:text-white">
                      {ex.fill_price != null ? `$${ex.fill_price.toFixed(2)}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400">
                      {ex.screener_premium != null ? `$${ex.screener_premium.toFixed(2)}` : '—'}
                    </td>
                    <td className={`px-4 py-3 text-right text-xs ${
                      slip == null ? 'text-gray-400 dark:text-gray-600'
                      : slip >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {slip != null ? `${slip >= 0 ? '+' : ''}$${slip.toFixed(2)}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-green-400 font-medium">
                      {ex.premium_collected ? `$${ex.premium_collected.toLocaleString()}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-500 dark:text-gray-600 text-xs">
                      {fmtTime(ex.exec_timestamp)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-300 dark:border-gray-700">
                <td colSpan={7} className="px-4 py-3 text-gray-500 text-xs">
                  {executions.filter(e => e.status === 'filled' || e.status === 'dry_run').length} filled
                </td>
                <td className="px-4 py-3 text-right font-bold text-gray-900 dark:text-white">
                  ${executions
                      .filter(e => e.status === 'filled' || e.status === 'dry_run' || e.status === 'partial_fill')
                      .reduce((sum, e) => sum + (e.premium_collected ?? 0), 0)
                      .toLocaleString()}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* Current-week wheel activity (covered calls + share sales) */}
      {wheelActivity.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800">
            <div className="text-gray-900 dark:text-white font-semibold text-sm">Wheel Activity</div>
            <div className="text-gray-500 text-xs mt-0.5">
              Covered calls and share sales this week
            </div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                {['Ticker', 'Action', 'Strike', 'Delta', 'Expiry', 'Shares', 'Proceeds', 'Premium / P&L'].map(h => (
                  <th key={h} className={`${h === 'Ticker' || h === 'Action' ? 'text-left' : 'text-right'} px-4 py-3`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {wheelActivity.map((a, i) => {
                const meta = WHEEL_ACTION[a.action] ?? { label: (a.action ?? '').replace(/_/g, ' '), cls: 'text-gray-600 dark:text-gray-400' }
                const isCC = a.action === 'cc_opened'
                // For CC: show premium (green). For sales: show realized P&L (signed).
                const ccPrem   = isCC && a.cc_premium != null ? a.cc_premium : null
                const realized = a.realized_pnl != null ? a.realized_pnl : null
                return (
                  <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-3 font-semibold text-gray-900 dark:text-white">{a.ticker}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs ${meta.cls}`}>{meta.label}</span>
                      {a.below_assigned && (
                        <span className="ml-1.5 text-[10px] text-yellow-400">below cost</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">
                      {a.cc_strike != null ? `$${a.cc_strike}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400 text-xs">
                      {a.cc_delta != null ? a.cc_delta.toFixed(2) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600 dark:text-gray-400 text-xs">
                      {a.cc_expiry ? fmtExpiry(a.cc_expiry) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">
                      {a.shares != null ? a.shares.toLocaleString() : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700 dark:text-gray-300">
                      {a.proceeds != null ? `$${Math.round(a.proceeds).toLocaleString()}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      {ccPrem != null ? (
                        <span className="text-green-400">${Math.round(ccPrem).toLocaleString()}</span>
                      ) : realized != null ? (
                        <span className={realized >= 0 ? 'text-green-400' : 'text-red-400'}>
                          {realized >= 0 ? '+' : '−'}${Math.abs(Math.round(realized)).toLocaleString()}
                        </span>
                      ) : (
                        <span className="text-gray-400 dark:text-gray-600">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-300 dark:border-gray-700">
                <td colSpan={7} className="px-4 py-3 text-gray-500 text-xs">
                  CC premium ${Math.round(pnl.cc_premium ?? 0).toLocaleString()}
                  {' · '}share-sale P&amp;L {(pnl.shares_sold_pnl ?? 0) >= 0 ? '+' : '−'}${Math.abs(Math.round(pnl.shares_sold_pnl ?? 0)).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right font-bold text-gray-900 dark:text-white">
                  {(() => {
                    const net = (pnl.cc_premium ?? 0) + (pnl.shares_sold_pnl ?? 0)
                    return <span className={net >= 0 ? 'text-green-400' : 'text-red-400'}>
                      {net >= 0 ? '+' : '−'}${Math.abs(Math.round(net)).toLocaleString()}
                    </span>
                  })()}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* YTD weekly summaries */}
      {weekly_summaries.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <div className="text-gray-900 dark:text-white font-semibold text-sm">All Weeks</div>
            <div className="text-gray-500 text-xs">Total: ${total_premium.toLocaleString()}</div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                <th className="text-left px-5 py-3">Week Of</th>
                <th className="text-right px-5 py-3">Premium</th>
                <th className="text-right px-5 py-3">Sales P&amp;L</th>
                <th className="text-right px-5 py-3">Realized</th>
                <th className="text-right px-5 py-3">Yield</th>
              </tr>
            </thead>
            <tbody>
              {[...weekly_summaries].reverse().map((w, i) => {
                const premium  = w.premium_collected ?? w.realized ?? 0
                const salesPnl = w.shares_sold_pnl ?? 0
                const realized = w.total_realized ?? w.realized ?? premium
                return (
                  <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                    <td className="px-5 py-3 text-gray-700 dark:text-gray-300">{fmtDate(w.week_start + 'T00:00:00')}</td>
                    <td className="px-5 py-3 text-right text-green-400 font-medium">
                      ${premium.toLocaleString()}
                    </td>
                    <td className={`px-5 py-3 text-right font-medium ${
                      salesPnl > 0 ? 'text-green-400' : salesPnl < 0 ? 'text-red-400' : 'text-gray-500 dark:text-gray-600'
                    }`}>
                      {salesPnl === 0 ? '—' : `${salesPnl >= 0 ? '+' : ''}$${salesPnl.toLocaleString()}`}
                    </td>
                    <td className={`px-5 py-3 text-right font-semibold ${
                      realized >= 0 ? 'text-gray-900 dark:text-white' : 'text-red-400'
                    }`}>
                      {realized >= 0 ? '' : '−'}${Math.abs(realized).toLocaleString()}
                    </td>
                    <td className={`px-5 py-3 text-right font-medium ${
                      (w.yield_pct ?? 0) >= 1 ? 'text-green-400'
                      : (w.yield_pct ?? 0) >= 0.5 ? 'text-yellow-400'
                      : 'text-red-400'
                    }`}>
                      {(w.yield_pct ?? 0).toFixed(3)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {executions.length === 0 && wheelActivity.length === 0 && weekly_summaries.length === 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-12 text-center">
          <div className="text-gray-500 dark:text-gray-600">No trade history yet</div>
        </div>
      )}
    </div>
  )
}
