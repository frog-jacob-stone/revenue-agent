export type AgentId =
  | 'sdr-researcher'
  | 'outreach-agent'
  | 'content-writer'
  | 'proposal-generator'
  | 'slide-deck-agent'
  | 'revenue-recognition';

export type AgentStatus = 'idle' | 'running' | 'error' | 'disabled';
export type ActionType = 'create' | 'update' | 'delete';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';
export type AuditOutcome = 'success' | 'failed' | 'pending' | 'rejected';

export interface Agent {
  id: AgentId;
  name: string;
  status: AgentStatus;
  lastRun: string;
  pendingCount: number;
  actionedToday: number;
  color: string;
  description: string;
}

export interface ApprovalItem {
  id: string;
  agentId: AgentId;
  actionType: ActionType;
  target: string;
  timestamp: string;
  summary: string;
  status: ApprovalStatus;
  payload: Record<string, unknown>;
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  agentId: AgentId;
  actionType: ActionType;
  target: string;
  outcome: AuditOutcome;
  reason: string;
  payload: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: string;
}

export interface MemoryEntry {
  id: string;
  agentId: AgentId;
  content: string;
  source: string;
  date: string;
  tags: string[];
}

export interface DailyRun {
  date: string;
  [key: string]: number | string;
}

// ── Agents ─────────────────────────────────────────────────────────────────

export const AGENTS: Agent[] = [
  {
    id: 'sdr-researcher',
    name: 'SDR Researcher',
    status: 'idle',
    lastRun: '2026-04-23T08:14:00Z',
    pendingCount: 3,
    actionedToday: 7,
    color: '#6366f1',
    description: 'Researches new accounts and enriches HubSpot contact records.',
  },
  {
    id: 'outreach-agent',
    name: 'Outreach Agent',
    status: 'running',
    lastRun: '2026-04-23T09:02:00Z',
    pendingCount: 5,
    actionedToday: 12,
    color: '#06b6d4',
    description: 'Drafts and queues personalized outreach sequences.',
  },
  {
    id: 'content-writer',
    name: 'Content Writer',
    status: 'idle',
    lastRun: '2026-04-22T17:30:00Z',
    pendingCount: 1,
    actionedToday: 3,
    color: '#10b981',
    description: 'Generates blog posts, case studies, and LinkedIn content.',
  },
  {
    id: 'proposal-generator',
    name: 'Proposal Generator',
    status: 'error',
    lastRun: '2026-04-23T07:45:00Z',
    pendingCount: 2,
    actionedToday: 1,
    color: '#f59e0b',
    description: 'Assembles deal-specific proposals from templates and CRM data.',
  },
  {
    id: 'slide-deck-agent',
    name: 'Slide Deck Agent',
    status: 'idle',
    lastRun: '2026-04-21T14:00:00Z',
    pendingCount: 0,
    actionedToday: 0,
    color: '#ec4899',
    description: 'Converts proposals into formatted slide decks.',
  },
  {
    id: 'revenue-recognition',
    name: 'Revenue Recognition',
    status: 'disabled',
    lastRun: '2026-04-01T06:00:00Z',
    pendingCount: 0,
    actionedToday: 0,
    color: '#8b5cf6',
    description: 'Runs monthly revenue recognition and generates reports.',
  },
];

export const AGENT_MAP: Record<AgentId, Agent> = Object.fromEntries(
  AGENTS.map((a) => [a.id, a]),
) as Record<AgentId, Agent>;

// ── Approval Items ─────────────────────────────────────────────────────────

