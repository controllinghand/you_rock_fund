// Wheel (covered-call) card — deliberately the same shape as PositionCard: same
// 3×3 stat grid, same fill footer, same capital line. A CSP and the CC written
// against the shares it assigned are the two halves of one trade, so reading one
// should not mean learning a second layout.
//
// Everything here about the CC's entry (delta, IV, entry price, fill, quote)
// comes from the API's trade_log enrichment (`cc_*` fields), keyed on the CC's
// own strike + expiry — so when the call expires or the name is re-assigned the
// numbers go blank instead of showing the previous cycle's.

// '20260925' → 'Sep 25'. IBKR's compact expiry is not a Date-parseable string.
function fmtExpiry(raw) {
  if (!raw) return null
  const s = String(raw)
  const iso = /^\d{8}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : s
  const d = new Date(`${iso}T00:00:00Z`)
  return isNaN(d) ? s : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

const STATUS_CLASS = {
  open:    'bg-green-100 text-green-700 border-green-300 dark:bg-green-900/40 dark:text-green-400 dark:border-green-800',
  partial: 'bg-orange-100 text-orange-800 border-orange-300 dark:bg-orange-900/40 dark:text-orange-400 dark:border-orange-800',
  pending: 'bg-yellow-100 text-yellow-800 border-yellow-300 dark:bg-yellow-900/40 dark:text-yellow-400 dark:border-yellow-800',
}

export default function WheelCard({ holding: h }) {
  // `||`, not `??`: a zero net_cost is missing data, not a $0 basis — and it
  // has to fall through the same way the API's cc_yield_pct does.
  const basis   = h.net_cost || h.assigned_strike || 0
  const covered = h.cc_contracts
  const needed  = h.cc_contracts_needed ?? Math.floor((h.shares ?? 0) / 100)
  const hasCC   = h.current_cc_strike != null

  const upnl = h.current_price != null
    ? (h.current_price - h.assigned_strike) * h.shares
    : null

  // Fill vs the mid the CC was quoted at when it was picked — the covered call's
  // equivalent of the CSP card's "vs screener".
  const slip = (h.cc_fill_price != null && h.cc_ref_mid != null)
    ? (h.cc_fill_price - h.cc_ref_mid).toFixed(2)
    : null

  const buf      = h.cc_buffer_pct
  const bufColor = buf == null ? 'text-gray-900 dark:text-white'
    : buf >= 10 ? 'text-green-600 dark:text-green-400'
    : buf >= 5  ? 'text-yellow-600 dark:text-yellow-400'
    :             'text-red-600 dark:text-red-400'

  const entryPrice = h.cc_stock_price_at_entry ?? h.current_price
  const belowCost  = hasCC && basis > 0 && h.current_cc_strike < basis

  const badgeClass = STATUS_CLASS[h.cc_status]
    || 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700'

  const stats = [
    { label: 'CC Strike', value: hasCC ? `$${h.current_cc_strike}` : '—' },
    {
      label: 'Contracts',
      value: covered == null ? '—' : (needed && covered < needed ? `${covered}/${needed}` : covered),
      className: (needed && covered != null && covered < needed)
        ? 'text-orange-600 dark:text-orange-400' : undefined,
      title: `${covered ?? 0} of ${needed} contracts covering ${h.shares} shares`,
    },
    {
      label: 'Buffer',
      value: buf != null ? `${buf.toFixed(1)}%` : '—',
      className: bufColor,
      title: 'Headroom above the stock before the shares are called away — (CC strike − price) ÷ price',
    },
    {
      label: 'Price',
      value: entryPrice != null ? `$${entryPrice.toFixed(2)}` : '—',
      title: h.cc_stock_price_at_entry != null ? 'Stock price when the CC was written' : 'Latest stock price',
    },
    {
      label: 'Act. Yield',
      value: h.cc_yield_pct != null ? `${h.cc_yield_pct.toFixed(2)}%` : '—',
      className: h.cc_yield_pct != null ? 'text-green-600 dark:text-green-400' : undefined,
      title: 'CC premium per contract against the cost basis of the shares it covers',
    },
    { label: 'Entry δ',  value: h.cc_delta_at_entry != null ? h.cc_delta_at_entry.toFixed(3) : '—' },
    { label: 'Entry IV', value: h.cc_iv_at_entry != null ? `${(h.cc_iv_at_entry * 100).toFixed(1)}%` : '—' },
    {
      label: 'Unrealized P&L',
      // Signed both ways — the old card printed a loss as a bare "$3,118" and
      // left the colour to carry the minus.
      value: upnl != null ? `${upnl >= 0 ? '+' : '−'}$${Math.round(Math.abs(upnl)).toLocaleString()}` : '—',
      className: upnl == null ? 'text-gray-900 dark:text-white'
        : upnl >= 0 ? 'text-green-400' : 'text-red-400',
    },
    {
      label: 'Stop Loss',
      value: basis ? `$${(basis * 0.9).toFixed(2)}` : '—',
      className: basis ? 'text-red-400' : undefined,
    },
  ]

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xl font-bold text-gray-900 dark:text-white">{h.ticker}</span>
            {belowCost && (
              <span
                className="text-xs bg-amber-100 text-amber-700 border border-amber-300 dark:bg-amber-900/40 dark:text-amber-400 dark:border-amber-800 px-2 py-0.5 rounded-full"
                title={`CC strike $${h.current_cc_strike} is below the $${basis} cost basis — kept the shares and the premium rather than force-selling`}
              >
                below cost
              </span>
            )}
          </div>
          <div className="text-gray-500 text-sm">
            {h.shares} shares @ ${h.assigned_strike} avg cost
            {(h.tranches?.length ?? 0) > 1 && (
              <span className="ml-1 text-gray-400 dark:text-gray-600">({h.tranches.length} tranches)</span>
            )}
            {h.current_price != null && (
              <span className="ml-2 text-gray-400 dark:text-gray-600">· now ${h.current_price}</span>
            )}
            <span className="ml-2 text-gray-400 dark:text-gray-600">· week {h.weeks_held ?? 1}</span>
          </div>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-full border font-medium capitalize ${badgeClass}`}>
          CC: {h.cc_status ?? '—'}
          {h.cc_status === 'partial' && needed ? ` ${covered ?? 0}/${needed}` : ''}
        </span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-x-4 gap-y-3 text-sm">
        {stats.map(({ label, value, className = 'text-gray-900 dark:text-white', title }) => (
          <div key={label} title={title}>
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">{label}</div>
            <div className={`font-semibold ${className}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Fill details — only once a CC is actually on the books */}
      {hasCC && (h.cc_fill_price != null || h.current_cc_premium) && (
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-800 flex justify-between items-end">
          <div>
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">Fill</div>
            <div className="text-gray-900 dark:text-white font-semibold">
              {h.cc_fill_price != null ? `$${h.cc_fill_price.toFixed(2)}` : '—'}
              {slip != null && (
                <span
                  className={`ml-2 text-xs ${parseFloat(slip) >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}
                  title="Fill against the quoted mid this CC was priced off"
                >
                  ({parseFloat(slip) >= 0 ? '+' : ''}{slip} vs quote)
                </span>
              )}
            </div>
            {h.cc_order_type && (
              <div className="text-gray-500 dark:text-gray-600 text-xs mt-0.5">via {h.cc_order_type.replace(/_/g, ' ')}</div>
            )}
          </div>
          <div className="text-right">
            <div className="text-gray-500 dark:text-gray-600 text-xs mb-0.5">Collected</div>
            <div className="text-green-400 font-bold text-xl">
              ${Math.round(h.current_cc_premium ?? 0).toLocaleString()}
            </div>
          </div>
        </div>
      )}

      {/* Capital tied up in the shares */}
      <div className="mt-3 text-xs text-gray-400 dark:text-gray-700">
        ${Math.round(h.cc_capital_held ?? (h.shares ?? 0) * basis).toLocaleString()} held
        {h.current_cc_expiry && ` · exp ${fmtExpiry(h.current_cc_expiry)}`}
      </div>
    </div>
  )
}
