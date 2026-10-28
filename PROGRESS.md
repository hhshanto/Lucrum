# Progress

Volatile session state. Auto-loaded in full into every session, so it is
kept **short** on purpose -- detail goes in the sibling files, read on
demand:

- `PROGRESS-ARCHIVE.md` -- per story: what shipped, what each audit caught.
- `DECISIONS.md` -- settled decisions, interim rulings, conventions.
- `SESSION-LOG.md` -- one line per session, newest first. Append-only.

Stable rules live in `AGENTS.md` and `docs/`. Nothing here is a business
rule -- if you find one, it is in the wrong place.

---

## Scope: cut and locked 2026-10-28

Target is a **usable orders-and-costing tool**, not the full 134-criterion
specification. Full entry in `DECISIONS.md`.

**In scope (~28 ACs):** US-005b-2 *(done)*, US-002b, US-006, US-007,
US-008, US-009, US-010, US-011.

**Deferred, not cancelled (~104 ACs):** slices 4-8 entirely (US-015-028,
US-031, US-033) plus US-005c, US-012, US-013, US-014, US-030, US-032.

No slice-0 invariant is relaxed by this cut. The acceptance criteria were
checked for padding and are **not** padding -- the reduction comes from
building fewer stories, never from weakening a criterion.

**Escalation rule changed the same day:** decide by default, record in
`DECISIONS.md`, keep moving. Escalate only for (1) profit calculation,
(2) closed-month protection or historical financials, (3) a flat
contradiction between authoritative documents. Full pipeline only for
stories touching money, period protection, or the four state dimensions;
others get test-author -> implementer -> one audit. **Tests always
precede implementation** -- that ordering is not part of the saving.

## Current position

**US-005b-2 is done.** Audited **pass-with-findings** at `3a81b7a`, no
blocking findings, after a first audit returned **fail** at `3054b97`.
7 story tests, suite 325 passed / 4 skipped, scan clean, 0 suppressions.
Route `GET /app/stores/{store_id}/orders`, template
`src/templates/orders_list.html`, plus one read-only
`get_order_level_freshness` in `orders/repository.py`.

**AC-03 is now complete** -- the backend half landed in US-005b-1, the
screen half here.

*The first audit is kept visible below rather than deleted. A progress
record that quietly rewrites its own errors is worth less than one that
shows them -- and both findings were things the passing tests were
structurally unable to catch, which is the reusable lesson.*

**F-100 (was blocking, now closed) -- B-22 was not implemented.**
`DECISIONS.md` closed B-22 as "render **both**" order-level
(`orders.last_imported_at`) and run-level (`import_batches`) freshness.
Only run-level was rendered; `orders.last_imported_at` had **no reader
anywhere in the codebase**. The tests were deliberately written
source-agnostic *before* B-22 was ruled, so they could not detect it; once
the Owner ruled "both", a source-specific assertion was owed and was never
added. **Root cause worth keeping:** order-level freshness cannot be read
without a function in `orders/repository.py` -- outside this story's
declared `routes.py`-only boundary. The ruling and the boundary were in
direct collision, and the collision was resolved by silently narrowing the
ruling. The correct move was to stop and report it. Owner ruling, now
standing: **the boundary widens, the ruling does not move** -- and when a
ruling and a boundary collide, stop and report rather than reinterpret.

**F-101 (was blocking, now closed) -- `partial` was rendered as "failed".**
The route flattened every non-`completed` status into one hardcoded word,
though `import_batches.status_code` has five values and the service
deliberately computes a three-way outcome. A `partial` import *did* update
most orders. Status is now rendered verbatim and `last-import-failure` is
gated on `"failed"` literally. Unplanned gain: a `failed` batch with a
NULL `finished_at` used to vanish from the page entirely and now surfaces.

B-20 passed cleanly both times: identical `OutOfScope` for unassigned and
nonexistent at the same first query, 404 for both, no timing or
error-shape disclosure. The new `get_order_level_freshness` composes the
same `StoreScope` into SQL and was checked on the same basis.

**Open non-blocking findings, none urgent:** N-02/N-03 -- two unpinned
behaviours (`last-attempt-status` present for `completed`;
`order-last-imported-at` absent for a store with zero orders). Both are
correct in code but no test holds them, so a plausible future tidy-up
would silently reintroduce F-101. Worth one cheap test each next time this
file is open.

