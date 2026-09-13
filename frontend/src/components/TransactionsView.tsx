import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, Search } from 'lucide-react'
import { api, RegisteredAccount, Tag, Transaction } from '@/api'
import TagPicker from '@/components/TagPicker'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { formatCurrency, formatDate } from '@/lib/utils'

const ALL_TAGS = '__all__'
const ALL_ACCOUNTS = '__all__'
const ALL_MONTHS = '__all__'
const ALL_YEARS = '__all__'
const PAGE_SIZE = 200

const MONTHS = [
  { value: '1', label: 'January' },
  { value: '2', label: 'February' },
  { value: '3', label: 'March' },
  { value: '4', label: 'April' },
  { value: '5', label: 'May' },
  { value: '6', label: 'June' },
  { value: '7', label: 'July' },
  { value: '8', label: 'August' },
  { value: '9', label: 'September' },
  { value: '10', label: 'October' },
  { value: '11', label: 'November' },
  { value: '12', label: 'December' },
]

const CURRENT_YEAR = new Date().getFullYear()
const YEARS = Array.from({ length: 10 }, (_, i) => CURRENT_YEAR - i)

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

export type TransactionsViewProps = {
  title: string
  /** Shown under the title once data has loaded and there's nothing to show. */
  emptyMessage: string
  /**
   * Lock this view to one or more account kinds ('bank' | 'credit_card' |
   * 'upi') — always applied on top of whatever the user filters by, and
   * not exposed as a filter control itself. Omit to show every kind.
   */
  fixedKinds?: string[]
}

