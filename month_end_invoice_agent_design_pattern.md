# Month-End Invoice Creation Agent Design Pattern

## 1. Use Case Understanding

This system is a **month-end invoice creation assistant** for Harvest.

The primary user command should be:

```text
Create month-end invoices for last month.
```

The assistant should then gather data, infer what invoices should be created, propose invoice drafts, identify missing or uncovered active projects, and wait for human approval before creating anything in Harvest.

### Core workflow context

The current manual process includes:

- Reviewing a shared Excel sheet for hosting-related invoices.
- Looking at Harvest invoices from the previous month.
- Using previous invoices to determine what should likely be created this month.
- Creating invoices in Harvest.
- Handling a mix of invoice types:
  - Hosting
  - Time and materials
  - Retainers
  - Fixed-fee projects
- Reviewing active Harvest projects that may or may not need an invoice.

### Important clarification

Not all invoices map one-to-one with projects.

The system must support:

```text
1 invoice = 1 or more projects
```

That means the agent should not generate invoices directly from individual projects. It should first generate **invoice groups**, then assign projects and line items to those invoice groups.

### Key requirements

The system should:

- Pull from Harvest instead of Slack.
- Use prior Harvest invoices to infer likely recurring invoices.
- Use the hosting Excel sheet for hosting-specific costs.
- Determine the target billing month from the command.
- Propose invoices before creating them.
- Include line items and reasoning for every proposed invoice.
- Support multi-project invoices.
- Identify active Harvest projects that are not represented on any proposed or existing invoice.
- Detect invoices already created for the target month.
- Require human approval before creating Harvest draft invoices.
- Create drafts only, not send invoices automatically.

### Key constraints

- Accuracy is important because invoice mistakes affect clients, revenue, and trust.
- Human approval is required before creating invoices.
- Harvest should be the source of truth for prior invoices and project activity.
- The Excel hosting sheet remains the source of truth for hosting-related costs.
- Some billing logic is recurring, but exceptions exist.
- Project coverage must be tracked separately from invoice count.
- The workflow should be idempotent, meaning it should be safe to run more than once without creating duplicates.

---

## 2. Recommended Design Pattern

### Primary Pattern

**Planning + Prompt Chaining with Human-in-the-Loop**

### Supporting Patterns

- **Tool Use**
  - Harvest API
  - Excel or shared spreadsheet connector
  - Optional database for rules and memory

- **Retrieval / RAG over Harvest invoice history**
  - Retrieve previous invoices.
  - Extract invoice patterns.
  - Infer grouping, line item, and billing behavior.

- **Reflection**
  - Review proposed invoices before presenting them to the human.
  - Validate grouping, project coverage, and duplicate risk.

- **Exception Handling & Recovery**
  - Handle missing data, API failures, ambiguous project matches, duplicate invoices, and unsupported invoice patterns.

- **Memory Management**
  - Store confirmed billing rules, client preferences, project aliases, and grouping rules.

- **Evaluation & Monitoring**
  - Track approval rate, corrections, missed invoices, duplicate prevention, and project coverage accuracy.

### Final pattern summary

```text
Planning decides the billing run.
Prompt chaining executes the billing workflow.
Tool use pulls and creates data.
Retrieval uses Harvest history to infer patterns.
Reflection validates proposals.
Human approval gates invoice creation.
Memory improves future billing runs.
```

---

## 3. Why This Pattern Fits

The user command is broad:

```text
Create month-end invoices for last month.
```

This is not a simple one-off invoice task. It requires the system to plan, gather data, infer expected invoices, group projects correctly, validate the proposal, and then ask for approval.

### Why planning is needed

The assistant must determine:

- What is the target billing period?
- What data sources are needed?
- Which invoices already exist for that period?
- Which prior invoices should influence this month?
- Which projects were active?
- Which projects had billable activity?
- Which projects appear in the hosting sheet?
- Which invoices should be grouped across multiple projects?
- Which active projects are not represented anywhere?

### Why prompt chaining is needed

The workflow has natural stages:

