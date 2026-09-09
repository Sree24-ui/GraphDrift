import { createContext, useContext } from 'react'

import type { CurrentUser } from '../api/types'

export interface AuthContextValue {
  user: CurrentUser | null
  checking: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// Kept out of AuthContext.tsx so that file only exports a component and React
// Fast Refresh keeps working.
export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