export const APPROVAL_ITEMS: ApprovalItem[] = [
  {
    id: 'ai-001',
    agentId: 'sdr-researcher',
    actionType: 'create',
    target: 'Acme Corp (HubSpot Contact)',
    timestamp: '2026-04-23T09:15:00Z',
    summary: 'Create new contact record for Sarah Chen, VP Engineering at Acme Corp. Enriched from LinkedIn and Apollo.',
    status: 'pending',
    payload: {
      firstName: 'Sarah',
      lastName: 'Chen',
      email: 'schen@acmecorp.com',
      title: 'VP Engineering',
      company: 'Acme Corp',
      linkedinUrl: 'https://linkedin.com/in/sarahchen',
      apolloScore: 87,
      enrichedAt: '2026-04-23T09:10:00Z',
    },
  },
  {
    id: 'ai-002',
    agentId: 'outreach-agent',
    actionType: 'create',
    target: 'James Okafor <jokafor@vertex.io>',
    timestamp: '2026-04-23T09:01:00Z',
    summary: 'Queue 3-step email sequence for James Okafor. Subject: "How Frogslayer cut their QA cycle by 40%".',
    status: 'pending',
    payload: {
      sequenceId: 'seq-enterprise-2026-q2',
      steps: 3,
      firstSubject: 'How Frogslayer cut their QA cycle by 40%',
      sendAt: '2026-04-24T10:00:00Z',
    },
  },
  {
    id: 'ai-003',
    agentId: 'proposal-generator',
    actionType: 'create',
    target: 'Vertex IO — Deal #D-2241',
    timestamp: '2026-04-23T08:50:00Z',
    summary: 'Generate proposal document for Vertex IO modernization project. Est. value $180k.',
    status: 'pending',
    payload: {
      dealId: 'D-2241',
      company: 'Vertex IO',
      estValue: 180000,
      template: 'modernization-v3',
      sections: ['executive-summary', 'scope', 'timeline', 'pricing'],
    },
  },
  {
    id: 'ai-004',
    agentId: 'sdr-researcher',
    actionType: 'update',
    target: 'BrightPath Systems (HubSpot)',
    timestamp: '2026-04-23T08:30:00Z',
    summary: 'Update company enrichment data for BrightPath Systems. Industry reclassified from "Technology" to "FinTech".',
    status: 'pending',
    payload: {
      companyId: 'hs-829304',
      field: 'industry',
      oldValue: 'Technology',
      newValue: 'FinTech',
      confidence: 0.94,
    },
  },
  {
    id: 'ai-005',
    agentId: 'outreach-agent',
    actionType: 'update',
    target: 'Marco Rivera <mrivera@northlight.com>',
    timestamp: '2026-04-23T08:10:00Z',
    summary: 'Reschedule follow-up email for Marco Rivera — original send bounced, updated to new domain.',
    status: 'pending',
    payload: {
      contactId: 'c-44201',
      oldEmail: 'mrivera@northlight.io',
      newEmail: 'mrivera@northlight.com',
      rescheduledFor: '2026-04-24T09:00:00Z',
    },
  },
  {
    id: 'ai-006',
    agentId: 'content-writer',
    actionType: 'create',
    target: 'Blog Post: "The Hidden Cost of Legacy Code"',
    timestamp: '2026-04-23T07:55:00Z',
    summary: 'Draft 1,200-word blog post for Frogslayer website. SEO target: "software modernization ROI".',
    status: 'pending',
    payload: {
      title: 'The Hidden Cost of Legacy Code',
      wordCount: 1200,
      seoKeyword: 'software modernization ROI',
      targetPublish: '2026-04-28',
    },
  },
  {
    id: 'ai-007',
    agentId: 'sdr-researcher',
    actionType: 'delete',
    target: 'Duplicate Contact: Tom Nguyen (ID: c-38821)',
    timestamp: '2026-04-23T07:40:00Z',
    summary: 'Remove duplicate contact record. Primary record is c-38800. Duplicate created by HubSpot form import.',
    status: 'pending',
    payload: {
      duplicateId: 'c-38821',
      primaryId: 'c-38800',
      reason: 'Form import duplication',
      mergeData: false,
    },
  },
  {
    id: 'ai-008',
    agentId: 'outreach-agent',
    actionType: 'delete',
    target: 'Stale Sequence: "Q1 Cold Outreach" (seq-q1-cold)',
    timestamp: '2026-04-22T17:00:00Z',
    summary: 'Archive Q1 cold outreach sequence. All contacts have completed or been removed. No active enrollments.',
    status: 'pending',
    payload: {
      sequenceId: 'seq-q1-cold',
      activeEnrollments: 0,
      completedContacts: 214,
    },
  },
  {
    id: 'ai-009',
    agentId: 'proposal-generator',
    actionType: 'update',
    target: 'CloudNine Logistics — Deal #D-2198',
    timestamp: '2026-04-22T15:30:00Z',
    summary: 'Update proposal pricing section — client requested 10% discount for annual contract commitment.',
    status: 'pending',
    payload: {
      dealId: 'D-2198',
      originalPrice: 95000,
      discountPct: 10,
      newPrice: 85500,
      condition: 'Annual contract',
    },
  },
  {
    id: 'ai-010',
    agentId: 'outreach-agent',
    actionType: 'create',
    target: 'Priya Mehta <pmehta@databridge.co>',
    timestamp: '2026-04-22T14:10:00Z',
    summary: 'Queue LinkedIn connection request + note for Priya Mehta. Part of DataBridge account campaign.',
    status: 'pending',
    payload: {
      platform: 'linkedin',
      contactId: 'c-50012',
      note: 'Hi Priya — saw your talk at SaaStr. Would love to share how we helped a similar team…',
      campaignId: 'camp-databridge-2026',
    },
  },
];

