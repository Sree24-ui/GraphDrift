import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AuthProvider, useAuth } from './auth/AuthContext'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import LoadingSpinner from './components/LoadingSpinner'
import AccountDetail from './pages/AccountDetail'
import AlertQueue from './pages/AlertQueue'
import Login from './pages/Login'
import Reports from './pages/Reports'
import Settings from './pages/Settings'

const LiveMonitor = lazy(() => import('./pages/LiveMonitor'))

function ProtectedLayout() {
  const { user, checking } = useAuth()
  const location = useLocation()
  if (checking) {
    return <LoadingSpinner label="Validating secure session…" className="min-h-screen" />
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Layout />
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Suspense fallback={<LoadingSpinner label="Loading workspace…" className="min-h-screen" />}>
            <Routes>
              <Route path="login" element={<Login />} />
              <Route element={<ProtectedLayout />}>
                <Route index element={<LiveMonitor />} />
                <Route path="alerts" element={<AlertQueue />} />
                <Route path="accounts/:accountId" element={<AccountDetail />} />
                <Route path="reports" element={<Reports />} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </Suspense>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
