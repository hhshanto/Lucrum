# Walmart Marketplace API — Settlement / Reconciliation report, v1

Walmart's own name for this report family is **"Recon report"** /
**"reconreport"** (path segment `/v3/report/reconreport/...`), not
"settlement report." The two terms are used interchangeably by sellers and
by Walmart's own documentation ("Date of the settlement report" appears as
a parameter description on the exact same endpoint whose summary says
"Recon report"), so I am treating them as the same thing throughout, but
flagging the naming mismatch now in case a future reader searches
Walmart's docs for "settlement" and does not immediately find "reconreport."

Probed live on 2026-10-28, same credentials and session as `orders-v3.md`.
Authentication for this endpoint family uses **Basic auth at the token
step** (shared with Orders) and then the header `WM_SEC.ACCESS_TOKEN` per
request — **not** `Authorization: Bearer`. Confirmed from the endpoint's
own published OpenAPI parameter list, not assumed.

## Endpoints probed

| Operation | Method/path | Fixture(s) |
|---|---|---|
| Available recon report dates | `GET /v3/report/reconreport/availableReconFiles?reportVersion=v1` | `fixtures/walmart/settlement-recon/v1/available_recon_dates.json` |
| Recon report, JSON | `GET /v3/report/reconreport/reconFileJson?reportDate=...&offset=...&noOfRecords=...` | `report_10202026_curated_excerpt_all_transaction_types.json`, `report_10202026_page1_of_N_noOfRecords50.json`, `report_10202026_page2_of_N_noOfRecords50.json`, `report_offset_overrun_400_INVALID_OFFSET.json`, `reportDate_format_requirement.json` |
| Recon report, CSV/zip | `GET /v3/report/reconreport/reconFile?reportDate=...&reportVersion=v1` | **Not probed.** `reconFileJson` was used instead because it is directly parseable; the CSV/zip variant was left unfetched since the JSON endpoint already demonstrates every real field, and the response wraps a compressed binary attachment the brief has no use for as a JSON fixture. Noting as a disclosed gap, not an oversight. |

No credential, token, or store identifier was found anywhere in this
report's real rows (checked: `Store Id` was present as a column but empty
in every row observed; no business name, partner id, or seller id appeared
anywhere in 325 real rows). The only redaction applied to these fixtures is
structural trimming (see below), not PII/credential removal.

## Real report characteristics (reportDate `10202026`, one biweekly batch)

