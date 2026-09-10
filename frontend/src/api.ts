const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, init)
  if (!r.ok) {
    const txt = await r.text()
    throw new Error(`${r.status}: ${txt}`)
  }
  return r.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body: unknown) =>
    req<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    req<T>(path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  del: <T>(path: string) => req<T>(path, { method: 'DELETE' }),
  upload: async <T>(
    path: string,
    file: File,
    fields?: Record<string, string | undefined>
  ): Promise<T> => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields ?? {})) {
      if (v) fd.append(k, v)
    }
    const r = await fetch(BASE + path, { method: 'POST', body: fd })
    if (!r.ok) {
      const txt = await r.text()
      throw new Error(`${r.status}: ${txt}`)
    }
    return r.json() as Promise<T>
  },
}

export type Transaction = {
  id: number
  txn_date: string
  description: string
  amount: number
  currency: string
  source: string
  txn_type: 'debit' | 'credit' | null
  account_last4: string | null
  instrument: string | null
  txn_ref: string | null
  txn_time: string | null
  transaction_id: string | null
  source_file: string | null
  imported_at: string
  tag_id: number | null
  tag_name: string | null
  tag_color: string | null
}

export type Tag = {
  id: number
  name: string
  color: string | null
  txn_count: number
}

export type Account = {
  instrument: string
  account_last4: string | null
}

export type Rule = {
  id: number
  tag_id: number
  tag_name: string
  match_type: 'contains' | 'regex'
  pattern: string
  priority: number
}

export type DashboardSummary = {
  total_spend: number
  total_credit: number
  net: number
  by_tag: { tag: string; amount: number }[]
  recent: {
    id: number
    txn_date: string
    description: string
    amount: number
    source: string
    tag: string | null
  }[]
}

export type UploadResult = {
  parser: string | null
  parsed: number
  inserted: number
  skipped_duplicates: number
  auto_tagged?: number
  message?: string
}

export type RegisteredAccount = {
  id: number
  kind: 'bank' | 'credit_card' | 'upi'
  provider: string
  nickname: string | null
  account_last4: string | null
  label: string
  parser: string | null
  has_password: boolean
}

export type ParsedRow = {
  index: number
  txn_date: string
  description: string
  amount: string
  txn_type: string | null
  account_last4: string | null
  instrument: string | null
  txn_ref: string | null
  txn_time: string | null
  transaction_id: string | null
  duplicate: boolean
}

export type ParseResult =
  | { status: 'duplicate_file'; message: string }
  | { status: 'ok'; upload_token: string; parser: string; rows: ParsedRow[] }
