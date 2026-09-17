import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import LoadingSpinner from './components/LoadingSpinner'
import Login from './pages/Login'

// Every authenticated page is split out so the first load only ships the
// shell and the login form. Charts (recharts) and the force graph load with
// the page that needs them.
const LiveMonitor = lazy(() => import('./pages/LiveMonitor'))
const AlertQueue = lazy(() => import('./pages/AlertQueue'))
const AccountDetail = lazy(() => import('./pages/AccountDetail'))
const Reports = lazy(() => import('./pages/Reports'))
const Settings = lazy(() => import('./pages/Settings'))

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
