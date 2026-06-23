const SUCCESS = new Set(['filled', 'dry_run', 'partial_fill'])
const FAILED  = new Set(['failed', 'failed_qualify', 'failed_market_data', 'unfilled'])

export default function PositionCard({ position: p }) {
  const ok   = SUCCESS.has(p.status)
  const fail = FAILED.has(p.status)
  const skip = p.status === 'skipped_liquidity'

  const borderClass = ok   ? 'border-green-300 bg-green-50 dark:border-green-800/60 dark:bg-green-900/10'
    : fail ? 'border-red-300 bg-red-50 dark:border-red-800/60 dark:bg-red-900/10'
    : skip ? 'border-yellow-300 bg-yellow-50 dark:border-yellow-800/60 dark:bg-yellow-900/10'
    : 'border-gray-200 dark:border-gray-800'

  const badgeClass = ok   ? 'bg-green-100 text-green-700 border-green-300 dark:bg-green-900/50 dark:text-green-400 dark:border-green-800'
    : fail ? 'bg-red-100 text-red-700 border-red-300 dark:bg-red-900/50 dark:text-red-400 dark:border-red-800'
    : skip ? 'bg-yellow-100 text-yellow-700 border-yellow-300 dark:bg-yellow-900/50 dark:text-yellow-400 dark:border-yellow-800'
    : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700'

  const slip = p.fill_price != null && p.premium != null
    ? (p.fill_price - p.premium).toFixed(2)
    : null

  const displayYield  = ok && p.fill_yield_pct != null ? p.fill_yield_pct : p.yield_pct
  const yieldLabel    = ok && p.fill_yield_pct != null ? 'Act. Yield' : 'Yield'
  const displayPrice  = p.stock_price_at_entry ?? p.latest_price
  const displayBuf    = p.buffer_pct_at_entry  ?? p.buffer_pct
  const bufPctDisplay = typeof displayBuf === 'number' ? displayBuf : 0
  const bufColor2     = bufPctDisplay >= 10 ? 'text-green-600 dark:text-green-400' : bufPctDisplay >= 5 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400'

  const statusLabel = (p.status || 'unknown').replace(/_/g, ' ')

  return (
    <div className={`border rounded-xl p-5 ${borderClass}`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xl font-bold text-gray-900 dark:text-white">{p.ticker}</span>
            {p.buyzone && (
              <span className="text-xs bg-blue-100 text-blue-700 border border-blue-300 dark:bg-blue-900/50 dark:text-blue-400 dark:border-blue-800 px-2 py-0.5 rounded-full">
                buyzone
              </span>
            )}
          </div>
          <div className="text-gray-500 text-sm">{p.sector || '—'}</div>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-full border font-medium capitalize ${badgeClass}`}>
          {statusLabel}
        </span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-x-4 gap-y-3 text-sm">
        {[
          { label: 'Strike',    value: `$${p.strike}` },
          { label: 'Contracts', value: p.contracts },
          { label: 'Buffer',    value: `${bufPctDisplay.toFixed(1)}%`, className: bufColor2 },
          { label: 'Price',     value: displayPrice != null ? `$${displayPrice.toFixed(2)}` : '—' },
          { label: yieldLabel,  value: `${displayYield?.toFixed(2) ?? '—'}%`, className: 'text-green-600 dark:text-green-400' },
          { label: 'Entry δ',   value: p.delta_at_entry?.toFixed(3) ?? p.delta?.toFixed(3) ?? '—' },
          { label: 'Entry IV',  value: (p.iv_at_entry ?? p.iv_atm) != null ? `${((p.iv_at_entry ?? p.iv_atm) * 100).toFixed(1)}%` : '—' },
        ].map(({ label, value, className = 'text-gray-900 dark:text-white' }) => (
          <div key={label}>
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">{label}</div>
            <div className={`font-semibold ${className}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Fill details (only for filled orders) */}
      {ok && (
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-800 flex justify-between items-end">
          <div>
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">Fill</div>
            <div className="text-gray-900 dark:text-white font-semibold">
              ${p.fill_price?.toFixed(2) ?? '—'}
              {slip != null && (
                <span className={`ml-2 text-xs ${parseFloat(slip) >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                  ({parseFloat(slip) >= 0 ? '+' : ''}{slip} vs screener)
                </span>
              )}
            </div>
            {p.order_type && (
              <div className="text-gray-500 dark:text-gray-600 text-xs mt-0.5">via {p.order_type.replace(/_/g, ' ')}</div>
            )}
          </div>
          <div className="text-right">
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">Collected</div>
            <div className="text-green-400 font-bold text-xl">
              ${(p.premium_collected ?? 0).toLocaleString()}
            </div>
          </div>
        </div>
      )}

      {/* Capital deployed */}
      <div className="mt-3 text-xs text-gray-400 dark:text-gray-700">
        ${(p.capital_used ?? 0).toLocaleString()} deployed
        {p.expiry && ` · exp ${new Date(p.expiry).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })}`}
      </div>
    </div>
  )
}
