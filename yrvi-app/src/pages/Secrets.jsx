import { useEffect, useState, useCallback } from 'react'
import axios from 'axios'
import {
  Lock,
  CheckCircle,
  AlertTriangle,
  XCircle,
  RefreshCw,
  Eye,
  EyeOff,
  Save,
  X as XIcon,
} from 'lucide-react'

const LABELS = {
  account_paper:               { label: 'IBKR Paper Account ID',        required: true  },
  tws_userid_paper:            { label: 'IBKR Paper Username',          required: true  },
  tws_password_paper:          { label: 'IBKR Paper Trading Password',  required: true  },
  tws_password_live:           { label: 'IBKR Live Trading Password',   required: false },
  render_secret:               { label: 'Render Screener API Secret',   required: true  },
  account_live:                { label: 'IBKR Live Account ID',         required: false },
  tws_userid_live:             { label: 'IBKR Live Username',           required: false },
  vnc_server_password:         { label: 'VNC Password',                 required: false, description: 'Default: ibgateway123!test — used to connect via VNC to the IB Gateway container (port 5900)' },
  discord_webhook_url:          { label: 'Discord Webhook URL',          required: false, description: 'All Discord notifications — weekly plan, execution results, assignments, and alerts — go to this channel.' },
  discord_feedback_webhook_url: { label: 'Discord Feedback Webhook URL', required: false, description: 'Powers the bug/feature feedback form on the Help page. Pre-configured by default — only set this to override which Discord channel feedback goes to.' },
  flex_token:    { label: 'IBKR Flex Token',    required: false, description: 'Flex Web Service token from IBKR Portal → Performance & Reports → Flex Queries. Required for "Fetch from IBKR" in the Reconciler.' },
  flex_query_id: { label: 'IBKR Flex Query ID', required: false, description: 'Numeric query ID of your Activity Flex Query (Executions sub-type, XML format). Required for "Fetch from IBKR" in the Reconciler.' },
}

function deriveSecrets(statusSecrets) {
  if (!statusSecrets) return []
  return Object.keys(statusSecrets).map(name => {
    const meta = LABELS[name] || { label: name, required: false }
    return { name, label: meta.label, required: meta.required, description: meta.description }
  })
}

function StatusBanner({ status, loading, secrets }) {
  if (loading && !status) {
    return (
      <div className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 text-sm text-gray-500">
        Loading status…
      </div>
    )
  }
  if (status?.error) {
    return (
      <div className="rounded-xl border border-red-300 dark:border-red-900/60 bg-red-50 dark:bg-red-950/40 p-4 flex items-center gap-3">
        <XCircle className="text-red-500 shrink-0" size={20} />
        <div className="text-sm font-medium text-red-700 dark:text-red-300">
          Secrets container unreachable
        </div>
      </div>
    )
  }
  if (status?.complete) {
    const anyOptionalMissing = secrets.some(
      s => !s.required && status.secrets?.[s.name] !== 'set',
    )
    const text = anyOptionalMissing
      ? 'All required secrets configured (some optional secrets not set)'
      : 'All required secrets configured'
    return (
      <div className="rounded-xl border border-green-300 dark:border-green-900/60 bg-green-50 dark:bg-green-950/30 p-4 flex items-center gap-3">
        <CheckCircle className="text-green-500 shrink-0" size={20} />
        <div className="text-sm font-medium text-green-700 dark:text-green-300">
          {text}
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-yellow-300 dark:border-yellow-900/60 bg-yellow-50 dark:bg-yellow-950/30 p-4 flex items-center gap-3">
      <AlertTriangle className="text-yellow-500 shrink-0" size={20} />
      <div className="text-sm font-medium text-yellow-700 dark:text-yellow-300">
        Setup incomplete — required secrets missing
      </div>
    </div>
  )
}

function SecretRow({ secret, state, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const isSet = state === 'set'

  function startEdit() {
    setValue('')
    setError('')
    setShow(false)
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setValue('')
    setError('')
  }

  async function save() {
    setError('')
    if (!value && secret.required) {
      setError('Value cannot be empty')
      return
    }
    setSaving(true)
    try {
      await axios.post(`/api/secrets/${secret.name}`, { value })
      setEditing(false)
      setValue('')
      onSaved()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-t border-gray-200 dark:border-gray-800 first:border-t-0 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
              {secret.label}
            </div>
            {secret.required ? (
              <span className="text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300">
                required
              </span>
            ) : (
              <span className="text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500">
                optional
              </span>
            )}
          </div>
          <div className="text-xs text-gray-500 mt-0.5 font-mono">{secret.name}</div>
          {secret.description && (
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{secret.description}</div>
          )}
        </div>

        <div className="text-sm">
          {isSet ? (
            <span className="text-green-600 dark:text-green-400 inline-flex items-center gap-1">
              <CheckCircle size={14} /> Configured
            </span>
          ) : secret.required ? (
            <span className="text-yellow-600 dark:text-yellow-400 inline-flex items-center gap-1">
              <AlertTriangle size={14} /> Missing
            </span>
          ) : (
            <span className="text-gray-500 inline-flex items-center gap-1">
              <AlertTriangle size={14} /> Missing
            </span>
          )}
        </div>

        {!editing && (
          <button
            type="button"
            onClick={startEdit}
            className="text-xs px-3 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {isSet ? 'Update' : 'Set'}
          </button>
        )}
      </div>

      {editing && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <input
                type={show ? 'text' : 'password'}
                value={value}
                onChange={e => setValue(e.target.value)}
                placeholder="Enter value"
                className="w-full bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-md text-sm px-3 py-2 pr-10 text-gray-900 dark:text-white font-mono focus:outline-none focus:border-blue-500"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShow(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                title={show ? 'Hide' : 'Show'}
              >
                {show ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="text-xs px-3 py-2 rounded-md bg-blue-600 hover:bg-blue-700 text-white font-medium inline-flex items-center gap-1 disabled:opacity-50"
            >
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              disabled={saving}
              className="text-xs px-3 py-2 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 inline-flex items-center gap-1 disabled:opacity-50"
            >
              <XIcon size={14} /> Cancel
            </button>
          </div>
          {error && (
            <div className="text-xs text-red-600 dark:text-red-400">{error}</div>
          )}
        </div>
      )}
    </div>
  )
}

function SecretGroup({ title, secrets, status, onSaved }) {
  if (secrets.length === 0) return null
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5">
      <div className="text-gray-900 dark:text-white font-semibold text-sm mb-2">
        {title}
      </div>
      <div>
        {secrets.map(secret => (
          <SecretRow
            key={secret.name}
            secret={secret}
            state={status?.secrets?.[secret.name]}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  )
}

export default function Secrets() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const r = await axios.get('/api/secrets/status')
      setStatus(r.data)
    } catch (e) {
      setStatus({ complete: false, error: 'secrets container unreachable', secrets: {} })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const secrets  = deriveSecrets(status?.secrets)
  const required = secrets.filter(s => s.required)
  const optional = secrets.filter(s => !s.required)

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-1 flex items-center gap-2">
            <Lock size={20} /> Secrets Manager
          </h1>
          <div className="text-gray-500 text-sm">Manage encrypted credentials for YRVI</div>
        </div>
        <button
          type="button"
          onClick={load}
          className="text-xs px-3 py-2 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 inline-flex items-center gap-1.5"
          title="Refresh status"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <StatusBanner status={status} loading={loading} secrets={secrets} />

      <SecretGroup title="Required secrets" secrets={required} status={status} onSaved={load} />
      <SecretGroup title="Optional secrets" secrets={optional} status={status} onSaved={load} />
    </div>
  )
}
