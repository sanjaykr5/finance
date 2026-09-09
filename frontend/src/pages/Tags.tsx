import { useEffect, useState } from 'react'
import { Play, Plus, Trash2 } from 'lucide-react'
import { api, Rule, Tag } from '@/api'
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
import { Separator } from '@/components/ui/separator'

type NewRule = {
  tag_id: number
  match_type: 'contains' | 'regex'
  pattern: string
  priority: number
}

export default function TagsPage() {
  const [tags, setTags] = useState<Tag[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [newName, setNewName] = useState('')
  const [newRule, setNewRule] = useState<NewRule>({
    tag_id: 0,
    match_type: 'contains',
    pattern: '',
    priority: 0,
  })
  const [applyResult, setApplyResult] = useState('')

  async function load() {
    const [t, r] = await Promise.all([
      api.get<Tag[]>('/tags'),
      api.get<Rule[]>('/rules'),
    ])
    setTags(t)
    setRules(r)
  }

  useEffect(() => {
    load()
  }, [])

  async function createTag() {
    if (!newName.trim()) return
    await api.post('/tags', { name: newName.trim() })
    setNewName('')
    load()
  }

  async function deleteTag(id: number) {
    if (!confirm('Delete tag, its rules, and all assignments?')) return
    await api.del(`/tags/${id}`)
    load()
  }

  async function addRule() {
    if (!newRule.tag_id || !newRule.pattern.trim()) return
    await api.post('/rules', newRule)
    setNewRule({
      tag_id: 0,
      match_type: 'contains',
      pattern: '',
      priority: 0,
    })
    load()
  }

  async function deleteRule(id: number) {
    await api.del(`/rules/${id}`)
    load()
  }

  async function applyRules() {
    const r = await api.post<{ updated: number }>('/rules/apply', {})
    setApplyResult(`Tagged ${r.updated} previously untagged transactions.`)
    load()
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tags & Rules</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Define tags and the patterns that auto-assign them to incoming
          transactions.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tags</CardTitle>
          <CardDescription>
            Each transaction can carry one tag. Counts reflect current
            assignments.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && createTag()}
              placeholder="New tag name (e.g. groceries)"
            />
            <Button onClick={createTag}>
              <Plus className="h-4 w-4" /> Create
            </Button>
          </div>

          {tags.length === 0 ? (
            <p className="rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
              No tags yet — create one above.
            </p>
          ) : (
            <ul className="divide-y rounded-md border">
              {tags.map((t) => (
                <li
                  key={t.id}
                  className="flex items-center justify-between px-4 py-2.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{t.name}</span>
                    <Badge variant="secondary">{t.txn_count} txns</Badge>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => deleteTag(t.id)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                    Delete
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Auto-tag rules</CardTitle>
          <CardDescription>
            Higher priority wins ties. "Apply to existing" backfills rules onto
            untagged transactions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
            <div className="md:col-span-3">
              <Label className="text-xs text-muted-foreground">Tag</Label>
              <Select
                value={newRule.tag_id ? String(newRule.tag_id) : ''}
                onValueChange={(v) =>
                  setNewRule({ ...newRule, tag_id: Number(v) })
                }
              >
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="Select tag…" />
                </SelectTrigger>
                <SelectContent>
                  {tags.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label className="text-xs text-muted-foreground">Match</Label>
              <Select
                value={newRule.match_type}
                onValueChange={(v) =>
                  setNewRule({
                    ...newRule,
                    match_type: v as 'contains' | 'regex',
                  })
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
            <div className="md:col-span-4">
              <Label className="text-xs text-muted-foreground">Pattern</Label>
              <Input
                value={newRule.pattern}
                onChange={(e) =>
                  setNewRule({ ...newRule, pattern: e.target.value })
                }
                placeholder="e.g. SWIGGY"
                className="mt-1"
              />
            </div>
            <div className="md:col-span-1">
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
            <div className="flex items-end md:col-span-2">
              <Button onClick={addRule} className="w-full">
                <Plus className="h-4 w-4" /> Add rule
              </Button>
            </div>
          </div>

          {rules.length === 0 ? (
            <p className="rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
              No rules yet.
            </p>
          ) : (
            <ul className="divide-y rounded-md border">
              {rules.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between px-4 py-2.5 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge>{r.tag_name}</Badge>
                    <span className="text-muted-foreground">
                      ← {r.match_type}
                    </span>
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                      {r.pattern}
                    </code>
                    <span className="text-xs text-muted-foreground">
                      priority {r.priority}
                    </span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => deleteRule(r.id)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <Separator />

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="secondary" onClick={applyRules}>
              <Play className="h-4 w-4" /> Apply rules to existing
            </Button>
            {applyResult && (
              <span className="text-sm text-muted-foreground">
                {applyResult}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
