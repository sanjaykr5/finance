import { useEffect, useState } from 'react'
import {
  ArrowDownRight,
  ArrowUpRight,
  Loader2,
  TrendingUp,
} from 'lucide-react'
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import { api, DashboardSummary, Transaction } from '@/api'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { formatCurrency, formatDate } from '@/lib/utils'

const COLORS = [
  '#6366f1',
  '#14b8a6',
  '#f59e0b',
  '#ef4444',
  '#a855f7',
  '#22c55e',
  '#0ea5e9',
  '#eab308',
  '#94a3b8',
]

function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function Dashboard() {
  const [month, setMonth] = useState<string | null>(null)
  const [data, setData] = useState<DashboardSummary | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // On mount, pick the latest month that actually has data so the dashboard
  // never opens on an empty period when historic data exists.
  useEffect(() => {
    api
      .get<Transaction[]>('/transactions?limit=1')
      .then((rows) => {
        if (rows.length > 0) {
          setMonth(rows[0].txn_date.slice(0, 7))
        } else {
          setMonth(currentMonth())
        }
      })
      .catch(() => setMonth(currentMonth()))
  }, [])

  useEffect(() => {
    if (month === null) return
    setError('')
    setLoading(true)
    api
      .get<DashboardSummary>(`/dashboard/summary?month=${month}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [month])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        subtitle="Overview of spend and credit for the selected month."
        action={
          <Input
            type="month"
            value={month ?? ''}
            onChange={(e) => setMonth(e.target.value)}
            className="w-[180px]"
          />
        }
      />

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Could not load dashboard</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!data && !error ? (
        <SkeletonGrid />
      ) : (
        data && (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Kpi
                title="Spend"
                value={data.total_spend}
                tone="negative"
                icon={<ArrowDownRight className="h-4 w-4" />}
              />
              <Kpi
                title="Credits & Refunds"
                value={data.total_credit}
                tone="positive"
                icon={<ArrowUpRight className="h-4 w-4" />}
              />
              <Kpi
                title="Net"
                value={data.net}
                tone={data.net < 0 ? 'negative' : 'positive'}
                icon={<TrendingUp className="h-4 w-4" />}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
              <Card className="lg:col-span-3">
                <CardHeader>
                  <CardTitle>Spend by tag</CardTitle>
                  <CardDescription>
                    Distribution of debits across your tags this month.
                  </CardDescription>
                </CardHeader>
                <CardContent className="h-[320px]">
                  {data.by_tag.length === 0 ? (
                    <EmptyState
                      message={
                        loading ? 'Loading…' : 'No spend recorded in this period.'
                      }
                    />
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={data.by_tag}
                          dataKey="amount"
                          nameKey="tag"
                          outerRadius={110}
                          innerRadius={60}
                          paddingAngle={2}
                          stroke="hsl(var(--background))"
                          strokeWidth={2}
                        >
                          {data.by_tag.map((_, i) => (
                            <Cell key={i} fill={COLORS[i % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={{
                            background: 'hsl(var(--popover))',
                            border: '1px solid hsl(var(--border))',
                            borderRadius: 8,
                            fontSize: 12,
                          }}
                          formatter={(v: number) => formatCurrency(v)}
                        />
                        <Legend
                          verticalAlign="bottom"
                          height={36}
                          iconType="circle"
                          wrapperStyle={{ fontSize: 12 }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              <Card className="lg:col-span-2">
                <CardHeader>
                  <CardTitle>Recent activity</CardTitle>
                  <CardDescription>Last 10 transactions in scope.</CardDescription>
                </CardHeader>
                <CardContent className="p-0">
                  {data.recent.length === 0 ? (
                    <div className="px-6 pb-6">
                      <EmptyState message="No transactions yet." />
                    </div>
                  ) : (
                    <ul className="divide-y">
                      {data.recent.map((t) => (
                        <li
                          key={t.id}
                          className="flex items-center justify-between gap-3 px-6 py-3"
                        >
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium">
                              {t.description}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                              <span>{formatDate(t.txn_date)}</span>
                              {t.tag && (
                                <>
                                  <span>·</span>
                                  <Badge variant="secondary" className="px-1.5 py-0">
                                    {t.tag}
                                  </Badge>
                                </>
                              )}
                            </div>
                          </div>
                          <div
                            className={
                              t.amount < 0
                                ? 'shrink-0 text-sm font-semibold text-destructive'
                                : 'shrink-0 text-sm font-semibold text-emerald-600'
                            }
                          >
                            {formatCurrency(t.amount)}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )
      )}
    </div>
  )
}

function Kpi({
  title,
  value,
  tone,
  icon,
}: {
  title: string
  value: number
  tone: 'positive' | 'negative'
  icon: React.ReactNode
}) {
  const toneClass =
    tone === 'negative' ? 'text-destructive' : 'text-emerald-600'
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <div className={'rounded-md bg-muted p-1.5 ' + toneClass}>{icon}</div>
      </CardHeader>
      <CardContent>
        <div className={'text-2xl font-semibold tabular-nums ' + toneClass}>
          {formatCurrency(value)}
        </div>
      </CardContent>
    </Card>
  )
}

function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string
  subtitle?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && (
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {action}
    </div>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full min-h-[180px] flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-5 w-5 opacity-40" />
      {message}
    </div>
  )
}

function SkeletonGrid() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Card key={i}>
            <CardHeader className="pb-2">
              <div className="h-4 w-20 animate-pulse rounded bg-muted" />
            </CardHeader>
            <CardContent>
              <div className="h-7 w-32 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card className="h-[380px] animate-pulse lg:col-span-3" />
        <Card className="h-[380px] animate-pulse lg:col-span-2" />
      </div>
    </div>
  )
}