1. Resolve target month.
2. Pull Harvest and Excel data.
3. Normalize and match projects.
4. Build project evidence.
5. Build invoice groups.
6. Generate line items.
7. Check project coverage.
8. Validate proposals.
9. Present for approval.
10. Create approved drafts.

Each stage should validate its output before the next stage begins.

### Why human-in-the-loop is required

Creating invoices is a financial action. The agent should propose and explain, but the human should approve.

Recommended rule:

```text
The agent may create Harvest draft invoices only after explicit approval.
The agent may not send invoices automatically.
```

### Why not a simpler pattern

#### Pure tool use is not enough

Tool use can call Harvest and read Excel, but it does not provide the structured reasoning needed to infer invoice groups and validate coverage.

#### Pure RAG is not enough

Retrieving prior invoices helps, but the system must also reconcile current activity, create proposals, and take approved action.

#### Pure multi-agent collaboration is overkill for the MVP

Specialist agents may be useful later, but the first version should be a reliable staged workflow.

#### One-project-one-invoice logic is wrong

Because some invoices contain multiple projects, project-level invoice generation would produce incorrect results.

---

## 4. What the Design Looks Like

### High-level workflow

```text
User command
  ↓
Resolve target billing month
  ↓
Pull Harvest data
  ↓
Pull hosting spreadsheet
  ↓
Normalize clients and projects
  ↓
Build project evidence table
  ↓
Build invoice group candidates
  ↓
Assign projects to invoice groups
  ↓
Generate line items
  ↓
Check project coverage
  ↓
Validate proposals
  ↓
Human review
  ↓
Create approved Harvest draft invoices
  ↓
Generate audit summary
```

### Core design principle

```text
The unit of proposal is the invoice.
The unit of validation is the project.
The unit of approval is the invoice group.
```

This distinction is critical.

The system should not ask only:

```text
Does this project have an invoice?
```

It should ask:

```text
Is this project represented on a proposed invoice, existing invoice, exclusion, or review item?
```

---

## 5. Detailed Workflow

## 5.1 User command

Example:

```text
Create month-end invoices for last month.
```

The system interprets this as:

```json
{
  "workflow": "month_end_invoice_run",
  "target_period": "previous_calendar_month",
  "mode": "proposal_first",
  "create_policy": "draft_only_after_approval"
}
```

If the current date is May 5, 2026, then:

```text
Target billing period: April 1, 2026 to April 30, 2026
Comparison period: March 1, 2026 to March 31, 2026
```

The comparison period is used to infer prior invoice patterns.

---

## 5.2 Source data collection

### Harvest data to pull

The Harvest tool should retrieve:

- Active projects
- Clients
- Invoices from the target month
- Invoices from the comparison month
- Invoice line items
- Invoice statuses
- Project associations
- Billable time entries from the target month
- Approved or uninvoiced time, depending on billing policy
- Client billing terms, if available

### Hosting sheet data to pull

The Excel or shared spreadsheet tool should retrieve:

- Project
- Month
- Hosting cost
- Tooling fees
- Hosting fees
- Notes
- Any manually entered exceptions

### Optional stored memory

The system should retrieve saved rules such as:

- Project aliases
- Client-specific invoice grouping rules
- Projects that should not be invoiced
- Retainer projects
- Fixed-fee schedules
- Hosting line item preferences
- Line item formatting rules

---

## 5.3 Normalize and match data

The system standardizes:

- Client names
- Project names
- Harvest client IDs
- Harvest project IDs
- Billing period
- Invoice type
- Line item descriptions
- Amounts
- Prior invoice references

### Matching logic

```text
Exact Harvest project match
  → accept

Known alias match
  → accept and note alias

High-confidence fuzzy match
  → propose and mark confidence

Low-confidence match
  → flag for review

No match
  → do not create invoice
  → flag as unmatched source data
```

---

## 5.4 Build the Project Evidence Table

The Project Evidence Table has one row per Harvest project.

Example:

