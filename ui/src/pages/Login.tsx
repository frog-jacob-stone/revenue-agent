import { FormEvent, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';

interface LocationState {
  from?: { pathname: string };
}

export default function Login() {
  const { session, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session) {
    const dest = (location.state as LocationState)?.from?.pathname ?? '/';
    return <Navigate to={dest} replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password);
      const dest = (location.state as LocationState)?.from?.pathname ?? '/';
      navigate(dest, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-950 text-slate-100">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 p-6 rounded-lg border border-slate-800 bg-slate-900"
      >
        <h1 className="text-lg font-semibold">Revenue Agents</h1>
        <p className="text-sm text-slate-400">Sign in to continue.</p>

        <div className="space-y-1">
          <label htmlFor="email" className="text-xs uppercase text-slate-400">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-3 py-2 rounded bg-slate-800 border border-slate-700 focus:border-slate-500 focus:outline-none text-sm"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="text-xs uppercase text-slate-400">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-3 py-2 rounded bg-slate-800 border border-slate-700 focus:border-slate-500 focus:outline-none text-sm"
          />
        </div>

        {error && (
          <div className="text-sm text-red-400 border border-red-900 bg-red-950/50 rounded px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
