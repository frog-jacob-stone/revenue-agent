import type { Action } from './types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getActions(status: string): Promise<Action[]> {
  return apiFetch<Action[]>(`/actions?status=${encodeURIComponent(status)}`);
}

export function approveAction(
  id: string,
  approvedBy: string,
  executedPayload: Record<string, unknown>,
): Promise<Action> {
  return apiFetch<Action>(`/actions/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved_by: approvedBy, executed_payload: executedPayload }),
  });
}

export function rejectAction(id: string, rejectionReason: string): Promise<Action> {
  return apiFetch<Action>(`/actions/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rejected_by: 'system', rejection_reason: rejectionReason }),
  });
}
