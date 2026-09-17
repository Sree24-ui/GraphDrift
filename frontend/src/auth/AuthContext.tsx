import {
  type ReactNode,
  useCallback,
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
  UNAUTHORIZED_EVENT,
} from '../api/client'
import type { CurrentUser } from '../api/types'
import { AuthContext } from './useAuth'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [checking, setChecking] = useState(true)

  const clearSession = useCallback(() => {
    clearStoredToken()
    setUser(null)
  }, [])

  useEffect(() => {
    const onUnauthorized = () => clearSession()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
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
