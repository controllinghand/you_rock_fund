import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { LayoutDashboard, TrendingUp, Calendar, History, Settings, Lock, HelpCircle } from 'lucide-react'
import StatusBar from './components/StatusBar.jsx'
import { UnsavedChangesProvider, useUnsavedChanges } from './components/UnsavedChanges.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Performance from './pages/Performance.jsx'
import ThisWeek from './pages/ThisWeek.jsx'
import TradeHistory from './pages/TradeHistory.jsx'
import SettingsPage from './pages/Settings.jsx'
import Secrets from './pages/Secrets.jsx'
import Help from './pages/Help.jsx'

const NAV = [
  { path: '/',             label: 'Dashboard',     icon: LayoutDashboard, end: true },
  { path: '/performance',  label: 'Performance',   icon: TrendingUp },
  { path: '/this-week',    label: 'This Week',     icon: Calendar },
  { path: '/trade-history',label: 'Trade History', icon: History },
  { path: '/settings',     label: 'Settings',      icon: Settings },
  { path: '/secrets',      label: 'Secrets',       icon: Lock },
]

export default function App() {
  return (
    <BrowserRouter>
      <UnsavedChangesProvider>
        <Shell />
      </UnsavedChangesProvider>
    </BrowserRouter>
  )
}

function Shell() {
  const { dirty, setDirty } = useUnsavedChanges()
  const loc = useLocation()

  // Intercept sidebar navigation while the Settings form has unsaved edits.
  // Skip when the link targets the current page (e.g. clicking Settings while
  // already editing it) so we never prompt on a no-op navigation.
  const guard = (path) => (e) => {
    if (!dirty || path === loc.pathname) return
    if (window.confirm('You have unsaved changes on the Settings page.\n\nLeave without saving?')) {
      setDirty(false)      // proceeding — drop the flag so beforeunload stays quiet
    } else {
      e.preventDefault()   // stay on the page
    }
  }

  return (
      <div className="flex h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-white overflow-hidden">
        {/* Sidebar */}
        <nav className="w-52 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col shrink-0">
          <NavLink to="/" onClick={guard('/')} className="px-5 py-4 border-b border-gray-200 dark:border-gray-800 flex items-center">
            <img src="/yrvi_logo.png" alt="YRVI" className="w-[60px]" />
          </NavLink>

          <div className="flex-1 p-2 space-y-0.5 overflow-y-auto">
            {NAV.map(({ path, label, icon: Icon, end }) => (
              <NavLink
                key={path}
                to={path}
                end={end}
                onClick={guard(path)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-200'
                  }`
                }
              >
                <Icon size={16} />
                {label}
              </NavLink>
            ))}
          </div>

          {/* Help — pinned to bottom */}
          <div className="p-2 border-t border-gray-200 dark:border-gray-800">
            <NavLink
              to="/help"
              onClick={guard('/help')}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-200'
                }`
              }
            >
              <HelpCircle size={16} />
              Help
            </NavLink>
          </div>
        </nav>

        {/* Main */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <StatusBar />
          <main className="flex-1 overflow-y-auto p-6">
            <Routes>
              <Route path="/"              element={<Dashboard />} />
              <Route path="/performance"   element={<Performance />} />
              <Route path="/this-week"     element={<ThisWeek />} />
              <Route path="/trade-history" element={<TradeHistory />} />
              <Route path="/settings"      element={<SettingsPage />} />
              <Route path="/secrets"       element={<Secrets />} />
              <Route path="/help"          element={<Help />} />
            </Routes>
          </main>
        </div>
      </div>
  )
}