// ── Audit Log ──────────────────────────────────────────────────────────────

export const AUDIT_ENTRIES: AuditEntry[] = [
  {
    id: 'al-001', timestamp: '2026-04-23T09:20:00Z', agentId: 'sdr-researcher',
    actionType: 'create', target: 'Sarah Chen (Acme Corp)', outcome: 'pending',
    reason: 'Awaiting human approval', payload: { contactId: null, status: 'proposed' },
  },
  {
    id: 'al-002', timestamp: '2026-04-23T09:05:00Z', agentId: 'outreach-agent',
    actionType: 'create', target: 'James Okafor sequence', outcome: 'pending',
    reason: 'Awaiting human approval', payload: { sequenceId: 'seq-enterprise-2026-q2' },
  },
  {
    id: 'al-003', timestamp: '2026-04-23T08:55:00Z', agentId: 'proposal-generator',
    actionType: 'create', target: 'Vertex IO Proposal', outcome: 'pending',
    reason: 'Awaiting human approval', payload: { dealId: 'D-2241' },
  },
  {
    id: 'al-004', timestamp: '2026-04-23T08:00:00Z', agentId: 'sdr-researcher',
    actionType: 'update', target: 'NovaTech (HubSpot)', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { field: 'phone', newValue: '+1-555-0192' },
  },
  {
    id: 'al-005', timestamp: '2026-04-23T07:50:00Z', agentId: 'outreach-agent',
    actionType: 'create', target: 'Elena Torres sequence', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { sequenceId: 'seq-mid-market-q2' },
  },
  {
    id: 'al-006', timestamp: '2026-04-22T16:30:00Z', agentId: 'content-writer',
    actionType: 'create', target: 'LinkedIn post: AI in PE', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { platform: 'linkedin', wordCount: 280 },
  },
  {
    id: 'al-007', timestamp: '2026-04-22T15:00:00Z', agentId: 'proposal-generator',
    actionType: 'create', target: 'Meridian Group Proposal', outcome: 'failed',
    reason: 'HubSpot API timeout — retry scheduled', payload: { dealId: 'D-2190', error: 'ETIMEDOUT' },
  },
  {
    id: 'al-008', timestamp: '2026-04-22T14:00:00Z', agentId: 'outreach-agent',
    actionType: 'delete', target: 'Bounced contact: raj@oldco.com', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { contactId: 'c-30041' },
  },
  {
    id: 'al-009', timestamp: '2026-04-22T11:30:00Z', agentId: 'sdr-researcher',
    actionType: 'create', target: 'BluePeak Capital (HubSpot Company)', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { companyId: 'hs-990012' },
  },
  {
    id: 'al-010', timestamp: '2026-04-22T10:00:00Z', agentId: 'outreach-agent',
    actionType: 'update', target: 'Carlos Vega — email address', outcome: 'rejected',
    reason: 'Rejected: wrong contact, different company', payload: { contactId: 'c-20199' },
  },
  {
    id: 'al-011', timestamp: '2026-04-22T09:15:00Z', agentId: 'sdr-researcher',
    actionType: 'update', target: 'Sunrise Analytics (industry tag)', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { field: 'industry', newValue: 'DataOps' },
  },
  {
    id: 'al-012', timestamp: '2026-04-21T17:00:00Z', agentId: 'slide-deck-agent',
    actionType: 'create', target: 'Slide deck: Vertex IO v1', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { dealId: 'D-2180', slides: 14 },
  },
  {
    id: 'al-013', timestamp: '2026-04-21T15:30:00Z', agentId: 'content-writer',
    actionType: 'create', target: 'Blog: "What Makes Software Durable"', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { wordCount: 1050, published: false },
  },
  {
    id: 'al-014', timestamp: '2026-04-21T14:00:00Z', agentId: 'proposal-generator',
    actionType: 'update', target: 'OmniCorp Deal — pricing', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { dealId: 'D-2150', discount: 0.05 },
  },
  {
    id: 'al-015', timestamp: '2026-04-21T11:00:00Z', agentId: 'outreach-agent',
    actionType: 'create', target: 'Aarav Patel cold email', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { contactId: 'c-60044' },
  },
  {
    id: 'al-016', timestamp: '2026-04-20T16:00:00Z', agentId: 'sdr-researcher',
    actionType: 'create', target: 'Ironclad Systems (HubSpot Company)', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { companyId: 'hs-880204' },
  },
  {
    id: 'al-017', timestamp: '2026-04-20T14:30:00Z', agentId: 'outreach-agent',
    actionType: 'delete', target: 'Stale contact: info@legacyco.com', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { contactId: 'c-11100' },
  },
  {
    id: 'al-018', timestamp: '2026-04-20T09:00:00Z', agentId: 'content-writer',
    actionType: 'create', target: 'Case study: NovaTech modernization', outcome: 'rejected',
    reason: 'Rejected: NovaTech not yet a reference customer', payload: { docId: 'doc-cs-novatech' },
  },
  {
    id: 'al-019', timestamp: '2026-04-19T17:00:00Z', agentId: 'sdr-researcher',
    actionType: 'update', target: 'RedwoodAI — company size', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { field: 'employees', newValue: '50-200' },
  },
  {
    id: 'al-020', timestamp: '2026-04-19T14:00:00Z', agentId: 'proposal-generator',
    actionType: 'create', target: 'CloudNine Logistics Proposal', outcome: 'success',
    reason: 'Approved by Jacob Stone', payload: { dealId: 'D-2198' },
  },
];

