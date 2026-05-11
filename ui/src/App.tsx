import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import Dashboard from './pages/Dashboard';
import InboxList from './pages/Inbox/InboxList';
import InboxDetail from './pages/Inbox/InboxDetail';
import AgentList from './pages/Agents/AgentList';
import AgentDetail from './pages/Agents/AgentDetail';
import AuditLog from './pages/AuditLog';
import ChatLayout from './pages/Chat/ChatLayout';
import KnowledgeBase from './pages/KnowledgeBase';
import Analytics from './pages/Analytics';
import LlmCalls from './pages/LlmCalls';
import Settings from './pages/Settings';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inbox" element={<InboxList />} />
          <Route path="/inbox/:itemId" element={<InboxDetail />} />
          <Route path="/agents" element={<AgentList />} />
          <Route path="/agents/:agentId" element={<AgentDetail />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="/chat" element={<ChatLayout />} />
          <Route path="/chat/:agentId" element={<ChatLayout />} />
          <Route path="/knowledge" element={<KnowledgeBase />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/llm-calls" element={<LlmCalls />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