| Project | Client | Active | Target Month Time | Hosting Row | Prior Invoice | Prior Group | Expected Action |
|---|---|---:|---:|---:|---:|---|---|
| Website Redesign | Acme Co. | Yes | Yes | No | Yes | March Services Invoice | Include |
| SEO Support | Acme Co. | Yes | Yes | No | Yes | March Services Invoice | Include |
| Hosting | Acme Co. | Yes | No | Yes | Yes | March Hosting Invoice | Include |
| Internal Planning | Acme Co. | Yes | No | No | No | None | Review |

### Project evidence signals

A project may be a candidate for invoicing if:

- It has billable time in the target month.
- It appears in the hosting sheet for the target month.
- It was included in a prior recurring invoice.
- It is part of a confirmed monthly retainer.
- It is part of a fixed-fee billing rule.
- It is active and historically billed at month-end.

A project may need review if:

- It is active but has no billing evidence.
- It had a prior invoice but no current activity.
- It has current time but no prior invoice pattern.
- It appears in the hosting sheet but cannot be matched.
- Its billing type changed from the prior month.

---

## 5.5 Build the Invoice Group Table

The Invoice Group Table has one row per proposed invoice.

Example:

| Invoice Group | Client | Type | Projects Included | Source Pattern | Confidence |
|---|---|---|---|---|---|
| Acme April Services | Acme Co. | T&M | Website Redesign, SEO Support | Same projects grouped on March invoice | High |
| Acme April Hosting | Acme Co. | Hosting | Hosting | Hosting sheet and prior invoice | High |
| Beta April Retainer | Beta LLC | Retainer | Platform Support | Confirmed recurring retainer | High |

### Why this table matters

This prevents the system from assuming one project equals one invoice.

Instead of:

```text
Project A → Invoice A
Project B → Invoice B
Project C → Invoice C
```

The system should support:

```text
Invoice Group 1
  - Project A
  - Project B

Invoice Group 2
  - Project C

Invoice Group 3
  - Project D
  - Project E
  - Project F
```

---

## 5.6 Invoice grouping logic

### Group when

Projects should be grouped when:

- They were grouped together on the prior month's Harvest invoice.
- They belong to the same client.
- They share compatible billing terms.
- They share the same invoice type.
- A saved client billing rule says to group them.
- The client prefers one monthly services invoice across multiple projects.

### Do not group when

Projects should usually not be grouped when:

- They belong to different clients.
- They have different billing terms.
- One is hosting and one is T&M, unless prior invoices show this pattern.
- One is retainer and one is fixed-fee, unless explicitly configured.
- The prior month had them on separate invoices.
- The grouping is new and confidence is low.

### Flag for review when

The system should flag grouping for review when:

- Prior grouping changed this month.
- A project was added to a prior group.
- A project was removed from a prior group.
- A client has multiple plausible grouping strategies.
- A project has billable time but no obvious invoice group.
- A previous multi-project invoice included a project that is now inactive.

---

## 5.7 Generate invoice proposals

Each proposed invoice should include:

```json
{
  "invoice_group_id": "client-period-type-or-hash",
  "client": "Client Name",
  "billing_period": "April 2026",
  "invoice_type": "T&M | Retainer | Fixed Fee | Hosting | Mixed",
  "projects_included": [
    {
      "project": "Project A",
      "harvest_project_id": "123",
      "inclusion_reason": "Had billable time in April and was grouped with Project B on March invoice."
    },
    {
      "project": "Project B",
      "harvest_project_id": "456",
      "inclusion_reason": "Same client and historically included on the same invoice as Project A."
    }
  ],
  "line_items": [
    {
      "description": "Project A - April 2026 professional services",
      "amount": 5000,
      "source": "Harvest time entries"
    },
    {
      "description": "Project B - April 2026 professional services",
      "amount": 3000,
      "source": "Harvest time entries"
    }
  ],
  "total": 8000,
  "reason": "The March invoice grouped Project A and Project B for this client. Both projects are active and have April billable activity.",
  "confidence": "High",
  "recommended_action": "Create one draft invoice with both projects represented."
}
```

