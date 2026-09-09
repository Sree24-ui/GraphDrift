import { type FormEvent, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import AppBackground from '../components/AppBackground'
import MaterialIcon from '../components/MaterialIcon'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
      const destination =
        (location.state as { from?: string } | null)?.from || '/'
      navigate(destination, { replace: true })
    } catch {
      setError('Login failed. Check your username and password.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden p-5">
      <AppBackground />
      <section className="glass-panel-elevated relative z-10 w-full max-w-md rounded-2xl p-8 shadow-glacier">
        <div className="mb-8 flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full border border-primary/30 bg-primary/15 text-primary shadow-primary-glow">
            <MaterialIcon name="shield_lock" size={25} />
          </div>
          <div>
            <h1 className="font-headline text-2xl font-semibold text-primary">GraphDrift</h1>
            <p className="text-sm text-on-surface-variant">Secure fraud operations</p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-5">
          <div>
            <label htmlFor="username" className="mb-2 block text-xs font-medium uppercase tracking-wide text-on-surface-variant">
              Username
            </label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              autoFocus
              className="w-full rounded-xl border border-primary/15 bg-surface/70 px-4 py-3 text-on-surface outline-none transition focus:border-primary/60 focus:shadow-primary-glow"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-2 block text-xs font-medium uppercase tracking-wide text-on-surface-variant">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              className="w-full rounded-xl border border-primary/15 bg-surface/70 px-4 py-3 text-on-surface outline-none transition focus:border-primary/60 focus:shadow-primary-glow"
            />
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-error/30 bg-error/10 px-3 py-2 text-sm text-error">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-primary/30 bg-primary/20 px-5 py-3 font-medium text-primary transition hover:bg-primary/30 disabled:opacity-50"
          >
            <MaterialIcon name="login" size={19} />
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}
