# Progress

Volatile session state, kept intentionally short because it is auto-loaded
in full into every session (`opencode.jsonc`'s `instructions` array).
**Update this at the end of every session** -- but keep it short; detail
goes in the sibling files below, which are read on demand, not by default.

- `PROGRESS-ARCHIVE.md` -- one section per story: what shipped, what each
  audit pass caught, carried-forward findings. Read the relevant section
  before resuming a story that already has one.
- `DECISIONS.md` -- settled non-business-rule decisions (stack choices,
  interim rulings, naming conventions). Append here, not in `docs/`.
- `SESSION-LOG.md` -- one line per session, newest first. Append-only;
  never needs re-reading at session start.

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file, or its
siblings, is a business rule -- if you find one here, it is in the wrong
place.

---

## Current position

**US-005b-1 implemented and audited to a pass (`89707a9`), two rounds.**
US-005 AC-03's backend half (durable, queryable import-run record) is
covered by passing tests. **AC-03 itself is NOT done** -- the screen half
(US-005b-2, UI-04) has not been started; "viewing the list" has nothing to
view yet. 318 passed, 4 skipped; guardrail scan clean, 29 files, 0
suppressions. Full account: `PROGRESS-ARCHIVE.md`, section "US-005b-1".

**Slice 0 (skeleton): complete**, guardrail proof demonstrated 2026-10-24.
**Slice 1 (access/stores):** all four stories (US-001-004) plus UI-01 exist
in code and are audited; not fully closable -- B-5 (deployment) and B-10
through B-15 remain open Owner one-liners, none of which block slice 2.
**Slice 2 (orders):** US-005 split three ways. US-005a (AC-01/02) and
US-005b-1 (AC-03 backend half) are both implemented and audited to a pass.
US-005b-2 (the UI-04 screen) and US-005c (AC-04-08, blocked on B-17) have
not started. Full per-story detail for every story above:
`PROGRESS-ARCHIVE.md`.

---
## Next action

**CHECKPOINT, 2026-10-28 (second session of the day).** US-005b-1 is
implemented and audited to a pass (`89707a9`). Working tree clean, suite
green (318 passed, 4 skipped), scanner clean (29 files, 0 suppressions).

**Exact next step: US-005b-2, the UI-04 screen half of AC-03.** US-005b
was split (see the US-005b-1 section above) because it spanned two
modules -- `src/orders/` (done, this session) and `src/web/` +
`src/templates/` (not started). **AC-03 is not done until this lands** --
"viewing the list" has nothing to view yet. Start with a fresh
`spec-oracle` pass scoped narrowly to: what UI-04 must show for AC-03
specifically (last success, latest failure, never labeled current --
`get_import_run_state` already answers this), where it sits relative to
US-006 (which owns the rest of UI-04's columns and has not started), and
whether building a minimal AC-03-only slice of UI-04 now versus waiting
for US-006 to build the whole screen is the smaller slice. Do not build
more of UI-04 than AC-03 needs -- that would be scope creep in the other
direction, preempting US-006's filter/sort/pagination work.

**Three Owner items raised this session, all interim rulings pending
confirmation:**
- **B-19** -- cross-batch SKU disagreement (a later import's `sku`
  disagreeing with an already-stored row for the same
  `(store_id, walmart_order_id)`). Undecided: legitimate correction, or
  the same BR-02 contradiction arriving one call later? The Owner's B-18
  ruling covers only the within-batch case. Live, unguarded today --
  `repository.py:91` still does `sku = excluded.sku`. Its test is written
  in full and skipped, citing this blocker.
- **B-20** -- who may read a store's import run state. No capability-matrix
  row distinguishes "view import history" from "trigger an import"
  (`walmart-user-stories.md:674` grants all three roles the latter).
  Applied store-scope-only (not Owner-only, unlike B-13/B-15 -- this is
  operational data about a store the caller already has scope to, not
  administrative power over other users). Labelled interim.
- **B-21** -- a same-SKU duplicate line (two submitted lines, one
  `walmart_order_id`, same `sku`) is currently treated as a second,
  separate BR-02 violation and rejected whole, same as a genuine multi-SKU
  conflict. Could instead be a legitimate quantity split. Interim ruling
  follows BR-02's literal "there are no order line items" plus the
  Owner's "never a silent skip". Reversible in one branch.

Also still open from the prior session, unaffected by this one:
**G-7** (`failed` vs `partial` batch classification for order imports has
no documented transition rule -- deliberately left unpinned, tests assert
only the AC-03-mandated negative) and **F-99** (a comment in
`service.py:600-617` explaining the `conn.commit()` fix still
misattributes the rollback mechanism to the wrong object -- the `commit()`
itself is correct and proven by two tests, only the prose is wrong; fix
the wording next time the file is opened, do not re-trust it).

**US-005c (AC-04-08, posting-period assignment) still cannot start** until
**B-17** is answered -- nobody owns accounting-month provisioning, confirmed
by exhaustive search across every story in the document, not inferred.
Needs a new US-id or an explicit amendment naming an existing story.

**Slice 1 is still not fully closable** -- B-5 (deployment) and B-10 through
B-15 remain open Owner one-liners. None of them block slice 2's code.

**Process notes carried forward, both reconfirmed working this session:**
commit before requesting a second audit pass, every time (F-94/F-18's
lesson); and when a fix touches already-merged, already-audited code from
an earlier story, it still goes through the full pipeline -- spec-oracle,
a failing test first, implementation, then an audit pass -- even though
the earlier story's own audit was a verdict on a point in time, not a
permanent exemption. This session's B-18 fix (to US-005a's merged
`service.py`) followed that rule.

Carried from slice 0, now discharged: step 5 was demonstrated 2026-10-24.

## Slice status

| Slice | Stories | Status |
|---|---|---|
| 0 Skeleton | -- | **done.** Step 5 guardrail proof demonstrated 2026-10-24 |
| 1 Access and stores | US-001-004, UI-01 | US-001 done. US-002a (AC-01, AC-02) audited, blocked on B-10. US-002b (AC-03, AC-04) blocked on B-8, deferred to slice 2. US-003 audited to a pass, blocked on B-11; carries B-12, B-13, B-14. **US-004 implemented and audited to a pass** (`a9ea6aa`); carries B-15. **UI-01 (sign-in screen, authenticated shell) implemented and green** against `tests/test_ui001_sign_in_screen.py` (34 passed, 1 skipped alone; 262 passed, 3 skipped full suite). All four stories exist in code; **slice not deployed (B-5)** |
| 2 Orders | US-005…007 | **US-005 split three ways. US-005a (AC-01, AC-02) implemented and audited to a pass** (`663eaae`, 295 passed/3 skipped, scan clean). **US-005b split again: US-005b-1 (backend import-run record) implemented and audited to a pass** (`89707a9`, 318 passed/4 skipped, scan clean). US-005b-2 (UI-04 screen) not started -- AC-03 not done until it lands. US-005c (AC-04-08) blocked on B-17 |
| 3 Costing and estimates | US-008ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦014, US-030 | not started |
| 4 Settlement evidence | US-015ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦017 | not started |
| 5 Reconciliation | US-018ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦021, US-031 | not started |
| 6 Month close | US-022ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦025 | not started |
| 7 Reports | US-026ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦028 | not started |
| 8 Overview | US-033 | not started |

Full contents and per-slice traps: `docs/walmart-slice-plan.md`.


## Open blockers

| # | Blocker | Blocks | Owner |
|---|---|---|---|
| ~~B-1~~ | ~~Golden orders~~ | -- | **Closed 2026-10-23.** See `DECISIONS.md`. |
| B-2 | **C-01 narrowed, not closed.** `walmart-probe` ran against the live store and proposed 13 settlement-category mappings in `docs/walmart/settlement-recon-v1.md`, each marked confirmed or explicitly uncertain, with one real category (`Fee/Reimbursement`) deliberately left unmapped and recommended for suspense rather than guessed. **No row has been applied to any running config/table** — the proposal is evidence, not a decision. Needs Owner review before `settlement_category_mappings` (or equivalent) is actually populated | Slice 4 commit | **You** — review `docs/walmart/settlement-recon-v1.md` and approve/amend the 13 proposed rows |
| ~~B-3~~ | ~~`.env` credential naming/value conflict~~ | -- | **Closed.** See `DECISIONS.md`. |
| ~~B-18~~ | ~~A real order contradicts BR-02 (within-batch)~~ | -- | **Closed, implemented in US-005b-1** (`89707a9`). See `DECISIONS.md` and `PROGRESS-ARCHIVE.md`. Split off two further shapes, both still open below: **B-19**, **B-21**. |
| B-19 | **Cross-batch SKU disagreement.** A *later* import whose `sku` disagrees with the already-stored row for the same `(store_id, walmart_order_id)` -- not covered by B-18's ruling, which is phrased around the within-batch case. Could be a legitimate correction (Walmart fixed a wrong SKU) or the same BR-02 contradiction arriving one call later. **Live and unguarded today** -- `src/orders/repository.py:91` still does `sku = excluded.sku`, silently overwriting. Its test (`tests/test_us005b1_import_run_record.py`) is written in full and `@pytest.mark.skip`'d, citing this blocker, so the gap stays visible on every run | US-005b-2 and beyond -- any future refresh of a store that already has orders | **You** — correction or contradiction? |
| B-20 | **Who may read a store's import run state?** No capability-matrix row distinguishes "view import history" from "trigger an import" (`walmart-user-stories.md:674` grants Owner/Admin/Manager the latter, nothing addresses the former). **Interim: store-scope-only** — not the Owner-only B-13/B-15 shape, because this is operational data about a store the caller already has scope to, not administrative power over other users; a Manager who may trigger an import but may not see whether it failed would defeat AC-03's purpose. Enforced in code (`src/orders/repository.py`, `get_import_run_state` docstring), labelled interim | Nothing today (no HTTP caller exists yet); becomes load-bearing the moment US-005b-2 calls it | **You** — rule on it |
| B-21 | **Is a same-SKU duplicate line a legitimate quantity split, or a second BR-02 violation?** Two submitted lines sharing one `walmart_order_id` AND the same `sku` -- distinct from B-18's differing-SKU case. **Interim: treated the same as B-18** — rejected whole, visibly, under a distinct `import_errors.error_code` (`DUPLICATE_ORDER_LINE`, kept separate from `MULTIPLE_SKUS_FOR_ORDER` so the two conditions stay distinguishable in durable data). Follows BR-02's literal "there are no order line items" (which does not turn on whether the SKUs match) plus the Owner's explicit "never a silent skip". Reversible in one branch — covered by a real, unskipped test (not a skip like B-19, since "must not vanish silently" needs no ruling either way) | Nothing today; would need a code change (and a new test) if ruled the other way | **You** — rule on it |
| B-17 | **Nobody owns accounting-month provisioning.** `walmart-slice-plan.md:106` assigns "the month provisioning job" to slice 2. `api-service-design.md:266` makes `POST /months/provision` the **only** way a month comes into existence, and D-09 (`walmart-user-stories.md:1154`) forbids import from ever creating one. A capability row exists for the *authority* (`:673`, Owner/Admin yes, Manager no) — but **no acceptance criterion in US-005, US-006, US-007, US-022–025 or US-030 builds the endpoint or the job.** Confirmed by exhaustive search, not inference. US-005c *consumes* provisioned months; consuming is not building. **Not folded into US-005a/b/c** — that would invent an owner | **US-005c** (AC-05/06/07 are untestable without it) | **You** — a new US-id, or amend an existing story to own it |
| B-4 | No Postgres instance provisioned | Slice 0 | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â pick Neon, Railway or RDS |
| B-5 | No deployment target chosen | Slice 0 "deployed" criterion | **You** |
| B-6 | No refunded order in the golden set. The refund and cost-recovery path is covered only by synthetic fixtures | Slice 5 real-data confidence | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â add 1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“2 refunded orders to the workbook |
| ~~B-7~~ | ~~Inactive but assigned store in the selector~~ | -- | **Closed 2026-10-24.** See `DECISIONS.md`. Spec amendment still pending -- see B-10. |
| ~~B-8~~ | ~~Where the current store selection lives~~ | -- | **Closed 2026-10-24.** See `DECISIONS.md`. |
| B-9 | **Does an inactive store accept writes?** Currently **yes, by default** -- `get_store` has no `is_active` filter, so `POST /stores/{id}/notes` succeeds against a deactivated store and writes an audit event. Pre-existing, not introduced by `10b6fda`, but B-7 now *puts the store in the selector*, so users will find the door. The Owner settled visibility only and explicitly did not settle this | First store-scoped write story in slice 2 | **You** |
| B-10 | **B-7 amendment not yet in `docs/`.** The agent is deny-listed from editing `docs/**`. Until the sentence is pasted after `docs/walmart-user-stories.md:109`, the rule exists only in a `repository.py` docstring citing a blocker id, which is not a specification | **Declaring US-002a done** | **You** -- one paste |
| B-11 | **AC-02's "required integration configuration" is undefined.** No document says what it consists of. The only adjacent text is US-030 AC-04's *integration secrets*, a different story in slice 3; the only plausible column is `walmart_store_key`, but nothing says so -- and it is `NOT NULL` at creation, so it is a validation error, not an activation blocker. The clause has a **skipped test** citing B-11 rather than an invented third blocker | **Declaring US-003 done** | **You** -- define it or rule it out of US-003 |
| B-12 | **No audit event type for store creation.** `api-service-design.md:1186-1209` names `store_settings_updated`, `store_timezone_changed`, `store_assignment_changed` -- but nothing for creation. Slice 0 makes an audit event mandatory on every write, so one *must* be emitted; only the type string is unspecified. `store_created` was chosen by convention, disclosed at the emit site. It is now **load-bearing**: it is the token keeping `POST /stores` and `PATCH` idempotency keys apart (F-31, F-62) | Nothing today; confirm before slice 2 copies the convention | **You** -- confirm the string |
| B-13 | **Who may move `managed_from`?** The capability matrix (`walmart-user-stories.md:671`) grants Admin "change store settings". But `:171` says `managed_from` is set by **the Owner**, `database-schema.md:193` says "**Owner** may change it, audited", and `:693` denies Admin "re-post an order into or out of a protected period" -- which is the effect of moving `managed_from` across the `pre_onboarding` boundary. **Interim: Owner-only**, the intersection of all readings, since the narrower one cannot grant a power no document grants. Enforced in code and labelled interim | A rule is being applied that no document states | **You** -- rule on it |
| B-14 | **`managed_from` auto-default is not implemented.** `walmart-user-stories.md:171` and `database-schema.md:193` both say it defaults to the month the store is added; the code stores `NULL` and reports it as an activation blocker. The spec is **circular here**: "the month the store is added" is a calendar month in the store's timezone, which AC-02 forbids guessing and which may itself be `NULL` at creation. The safe direction was taken | Slice 2, when `scope_status_code` is computed against a NULL boundary | **You** -- resolve the circularity |
| B-15 | **Who may read a store's team list?** No capability-matrix row or AC names who may call `GET /stores/{store_id}/managers`. The two nearest rows answer different questions ("View stores in scope" is *which stores*, not *who else is on this one*; "Administer users" is user administration generally). **Interim: Owner-only** -- the same narrowest-available-reading precedent as B-13 -- enforced in code and labelled interim in both `src/main.py:569-588` and `src/team/service.py:148-160` | Nothing today; confirm before any other story reads this list | **You** -- rule on it |
| ~~B-16~~ | ~~Is `theme.txt` this project's design-system contract?~~ | -- | **Closed 2026-10-27.** See `DECISIONS.md`. |

B-5 is the remaining critical-path item, and only when you first deploy. Local development is unblocked. B-2 and B-3 run in parallel and should start now.

B-10 is one paste and closes US-002a.

## What the golden orders proved

Twenty real orders were loaded on 2026-10-23 and `profit-v1` was checked against them:

| Rule | Result |
|---|---|
| Cost formula and profit | Reproduces 19 of 20 **exactly** |
| ROI = profit ÃƒÆ’Ã‚Â· total cost | Matches the workbook's "Margin" column |
| Warehouse charged once per order | Confirmed at quantity 1, 2, 3 and 5 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â flat 1.50 throughout |
| Unit price ÃƒÆ’Ã¢â‚¬â€ quantity = total | All 20 |

Two things came out of it that changed the specification:

- **GR-06 found a real error in the business workbook.** A cancelled order omits the 1.50 warehouse charge it actually incurred, so that loss is understated. The golden case records the corrected ÃƒÂ¢Ã‹â€ Ã¢â‚¬â„¢9.20 and says so, to stop anyone "fixing" the test back.
- **Estimate precision was wrong in `profit-v1`.** Estimated fees carry more than 2 decimals (4.095, 5.1152); rounding them to 2 dp before summing shifted two of twenty orders by a cent. `profit-v1` now stores estimates at 4 dp and settled amounts at 2 dp.


---

Full story-by-story detail: `PROGRESS-ARCHIVE.md`. Full decisions log:
`DECISIONS.md`. Full session log: `SESSION-LOG.md`.