// ── Chat Messages ──────────────────────────────────────────────────────────

export const CHAT_HISTORIES: Record<AgentId, ChatMessage[]> = {
  'sdr-researcher': [
    { id: 'c1', role: 'user', content: 'What accounts did you research today?', timestamp: '2026-04-23T09:00:00Z' },
    { id: 'c2', role: 'agent', content: 'I researched 7 accounts today:\n\n**Acme Corp** — Sarah Chen (VP Eng) added, 94% confidence on email.\n**BrightPath Systems** — Reclassified to FinTech, updated 3 contacts.\n**NovaTech** — Phone number corrected via Apollo enrichment.\n**BluePeak Capital** — New company record created.\n**Sunrise Analytics** — Industry tag updated.\n**Ironclad Systems** — New company + 2 contacts created.\n**RedwoodAI** — Headcount range updated to 50-200.\n\n3 actions are pending your approval in the inbox.', timestamp: '2026-04-23T09:00:30Z' },
    { id: 'c3', role: 'user', content: 'Skip BluePeak for now — they are not a fit yet.', timestamp: '2026-04-23T09:05:00Z' },
    { id: 'c4', role: 'agent', content: 'Understood. I have flagged BluePeak Capital as "not a fit — hold" in my research queue. I will not surface them again until you explicitly un-hold the account. No HubSpot changes will be proposed until then.', timestamp: '2026-04-23T09:05:10Z' },
  ],
  'outreach-agent': [
    { id: 'c5', role: 'user', content: 'How many sequences are currently active?', timestamp: '2026-04-23T08:45:00Z' },
    { id: 'c6', role: 'agent', content: 'Currently 4 active sequences:\n\n1. **Enterprise Q2** — 28 contacts, step 1 sending today\n2. **Mid-Market Q2** — 41 contacts, mixed steps\n3. **DataBridge Campaign** — 6 contacts, LinkedIn-first\n4. **Warm Reactivation** — 12 contacts, step 2 in progress\n\n5 actions are pending approval, mostly new enrollments and one reschedule.', timestamp: '2026-04-23T08:45:15Z' },
    { id: 'c7', role: 'user', content: 'Pause the Warm Reactivation sequence.', timestamp: '2026-04-23T08:50:00Z' },
    { id: 'c8', role: 'agent', content: 'Proposing a pause on **Warm Reactivation** (seq-warm-react-q2). This will prevent any further steps from sending until you resume it. The 12 enrolled contacts will remain in the sequence at their current step.\n\nI have added this to your approval inbox — one action required.', timestamp: '2026-04-23T08:50:08Z' },
  ],
  'content-writer': [
    { id: 'c9', role: 'user', content: 'What content is queued?', timestamp: '2026-04-22T17:00:00Z' },
    { id: 'c10', role: 'agent', content: 'Currently queued:\n\n- **Blog post** — "The Hidden Cost of Legacy Code" (1,200 words, draft ready)\n- **LinkedIn post** — AI in PE sector (280 words, pending approval)\n- **Case study** — NovaTech modernization (rejected pending reference approval)\n\n1 item awaiting your approval in the inbox.', timestamp: '2026-04-22T17:00:20Z' },
  ],
  'proposal-generator': [
    { id: 'c11', role: 'user', content: 'What happened with the Meridian proposal?', timestamp: '2026-04-22T16:00:00Z' },
    { id: 'c12', role: 'agent', content: 'The Meridian Group proposal failed during execution due to a HubSpot API timeout. The action was logged as **failed** in the audit log. I have scheduled an automatic retry for **2026-04-23 at 06:00 UTC**.\n\nThe proposal document was not created — no partial data was written to HubSpot.', timestamp: '2026-04-22T16:00:18Z' },
  ],
  'slide-deck-agent': [
    { id: 'c13', role: 'user', content: 'Can you generate a deck for the CloudNine proposal?', timestamp: '2026-04-22T10:00:00Z' },
    { id: 'c14', role: 'agent', content: 'To generate a deck for CloudNine Logistics, I need the approved proposal document first. I can see **Deal #D-2198** has an approved proposal. I will now draft a 12-slide deck based on that proposal.\n\nThe action will appear in your approval inbox once the draft is ready. Estimated time: 2–3 minutes.', timestamp: '2026-04-22T10:00:25Z' },
  ],
  'revenue-recognition': [
    { id: 'c15', role: 'user', content: 'When does the next monthly run trigger?', timestamp: '2026-04-01T08:00:00Z' },
    { id: 'c16', role: 'agent', content: 'The Revenue Recognition agent is scheduled to run on the **1st of each month at 06:00 UTC**. The next run is **May 1, 2026 at 06:00 UTC**.\n\nThis agent is currently **disabled**. Enable it in Settings before the scheduled date to ensure it runs.', timestamp: '2026-04-01T08:00:12Z' },
  ],
};

