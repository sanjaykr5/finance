import { useState } from 'react'
import { api, Tag } from '@/api'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const NONE = '__none__'

export default function TagPicker({
  txnId,
  current,
  tags,
  onChange,
}: {
  txnId: number
  current: number | null
  tags: Tag[]
  onChange?: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function handle(v: string) {
    setBusy(true)
    try {
      await api.patch(`/transactions/${txnId}`, {
        tag_id: v === NONE ? null : Number(v),
      })
      onChange?.()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Select
      value={current == null ? NONE : String(current)}
      onValueChange={handle}
      disabled={busy}
    >
      <SelectTrigger className="h-8 w-[140px] text-xs">
        <SelectValue placeholder="Untagged" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>
          <span className="text-muted-foreground">Untagged</span>
        </SelectItem>
        {tags.map((t) => (
          <SelectItem key={t.id} value={String(t.id)}>
            {t.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
