import { NavLink, Outlet } from 'react-router-dom'

const navItems = [
  { to: '/', label: 'Live Monitor', end: true },
  { to: '/alerts', label: 'Alert Queue' },
  { to: '/accounts/mdeshpande@okicici', label: 'Account Detail' },
  { to: '/reports', label: 'Reports' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout() {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-48 shrink-0 border-r border-charcoal-lighter bg-charcoal-light p-4 md:block lg:w-52">
        <h1 className="mb-6 text-lg font-semibold text-teal-accent">
          GraphDrift
        </h1>
        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  'rounded px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-charcoal-lighter text-teal-accent'
                    : 'text-gray-400 hover:bg-charcoal-lighter hover:text-gray-200',
                ].join(' ')
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden p-4 lg:p-6">
        <nav className="mb-4 flex gap-1 overflow-x-auto border-b border-charcoal-lighter pb-2 md:hidden">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  'shrink-0 rounded px-2.5 py-1.5 text-xs transition-colors',
                  isActive
                    ? 'bg-charcoal-lighter text-teal-accent'
                    : 'text-gray-500 hover:text-gray-300',
                ].join(' ')
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </main>
    </div>
  )
}
