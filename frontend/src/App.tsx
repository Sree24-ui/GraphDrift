import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import AccountDetail from './pages/AccountDetail'
import AlertQueue from './pages/AlertQueue'
import LiveMonitor from './pages/LiveMonitor'
import Reports from './pages/Reports'
import Settings from './pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<LiveMonitor />} />
          <Route path="alerts" element={<AlertQueue />} />
          <Route path="accounts/:accountId" element={<AccountDetail />} />
          <Route path="reports" element={<Reports />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
