import { NavLink, Outlet } from 'react-router'

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/documents', label: 'Documents', end: false },
  { to: '/upload', label: 'Upload', end: false },
  { to: '/agent', label: 'Agent', end: false },
]

export default function Layout() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <nav className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3" aria-label="Main">
          <span className="text-lg font-semibold">📄 DocuAgent</span>
          <ul className="flex gap-4">
            {LINKS.map(({ to, label, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `rounded px-2 py-1 text-sm ${isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'}`
                  }
                >
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}
