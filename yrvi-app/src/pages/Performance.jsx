import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine
} from 'recharts'
import { TrendingUp, Award, AlertTriangle, Target } from 'lucide-react'
import { useThemeContext } from '../ThemeProvider.jsx'

function fmtDate(s) {
  if (!s) return ''
  return new Date(s + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function StatCard({ label, value, sub, icon: Icon, accent = 'text-gray-900 dark:text-white' }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="text-gray-500 text-xs">{label}</div>
        {Icon && <Icon size={16} className="text-gray-300 dark:text-gray-700" />}
      </div>
      <div className={`text-2xl font-bold ${accent}`}>{value}</div>
      {sub && <div className="text-gray-500 dark:text-gray-600 text-xs mt-1">{sub}</div>}
    </div>
  )
}

export default function Performance() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const { isDark }            = useThemeContext()

  useEffect(() => {
    axios.get('/api/performance')
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

  const {
    weeks = [], total_premium = 0, total_realized = 0, weeks_traded = 0,
    avg_yield_pct = 0, best_week, worst_week,
    annual_target = 100_000, progress_pct = 0,
    capital = 0, account_target = 0, net_liq = null,
    net_growth = null, net_growth_pct = null
  } = data ?? {}

  // Fill tracks GROWTH toward the goal (capital → target), not net_liq from
  // zero — otherwise an underwater account reads as a mostly-full bar. The
  // denominator (target − capital) equals capital × goal_pct = the premium
  // goal, so both bars share the same "$X of goal" axis. Below capital → empty.
  const goalAmount   = account_target - capital
  const accountPct   = (net_liq != null && goalAmount > 0)
    ? Math.min(100, Math.max(0, (net_growth / goalAmount) * 100)) : 0
  const growthUp     = (net_growth ?? 0) >= 0
  const growthPctVal = net_growth_pct ?? (net_liq != null && capital ? (net_growth / capital) * 100 : null)

  const maxPremium = weeks.length ? Math.max(...weeks.map(w => w.premium_collected ?? 0)) : 0

  const gridColor   = isDark ? '#1f2937' : '#e5e7eb'
  const axisStroke  = isDark ? '#374151' : '#d1d5db'
  const tickColor   = isDark ? '#6b7280' : '#9ca3af'
  const tooltipBg   = isDark ? '#1f2937' : '#ffffff'
  const tooltipBdr  = isDark ? '#374151' : '#e5e7eb'
  const tooltipText = isDark ? '#ffffff'  : '#111827'
  const tooltipSub  = isDark ? '#9ca3af' : '#6b7280'

  function CustomTooltip({ active, payload }) {
    if (!active || !payload?.length) return null
    const d = payload[0].payload
    return (
      <div style={{ background: tooltipBg, border: `1px solid ${tooltipBdr}` }}
        className="rounded-lg p-3 text-sm shadow-xl">
        <div style={{ color: tooltipSub }} className="mb-1">Week of {fmtDate(d.week_start)}</div>
        <div style={{ color: tooltipText }} className="font-bold text-base">${d.premium_collected?.toLocaleString()} premium</div>
        {(d.shares_sold_pnl ?? 0) !== 0 && (
          <div style={{ color: tooltipSub }} className="text-xs">
            {(d.shares_sold_pnl ?? 0) >= 0 ? '+' : ''}${d.shares_sold_pnl?.toLocaleString()} sales P&L
          </div>
        )}
        <div className="text-green-400">{d.yield_pct?.toFixed(3)}% yield</div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1">Performance</h1>
        <div className="text-gray-500 text-sm">Year-to-date results</div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard label="Total Premium"    value={`$${total_premium.toLocaleString()}`}  icon={TrendingUp} accent="text-green-400"
          sub="option premium only" />
        <StatCard label="Total Realized"   value={`$${total_realized.toLocaleString()}`} icon={TrendingUp}
          accent={total_realized >= total_premium ? 'text-green-400' : 'text-yellow-400'}
          sub="premium + sales P&L" />
        <StatCard label="Weeks Traded"     value={weeks_traded}   sub="weeks executed"            icon={Target} />
        <StatCard
          label="Avg Weekly Yield"
          value={`${avg_yield_pct.toFixed(2)}%`}
          sub="of fund budget"
          icon={TrendingUp}
          accent={avg_yield_pct >= 1 ? 'text-green-400' : avg_yield_pct >= 0.5 ? 'text-yellow-400' : 'text-red-400'}
        />
        <StatCard label="Annual Goal" value={`${progress_pct.toFixed(1)}%`}
          sub={`$${total_premium.toLocaleString()} of $${annual_target.toLocaleString()}`}
          icon={Target} accent="text-blue-400"
        />
      </div>

      {/* Best / worst */}
      <div className="grid grid-cols-2 gap-4">
        {best_week && (
          <div className="bg-green-900/10 border border-green-800/40 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-2">
              <Award size={16} className="text-green-400" />
              <span className="text-green-400 text-xs font-medium">Best Week</span>
            </div>
            <div className="text-2xl font-bold text-gray-900 dark:text-white">${(best_week.premium_collected ?? best_week.realized)?.toLocaleString()}</div>
            <div className="text-gray-500 text-sm mt-1">
              {fmtDate(best_week.week_start)} · {best_week.yield_pct?.toFixed(2)}% yield
            </div>
          </div>
        )}
        {worst_week && best_week?.week_start !== worst_week?.week_start && (
          <div className="bg-red-900/10 border border-red-800/40 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle size={16} className="text-red-400" />
              <span className="text-red-400 text-xs font-medium">Worst Week</span>
            </div>
            <div className="text-2xl font-bold text-gray-900 dark:text-white">${(worst_week.premium_collected ?? worst_week.realized)?.toLocaleString()}</div>
            <div className="text-gray-500 text-sm mt-1">
              {fmtDate(worst_week.week_start)} · {worst_week.yield_pct?.toFixed(2)}% yield
            </div>
          </div>
        )}
      </div>

      {/* Annual goal progress — premium income + account value */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 space-y-5">
        <div className="text-gray-900 dark:text-white font-semibold text-sm">Annual Goal Progress</div>

        {/* Premium income (gross) */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="text-gray-700 dark:text-gray-300 text-xs font-medium">Premium Income</div>
            <div className="text-gray-500 text-xs">
              ${total_premium.toLocaleString()} / ${annual_target.toLocaleString()}
            </div>
          </div>
          <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-3">
            <div
              className="bg-gradient-to-r from-blue-600 to-green-500 h-3 rounded-full transition-all duration-700"
              style={{ width: `${progress_pct}%` }}
            />
          </div>
          <div className="flex justify-between mt-2 text-xs text-gray-500 dark:text-gray-600">
            <span>{progress_pct.toFixed(1)}%</span>
            <span>${(annual_target - total_premium).toLocaleString()} to go</span>
          </div>
        </div>

        {/* Account value (wealth) — Net Liq vs capital + goal */}
        {net_liq != null && account_target > 0 && (
          <div className="border-t border-gray-100 dark:border-gray-800 pt-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-gray-700 dark:text-gray-300 text-xs font-medium">Account Value</div>
              <div className="text-gray-500 text-xs">
                ${Math.round(net_liq).toLocaleString()} / ${account_target.toLocaleString()}
              </div>
            </div>
            <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all duration-700 ${growthUp ? 'bg-gradient-to-r from-blue-600 to-green-500' : 'bg-gradient-to-r from-amber-500 to-red-500'}`}
                style={{ width: `${accountPct}%` }}
              />
            </div>
            <div className="flex justify-between mt-2 text-xs">
              <span className={growthUp ? 'text-green-500' : 'text-red-500'}>
                {growthUp ? '+' : '−'}${Math.abs(Math.round(net_growth)).toLocaleString()}
                {growthPctVal != null && ` (${growthUp ? '+' : '−'}${Math.abs(growthPctVal).toFixed(1)}%)`}
                {' '}vs ${capital.toLocaleString()} capital
              </span>
              <span className="text-gray-500 dark:text-gray-600">
                ${Math.round(account_target - net_liq).toLocaleString()} to go
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Bar chart */}
      {weeks.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
          <div className="text-gray-900 dark:text-white font-semibold text-sm mb-5">Weekly Premium by Week</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={weeks} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
              <XAxis dataKey="week_start" tickFormatter={fmtDate} stroke={axisStroke}
                tick={{ fill: tickColor, fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis stroke={axisStroke} tick={{ fill: tickColor, fontSize: 11 }}
                tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} axisLine={false} tickLine={false} width={50} />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: isDark ? '#ffffff06' : '#00000004' }} />
              <Bar dataKey="premium_collected" radius={[4, 4, 0, 0]}>
                {weeks.map((w, i) => (
                  <Cell key={i} fill={w.premium_collected === maxPremium ? '#10b981' : '#2563eb'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Weekly table */}
      {weeks.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800">
            <div className="text-gray-900 dark:text-white font-semibold text-sm">Weekly Breakdown</div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-200 dark:border-gray-800">
                <th className="text-left px-5 py-3">Week Of</th>
                <th className="text-right px-5 py-3">Premium</th>
                <th className="text-right px-5 py-3">Sales P&L</th>
                <th className="text-right px-5 py-3">Yield</th>
              </tr>
            </thead>
            <tbody>
              {[...weeks].reverse().map((w, i) => {
                const salesPnl = w.shares_sold_pnl ?? 0
                return (
                  <tr key={i} className="border-b border-gray-100 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                    <td className="px-5 py-3 text-gray-700 dark:text-gray-300">{fmtDate(w.week_start)}</td>
                    <td className="px-5 py-3 text-right font-medium text-green-400">
                      ${w.premium_collected?.toLocaleString()}
                    </td>
                    <td className={`px-5 py-3 text-right font-medium ${
                      salesPnl > 0 ? 'text-green-400' : salesPnl < 0 ? 'text-red-400' : 'text-gray-500 dark:text-gray-600'
                    }`}>
                      {salesPnl === 0 ? '—' : `${salesPnl >= 0 ? '+' : ''}$${salesPnl.toLocaleString()}`}
                    </td>
                    <td className={`px-5 py-3 text-right font-medium ${
                      (w.yield_pct ?? 0) >= 1 ? 'text-green-400'
                      : (w.yield_pct ?? 0) >= 0.5 ? 'text-yellow-400'
                      : 'text-red-400'
                    }`}>
                      {w.yield_pct?.toFixed(3)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-300 dark:border-gray-700">
                <td className="px-5 py-3 text-gray-600 dark:text-gray-400 font-medium">Total</td>
                <td className="px-5 py-3 text-right font-bold text-gray-900 dark:text-white">
                  ${total_premium.toLocaleString()}
                </td>
                <td className="px-5 py-3 text-right font-bold text-gray-900 dark:text-white">
                  {total_realized !== total_premium
                    ? `${total_realized >= total_premium ? '+' : ''}$${(total_realized - total_premium).toLocaleString()}`
                    : '—'}
                </td>
                <td className="px-5 py-3 text-right font-medium text-gray-600 dark:text-gray-400">
                  {avg_yield_pct.toFixed(3)}% avg
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {weeks.length === 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-12 text-center">
          <div className="text-gray-500 dark:text-gray-600">No weekly data yet — data populates after first execution</div>
        </div>
      )}
    </div>
  )
}
