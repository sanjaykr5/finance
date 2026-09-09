import { Navigate, Route, Routes } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import Dashboard from '@/pages/Dashboard'
import Upload from '@/pages/Upload'
import Transactions from '@/pages/Transactions'
import CreditCards from '@/pages/CreditCards'
import Tags from '@/pages/Tags'

export default function App() {
  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl px-6 py-8 lg:px-10">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/credit-cards" element={<CreditCards />} />
            <Route path="/tags" element={<Tags />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
