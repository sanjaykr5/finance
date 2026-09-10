import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, Search } from 'lucide-react'
import { Account, api, Tag, Transaction } from '@/api'
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
const PAGE_SIZE = 200

function accountLabel(a: Account) {
  return a.account_last4 ? `•• ${a.account_last4}` : a.instrument
}

export type TransactionsViewProps = {
  title: string
  /** Shown under the title once data has loaded and there's nothing to show. */
  emptyMessage: string
  /**
   * Lock this view to one or more sources (e.g. the credit-card parsers) —
   * always applied on top of whatever the user filters by, and not
   * exposed as a filter control itself. Omit to show every source.
   */
  fixedSources?: string[]
}

export default function TransactionsView({
  title,
  emptyMessage,
  fixedSources,
}: TransactionsViewProps) {
  const [txns, setTxns] = useState<Transaction[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [q, setQ] = useState('')
  const [tagFilter, setTagFilter] = useState(ALL_TAGS)
  const [accountFilter, setAccountFilter] = useState(ALL_ACCOUNTS)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)

  function sourceParams() {
    const params = new URLSearchParams()
    for (const s of fixedSources ?? []) params.append('source', s)
    return params
  }

  function filterParams() {
    const params = sourceParams()
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

  function applyFilters() {
    load(0)
  }

  useEffect(() => {
    load(0)
    api.get<Tag[]>('/tags').then(setTags)
    api
      .get<Account[]>(`/transactions/accounts?${sourceParams()}`)
      .then(setAccounts)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
          <CardTitle className="text-sm">Filters</CardTitle>
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
                  onKeyDown={(e) => e.key === 'Enter' && applyFilters()}
                  placeholder="Description contains…"
                  className="pl-8"
                />
              </div>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">From</Label>
              <Input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="mt-1"
              />
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">To</Label>
              <Input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
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
                    <SelectItem key={a.instrument} value={a.instrument}>
                      {accountLabel(a)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end gap-2 md:col-span-2">
              <Button onClick={applyFilters} disabled={loading} className="w-full">
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  'Apply'
                )}
              </Button>
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
              </TableRow>
            ))}
            {txns.length === 0 && !loading && (
              <TableRow>
                <TableCell
                  colSpan={8}
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