export default function TransactionsView({
  title,
  emptyMessage,
  fixedKinds,
}: TransactionsViewProps) {
  const [txns, setTxns] = useState<Transaction[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])
  const [q, setQ] = useState('')
  const [tagFilter, setTagFilter] = useState(ALL_TAGS)
  const [accountFilter, setAccountFilter] = useState(ALL_ACCOUNTS)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [monthFilter, setMonthFilter] = useState(ALL_MONTHS)
  const [yearFilter, setYearFilter] = useState(ALL_YEARS)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)

  function kindParams() {
    const params = new URLSearchParams()
    for (const k of fixedKinds ?? []) params.append('kind', k)
    return params
  }

  function filterParams() {
    const params = kindParams()
    if (q) params.set('q', q)
    if (tagFilter !== ALL_TAGS) params.set('tag', tagFilter)
    if (accountFilter !== ALL_ACCOUNTS) params.set('account', accountFilter)
    if (from) params.set('from', from)
    if (to) params.set('to', to)
    return params
  }

  async function load(toPage = page) {
    setLoading(true)
    try {
      const filters = filterParams()
      const rowsParams = new URLSearchParams(filters)
      rowsParams.set('limit', String(PAGE_SIZE))
      rowsParams.set('offset', String(toPage * PAGE_SIZE))

      const [rows, count] = await Promise.all([
        api.get<Transaction[]>(`/transactions?${rowsParams}`),
        api.get<{ total: number }>(`/transactions/count?${filters}`),
      ])
      setTxns(rows)
      setTotal(count.total)
      setPage(toPage)
    } finally {
      setLoading(false)
    }
  }

  /** Patch a transaction's notes/audited and reflect the change locally,
   * so editing a note or ticking "audited" doesn't require reloading the
   * whole page. */
  async function patchTxn(
    id: number,
    body: Partial<Pick<Transaction, 'notes' | 'audited'>>
  ) {
    await api.patch(`/transactions/${id}`, body)
    setTxns((prev) => prev.map((t) => (t.id === id ? { ...t, ...body } : t)))
  }

  useEffect(() => {
    api.get<Tag[]>('/tags').then(setTags)
    api
      .get<RegisteredAccount[]>(`/accounts?${kindParams()}`)
      .then(setAccounts)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auto-apply filters. The search box is debounced so it doesn't fire a
  // request on every keystroke; the rest apply immediately on change.
  useEffect(() => {
    const timer = setTimeout(() => load(0), q ? 400 : 0)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, tagFilter, accountFilter, from, to])

  // Month/Year pickers are a shortcut that fills in From/To. Picking a year
  // alone spans the whole year; picking a month uses the selected year (or
  // the current year if none is picked).
  useEffect(() => {
    if (monthFilter === ALL_MONTHS && yearFilter === ALL_YEARS) return
    const year = yearFilter === ALL_YEARS ? CURRENT_YEAR : Number(yearFilter)
    if (monthFilter === ALL_MONTHS) {
      setFrom(`${year}-01-01`)
      setTo(`${year}-12-31`)
    } else {
      const month = Number(monthFilter)
      const lastDay = new Date(year, month, 0).getDate()
      setFrom(`${year}-${pad2(month)}-01`)
      setTo(`${year}-${pad2(month)}-${pad2(lastDay)}`)
    }
  }, [monthFilter, yearFilter])

  function setCustomFrom(value: string) {
    setFrom(value)
    setMonthFilter(ALL_MONTHS)
    setYearFilter(ALL_YEARS)
  }

  function setCustomTo(value: string) {
    setTo(value)
    setMonthFilter(ALL_MONTHS)
    setYearFilter(ALL_YEARS)
  }

  const start = total === 0 ? 0 : page * PAGE_SIZE + 1
  const end = Math.min(total, page * PAGE_SIZE + txns.length)
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const canPrev = page > 0
  const canNext = (page + 1) * PAGE_SIZE < total

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {total === 0
              ? 'No transactions match the current filters.'
              : `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${total.toLocaleString()}`}
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            Filters
            {loading && (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
            <div className="md:col-span-4">
              <Label className="text-xs text-muted-foreground">Search</Label>
              <div className="relative mt-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && load(0)}
                  placeholder="Description contains…"
                  className="pl-8"
                />
              </div>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">Month</Label>
              <Select value={monthFilter} onValueChange={setMonthFilter}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_MONTHS}>Any month</SelectItem>
                  {MONTHS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">Year</Label>
              <Select value={yearFilter} onValueChange={setYearFilter}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_YEARS}>Any year</SelectItem>
                  {YEARS.map((y) => (
                    <SelectItem key={y} value={String(y)}>
                      {y}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">From</Label>
              <Input
                type="date"
                value={from}
                onChange={(e) => setCustomFrom(e.target.value)}
                className="mt-1"
              />
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">To</Label>
              <Input
                type="date"
                value={to}
                onChange={(e) => setCustomTo(e.target.value)}
                className="mt-1"
              />
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">Tag</Label>
              <Select value={tagFilter} onValueChange={setTagFilter}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_TAGS}>All tags</SelectItem>
                  {tags.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">Account</Label>
              <Select value={accountFilter} onValueChange={setAccountFilter}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_ACCOUNTS}>All accounts</SelectItem>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={String(a.id)}>
                      {a.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead>Date</TableHead>
              <TableHead>Time</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Account</TableHead>
              <TableHead>Transaction ID</TableHead>
              <TableHead>Tag</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead className="text-center">Audited</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {txns.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDate(t.txn_date)}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {t.txn_time ?? '—'}
                </TableCell>
                <TableCell className="max-w-md font-medium">
                  <span className="block truncate">{t.description}</span>
                </TableCell>
                <TableCell
                  className={
                    'whitespace-nowrap text-right font-semibold tabular-nums ' +
                    (t.amount < 0 ? 'text-destructive' : 'text-emerald-600')
                  }
                >
                  {formatCurrency(t.amount, t.currency || 'INR')}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className="font-normal">
                    {t.source}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {t.account_last4 ?? '—'}
                </TableCell>
                <TableCell
                  className="max-w-[10rem] truncate font-mono text-xs text-muted-foreground"
                  title={t.transaction_id ?? undefined}
                >
                  {t.transaction_id ?? '—'}
                </TableCell>
                <TableCell>
                  <TagPicker
                    txnId={t.id}
                    current={t.tag_id}
                    tags={tags}
                    onChange={() => load(page)}
                  />
                </TableCell>
                <TableCell className="min-w-[10rem]">
                  <Input
                    key={t.id}
                    defaultValue={t.notes ?? ''}
                    onBlur={(e) => {
                      const value = e.target.value
                      if (value !== (t.notes ?? '')) {
                        patchTxn(t.id, { notes: value || null })
                      }
                    }}
                    placeholder="Add a note…"
                    className="h-8 text-xs"
                  />
                </TableCell>
                <TableCell className="text-center">
                  <input
                    type="checkbox"
                    checked={t.audited}
                    onChange={(e) => patchTxn(t.id, { audited: e.target.checked })}
                    className="h-4 w-4 rounded border-input accent-primary"
                    aria-label="Audited"
                  />
                </TableCell>
              </TableRow>
            ))}
            {txns.length === 0 && !loading && (
              <TableRow>
                <TableCell
                  colSpan={10}
                  className="h-32 text-center text-sm text-muted-foreground"
                >
                  {emptyMessage}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>

        {total > 0 && (
          <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
            <div className="text-muted-foreground">
              Page {page + 1} of {pageCount.toLocaleString()}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => load(page - 1)}
                disabled={!canPrev || loading}
              >
                <ChevronLeft className="h-4 w-4" />
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => load(page + 1)}
                disabled={!canNext || loading}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
