# Decisions

Settled items that are not business rules (business rules go in
`docs/walmart-user-stories.md` instead, and need an Owner paste there --
see the open blockers in `PROGRESS.md` for anything still pending that
step). Append here when something is settled: stack choices, interim
rulings under the narrowest-reading precedent, naming conventions.

**Not auto-loaded.** `PROGRESS.md` is. If a decision needs to be visible
to every session by default, it does not belong only here -- summarise it
in `PROGRESS.md`'s Open Blockers or Current Position section too.

---
## Decisions log

Append here when something is settled that is not a business rule. Business rules go in `docs/walmart-user-stories.md` instead.

| Date | Decision |
|---|---|
| 2026-10-23 | Stack: Python 3.12, FastAPI, Jinja + htmx, plain managed PostgreSQL, raw SQL migrations, pytest |
| 2026-10-23 | Layout: app in `src/`, spec in `docs/`, previous build in untracked `old_pro/` |
| 2026-10-23 | profit-v1 amended: estimates stored at 4dp, settled amounts at 2dp. Verified against 20 real orders. |
| 2026-10-23 | All decision gates D-01…D-10 closed; recorded as seven versioned policies |
| 2026-10-24 | US-001 lockout values chosen (not specified by access-v1): threshold 5 failed attempts, lockout duration 15 minutes. Both are `access_policy` rows, reviewable by the Owner without a deploy. |
| 2026-10-24 | **B-7 closed by the Owner.** `is_active` is not a scope or visibility filter. Owner sees all stores; Admin and Manager see exactly their assignments, inactive ones included. Visibility only -- the write question is explicitly *not* settled (now B-9). This is a **business rule**, so it belongs in `docs/walmart-user-stories.md` after line 109, not here; it is recorded here only until the Owner pastes it (B-10). |
| 2026-10-24 | **B-8 closed by the Owner.** The current store selection lives in a **server-side session row**, chosen as the option the client cannot tamper with. **It is a pointer, never a grant:** scope must still be resolved per request and the stored selection re-checked against it, or revoking an assignment stops taking effect on the next request as `access-v1` requires. Not yet implemented -- lands with US-002b. |
| 2026-10-27 | **US-005 split three ways by the Owner.** US-005a = AC-01, AC-02 (order identity and idempotent persistence). US-005b = AC-03 (import run visibility and freshness). US-005c = AC-04…AC-08 (posting-period assignment under `period-v1`). Eight ACs across eight tables and three distinct concerns is more than one module; the split is per the standing rule to narrow rather than widen. One consequence, from `database-schema.md:1102-1103`: `orders` must carry its **full** documented column shape in its first migration — including `accounting_month_id` (nullable, no derivation), `scope_status_code`, `posting_status_code` — even though US-005a never populates them. So US-005a carries the schema cost and 005b/005c are logic only. `accounting_months` must also be created at full shape to satisfy the FK, empty and unpopulated. |
| 2026-10-27 | **B-9 given an interim ruling, not an Owner decision.** The Owner declined to decide ("take the most logical one, I can patch later"). Applying the **B-13/B-15 precedent** — narrowest reading that no document contradicts, enforced, labelled interim in code, reversible in one line: **an inactive store refuses writes, visibly.** The argument is asymmetry of reversal, not preference: refuse-then-relax is a boolean flip; allow-then-restrict is data cleanup. It must fail *visibly*, never silently skip. `spec-oracle` confirmed the gap is real — `is_active` has **zero matches** in `walmart-user-stories.md`, and no capability-matrix row is conditioned on store activity. **Still an Owner item.** |
| 2026-10-27 | **"A supported order payload" (US-005 AC-01) is an undefined term — gap, logged here rather than guessed.** C-02 as actually written (`walmart-user-stories.md:1175`) governs **settlement reports**, not orders; the matching table is `settlement_report_definitions`. There is no order-payload catalogue, no version set, and no AC saying what an *unsupported* order payload does. Decision: **build no unsupported-payload rejection branch.** A payload missing a required field fails as a missing field. Borrowing US-015 AC-04's "fails visibly" for orders would invent behaviour with no AC behind it. Reversible once the Owner rules. |
| 2026-10-27 | **B-16 closed by the Owner.** `theme.txt` is intentional for this project -- it is not to be followed literally (exact hex values, exact fonts, the mandatory SVG grain technique are not requirements), only as a loose aesthetic direction ("the idea and inspiration"). This does not override `docs/walmart-store-manager-design.md`'s UX baseline (data-dense, compact typography, sober operations tool) -- it is a palette/mood steer layered on top of that baseline, not a replacement for it. Non-normative UX/design guidance, not a business rule, so recorded here rather than requiring a `docs/` amendment (same tier as B-8). The orphaned test file (`tests/test_ui001_sign_in_screen.py`) pinned literal theme.txt compliance throughout its Group 5 and one Group 1 test -- being revised by test-author to test the actual, theme-agnostic engineering/accessibility requirements instead of exact token matches, before any screen is built against it. |
| 2026-10-27 | `theme.txt` added to `.gitignore` at the Owner's instruction -- "not wanted tracked." No further action; the file stays untracked and advisory, per B-16. |
| 2026-10-28 | **B-18 closed by the Owner.** A real order with two SKUs under one `purchaseOrderId` (contradicting BR-02) is rejected **whole** at import time, never split, never partially imported -- and the rejection is **visible**, not a silent skip. This is a **business rule about order-import behaviour**, so it belongs in `docs/walmart-user-stories.md` (BR-02 itself, or a new AC), not only here -- same relationship as B-7/B-10: recorded here now, Owner pastes the exact wording once US-005b works out the precise mechanics (what "visible" resolves to -- an `import_errors` row, surfaced on the orders list). |
| 2026-10-28 | **G-6 closed by the Owner.** US-005b's AC-03 is built with a real rendered screen, not API-only -- "I wanna see things and interact." This is the first slice-2 story to build a screen as part of its own scope rather than deferring rendering to a dedicated UI story the way US-002a/US-003/US-004/US-005a all did; justified here because AC-03's own text ("when viewing the list") names the screen directly, unlike any earlier slice-1/2 AC. |