// ── Memory Entries ─────────────────────────────────────────────────────────

export const MEMORY_ENTRIES: MemoryEntry[] = [
  { id: 'm1', agentId: 'sdr-researcher', content: 'Acme Corp has a strict no-cold-email policy. Always route through LinkedIn first.', source: 'Jacob Stone (manual)', date: '2026-04-10', tags: ['acme-corp', 'outreach-rules'] },
  { id: 'm2', agentId: 'sdr-researcher', content: 'ICP profile: B2B SaaS or PE-backed companies, 50-500 employees, CTO or VP Eng as primary contact.', source: 'Jacob Stone (manual)', date: '2026-04-01', tags: ['icp', 'targeting'] },
  { id: 'm3', agentId: 'sdr-researcher', content: 'Apollo confidence threshold is 85%. Do not propose contacts below this score without flagging.', source: 'System config', date: '2026-03-15', tags: ['apollo', 'data-quality'] },
  { id: 'm4', agentId: 'sdr-researcher', content: 'BluePeak Capital is on hold — not a fit yet. Do not surface until explicitly released.', source: 'Agent Chat (2026-04-23)', date: '2026-04-23', tags: ['bluepeak', 'hold'] },
  { id: 'm5', agentId: 'sdr-researcher', content: 'NovaTech modernization deal is live. Flag any NovaTech contacts as high-priority.', source: 'Jacob Stone (manual)', date: '2026-04-18', tags: ['novatech', 'priority'] },
  { id: 'm6', agentId: 'outreach-agent', content: 'Warm Reactivation sequence paused by Jacob Stone on 2026-04-23. Resume only on explicit instruction.', source: 'Agent Chat (2026-04-23)', date: '2026-04-23', tags: ['sequences', 'pause'] },
  { id: 'm7', agentId: 'outreach-agent', content: 'Subject line format preference: question or stat-led, under 60 chars. Avoid "checking in" openers.', source: 'Jacob Stone (manual)', date: '2026-04-05', tags: ['copywriting', 'style'] },
  { id: 'm8', agentId: 'outreach-agent', content: 'Do not contact anyone at Acme Corp via email — LinkedIn DM only. (Shared rule from SDR Researcher.)', source: 'System sync', date: '2026-04-10', tags: ['acme-corp', 'outreach-rules'] },
  { id: 'm9', agentId: 'content-writer', content: 'Frogslayer brand voice: direct, technical, no buzzwords, first-person plural ("we"). Never use "leverage" as a verb.', source: 'Jacob Stone (manual)', date: '2026-03-20', tags: ['brand-voice', 'style'] },
  { id: 'm10', agentId: 'content-writer', content: 'NovaTech is not yet a reference customer. Do not use them in case studies until release is confirmed.', source: 'Jacob Stone (manual)', date: '2026-04-20', tags: ['novatech', 'reference-customers'] },
  { id: 'm11', agentId: 'proposal-generator', content: 'Standard proposal template is "modernization-v3". Use "enterprise-v2" only for deals over $500k.', source: 'System config', date: '2026-04-01', tags: ['templates', 'proposals'] },
  { id: 'm12', agentId: 'proposal-generator', content: 'Discount approval threshold: up to 10% can be proposed autonomously. Over 10% requires manual review flag.', source: 'Jacob Stone (manual)', date: '2026-04-12', tags: ['pricing', 'discounts'] },
  { id: 'm13', agentId: 'slide-deck-agent', content: 'Default deck length is 10-14 slides. Executive decks should stay under 10.', source: 'Jacob Stone (manual)', date: '2026-04-01', tags: ['decks', 'formatting'] },
  { id: 'm14', agentId: 'revenue-recognition', content: 'Monthly run should complete by 08:00 UTC on the 1st. Alert Jacob if it runs past 09:00.', source: 'System config', date: '2026-03-01', tags: ['scheduling', 'alerts'] },
];

