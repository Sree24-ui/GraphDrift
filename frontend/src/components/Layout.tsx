import { NavLink, Outlet } from 'react-router-dom'

import AppBackground from './AppBackground'
import MaterialIcon from './MaterialIcon'

const navItems = [
  { to: '/', label: 'Live Monitor', icon: 'monitoring', end: true },
  { to: '/alerts', label: 'Alert Queue', icon: 'warning' },
  { to: '/reports', label: 'Reports', icon: 'assessment' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

export default function Layout() {
  return (
    <div className="relative flex min-h-screen overflow-hidden">
      <AppBackground />

      <aside className="fixed left-0 top-0 z-50 hidden h-full w-64 flex-col rounded-r-xl border-r border-primary/10 bg-surface/60 py-6 shadow-glacier backdrop-blur-xl md:flex">
        <div className="mb-8 flex items-center gap-3 px-6">
          <div className="flex h-10 w-10 items-center justify-center rounded-full border border-primary/30 bg-primary/20 text-primary shadow-primary-glow">
            <MaterialIcon name="hub" size={20} />
          </div>
          <div>
            <h1 className="font-headline text-xl font-semibold tracking-tight text-primary">
              GraphDrift
            </h1>
            <p className="text-xs font-medium uppercase tracking-wider text-on-surface-variant">
              Fraud Detection
            </p>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-1 font-body text-sm font-medium tracking-wide">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  'nav-link',
                  isActive ? 'nav-link-active' : 'nav-link-inactive',
                ].join(' ')
              }
            >
              {({ isActive }) => (
                <>
                  <MaterialIcon name={item.icon} filled={isActive} size={20} />
                  <span>{item.label}</span>
                  {isActive && (
                    <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary shadow-primary-glow" />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto px-6">
          <div className="glass-panel flex items-center gap-3 rounded-xl p-3 transition-colors hover:bg-primary/5">
            <div className="flex h-10 w-10 items-center justify-center rounded-full border border-primary/20 bg-gradient-to-br from-primary/20 to-tertiary/10 text-primary">
              <MaterialIcon name="shield_person" size={20} />
            </div>
            <div>
              <p className="text-sm font-semibold text-on-surface">Analyst</p>
              <p className="text-xs text-on-surface-variant">Demo session</p>
            </div>
          </div>
        </div>
      </aside>

      <main className="relative z-10 flex min-h-screen min-w-0 flex-1 flex-col md:ml-64">
        <header className="sticky top-0 z-40 flex items-center justify-between border-b border-primary/10 bg-surface/60 px-4 py-3 backdrop-blur-lg md:hidden">
          <div className="flex items-center gap-2">
            <MaterialIcon name="hub" className="text-primary" size={22} />
            <span className="font-headline text-lg font-bold text-primary">
              GraphDrift
            </span>
          </div>
          <div className="flex items-center gap-2 text-on-surface-variant">
            <button
              type="button"
              className="rounded-full p-2 transition-colors hover:bg-surface-bright/50 hover:text-on-surface"
              aria-label="Notifications"
            >
              <MaterialIcon name="notifications" size={20} />
            </button>
            <button
              type="button"
              className="rounded-full p-2 transition-colors hover:bg-surface-bright/50 hover:text-on-surface"
              aria-label="Help"
            >
              <MaterialIcon name="help" size={20} />
            </button>
          </div>
        </header>

        <nav className="flex gap-1 overflow-x-auto border-b border-primary/10 px-3 py-2 md:hidden">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  'flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors',
                  isActive
                    ? 'bg-primary/10 text-primary shadow-[0_0_15px_rgba(125,211,252,0.1)]'
                    : 'text-on-surface-variant hover:text-on-surface',
                ].join(' ')
              }
            >
              <MaterialIcon name={item.icon} size={16} />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="min-h-0 flex-1 overflow-hidden p-4 md:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