**G-8 -- discovered, not fixed (out of this story's module boundary):**
`orders.repository.get_import_run_state`'s `order by started_at desc` has
no tiebreaker. Under full-suite load (many fast sequential imports), two
`import_batches` rows for the same store can land on the same
microsecond-resolution `started_at` (Windows clock granularity), and
Postgres's tie order is not guaranteed -- observed once in ~6 full-suite
runs, flipping which batch reads as "the most recent attempt" in both
`test_us005b1_import_run_record.py` (already-merged, not touched by this
story) and transitively in this story's tests. A fix belongs in
`orders/repository.py` (secondary `order by ... , id desc`, or a monotonic
batch sequence) -- backend module, different story's already-shipped code,
test-first via the pipeline, not patched here.

## Decision backlog -- code changes owed to merged code

Settled 2026-10-28, each needs the pipeline, test-first. Not blockers.

| # | Change | Where |
|---|---|---|
| B-9 | Inactive store **accepts** writes -- remove the interim refusal | store write path |
| B-15 | Team list: Owner-only -> **Owner + Admin** | `src/main.py:569`, `src/team/service.py:148` |
| B-14 | Default `managed_from` when `reporting_timezone` is present; NULL + blocker when absent | store creation |
| B-19 | Stop `sku = excluded.sku`; flag aside, batch `partial` | `src/orders/repository.py:91` |
| B-21 | Same order + same SKU + **same price** -> sum quantities; differing price -> flag aside. Warehouse charge stays once per order | `src/orders/service.py` |
| B-11 | Delete the skipped test; AC-02 closes on two blockers. **Closes US-003** | `tests/test_us003_create_store.py` |

## Slice status

| Slice | Status |
|---|---|
| 0 Skeleton | **done**, guardrail proof demonstrated 2026-10-24 |
| 1 Access and stores | US-001, US-003, US-004 + UI-01 done. US-002b in scope, not started. Not deployed (B-5) |
| 2 Orders | US-005a, US-005b-1, **US-005b-2 done.** US-006, US-007 in scope. US-005c deferred |
| 3 Costing | US-008-011 in scope, not started. US-012/013/014/030 deferred |
| 4-8 | deferred |

## Open blockers -- three

| # | Blocker | Owner |
|---|---|---|
| B-4 | No Postgres instance provisioned. Local dev unblocked | **You** -- Neon, Railway or RDS |
| B-5 | No deployment target chosen. Only when you first deploy | **You** |
| B-10 | Two paragraphs to paste after `docs/walmart-user-stories.md:109` (B-7 + B-9 `is_active` rules). Agents are deny-listed from `docs/**` by design. **Closes US-002a** | **You** -- one paste |

Also queued for a later `docs/` paste, not blocking code: capability rows
for **B-20** (Manager may view import run state), **B-13** (Owner-only
moves `managed_from`), **B-15** (Owner+Admin read team list), and **B-18**
(whole-order rejection on multi-SKU). All are decided and enforced in
code; only the spec text is outstanding.

**B-2 approved** -- the 13 settlement-category mappings in
`docs/walmart/settlement-recon-v1.md` are authorised but **not populated**;
that happens in slice 4, which is deferred. **B-17 is off the critical
path** now that US-005c is deferred. **B-6** (no refunded order in the
golden set) follows slice 5 into deferral.

## Carried notes

- **G-8 is now the top of the queue, and worse than first recorded.**
  `get_import_run_state`'s `order by started_at desc` has no tiebreaker;
  on Windows two `import_batches` rows can share a microsecond and
  Postgres's tie order is undefined. Observed failing roughly **1 run in
  4-5**, across *two* test files including already-merged
  `test_us005b1_import_run_record.py`. Worse (N-05): the new test helper
  `_latest_batch_status` orders by `started_at desc, id desc` -- a
  **different tiebreaker than production** -- so under a tie the fixture
  can assert one batch while production reads the other, making the
  failure surface as a wrong rendered status rather than as the real
  cause. A 1-in-5 flaky suite means "suite green" stops being evidence,
  which undermines every audit after it. Fix in `orders/repository.py`
  (secondary `order by ..., id desc`, or a monotonic batch sequence),
  test-first, and align the helper to production's ordering.
- **G-7** -- `failed` vs `partial` batch classification has no documented
  transition rule. B-19/B-21 now pin `partial` for flagged-aside cases;
  the rest stays unpinned.
- **F-99** -- a comment in `src/orders/service.py:600-617` misattributes
  the rollback mechanism. The `commit()` itself is correct and proven by
  two tests; only the prose is wrong. Fix the wording next time that file
  is opened.
- Commit before requesting a second audit pass, every time (F-94/F-18).
- A fix to already-merged code still goes through the pipeline, test
  first. An earlier audit was a verdict on a point in time, not an
  exemption.

## What the golden orders proved (2026-10-23)

`profit-v1` reproduces **19 of 20** real orders exactly; ROI matches the
workbook's Margin column; warehouse charged once per order (flat 1.50 at
qty 1, 2, 3, 5). The 20th disagreement was **an error in the business
workbook**, not the code -- a cancelled order omitting the 1.50 it
actually incurred. Estimate precision was corrected to 4dp as a result.
Full detail in `DECISIONS.md`. This de-risks slice 3 before it is built.
