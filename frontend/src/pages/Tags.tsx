import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
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
import { Badge } from '@/components/ui/badge'
import { TagRulesDialog } from '@/components/TagRulesDialog'

export default function TagsPage() {
  const [tags, setTags] = useState<Tag[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [newName, setNewName] = useState('')
  const [openTagId, setOpenTagId] = useState<number | null>(null)

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
    const created = await api.post<Tag>('/tags', { name: newName.trim() })
    setNewName('')
    await load()
    // Jump straight into adding rules for the tag just created.
    setOpenTagId(created.id)
  }

  async function deleteTag(id: number) {
    if (!confirm('Delete tag, its rules, and all assignments?')) return
    await api.del(`/tags/${id}`)
    if (openTagId === id) setOpenTagId(null)
    load()
  }

  const openTag = tags.find((t) => t.id === openTagId)

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tags & Rules</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Define tags, then click one to add the patterns that auto-assign it
          to incoming transactions.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tags</CardTitle>
          <CardDescription>
            Each transaction can carry one tag. Click a tag to manage its
            auto-tag rules. Counts reflect current assignments.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && createTag()}
              placeholder="New tag name (e.g. rent)"
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
              {tags.map((t) => {
                const ruleCount = rules.filter((r) => r.tag_id === t.id).length
                return (
                  <li key={t.id} className="flex items-center justify-between">
                    <button
                      type="button"
                      onClick={() => setOpenTagId(t.id)}
                      className="flex flex-1 items-center gap-2 px-4 py-2.5 text-left hover:bg-accent"
                    >
                      <span className="text-sm font-medium">{t.name}</span>
                      <Badge variant="secondary">{t.txn_count} txns</Badge>
                      <span className="text-xs text-muted-foreground">
                        {ruleCount === 0
                          ? 'no rules yet'
                          : `${ruleCount} rule${ruleCount === 1 ? '' : 's'}`}
                      </span>
                    </button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteTag(t.id)}
                      className="mr-2 text-destructive hover:text-destructive"
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete
                    </Button>
                  </li>
                )
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {openTag && (
        <TagRulesDialog
          tagId={openTag.id}
          tagName={openTag.name}
          rules={rules.filter((r) => r.tag_id === openTag.id)}
          onRulesChanged={load}
          onClose={() => setOpenTagId(null)}
        />
      )}
    </div>
  )
}
