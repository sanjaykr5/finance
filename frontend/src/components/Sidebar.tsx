import { NavLink } from 'react-router-dom'
import {
  CreditCard,
  LayoutDashboard,
  Receipt,
  Tags as TagsIcon,
  Upload as UploadIcon,
  Wallet,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const links = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/transactions', label: 'Transactions', icon: Receipt },
  { to: '/credit-cards', label: 'Credit Cards', icon: CreditCard },
  { to: '/upload', label: 'Upload', icon: UploadIcon },
  { to: '/tags', label: 'Tags & Rules', icon: TagsIcon },
]

export default function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r bg-card md:flex">
      <div className="flex h-14 items-center gap-2 border-b px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Wallet className="h-4 w-4" />
        </div>
        <div className="text-sm font-semibold tracking-tight">Ledger</div>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {links.map((l) => {
          const Icon = l.icon
          return (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-secondary text-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                )
              }
            >
              <Icon className="h-4 w-4" />
              {l.label}
            </NavLink>
          )
        })}
      </nav>

      <div className="border-t px-5 py-4">
        <div className="text-xs text-muted-foreground">Local · DuckDB</div>
        <div className="mt-0.5 text-xs text-muted-foreground/70">
          Data never leaves this machine.
        </div>
      </div>
    </aside>
  )
}
