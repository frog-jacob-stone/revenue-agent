import { useState } from 'react';
import type { NavTab } from './types';
import Layout from './components/Layout';

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('pending');
  return <Layout activeTab={activeTab} setActiveTab={setActiveTab} />;
}
