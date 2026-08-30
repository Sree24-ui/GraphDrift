import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  clearStoredToken,
  getCurrentUser,
  getStoredToken,
  loginUser,
  logoutUser,
  storeToken,
} from '../api/client'
import type { CurrentUser } from '../api/types'

interface AuthContextValue {
  user: CurrentUser | null
  checking: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [checking, setChecking] = useState(true)

  const clearSession = useCallback(() => {
    clearStoredToken()
    setUser(null)
  }, [])

  useEffect(() => {
    const onUnauthorized = () => clearSession()
    window.addEventListener('graphdrift:unauthorized', onUnauthorized)
    return () => window.removeEventListener('graphdrift:unauthorized', onUnauthorized)
  }, [clearSession])

  useEffect(() => {
    let cancelled = false
    async function validate() {
      if (!getStoredToken()) {
        setChecking(false)
        return
      }
      try {
        const current = await getCurrentUser()
        if (!cancelled) setUser(current)
      } catch {
        if (!cancelled) clearSession()
      } finally {
        if (!cancelled) setChecking(false)
      }
    }
    void validate()
    return () => {
      cancelled = true
    }
  }, [clearSession])

  const login = useCallback(async (username: string, password: string) => {
    const session = await loginUser(username, password)
    storeToken(session.access_token)
    setUser(await getCurrentUser())
  }, [])

  const logout = useCallback(async () => {
    try {
      if (getStoredToken()) await logoutUser()
    } finally {
      clearSession()
    }
  }, [clearSession])

  const value = useMemo(
    () => ({ user, checking, login, logout }),
    [user, checking, login, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