### Hosting proposal example

```json
{
  "client": "Acme Co.",
  "billing_period": "April 2026",
  "invoice_type": "Hosting",
  "projects_included": [
    {
      "project": "Acme Hosting",
      "harvest_project_id": "789",
      "inclusion_reason": "Project appears in the April hosting spreadsheet."
    }
  ],
  "line_items": [
    {
      "description": "April 2026 hosting cost",
      "amount": 250,
      "source": "Hosting spreadsheet"
    },
    {
      "description": "April 2026 tooling fees",
      "amount": 50,
      "source": "Hosting spreadsheet"
    },
    {
      "description": "April 2026 hosting management fee",
      "amount": 100,
      "source": "Hosting spreadsheet"
    }
  ],
  "total": 400,
  "reason": "The project appears in the April hosting spreadsheet and maps to an active Harvest project.",
  "confidence": "High",
  "recommended_action": "Create draft invoice"
}
```

---

## 5.8 Project coverage check

After invoice groups are generated, the system checks every active Harvest project.

Each project must have one of these statuses:

```text
Represented on proposed invoice
Already invoiced
Excluded by rule
No invoice recommended
Needs review
Missing coverage
```

### Active projects without proposed invoices

The output should categorize active projects that are not represented.

Recommended categories:

1. **Active with current time but no invoice proposal**
   - High risk
   - Usually needs invoice proposal or review

2. **Active with prior invoice but no current proposal**
   - High or medium risk
   - Could indicate a missed recurring invoice

3. **Active with no current activity and no prior invoice**
   - Lower risk
   - Could be legitimately not billable

4. **Active but likely internal or non-billable**
   - Low risk if rule exists
   - Otherwise needs confirmation

5. **Active with missing billing rule**
   - Needs human review

Example:

| Project | Why No Invoice Was Proposed | Risk | Suggested Action |
|---|---|---:|---|
| Client A Support | No April time, no March invoice | Low | Mark no invoice needed |
| Client B Website | Had March invoice, no April proposal | High | Review |
| Client C Strategy | Has April billable time but no invoice group | High | Create T&M proposal |
| Internal Ops | Active but likely internal | Low | Exclude |

---

## 5.9 Existing invoice and duplicate check

Before proposing or creating anything, the system checks Harvest invoices already created for the target month.

### Duplicate detection

Use an idempotency key such as:

```text
client_id + project_ids + billing_period + invoice_type
```

The system should detect:

- Exact duplicate invoices
- Similar invoice totals
- Same client and period
- Same project coverage
- Same invoice type
- Similar line item descriptions

### Duplicate behavior

```text
Exact duplicate found
  → do not propose new invoice
  → mark as already invoiced

Possible duplicate found
  → propose with warning
  → block creation until reviewed

No duplicate found
  → continue
```

---

## 5.10 Reflection and QA validation

Before the proposal is shown to the human, the system runs a QA step.

### Invoice-level validation

Check that every proposed invoice has:

- Client
- Billing period
- Invoice type
- One or more represented projects
- Line items
- Total
- Source evidence
- Reason
- Confidence score
- Recommended action

### Project-level validation

Check that:

- Every active Harvest project is accounted for.
- Every project with billable time is represented or flagged.
- Every hosting sheet row is represented or flagged.
- Every prior recurring project is represented, excluded, or flagged.
- Every invoice group has project attribution.

### Grouping validation

Check that:

- Grouped projects belong to the same client.
- Prior invoices support the grouping.
- Billing types are compatible.
- Client billing terms are compatible.
- Grouping changes are highlighted.
- New groupings are marked for review.

### Amount validation

Check that:

- Totals equal the sum of line items.
- Hosting costs match the spreadsheet.
- T&M amounts match approved billable time.
- Retainer amounts match stored rules or prior invoices.
- Fixed-fee amounts match stored rules or approved milestones.

---

## 5.11 Human review

The human receives a review packet with:

