import { useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { api, RegisteredAccount } from '@/api'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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

const KINDS = [
  { value: 'bank', label: 'Bank account' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'upi', label: 'UPI app' },
] as const

type Kind = (typeof KINDS)[number]['value']

// Which providers can be picked for each account kind.
const PROVIDERS_BY_KIND: Record<Kind, readonly string[]> = {
  bank: ['SBI', 'HDFC', 'AXIS', 'HSBC'],
  credit_card: ['SBI', 'HDFC', 'AXIS', 'HSBC'],
  upi: ['PhonePe', 'Google Pay'],
}

type FormState = {
  kind: Kind
  provider: string
  password: string
  clearPassword: boolean
}

const EMPTY_FORM: FormState = {
  kind: 'bank',
  provider: PROVIDERS_BY_KIND.bank[0],
  password: '',
  clearPassword: false,
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setAccounts(await api.get<RegisteredAccount[]>('/accounts'))
  }

  useEffect(() => {
    load()
  }, [])

  function startAdd() {
    setForm(EMPTY_FORM)
    setEditingId(null)
    setError('')
    setShowForm(true)
  }

  function startEdit(a: RegisteredAccount) {
    setForm({
      kind: a.kind,
      provider: a.provider,
      password: '',
      clearPassword: false,
    })
    setEditingId(a.id)
    setError('')
    setShowForm(true)
  }

  function cancelForm() {
    setShowForm(false)
    setEditingId(null)
  }

  async function submit() {
    setError('')
    try {
      if (editingId === null) {
        await api.post('/accounts', {
          kind: form.kind,
          provider: form.provider,
          password: form.password || null,
        })
      } else {
        await api.patch(`/accounts/${editingId}`, {
          ...(form.clearPassword
            ? { password: '' }
            : form.password
              ? { password: form.password }
              : {}),
        })
      }
      setShowForm(false)
      setEditingId(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove(a: RegisteredAccount) {
    if (!confirm(`Delete "${a.label}" and all of its transactions?`)) return
    await api.del(`/accounts/${a.id}`)
    load()
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Accounts</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Register the banks, credit cards, and UPI apps you'll upload
            statements for.
          </p>
        </div>
        {!showForm && (
          <Button onClick={startAdd}>
            <Plus className="h-4 w-4" /> Add account
          </Button>
        )}
      </div>

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle>{editingId === null ? 'Add account' : 'Edit account'}</CardTitle>
            <CardDescription>
              {editingId === null
                ? "Kind and provider can't be changed later — delete and re-add if you get them wrong."
                : 'Kind and provider are fixed for an existing account.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <Label className="text-xs text-muted-foreground">Kind</Label>
                {editingId === null ? (
                  <Select
                    value={form.kind}
                    onValueChange={(v) => {
                      const kind = v as Kind
                      setForm({ ...form, kind, provider: PROVIDERS_BY_KIND[kind][0] })
                    }}
                  >
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {KINDS.map((k) => (
                        <SelectItem key={k.value} value={k.value}>
                          {k.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    className="mt-1"
                    value={KINDS.find((k) => k.value === form.kind)?.label ?? form.kind}
                    disabled
                  />
                )}
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Provider</Label>
                {editingId === null ? (
                  <Select
                    value={form.provider}
                    onValueChange={(v) => setForm({ ...form, provider: v })}
                  >
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PROVIDERS_BY_KIND[form.kind].map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input className="mt-1" value={form.provider} disabled />
                )}
              </div>
              <div className="md:col-span-2">
                <Label className="text-xs text-muted-foreground">
                  Statement password (optional)
                </Label>
                <Input
                  className="mt-1"
                  type="password"
                  autoComplete="off"
                  value={form.password}
                  disabled={form.clearPassword}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder={
                    editingId === null
                      ? 'optional'
                      : '•••••• (leave blank to keep the saved password)'
                  }
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Only needed if the statement PDF is locked. Stored
                  encrypted, never shown again.
                </p>
                {editingId !== null && (
                  <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={form.clearPassword}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          clearPassword: e.target.checked,
                          password: '',
                        })
                      }
                    />
                    Clear saved password
                  </label>
                )}
              </div>
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button onClick={submit}>
                {editingId === null ? 'Add account' : 'Save changes'}
              </Button>
              <Button variant="outline" onClick={cancelForm}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead>Kind</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Parser</TableHead>
              <TableHead>Password</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((a) => (
              <TableRow key={a.id}>
                <TableCell>
                  <Badge variant="outline" className="font-normal">
                    {a.kind}
                  </Badge>
                </TableCell>
                <TableCell className="font-medium">{a.provider}</TableCell>
                <TableCell className="text-muted-foreground">
                  {a.parser ?? 'Not configured'}
                </TableCell>
                <TableCell>
                  {a.has_password ? (
                    <Badge variant="success">Saved</Badge>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => startEdit(a)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(a)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {accounts.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="h-32 text-center text-sm text-muted-foreground">
                  No accounts yet — add one above.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  )
}
