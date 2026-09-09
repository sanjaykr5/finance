import TransactionsView from '@/components/TransactionsView'

export default function TransactionsPage() {
  return (
    <TransactionsView
      title="Transactions"
      emptyMessage="No transactions match the filters. Try uploading a statement."
    />
  )
}