1. Proposed invoice groups
2. Projects included in each invoice
3. Already-created invoices for the target month
4. Active projects not represented anywhere
5. Projects with billable time not represented anywhere
6. Hosting rows not represented anywhere
7. Prior invoice groups that changed this month
8. Possible duplicates
9. Low-confidence items
10. Recommended actions

### Human actions

For each proposed invoice group, the human can:

```text
Approve
Edit
Reject
Change invoice type
Change grouping
Add project
Remove project
Mark already invoiced
Mark no invoice needed
Save as recurring rule
Ask agent to investigate
```

### Approval rule

```text
Only approved invoice groups can be created as Harvest drafts.
```

---

## 5.12 Harvest draft creation

After approval, the Harvest Execution Agent creates draft invoices.

### Draft creation steps

```text
Approved proposal
  ↓
Create Harvest draft invoice
  ↓
Attach line items
  ↓
Associate projects where Harvest supports it
  ↓
Store source attribution
  ↓
Return invoice ID and link
  ↓
Log result
```

### Safety rule

```text
Create drafts only.
Do not send invoices automatically.
```

---

## 5.13 Audit summary

After draft creation, the system produces a final summary:

```text
Month-End Invoice Run: April 2026

Created draft invoices:
  - Acme April Services: $8,000
  - Acme April Hosting: $400

Already created:
  - Beta April Retainer: $2,500

Skipped:
  - Internal Ops: marked non-billable

Needs review:
  - Client C Strategy: has billable time but no invoice group

Errors:
  - Hosting row for Project X could not be matched to Harvest

Active projects not represented:
  - Project Y
  - Project Z
```

---

## 6. Implementation Approach

## 6.1 Agents involved

This can be implemented as one orchestrated workflow with logical sub-agents.

### 1. Billing Orchestrator

Owns the workflow.

Responsibilities:

- Interpret the command.
- Resolve billing period.
- Call data tools.
- Maintain workflow state.
- Coordinate validation.
- Present review packet.
- Trigger approved draft creation.

### 2. Harvest Data Agent

Responsibilities:

- Pull active projects.
- Pull clients.
- Pull prior invoices.
- Pull existing target-month invoices.
- Pull billable time entries.
- Pull invoice line items.
- Normalize Harvest data.

### 3. Hosting Sheet Agent

Responsibilities:

- Pull the shared hosting spreadsheet.
- Filter rows for the target month.
- Extract hosting cost, tooling fees, and hosting fees.
- Match rows to Harvest projects.
- Flag unmatched rows.

### 4. Normalization Agent

Responsibilities:

- Standardize names and dates.
- Match aliases.
- Reconcile Harvest projects with hosting rows.
- Normalize prior invoice line items.
- Produce clean structured records.

### 5. Invoice Grouping Agent

Responsibilities:

- Analyze prior Harvest invoice groupings.
- Build invoice group candidates.
- Assign projects to invoice groups.
- Detect grouping changes.
- Apply saved grouping rules.

### 6. Invoice Proposal Agent

Responsibilities:

- Generate line items.
- Calculate totals.
- Produce reasons.
- Assign confidence.
- Recommend action.

### 7. Reconciliation Agent

Responsibilities:

- Check active project coverage.
- Detect projects with time but no invoice.
- Detect prior invoice groups not repeated.
- Detect hosting rows not represented.
- Detect duplicates.

### 8. QA / Reflection Agent

Responsibilities:

- Validate proposal completeness.
- Validate grouping.
- Validate line item sources.
- Validate totals.
- Flag low-confidence items.
- Request repair before human review.

### 9. Harvest Execution Agent

Responsibilities:

- Create approved Harvest draft invoices.
- Return draft invoice links.
- Log created invoice IDs.
- Handle API failures and retries.

---

## 6.2 Tools required

### Harvest API tool

Required capabilities:

- List clients.
- List active projects.
- List invoices by date range.
- Retrieve invoice line items.
- Retrieve project associations.
- Retrieve billable time entries.
- Retrieve invoice status.
- Create draft invoice.
- Update draft invoice.
- Return invoice URL.

### Spreadsheet tool

