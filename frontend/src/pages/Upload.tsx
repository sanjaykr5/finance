import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  FileText,
  Loader2,
  Upload as UploadIcon,
  XCircle,
} from 'lucide-react'
import { api, UploadResult } from '@/api'
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
import { cn } from '@/lib/utils'

export default function Upload() {
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()

  async function handleUpload() {
    if (!file) return
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const r = await api.upload<UploadResult>(
        '/upload',
        file,
        password || undefined
      )
      setResult(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function clearFile() {
    setFile(null)
    setResult(null)
    setError('')
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Upload statement
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Drop a PDF or CSV. Bank statements are parsed and stored locally in
          DuckDB.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>File</CardTitle>
          <CardDescription>
            PhonePe transaction statement CSVs are auto-detected.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
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
            <div className="text-sm font-medium">
              Drop file here or click to browse
            </div>
            <div className="text-xs text-muted-foreground">
              PDF or CSV, up to ~25 MB
            </div>
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
              <Button
                variant="ghost"
                size="sm"
                onClick={clearFile}
                disabled={busy}
              >
                Remove
              </Button>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="password">PDF password (if locked)</Label>
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
            <Button onClick={handleUpload} disabled={!file || busy}>
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Uploading…
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

      {error && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>Upload failed</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">
            {error}
          </AlertDescription>
        </Alert>
      )}

      {result && (
        <Alert variant="success">
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>File processed</AlertTitle>
          <AlertDescription>
            <div className="mt-1 flex flex-wrap gap-2">
              <Badge variant="outline">
                Parser: {result.parser ?? '—'}
              </Badge>
              <Badge variant="outline">Parsed: {result.parsed}</Badge>
              <Badge variant="outline">Inserted: {result.inserted}</Badge>
              <Badge variant="outline">
                Duplicates: {result.skipped_duplicates}
              </Badge>
              {typeof result.auto_tagged === 'number' && (
                <Badge variant="outline">
                  Auto-tagged: {result.auto_tagged}
                </Badge>
              )}
            </div>
            {result.message && (
              <div className="mt-2 text-sm">{result.message}</div>
            )}
            <div className="mt-3">
              <Button
                variant="outline"
                size="sm"
                onClick={() => nav('/transactions')}
              >
                View transactions →
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
