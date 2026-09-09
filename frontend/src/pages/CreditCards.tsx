import TransactionsView from '@/components/TransactionsView'

// Every parser whose `source` represents a credit card, as opposed to a
// bank account or a wallet/UPI app like PhonePe. Extend this list when a
// new credit-card statement parser is added (e.g. an SBI Card parser).
const CREDIT_CARD_SOURCES = ['HDFC Credit Card', 'HSBC Credit Card']

export default function CreditCardsPage() {
  return (
    <TransactionsView
      title="Credit Cards"
      emptyMessage="No credit card transactions match the filters. Try uploading a credit card statement."
      fixedSources={CREDIT_CARD_SOURCES}
    />
  )
}