Required capabilities:

- Read shared Excel or Google Sheet.
- Filter by month.
- Parse project, costs, fees, and notes.
- Preserve source row references.
- Detect missing or malformed values.

### Rule and memory store

Stores:

- Client billing profiles
- Project aliases
- Invoice grouping rules
- Retainer rules
- Fixed-fee rules
- Non-billable projects
- Client line item preferences
- Prior human corrections
- Historical invoice run logs

### Review UI

Could be implemented in:

- Retool
- Airtable
- Internal web app
- Google Sheet review tab
- Slack interactive approval message
- Harvest draft review dashboard

For the MVP, a structured review table is enough.

---

## 6.3 Data flow

```text
Harvest active projects
  → Project Evidence Table

Harvest prior invoices
  → Invoice Pattern Memory
  → Invoice Group Candidates

Harvest target-month invoices
  → Duplicate and already-created check

Harvest target-month time entries
  → T&M line item candidates

Hosting spreadsheet
  → Hosting line item candidates

Stored billing rules
  → Grouping and invoice type decisions

All normalized data
  → Invoice Group Proposals
  → Project Coverage Check
  → Human Review
  → Approved Draft Creation
```

---

## 6.4 Orchestration logic

```text
Start month-end invoice run

1. Resolve target billing period.
2. Resolve comparison billing period.
3. Pull Harvest active projects.
4. Pull Harvest invoices from comparison period.
5. Pull Harvest invoices from target period.
6. Pull Harvest time entries from target period.
7. Pull hosting spreadsheet rows for target period.
8. Normalize all data.
9. Build Project Evidence Table.
10. Build Invoice Group Table.
11. Generate invoice proposals.
12. Check existing invoices and duplicates.
13. Check active project coverage.
14. Validate groupings and line items.
15. Create review packet.
16. Wait for human approval.
17. Create approved Harvest draft invoices.
18. Generate final audit summary.
19. Store approved corrections and rules.
```

---

## 7. Feedback Loops & Validation

## 7.1 Proposal validation loop

```text
Generate invoice groups
  ↓
Check against prior Harvest invoices
  ↓
Check against current project evidence
  ↓
Check for duplicate invoices
  ↓
Check project coverage
  ↓
Check grouping changes
  ↓
Repair or flag issues
  ↓
Send to human review
```

## 7.2 Human feedback loop

When the human edits the proposal, the system should ask whether to remember the correction.

Examples:

```text
Always group Project A and Project B on one monthly invoice for Client X.

Do not group hosting with T&M for Client Y.

For Client Z, combine all active projects into one monthly services invoice.

For Client Q, create separate invoices per project even if same client.

For Client R, fixed-fee invoices are not project-specific.

Project M should never be invoiced.

Project N should invoice only when approved hours exist.
```

## 7.3 Retry logic

### Harvest API failure

```text
Temporary failure
  → retry with backoff

Repeated failure
  → stop draft creation
  → return partial review packet and error
```

### Spreadsheet failure

```text
Hosting sheet unavailable
  → continue non-hosting invoice analysis
  → flag hosting invoices as blocked
```

### Project matching failure

```text
No confident match
  → do not create invoice
  → flag for human review
```

### Duplicate invoice detected

```text
Exact duplicate
  → block creation
  → mark already created

Possible duplicate
  → show warning
  → require human decision
```

### Low-confidence grouping

```text
Low confidence
  → propose but require review
  → do not auto-create
```

---

## 8. Failure Modes & Risks

## 8.1 Copying last month too aggressively

### Risk

The system may repeat a prior invoice even though the current month does not support it.

### Mitigation

Use prior invoices as a pattern signal, not as the sole source of truth.

```text
Previous invoice = pattern signal
Current Harvest activity + hosting sheet + active project status = current evidence
```

---

## 8.2 Creating duplicate invoices

### Risk

The workflow may be run more than once.

### Mitigation

Use target-month invoice checks and idempotency keys.

```text
client_id + project_ids + billing_period + invoice_type
```

---

## 8.3 Incorrectly splitting grouped invoices

