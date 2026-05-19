import { useEffect, useState } from 'react'
import axios from 'axios'
import { Download } from 'lucide-react'

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

function exportCSV(executions, weekStart) {
  const rows = executions.map(ex => ({
    Week:             weekStart ?? '',
    Ticker:           ex.ticker ?? '',
    Status:           ex.status ?? '',
    Contracts:        ex.contracts ?? '',
    Strike:           ex.strike ?? '',
    FillPrice:        ex.fill_price ?? '',
    ScreenerPrice:    ex.screener_premium ?? '',
    Slippage:         ex.fill_price != null && ex.screener_premium != null
                        ? (ex.fill_price - ex.screener_premium).toFixed(2) : '',
    PremiumCollected: ex.premium_collected ?? '',
    OrderType:        ex.order_type ?? '',
    Timestamp:        ex.exec_timestamp ?? '',
  }))
  const header = Object.keys(rows[0]).join(',')
  const lines  = rows.map(r => Object.values(r).map(v => `"${v}"`).join(','))
  const csv    = [header, ...lines].join('\n')
  const blob   = new Blob([csv], { type: 'text/csv' })
  const url    = URL.createObjectURL(blob)
  const a      = document.createElement('a')
  a.href       = url
  a.download   = `yrvi_trades_${weekStart ?? 'export'}.csv`
  a.click()
  URL.revokeObjectURL(url)
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
  const executions = current_week.executions ?? []
  const pnl        = current_week.weekly_pnl ?? {}
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
          <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <div>
              <div className="text-gray-900 dark:text-white font-semibold text-sm">
                Current Week{weekStart && ` — ${fmtDate(weekStart)}`}
              </div>
              {avgSlippage != null && (
                <div className={`text-xs mt-0.5 ${avgSlippage >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  Avg fill vs screener: {avgSlippage >= 0 ? '+' : ''}${avgSlippage.toFixed(2)}/contract
                </div>
              )}
            </div>
            <button
              onClick={() => exportCSV(executions, weekStart)}
              className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 px-3 py-1.5 rounded-lg transition-colors"
            >
              <Download size={12} />
              Export CSV
            </button>
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
                  ${(pnl.csp_premium ?? 0).toLocaleString()}
                </td>
                <td />
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
                <th className="text-right px-5 py-3">Realized</th>
                <th className="text-right px-5 py-3">Yield</th>
              </tr>
            </thead>
            <tbody>
              {[...weekly_summaries].reverse().map((w, i) => (
                <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                  <td className="px-5 py-3 text-gray-700 dark:text-gray-300">{fmtDate(w.week_start + 'T00:00:00')}</td>
                  <td className="px-5 py-3 text-right text-green-400 font-medium">
                    ${(w.realized ?? 0).toLocaleString()}
                  </td>
                  <td className={`px-5 py-3 text-right font-medium ${
                    (w.yield_pct ?? 0) >= 1 ? 'text-green-400'
                    : (w.yield_pct ?? 0) >= 0.5 ? 'text-yellow-400'
                    : 'text-red-400'
                  }`}>
                    {(w.yield_pct ?? 0).toFixed(3)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {executions.length === 0 && weekly_summaries.length === 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-12 text-center">
          <div className="text-gray-500 dark:text-gray-600">No trade history yet</div>
        </div>
      )}
    </div>
  )
}
