import TransactionsView from '@/components/TransactionsView'

export default function CreditCardsPage() {
  return (
    <TransactionsView
      title="Credit Cards"
      emptyMessage="No credit card transactions match the filters. Try uploading a credit card statement."
      fixedKinds={['credit_card']}
    />
  )
}