// ── Analytics ──────────────────────────────────────────────────────────────

function daysAgo(n: number): string {
  const d = new Date('2026-04-23');
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export const DAILY_RUNS: DailyRun[] = Array.from({ length: 30 }, (_, i) => ({
  date: daysAgo(29 - i),
  'SDR Researcher': Math.floor(Math.random() * 8 + 2),
  'Outreach Agent': Math.floor(Math.random() * 12 + 4),
  'Content Writer': Math.floor(Math.random() * 4),
  'Proposal Generator': Math.floor(Math.random() * 5 + 1),
  'Slide Deck Agent': Math.floor(Math.random() * 3),
  'Revenue Recognition': i === 22 ? 1 : 0,
}));

export const APPROVAL_RATES = [
  { agent: 'SDR Researcher', rate: 92 },
  { agent: 'Outreach Agent', rate: 87 },
  { agent: 'Content Writer', rate: 78 },
  { agent: 'Proposal Generator', rate: 95 },
  { agent: 'Slide Deck Agent', rate: 100 },
  { agent: 'Revenue Recognition', rate: 100 },
];

export const SUMMARY_STATS = {
  accountsResearched: 142,
  outreachSent: 389,
  proposalsGenerated: 18,
  approvalRate: 91,
  avgTimeToApprove: '14 min',
  mostActiveAgent: 'Outreach Agent',
};
