import { useEffect, useState } from 'react'
import { Play, Plus, Trash2, X } from 'lucide-react'
import { api, RegisteredAccount, Rule } from '@/api'
import { Button } from '@/components/ui/button'
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
import { Separator } from '@/components/ui/separator'

type NewRule = {
  match_type: 'contains' | 'regex'
  pattern: string
  priority: number
  account_id: number | null
}

const EMPTY_RULE: NewRule = {
  match_type: 'contains',
  pattern: '',
  priority: 0,
  account_id: null,
}

// The Select primitive can't carry a null value, so "all accounts" is
// represented by this sentinel string and mapped back to null on submit.
const ALL_ACCOUNTS = 'all'

export function TagRulesDialog({
  tagId,
  tagName,
  rules,
  onRulesChanged,
  onClose,
}: {
  tagId: number
  tagName: string
  rules: Rule[]
  onRulesChanged: () => void | Promise<void>
  onClose: () => void
}) {
  const [newRule, setNewRule] = useState<NewRule>(EMPTY_RULE)
  const [applyResult, setApplyResult] = useState('')
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])

  useEffect(() => {
    api.get<RegisteredAccount[]>('/accounts').then(setAccounts)
  }, [])

  // Reset the draft rule and status message whenever a different tag's
  // dialog is opened, rather than carrying over the previous tag's state.
  useEffect(() => {
    setNewRule(EMPTY_RULE)
    setApplyResult('')
  }, [tagId])

  async function addRule() {
    if (!newRule.pattern.trim()) return
    await api.post('/rules', { tag_id: tagId, ...newRule })
    setNewRule(EMPTY_RULE)
    await onRulesChanged()
  }

  async function deleteRule(id: number) {
    await api.del(`/rules/${id}`)
    await onRulesChanged()
  }

  async function applyToExisting() {
    const r = await api.post<{ updated: number }>('/rules/apply', {
      tag_id: tagId,
    })
    setApplyResult(`Tagged ${r.updated} previously untagged transactions.`)
    await onRulesChanged()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border bg-card text-card-foreground shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between p-6 pb-0">
          <div>
            <h2 className="text-base font-semibold leading-none tracking-tight">
              Rules for “{tagName}”
            </h2>
            <p className="mt-1.5 text-sm text-muted-foreground">
              Add as many contains/regex patterns as you like. A transaction
              gets this tag when any one of them matches.
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="space-y-4 p-6">
          {rules.length === 0 ? (
            <p className="rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
              No rules yet for this tag.
            </p>
          ) : (
            <ul className="max-h-64 divide-y overflow-y-auto rounded-md border">
              {rules.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-2 px-4 py-2.5 text-sm"
                >
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="text-muted-foreground">
                      {r.match_type}
                    </span>
                    <code className="break-all rounded bg-muted px-1.5 py-0.5 text-xs">
                      {r.pattern}
                    </code>
                    <span className="text-xs text-muted-foreground">
                      priority {r.priority}
                    </span>
                    {r.account_label && (
                      <Badge variant="outline">{r.account_label}</Badge>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => deleteRule(r.id)}
                    className="shrink-0 text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-12">
            <div className="sm:col-span-3">
              <Label className="text-xs text-muted-foreground">Match</Label>
              <Select
                value={newRule.match_type}
                onValueChange={(v) =>
                  setNewRule({ ...newRule, match_type: v as 'contains' | 'regex' })
                }
              >
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="contains">contains</SelectItem>
                  <SelectItem value="regex">regex</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="sm:col-span-9">
              <Label className="text-xs text-muted-foreground">Pattern</Label>
              <Input
                value={newRule.pattern}
                onChange={(e) =>
                  setNewRule({ ...newRule, pattern: e.target.value })
                }
                onKeyDown={(e) => e.key === 'Enter' && addRule()}
                placeholder="e.g. Paid to Bank Account XXXXXX3811"
                className="mt-1"
              />
            </div>
            <div className="sm:col-span-8">
              <Label className="text-xs text-muted-foreground">
                Applies to
              </Label>
              <Select
                value={newRule.account_id === null ? ALL_ACCOUNTS : String(newRule.account_id)}
                onValueChange={(v) =>
                  setNewRule({
                    ...newRule,
                    account_id: v === ALL_ACCOUNTS ? null : Number(v),
                  })
                }
              >
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
            <div className="sm:col-span-4">
              <Label className="text-xs text-muted-foreground">Priority</Label>
              <Input
                type="number"
                value={newRule.priority}
                onChange={(e) =>
                  setNewRule({ ...newRule, priority: Number(e.target.value) })
                }
                className="mt-1"
              />
            </div>
          </div>
          <Button onClick={addRule} className="w-full sm:w-auto">
            <Plus className="h-4 w-4" /> Add rule
          </Button>

          <Separator />

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="secondary" onClick={applyToExisting}>
              <Play className="h-4 w-4" /> Apply to existing
            </Button>
            {applyResult && (
              <span className="text-sm text-muted-foreground">
                {applyResult}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