### Risk

The agent creates one invoice per project when the client expects one combined invoice.

### Mitigation

- Extract grouping from prior Harvest invoices.
- Store confirmed grouping rules.
- Show grouping changes during review.

---

## 8.4 Incorrectly grouping separate invoices

### Risk

The agent combines projects that should be billed separately.

### Mitigation

- Only group when prior invoices, explicit rules, or compatible billing metadata support it.
- Flag first-time groupings for review.

---

## 8.5 Missing project coverage

### Risk

A project may not have its own invoice but may be represented on a grouped invoice.

### Mitigation

Track project-to-invoice-group assignment.

Use representation-based validation:

```text
Represented on proposed invoice
Already invoiced
Excluded
No invoice recommended
Needs review
Missing coverage
```

---

## 8.6 Losing line item attribution

### Risk

Combined invoices may hide which project contributed which amount.

### Mitigation

Store source project IDs on each line item internally.

Example:

```json
{
  "line_item": "April professional services",
  "amount": 8000,
  "source_projects": ["Project A", "Project B"]
}
```

---

## 8.7 Incorrect T&M amounts

### Risk

The system may include unapproved, non-billable, or already-invoiced time.

### Mitigation

- Only include approved billable time, depending on billing policy.
- Show time source summary.
- Flag unapproved time separately.
- Check for already-invoiced time.

---

## 8.8 Retainer and fixed-fee ambiguity

### Risk

Prior invoices may not clearly distinguish retainers from fixed-fee invoices.

### Mitigation

- Use stored billing rules when available.
- Infer from prior invoices only with confidence scoring.
- Require review for inferred retainers and fixed-fee invoices.

---

## 8.9 Hosting spreadsheet mismatch

### Risk

The hosting sheet project name may not match Harvest.

### Mitigation

- Maintain alias mapping.
- Flag low-confidence matches.
- Never create drafts from unmatched hosting rows.

---

## 9. MVP Version

The MVP should be:

```text
Month-End Invoice Proposal Assistant
```

It should not begin as a fully autonomous invoice creator.

### MVP command

```text
Create month-end invoices for last month.
```

### MVP behavior

1. Resolve target billing period.
2. Pull Harvest active projects.
3. Pull Harvest prior-month invoices.
4. Pull Harvest target-month existing invoices.
5. Pull Harvest target-month billable time.
6. Pull hosting spreadsheet for target month.
7. Build Project Evidence Table.
8. Build Invoice Group Table.
9. Generate invoice proposals.
10. Show active projects not represented.
11. Show already-created invoices.
12. Show low-confidence and exception items.
13. Wait for approval.

### MVP output

The MVP should produce a review packet like:

```text
Month-End Invoice Run: April 2026

A. Proposed invoice groups
B. Projects included in each invoice
C. Already-created invoices
D. Active projects not represented
E. Projects with billable time not represented
F. Hosting rows not represented
G. Prior invoice groups that changed
H. Possible duplicates
I. Recommended actions
```

### MVP should support multi-project invoices

This is not optional. The MVP must avoid assuming:

```text
1 project = 1 invoice
```

### MVP should not yet

- Automatically send invoices.
- Create invoices without approval.
- Depend on Slack.
- Silently learn rules without confirmation.
- Fully automate ambiguous fixed-fee logic.
- Automatically group all projects by client without evidence.

---

## 10. Future-State Version

The mature version becomes a **Harvest Month-End Billing Agent**.

### Future-state command

```text
Create month-end invoices for last month.
```

### Future-state behavior

```text
User command
  ↓
Agent determines target month
  ↓
Agent pulls Harvest and hosting data
  ↓
Agent builds project evidence
  ↓
Agent builds invoice groups
  ↓
Agent validates coverage and duplicates
  ↓
Agent presents review packet
  ↓
Human approves or edits
  ↓
Agent creates Harvest draft invoices
  ↓
Agent logs audit trail
  ↓
Agent updates billing memory from approved corrections
```

### Client-level billing profiles