- **325 total rows** in one report. Rows are **not** one row per order —
  they are one row per `(order line event, amount component)`. A single
  order line's sale typically explodes into 4, 6, or 8 rows (observed
  distribution: 43 lines → 4 rows, 13 lines → 6 rows, 12 lines → 8 rows,
  12 lines → 1 row (the `Adjustment` rows, which don't decompose further)).
- **One `PaymentSummary` row per report** — a batch-level header embedded
  in the same flat row stream as the itemized transactions, carrying
  `Total Payable`, `Period Start Date`, `Period End Date`, and almost every
  other column empty. A parser must special-case
  `Transaction Type == "PaymentSummary"` or it will try to treat the
  payout total as a line-item amount.
- `offset`/`nextOffset` are **byte offsets into the underlying file, not
  row indices.** Confirmed empirically: after consuming exactly 50 rows,
  `nextOffset` jumped from `0` to `18192`, then to `35888` after the next
  50 — not `50`, `100`. A pager that assumes row-count semantics for
  `offset` will compute the wrong value on its second call.
- **There is no clean "zero rows left" terminal page.** Requesting an
  `offset` at or beyond the true end of data returns **HTTP 400
  `INVALID_OFFSET`**, not an empty `200`. The real termination signal is
  `nextOffset == -1` on the **prior**, still-populated page. See
  `report_offset_overrun_400_INVALID_OFFSET.json`.
- `reportDate` must be passed back **exactly** as returned by
  `availableReconFiles` — a bare `MMDDYYYY` string with no separators
  (e.g. `"10202026"`). `"10-20-2026"`, `"2026-10-20"`, and `"10/20/2026"`
  were all tried and all rejected with the **same** error code,
  `NO_REPORT_AVAILABLE_FOR_GIVEN_DATE` — which is misleading, since the
  report exists; only the date string's shape was wrong. See
  `reportDate_format_requirement.json`.

## Field map

Every row is a flat object; which columns are populated depends entirely on
`Transaction Type`. Columns not listed in a given `Transaction Type`'s row
in the table below were observed empty-string (`""`), not absent — the key
is always present.

| Source field | Type | Example | Always non-empty? | Sign convention | Maps to |
|---|---|---|---|---|---|
| `Transaction Type` | string enum | `"Sale"`, `"Refund"`, `"Adjustment"`, `"PaymentSummary"` | Yes | n/a | Selects `is_refund` (true for `"Refund"`) and routing logic; not itself a stored column |
| `Amount Type` | string | `"Product Price"`, `"Shipping"`, `"Commission on Product"`, `"Commission on Shipping"`, `"Product tax"`, `"Product tax withheld"`, `"Shipping tax"`, `"Shipping tax withheld"`, `"Fee/Reimbursement"` | Only on `Sale`/`Refund`/`Adjustment` rows; empty on `PaymentSummary` | n/a | `source_category_code` in `settlement_category_mappings` — see proposed C-01 rows below |
| `Amount` | string (numeric) | `"5.04"`, `"-1.9"` | Only on `Sale`/`Refund`/`Adjustment` rows | **Varies by category — see table below. Not a single global convention.** | The settled amount for this category |
| `Contract Category` | string | `"Grocery"` | Only on `Sale`/`Refund`/`Adjustment` rows | n/a | Only one value (`"Grocery"`) was observed in this account's single report — **no unrecognized Contract Category was found**, because this seller apparently lists in one category only. Cannot confirm behavior for a multi-category seller from this evidence. |
| `Transaction Key` | string | `"2026_09_23_484971"` | Only on itemized rows | n/a | **Not unique per row** — see idempotency finding below. Part of the composite key, never the whole key alone. |
| `Purchase Order #` | string (numeric) | matches `purchaseOrderId` from the Orders API | Only on itemized rows | n/a | Joins back to `orders.walmart_order_id` |
| `Purchase Order line #` | string (numeric) | `"7"` | Only on itemized rows | n/a | Joins back to the specific order line |
| `Customer Order #` | string (numeric) | matches `customerOrderId` | Only on itemized rows | n/a | Not currently stored on `orders`; available if needed |
| `Transaction Description` | string, free text | `"Purchase"`, `"Return Refund"`, `"Walmart Shipping Label Service Charge"`, `"Deposited in HYPERWALLET account"` | Only on itemized/summary rows | n/a | **Free text, not a stable enum.** Useful for a human reading a suspense queue; not safe to key logic on. |
| `Transaction Reason Description` | string, free text | `"Missing Items from Box"` | Only on `Refund` rows, and not even all of those | n/a | Not mapped; informational only |
| `Store Id` | string | always empty in this sample | Column present, value never populated | n/a | Not usable — may be populated for multi-location sellers we have no evidence of |
| `Ship to City`/`State`/`Zipcode`/`country` | string | `"Sahuarita"`, `"AZ"`, `"85629"`, `"USA"` | Only on itemized rows | n/a | Not mapped; kept in fixtures as tax-jurisdiction evidence only |
| `Currency` | string (ISO 4217) | `"USD"` | **Only on the `PaymentSummary` row — empty on all 324 itemized rows in this report.** | n/a | **Cannot be read per-row.** A naive importer reading `Currency` from an itemized row gets an empty string, not `"USD"`. Currency must come from the store's own configured `reporting_currency_code`, never assumed from this column, consistent with the non-negotiable "currencies are never summed" / never defaulted rule already in this codebase. |
| `Period Start Date` / `Period End Date` | string (`MM/DD/YYYY`) | `"09/19/2026"` / `"09/26/2026"` | **Only on the `PaymentSummary` row** | n/a | The batch's own settlement period — candidate for `settlement_batches.settlement_period_start/end` |
| `Total Payable` | string (numeric) | `"1038.46"` | **Only on the `PaymentSummary` row** | Positive in the one example seen | Candidate for `settlement_batches.net_amount` — **a batch-level total, explicitly must never be added to order-level entries per `database-schema.md:789`** |
| `Commission Rule`, `Base Commission Rate`, `Commission Rate`, `Commission Saving`, `Commission Incentive Program`, `Original Commission` | strings | e.g. `"PRO-119491-3.0"` | Inconsistently populated, mostly empty in this sample | n/a | Not mapped; recorded as present in case a future category needs them |
| `Product Tax Code`, `Product Type`, `Partner GTIN`, `Partner Item Id`, `Partner Item Name`, `Item Condition` | strings | catalog metadata | Mostly populated on `Sale`/`Refund` rows | n/a | Not mapped — catalog data, not financial |
| `Fulfillment Type`, `Fulfillment Details`, `Shipping Method` | strings | `"Seller Fulfilled"`, `"Delivery"`, `"Marketplace standard"` | Inconsistently populated | n/a | Not mapped |

### Sign convention by (`Transaction Type`, `Amount Type`) — observed, not inferred

| Transaction Type | Amount Type | Sign observed | Count (this report) |
|---|---|---|---|
| Sale | Product Price | always positive | 61 |
| Sale | Shipping | always positive | 61 |
| Sale | Commission on Product | **always negative** | 61 |
| Sale | Commission on Shipping | **always negative** | 61 |
| Sale | Product tax | always positive | 19 |
| Sale | Product tax withheld | **always negative** (exact mirror of Product tax) | 19 |
| Sale | Shipping tax | always positive | 13 |
| Sale | Shipping tax withheld | **always negative** (exact mirror) | 13 |
| Refund | Product Price | **negative** | 1 |
| Refund | Shipping | **negative** | 1 |
| Refund | Commission on Product | **positive** (commission given back) | 1 |
| Refund | Commission on Shipping | **positive** (commission given back) | 1 |
| Adjustment | Fee/Reimbursement | **mixed** — both signs observed (range −28.83 to +0.87 across 12 rows) | 12 |

The asymmetry between Sale and Refund commission sign is a real,
confirmed finding, not a guess: on a sale, commission is negative (money
taken from the seller); on a refund of that same product/shipping charge,
the corresponding commission row is **positive** (money given back to the
seller). A single hardcoded "commission is always negative" assumption
will silently invert the amount on every refund.

`Adjustment`/`Fee/Reimbursement` mixing both signs in a single category,
with only free-text `Transaction Description` distinguishing the 12
observed rows (all 12 in this sample happened to share the description
`"Walmart Shipping Label Service Charge"`, but nothing about the schema
guarantees that stays true) is exactly the kind of "settlement batch
containing a category I do not recognise" case the brief asked to find —
see suspense recommendation below.

## Idempotency — the sharp, confirmed finding

**`Transaction Key` is not a unique per-row identifier.** Across 325 real
rows, 74 distinct `Transaction Key` values cover 324 non-empty-keyed rows
(the single `PaymentSummary` row has no key at all) — a many-to-one
relationship, not one-to-one. One key
(`"2026_09_23_484971"`) groups exactly 4 rows: `Product Price`, `Shipping`,
`Commission on Product`, `Commission on Shipping` — all four components of
one sale event on one order line.

The real, row-level unique identifier, **verified with zero collisions
across all 325 rows**, is the composite:

```
(Transaction Key, Transaction Type, Amount Type)
```

Any importer keying idempotency on `Transaction Key` alone will treat four
real, distinct financial rows as duplicates of each other and silently
drop three of them.

## Proposed C-02 rows (report catalogue) — proposals only, not applied

| report_type_code | report_version | parser_version | is_active | Notes |
|---|---|---|---|---|
| `reconreport` | `v1` | *(proposed)* `walmart-reconreport-v1-1` | true | Confirmed against a real report. Walmart's own name is "Recon report"; "settlement report" is the term used elsewhere in this codebase for the same thing — recommend the Owner pick one name and note the alias. |

I have not probed `reconFile` (the CSV/zip variant) or any later report
version. **No row is proposed for any version beyond `v1`** because I have
no evidence of one — Walmart's own catalogue in `availableReconFiles` only
ever returned `v1`-style dates for this account, and the `reconFileJson`
endpoint path hardcodes no version segment other than the `v1`-only
restriction already stated in the endpoint's own description ("available
only for v1 reports").

## Proposed C-01 rows (category mappings) — proposals only, not applied

Target fields, per `database-schema.md:737`, are exactly four:
`settled_revenue_amount`, `settled_walmart_fee`,
`settled_walmart_label_cost`, `settled_other_amount`.

| source_category_code | target_field | sign_convention | is_refund | Confidence / notes |
|---|---|---|---|---|
| `Product Price` (Sale) | `settled_revenue_amount` | `1` (as-is) | false | Confirmed. Directly observed, consistent sign across 61 rows. |
| `Shipping` (Sale) | `settled_revenue_amount` | `1` (as-is) | false | Confirmed. Shipping charged to the customer is revenue to the seller, consistent with the Orders API's own `chargeType: SHIPPING` being a normal, positive order-line charge. |
| `Commission on Product` (Sale) | `settled_walmart_fee` | `-1` (invert — source is already negative; inverting yields a positive fee amount if `settled_walmart_fee` is stored as a positive cost) | false | Confirmed sign and category; **the exact invert/as-is choice depends on whether `settled_walmart_fee` is stored as a positive cost or a negative deduction, which I don't have visibility into from this probe alone — flagging the decision point rather than guessing which convention the column uses.** |
| `Commission on Shipping` (Sale) | `settled_walmart_fee` | same open question as above | false | Same caveat. Both commission categories map to the **same single target field** — confirming Q3's answer that Walmart's fee is reported as multiple source categories collapsing to one target. |
| `Product tax` (Sale) | *(unmapped — see note)* | — | false | **Not proposed for mapping.** It is fully offset by `Product tax withheld` in every one of 19 observed pairs (net zero). Mapping only one side would misstate revenue or suspense; mapping both as separate entries that net to zero adds rows without changing any total. Recommend leaving both out of the four target fields entirely, on the understanding that `selling_amount` (from the Orders API) is already tax-exclusive and the settlement side simply confirms it nets to zero. **This is a design question for whoever implements commit logic, not something I can settle from a probe.** |
| `Product tax withheld` (Sale) | *(unmapped — see note above)* | — | false | Same reasoning |
| `Shipping tax` (Sale) | *(unmapped — see note above, same reasoning)* | — | false | Same reasoning |
| `Shipping tax withheld` (Sale) | *(unmapped — see note above, same reasoning)* | — | false | Same reasoning |
| `Product Price` (Refund) | `settled_revenue_amount` | `1` (as-is — already negative, which is correct for a refund reducing revenue) | **true** | Confirmed, 1 example |
| `Shipping` (Refund) | `settled_revenue_amount` | `1` (as-is) | **true** | Confirmed, 1 example |
| `Commission on Product` (Refund) | `settled_walmart_fee` | `1` (as-is — already positive, correctly reducing the fee on a refund) | **true** | Confirmed, 1 example. **Note the sign here is the opposite convention from the Sale-side row above** — this is exactly why `sign_convention` must be a property of the (category, is_refund) pair and not of the category alone. |
| `Commission on Shipping` (Refund) | `settled_walmart_fee` | `1` (as-is) | **true** | Confirmed, 1 example, same note |
| `Fee/Reimbursement` (Adjustment) | **NOT PROPOSED — recommend suspense** | — | false (not confirmed either way) | **This is the category I do not have confidence in, by design, per the brief's own instruction not to fill a gap with a plausible value.** All 12 observed rows carried the free-text description "Walmart Shipping Label Service Charge," which reads like `settled_walmart_label_cost` — but `Amount Type` alone does not distinguish a label charge from a chargeback, a WFS storage fee, or any other adjustment reason Walmart might report under the same category in a future batch, since `Transaction Description` is unconstrained free text, not an enumerated field. I recommend this category go to suspense (`settled_other_amount`, flagged) until either (a) a wider, multi-month sample confirms `Transaction Description` is stable for this `Amount Type`, or (b) Walmart documents the category's full meaning somewhere I have not found. Mapping it now on the strength of 12 same-worded rows would be exactly the "confident guess" the brief asked not to make. |
| `PaymentSummary` (batch header) | **Not a category mapping at all** | — | — | This `Transaction Type` is a batch-level summary row, not an itemized entry. It should be parsed separately into `settlement_batches` fields (`payout_date`/`settlement_period_start`/`settlement_period_end`/a batch total), never fed through `settlement_category_mappings`. A parser that tries to map `Amount Type == ""` will either crash or silently drop this row — either way, the batch total it carries must still be captured somewhere, since `database-schema.md:789` explicitly expects a batch-level `net_amount`. |

## What I did not confirm

- Whether a multi-category seller (electronics + grocery + apparel, say)
  produces additional `Contract Category` values, and whether any of those
  values change the `Amount Type` vocabulary. This account only sells
  groceries.
- The CSV/zip `reconFile` variant's actual column layout — not fetched;
  see the disclosed gap above.
- Report versions other than `v1` — none were available to this account.
- Whether `Transaction Key`'s uniqueness-within-report finding holds across
  different report dates, or whether keys are ever reused **across**
  reports (only one report date was pulled in full; two other dates were
  confirmed to exist via `availableReconFiles` but not fetched).
