import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import RequireAuth from './auth/RequireAuth';
import Dashboard from './pages/Dashboard';
import InboxList from './pages/Inbox/InboxList';
import InboxDetail from './pages/Inbox/InboxDetail';
import AgentList from './pages/Agents/AgentList';
import AgentDetail from './pages/Agents/AgentDetail';
import InvoicesLayout from './pages/Invoices/InvoicesLayout';
import BillingRuns from './pages/Invoices/BillingRuns';
import BillingGroups from './pages/Invoices/BillingGroups';
import RunDetail from './pages/Invoices/RunDetail';
import GroupDetail from './pages/Invoices/GroupDetail';
import GroupForm from './pages/Invoices/GroupForm';
import Draws from './pages/Invoices/Draws';
import Drafted from './pages/Invoices/Drafted';
import RevenueLayout from './pages/Revenue/RevenueLayout';
import RevenueOverview from './pages/Revenue/Overview';
import RevenueRuns from './pages/Revenue/Runs';
import RevenueEntries from './pages/Revenue/Entries';
import Projects from './pages/Projects/ProjectList';
import Contracts from './pages/Contracts';
import AuditLog from './pages/AuditLog';
import ChatLayout from './pages/Chat/ChatLayout';
import LlmCalls from './pages/LlmCalls';
import SettingsLayout from './pages/Settings/SettingsLayout';
import SettingsBilling from './pages/Settings/Billing';
import SettingsExcludedClients from './pages/Settings/ExcludedClients';
import Login from './pages/Login';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route path="/" element={<Dashboard />} />
          <Route path="/inbox" element={<InboxList />} />
          <Route path="/inbox/:itemId" element={<InboxDetail />} />
          <Route path="/invoices" element={<InvoicesLayout />}>
            {/* Draws lands first: it is the only tab with work that expires.
                A redirect rather than an index element, so the tab that owns
                the screen is also the URL you are on. */}
            <Route index element={<Navigate to="draws" replace />} />
            {/* Listed in tab order; these are distinct static segments, so the
                order does not affect matching. */}
            <Route path="draws" element={<Draws />} />
            <Route path="runs" element={<BillingRuns />} />
            <Route path="drafted" element={<Drafted />} />
            <Route path="groups" element={<BillingGroups />} />
          </Route>
          <Route path="/invoices/runs/:runId" element={<RunDetail />} />
          {/* `new` must precede `:groupId` or it matches as an id. */}
          <Route path="/invoices/groups/new" element={<GroupForm />} />
          <Route path="/invoices/groups/:groupId/edit" element={<GroupForm />} />
          <Route path="/invoices/groups/:groupId" element={<GroupDetail />} />
          {/* Mockup only — every figure under here comes from
              pages/Revenue/mockData.ts. See RevenueLayout. */}
          <Route path="/revenue" element={<RevenueLayout />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<RevenueOverview />} />
            <Route path="runs" element={<RevenueRuns />} />
            <Route path="entries" element={<RevenueEntries />} />
          </Route>
          {/* Both are nav destinations ahead of their features: Projects is a
              sample-data stub, Contracts a bare PlaceholderPage. */}
          <Route path="/projects" element={<Projects />} />
          <Route path="/contracts" element={<Contracts />} />
          <Route path="/chat" element={<ChatLayout />} />
          <Route path="/chat/:agentId" element={<ChatLayout />} />
          <Route path="/chat/:agentId/:sessionId" element={<ChatLayout />} />
          {/* Pages below are parked under Settings tabs rather than the sidebar. */}
          <Route path="/settings" element={<SettingsLayout />}>
            {/* There was a "General" tab here: hardcoded integration rows that
                always read "Connected", cron schedules for five agents that no
                longer exist, and a timezone selector wired to nothing. Billing
                is the first tab that does something. */}
            <Route index element={<Navigate to="billing" replace />} />
            <Route path="billing" element={<SettingsBilling />} />
            <Route path="excluded-clients" element={<SettingsExcludedClients />} />
            <Route path="agents" element={<AgentList />} />
            <Route path="agents/:agentId" element={<AgentDetail />} />
            <Route path="audit" element={<AuditLog />} />
            <Route path="llm-calls" element={<LlmCalls />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
