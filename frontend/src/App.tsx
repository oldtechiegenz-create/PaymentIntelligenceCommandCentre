import { Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import Dashboard from './pages/Dashboard'
import PaymentDiscovery from './pages/PaymentDiscovery'
import Payment360 from './pages/Payment360'
import Simulation from './pages/Simulation'
import Drilldown from './pages/Drilldown'
import Kuber from './pages/Kuber'
import ComingSoon from './pages/ComingSoon'
import HealthCheck from './pages/HealthCheck'
import LiveFeed from './pages/LiveFeed'

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/discovery" element={<PaymentDiscovery />} />
        <Route path="/payment-360" element={<Payment360 />} />
        <Route path="/simulation" element={<Simulation />} />
        <Route path="/drilldown" element={<Drilldown />} />
        <Route path="/kuber-agents" element={<Kuber />} />
        <Route path="/health" element={<HealthCheck />} />
        <Route path="/live" element={<LiveFeed />} />
      </Route>
    </Routes>
  )
}

export default App
