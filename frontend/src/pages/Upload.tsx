import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  FileText,
  Loader2,
  Upload as UploadIcon,
  XCircle,
} from 'lucide-react'
import {
  api,
  ParsedRow,
  ParseResult,
  RegisteredAccount,
  UploadResult,
} from '@/api'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
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
import { cn, formatCurrency, formatDate } from '@/lib/utils'

type Stage = 'form' | 'previewing' | 'done'
type PreviewRow = ParsedRow & { selected: boolean }

export default function Upload() {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [duplicateMessage, setDuplicateMessage] = useState('')

  const [stage, setStage] = useState<Stage>('form')
  const [uploadToken, setUploadToken] = useState<string | null>(null)
  const [parserLabel, setParserLabel] = useState('')
  const [rows, setRows] = useState<PreviewRow[]>([])
  const [result, setResult] = useState<UploadResult | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()

  useEffect(() => {
    api.get<RegisteredAccount[]>('/accounts').then(setAccounts)
  }, [])

  async function handleParse() {
    if (!file || !accountId) return
    setBusy(true)
    setError('')
    setDuplicateMessage('')
    try {
      const r = await api.upload<ParseResult>('/upload/parse', file, {
        account_id: accountId,
        password: password || undefined,
      })
      if (r.status === 'duplicate_file') {
        setDuplicateMessage(r.message)
        return
      }
      setUploadToken(r.upload_token)
      setParserLabel(r.parser)
      setRows(r.rows.map((row) => ({ ...row, selected: !row.duplicate })))
      setStage('previewing')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function toggleRow(index: number) {
    setRows((rs) =>
      rs.map((r) => (r.index === index ? { ...r, selected: !r.selected } : r))
    )
  }

  async function handleConfirm() {
    if (!uploadToken) return
    setBusy(true)
    setError('')
    try {
      const selected_indices = rows.filter((r) => r.selected).map((r) => r.index)
      const r = await api.post<UploadResult>('/upload/confirm', {
        upload_token: uploadToken,
        selected_indices,
      })
      setResult(r)
      setStage('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    if (uploadToken) {
      api.del(`/upload/parse/${uploadToken}`).catch(() => {})
    }
    resetToForm()
  }

  function resetToForm() {
    setStage('form')
    setUploadToken(null)
    setRows([])
    setFile(null)
    setPassword('')
    setResult(null)
    setDuplicateMessage('')
    if (inputRef.current) inputRef.current.value = ''
  }

  const selectedCount = rows.filter((r) => r.selected).length
  const duplicateCount = rows.filter((r) => r.duplicate).length

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload statement</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Parse a statement, review the transactions, then confirm to store
          them.
        </p>
      </div>

      {stage === 'form' && (
        <Card>
          <CardHeader>
            <CardTitle>File</CardTitle>
            <CardDescription>
              PDF or CSV, matched against the account you pick below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div>
              <Label className="text-xs text-muted-foreground">Account</Label>
              {accounts.length === 0 ? (
                <p className="mt-1 text-sm text-muted-foreground">
                  No accounts yet —{' '}
                  <a href="/accounts" className="underline">
                    add one first
                  </a>
                  .
                </p>
              ) : (
                <Select value={accountId} onValueChange={setAccountId}>
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="Select account…" />
                  </SelectTrigger>
                  <SelectContent>
                    {accounts.map((a) => (
                      <SelectItem key={a.id} value={String(a.id)}>
                        {a.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                const f = e.dataTransfer.files?.[0]
                if (f) setFile(f)
              }}
              onClick={() => inputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors',
                dragOver
                  ? 'border-primary bg-accent'
                  : 'border-border hover:border-primary/40 hover:bg-accent/40'
              )}
            >
              <div className="rounded-full bg-secondary p-3 text-secondary-foreground">
                <UploadIcon className="h-5 w-5" />
              </div>
              <div className="text-sm font-medium">Drop file here or click to browse</div>
              <div className="text-xs text-muted-foreground">PDF or CSV, up to ~25 MB</div>
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.csv"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="hidden"
              />
            </div>

            {file && (
              <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-3 py-2 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate font-medium">{file.name}</span>
                  <Badge variant="outline" className="shrink-0">
                    {Math.round(file.size / 1024)} KB
                  </Badge>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setFile(null)} disabled={busy}>
                  Remove
                </Button>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="password">Password (only if different from the saved one)</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="optional"
                autoComplete="off"
              />
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={handleParse} disabled={!file || !accountId || busy}>
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Parsing…
                  </>
                ) : (
                  <>
                    <UploadIcon className="h-4 w-4" /> Upload & parse
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {duplicateMessage && (
        <Alert>
          <AlertTitle>Already uploaded</AlertTitle>
          <AlertDescription>{duplicateMessage}</AlertDescription>
        </Alert>
      )}

      {stage === 'previewing' && (
        <Card>
          <CardHeader>
            <CardTitle>Review parsed transactions</CardTitle>
            <CardDescription>
              Parser: {parserLabel} · {rows.length} parsed ·{' '}
              {rows.length - duplicateCount} new · {duplicateCount} duplicate
              {duplicateCount === 1 ? '' : 's'}. Uncheck anything you don't
              want to import.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead />
                    <TableHead>Date</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Instrument</TableHead>
                    <TableHead>Ref</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.index}>
                      <TableCell>
                        <input
                          type="checkbox"
                          role="checkbox"
                          checked={r.selected}
                          onChange={() => toggleRow(r.index)}
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDate(r.txn_date)}
                      </TableCell>
                      <TableCell className="max-w-xs truncate font-medium">
                        {r.description}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'whitespace-nowrap text-right font-semibold tabular-nums',
                          Number(r.amount) < 0 ? 'text-destructive' : 'text-emerald-600'
                        )}
                      >
                        {formatCurrency(Number(r.amount), 'INR')}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {r.txn_type ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {r.instrument ?? r.account_last4 ?? '—'}
                      </TableCell>
                      <TableCell className="max-w-[10rem] truncate font-mono text-xs text-muted-foreground">
                        {r.txn_ref ?? '—'}
                      </TableCell>
                      <TableCell>
                        {r.duplicate && <Badge variant="secondary">Duplicate</Badge>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex items-center gap-3">
              <Button onClick={handleConfirm} disabled={selectedCount === 0 || busy}>
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Confirming…
                  </>
                ) : (
                  `Confirm & import ${selectedCount}`
                )}
              </Button>
              <Button variant="outline" onClick={handleCancel} disabled={busy}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {error && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>Upload failed</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
        </Alert>
      )}

      {stage === 'done' && result && (
        <Alert variant="success">
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>File processed</AlertTitle>
          <AlertDescription>
            <div className="mt-1 flex flex-wrap gap-2">
              <Badge variant="outline">Parser: {result.parser ?? '—'}</Badge>
              <Badge variant="outline">Parsed: {result.parsed}</Badge>
              <Badge variant="outline">Inserted: {result.inserted}</Badge>
              <Badge variant="outline">Duplicates: {result.skipped_duplicates}</Badge>
              {typeof result.auto_tagged === 'number' && (
                <Badge variant="outline">Auto-tagged: {result.auto_tagged}</Badge>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <Button variant="outline" size="sm" onClick={() => nav('/transactions')}>
                View transactions →
              </Button>
              <Button variant="outline" size="sm" onClick={resetToForm}>
                Upload another
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