The future-state system should maintain profiles like:

```json
{
  "client": "Acme Co.",
  "month_end_billing": {
    "grouping_strategy": "group_selected_projects",
    "invoice_groups": [
      {
        "name": "Monthly Services",
        "invoice_type": "T&M",
        "projects": ["Website Redesign", "SEO Support"],
        "line_item_style": "separate_by_project"
      },
      {
        "name": "Hosting",
        "invoice_type": "Hosting",
        "projects": ["Website Hosting"],
        "line_item_style": "hosting_cost_tooling_fee_management_fee"
      }
    ]
  }
}
```

### Future capabilities

- Scheduled monthly billing runs.
- Automatic Harvest data refresh.
- Automatic hosting sheet ingestion.
- Client-specific billing profiles.
- Confirmed invoice grouping rules.
- Project alias management.
- Retainer rule storage.
- Fixed-fee milestone rules.
- T&M time validation.
- Duplicate prevention.
- Draft invoice creation after approval.
- Audit logs for every invoice run.
- Correction-based learning.
- Exception dashboard.
- One-off invoice workflow.

---

## 11. One-Off Invoice Mode

The system should also support a separate, smaller workflow for one-off invoice requests.

Example command:

```text
Create a one-off invoice for Project X for $5,000.
```

### One-off flow

```text
One-off invoice request
  ↓
Find Harvest client and project
  ↓
Confirm invoice details
  ↓
Generate draft line items
  ↓
Human approval
  ↓
Create Harvest draft invoice
```

### Recommended pattern for one-off mode

```text
Tool Use + Human-in-the-Loop + Exception Handling
```

This does not need the full month-end planning workflow unless the request is ambiguous.

---

## 12. Recommended Review Packet Format

### Section A: Proposed Invoice Groups

| Invoice | Client | Type | Projects Included | Total | Confidence | Reason | Action |
|---|---|---|---|---:|---|---|---|
| April Services | Acme Co. | T&M | Website Redesign, SEO Support | $8,000 | High | Same grouping as March and both projects have April time | Approve |
| April Hosting | Acme Co. | Hosting | Hosting | $400 | High | Found in April hosting sheet | Approve |
| April Retainer | Beta LLC | Retainer | Platform Support | $2,500 | Medium | Similar retainer invoice in March | Review |

### Section B: Active Projects Not Represented

| Project | Client | Reason Not Proposed | Risk | Suggested Action |
|---|---|---|---|---|
| Internal Planning | Acme Co. | No April time, no prior invoice | Low | Mark no invoice needed |
| Strategy | Client C | Has April time but no invoice group | High | Create T&M proposal |
| Support | Client D | Had March invoice but no current proposal | High | Review |

### Section C: Changed Invoice Groups

| Prior Invoice Group | Change | Reason | Risk |
|---|---|---|---|
| Acme March Services | Project SEO Support removed | No April billable time found | Medium |
| Beta March Retainer | Amount changed | No stored rule, inferred from prior invoice | High |

### Section D: Duplicate Warnings

| Candidate Invoice | Possible Duplicate | Reason | Action |
|---|---|---|---|
| Acme April Hosting | Harvest Invoice #1234 | Same client, period, type, and amount | Block creation |

---

## 13. Final Recommended Architecture

```text
Primary:
Planning + Prompt Chaining with Human-in-the-Loop

Supporting:
Tool Use
Retrieval / RAG over Harvest invoice history
Reflection
Exception Handling & Recovery
Memory Management
Evaluation & Monitoring
```

### Final operating principle

```text
Harvest replaces Slack as the recurring invoice memory.
Previous invoices suggest what should happen.
Current month Harvest and hosting data confirm what should happen.
Invoice groups are proposed before invoices are created.
Projects are validated by representation, not by invoice count.
Human approval decides what actually gets created.
```

### Final design rule

```text
Do not generate project-level invoices directly.
Generate invoice groups first.
Assign projects to invoice groups.
Validate every active project is represented, excluded, or flagged.
Create only approved Harvest draft invoices.
```
