import { Navigate } from 'react-router-dom';
import { AGENTS } from '../../mocks';

export default function AgentList() {
  return <Navigate to={`/agents/${AGENTS[0].id}`} replace />;
}
