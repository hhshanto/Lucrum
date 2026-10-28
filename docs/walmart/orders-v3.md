# Walmart Marketplace API — Orders, v3

Probed live against the production API (`https://marketplace.walmartapis.com`)
on 2026-10-28, using the real, newly-corrected `WALMART_CLIENT_ID` /
`WALMART_SECRET_KEY` pair. Authentication succeeded (`POST /v3/token`, 200,
Bearer token, `expires_in: 900`). 587 real orders were pulled across the
full 180-day window the API allows (`GET /v3/orders`, 3 pages,
`createdStartDate=2026-05-01` through `2026-10-28`), plus one single-order
fetch (`GET /v3/orders/{purchaseOrderId}`).

Every fixture referenced below is redacted: customer name, street address,
phone, tracking number, and the seller's own store identifiers
(`shipNode.id`, `shipNode.name`) and brand-name substrings embedded in SKUs
are replaced with `<redacted-...>` placeholders. City/state/postal code are
kept deliberately — low-sensitivity aggregate geography, and useful evidence
for the per-state tax behaviour described below. Verified by scanning every
committed fixture against all 612 distinct customer names and 52 distinct
phone numbers present anywhere in the full 587-order pull, not just the
orders used as fixtures.

Two response shapes exist for the same order data and they are **not**
identical — see "Two different envelopes" below.

## Endpoints probed

| Operation | Method/path | Fixture(s) |
|---|---|---|
| All orders | `GET /v3/orders` | `fixtures/walmart/orders/v3/list_page1_of_3_paginated.json`, `list_page2_of_3_paginated.json`, `list_page3_of_3_final_nextCursor_null.json`, `list_empty_result.json` |
| An order | `GET /v3/orders/{purchaseOrderId}` | `fixtures/walmart/orders/v3/get_single_order_by_id.json` |

## Two different envelopes (confirmed, not assumed)

- `GET /v3/orders` wraps each order under `list.elements.order[]`.
- `GET /v3/orders/{id}` wraps the **same** order shape under a top-level
  `order` key — not inside a list.

A single parser that only knows one of these shapes will silently return
`None`/`KeyError` on the other endpoint, not an error that points at the
cause.

## Field map — order header

| Source field | Type | Example | Always present? | Sign convention | Maps to |
|---|---|---|---|---|---|
| `purchaseOrderId` | string (numeric) | `"129123682349583"` | Yes | n/a | `orders.walmart_order_id` (and `walmart_purchase_order_id`, same value in every case observed — see note below) |
| `customerOrderId` | string (numeric) | `"200014936852105"` | Yes | n/a | No current column. 1:1 with `purchaseOrderId` in all 587 orders observed; docs say one customer order **can** map to several purchase orders, which did not occur in this sample. Not mapped — no evidence it's needed yet. |
| `customerEmailId` | string | `<redacted, was \"...@relay.walmart.com\">` | Yes | n/a | Not mapped. Already a Walmart relay address in every one of 587 orders (100% `@walmartone.com`/relay pattern) — not the real customer email in any case observed. |
| `orderType` | string enum | `"REGULAR"` | Only when `replacementInfo=true` is requested | n/a | Not mapped. `REPLACEMENT`/`PREORDER` values not observed in this sample. |
| `orderDate` | **integer, Unix epoch milliseconds, UTC** | `1787526510851` | Yes | n/a | `orders.order_placed_at_utc` (after ms→timestamptz conversion) |
| `buyerId` | string (uuid) | not observed non-null in this sample | No (`productInfo`/default fields) | n/a | Not mapped |
| `mart` | string | not observed in this sample | No | n/a | Not mapped |
| `isGuest` | boolean | not observed in this sample | No | n/a | Not mapped |
| `shippingInfo.phone` | string | `<redacted-phone>` | Yes | n/a | Not mapped — not in the documented `orders` schema |
| `shippingInfo.postalAddress.*` | object | see fixtures | Yes | n/a | Not mapped — not in the documented `orders` schema. City/state/zip kept in fixtures only as tax-jurisdiction evidence, not proposed as a stored column. |
| `shipNode.id` | string (numeric) | `<redacted-store-identifier>` | Yes in all orders observed | n/a | **This is the seller's own Walmart partner/seller identifier** (confirmed: identical value also appears embedded in every `nextCursor` string as `partnerId=`). Candidate for `stores.walmart_store_key`, **not confirmed** — I have not cross-checked it against how `walmart_store_key` is actually populated elsewhere in this codebase. Flagging, not asserting. |
| `shipNode.name` | string | `<redacted-store-name>` | Yes | n/a | The seller's own registered business name. Not a candidate for any `orders` column — recorded only because it had to be redacted. |

## Field map — order line (`orderLines.orderLine[]`)

| Source field | Type | Example | Always present? | Sign convention | Maps to |
|---|---|---|---|---|---|
| `lineNumber` | string (numeric) | `"1"`, `"2"` | Yes | n/a | No current column — see **BR-02 contradiction** below |
| `item.sku` | string | `"Ap-17699066821"` | Yes | n/a | `orders.sku` |
| `item.productName` | string | `"Sliced Dried Beef, Jarred Meat, 2.25 oz."` | Yes | n/a | `orders.item_description` |
| `item.condition` | string | `"New"` | Yes in sample | n/a | Not mapped |
| `item.imageUrl` | string (URL) | Walmart CDN image URL | Only with `productInfo=true` | n/a | Not mapped (public product image, not redacted) |
| `item.weight` | object | `{"value": "2.25", "unit": "Ounces"}` | Only with `productInfo=true` | n/a | Not mapped |
| `orderLineQuantity.amount` | **string** (numeric) | `"1"`, `"12"` | Yes | n/a | `orders.quantity` (cast from string) |
| `charges.charge[].chargeType` | string enum | `"PRODUCT"`, `"SHIPPING"` | Yes | n/a | Used to select which charge to sum into `selling_amount` — see below |
| `charges.charge[].chargeName` | string | `"ItemPrice"`, `"Shipping"` | Yes | n/a | See below |
| `charges.charge[].chargeAmount.amount` | number | `18.0` | Yes | **Positive.** Confirmed exclusive of tax — see Q2 below | `orders.selling_amount` component |
| `charges.charge[].chargeAmount.currency` | string (ISO 4217) | `"USD"` | Yes | n/a | `orders.currency_code` |
| `charges.charge[].tax` | object, nullable | `{"taxName": "Tax1", "taxAmount": {"currency": "USD", "amount": 0.99}}` | **No** — null on most charges in this sample; non-null on some `ItemPrice`/`Shipping` charges | Positive | Not currently mapped to any `orders` column (orders table has no tax field); relevant to settlement-side tax reconciliation only |
| `orderLineStatuses.orderLineStatus[].status` | string enum | `"Delivered"`, `"Shipped"`, `"Cancelled"` | Yes, one entry per line in every order observed (never more than one — see note) | n/a | `orders.fulfillment_status_code`, after mapping `Delivered→delivered`, `Shipped→shipped_in_transit`, `Cancelled→cancelled`. **`Acknowledged` is documented as a valid value but was never observed in 587 real orders.** |
| `orderLineStatuses.orderLineStatus[].trackingInfo.*` | object | carrier, tracking number, ship date | Only once shipped | n/a | `shipped_at` (from `trackingInfo.shipDateTime`) |
| `orderLineStatuses.orderLineStatus[].cancellationReason` | string, nullable | always `null` in this sample's cancelled orders | N/A | n/a | Not mapped — no cancellation reason text was ever populated, even on confirmed-cancelled lines |
| `refund` | object, nullable | see **Q4 answer** below | **No — null on 582 of 587 orders' line(s); non-null on 5** | n/a | Not currently mapped to any `orders` column (the schema correctly keeps refunds out of `orders` — see `walmart-store-manager-database-schema.md:468`) |
| `fulfillment.fulfillmentOption` | string | `"DELIVERY"` | Yes | n/a | Not mapped |
| `fulfillment.pickUpBy` | string | customer name (pickup orders) | Only on pickup orders | n/a | **Customer PII — redacted in every fixture.** Not mapped. |
| `statusDate` | integer, epoch ms | — | Yes | n/a | Not mapped |

## Answers to the required questions, with evidence

### 1. Which timestamp field is the customer's purchase time, and what timezone is it in?

`orderDate`. It is a Unix epoch in **milliseconds**, and epoch timestamps
are UTC by construction — there is no ambiguity to resolve.

**Contradiction with Walmart's own published OpenAPI schema, confirmed
empirically, not assumed:** the single-order endpoint
(`GET /v3/orders/{purchaseOrderId}`)'s own published schema says `orderDate`
is `type: string, format: date-time`, with the stated example
`"2025-06-15T06:00:00Z"`. The **actual, live response** is still a raw
integer epoch-ms value (`1787526510851`), identical in shape to the list
endpoint, not an ISO-8601 string. See
`fixtures/walmart/orders/v3/get_single_order_by_id.json`. A parser written
against Walmart's own published spec for this one endpoint would break on
the real response. This is Walmart's documentation being wrong about its
own API, not a contradiction in our specification — flagging because it
will surprise whoever builds the importer.

### 2. Is the selling amount inclusive or exclusive of buyer sales tax?

**Exclusive — `profit-v1`'s assumption is confirmed, not contradicted.**

Evidence: a real order line charged `chargeAmount.amount = 18.00` under
`chargeName: "ItemPrice"`, with a **separate**, sibling `tax` object on the
same charge: `{"taxName": "Tax1", "taxAmount": {"amount": 0.99}}`. The
`18.00` figure does not include the `0.99`; they are reported side-by-side
as two distinct numbers. The settlement/recon report (see
`settlement-recon-v1.md`) independently confirms this: it carries a
positive `"Product tax"` row and an exactly-offsetting negative `"Product
tax withheld"` row, netting to zero — Walmart collects the tax and remits
it itself; the seller's `"Product Price"` row never includes it.

### 3. Which fields carry Walmart fees, and are they separate categories or one aggregate?

Not answerable from the Orders API alone — the order payload carries no fee
field. Answered from the settlement report instead; see
`settlement-recon-v1.md`. Short answer: **separate**, at minimum
`Commission on Product` and `Commission on Shipping`.

### 4. How does a refund appear?

**A separate, nullable `refund` object on the order line — a sibling of
`charges` and `orderLineStatuses`, not a modification of either.**

Confirmed on 5 of 587 real orders (fixtures: `order_refund_single_charge.json`,
`order_refund_partial_product_and_shipping.json`, `..._2.json`,
`order_refund_with_tax_withholding.json`). In every one of the 5:

- `orderLineStatuses` still shows `"Delivered"` or `"Shipped"` — **the
  fulfillment status is never changed by a refund.** This matches
  `database-schema.md:468` exactly ("a refunded order keeps its own
  fulfillment state, which is usually `delivered`").
- `refund.refundCharges.refundCharge[]` is an **array** — on 2 of the 5
  orders it carries two entries (one `PRODUCT`, one `SHIPPING`), i.e. **a
  genuine partial-refund case where more than one charge component was
  refunded on the same line** (the "several partial refunds" case the brief
  asked for, found at the charge level — I did not find an order with
  multiple refund **events over time** on the same line in this 587-order,
  ~4-month sample; the schema shape (an array with no per-entry timestamp)
  does not obviously distinguish "two charges refunded in one event" from
  "two separate refund events", which is worth flagging on its own).
- `refund.refundId` and `refund.refundComments` were `null` in **all 5**
  observed cases — present in the schema, never populated in this sample.
  Their purpose is unconfirmed.
- **The refunded charge's `chargeName` does not always match the original
  charge's `chargeName`.** Original: `"ItemPrice"` / `"Shipping"`. Refund:
  `"Product"` / `"ShippingNoThreshold"`. A parser that joins refund rows
  back to original charges by matching `chargeName` exactly will fail to
  match real data. Use `chargeType` (`PRODUCT`/`SHIPPING`) instead, which
  was stable in all 5 cases.
- One refund (`order_refund_with_tax_withholding.json`) carries a non-null
  `tax` **and** a non-null `taxAndOtherFees` object on the refunded charge,
  both negative, both with the **same** amount, but `taxName` /
  `taxAndOtherFeesName` is a bare UUID-looking string
  (`"120d64b7-a390-4a89-9203-62d9ec4580a2"`), not `"Tax1"`/`"Tax2"` as seen
  on ordinary sale charges. I do not know what this UUID identifies. Not
  guessed at — flagged as the "row that made the parser hesitate."

### 5. Is there a stable per-transaction identifier for idempotency?

**Within Orders: yes — `purchaseOrderId` was unique across all 587 orders
pulled, and 1:1 with `customerOrderId` in every case.** `(store_id,
walmart_order_id)` as the documented unique constraint is sound against
this evidence.

**Within the settlement report: no single field is safe alone — see the
sharp finding in `settlement-recon-v1.md`.** `Transaction Key` looks
unique at a glance but is **not**: it groups every row belonging to one
order-line event (e.g. `Product Price` + `Shipping` + two commission rows
+ two tax rows can all share one `Transaction Key`). The real per-row
unique key, confirmed empirically with zero collisions across 325 real
rows, is the composite **(`Transaction Key`, `Transaction Type`, `Amount
Type`)**.

### 6. Are order identifiers unique across stores, or only within one?

**Not empirically testable from this session — only one store/seller
credential was available.** `purchaseOrderId` is Walmart-assigned and
confirmed unique within this one store's 587 orders; I have no second
store's credentials to confirm or refute global uniqueness. Recording this
as **unconfirmed**, not as "probably yes" — `database-schema.md:1055`
already takes the conservative position (`orders (store_id,
walmart_order_id)` scoped to the store, not global, "two stores may
legitimately receive the same external order identifier"), and nothing in
this session's evidence contradicts that conservative choice. I have no
evidence either way for the stronger claim.

### 7. How far back does order history go, and how much of it would a new store pull?

**Exactly 180 days, silently clamped — confirmed empirically, not read
from documentation alone.** Walmart's own docs state orders older than 180
days aren't retrievable. I tested the actual clamp behaviour directly:
requesting `createdStartDate=2024-01-01` (well outside the window) returned
**the same 587 orders**, with the `nextCursor` showing the server had
silently rewritten the start date to `2026-05-01T09:23:03.248Z` — exactly
180 days back from the moment of the call. **No error, no warning.** A new
store's first import will only ever receive the trailing 180 days of
history regardless of what date range it requests, and the API gives no
signal that anything was clamped. If `managed_from` for a new store is set
further back than 180 days before onboarding, the gap between
`managed_from` and the 180-day floor can **never** be backfilled through
this endpoint — there is no deeper history API in the Orders family. This
is a hard ceiling worth stating plainly in any onboarding documentation.

## Contradiction flagged for the Owner — not resolved here

**At least one real order carries two order lines with two distinct SKUs
under a single `purchaseOrderId`.** Fixture:
`fixtures/walmart/orders/v3/order_MULTI_SKU_CONTRADICTS_BR-02.json`
(purchase order id redacted per policy, but the two-line, two-SKU shape is
preserved exactly as returned).

This directly contradicts BR-02 ("An order carries exactly one SKU... There
are no order line items"), which is treated as settled and is
guardrail-enforced (`SC-09`, `DB-07`, `check_invariants.py:234`,
`002_selftest.sql:103`) — "No `order_items` or line-item table was
introduced" is a release checklist item.

Rate observed: 1 multi-line order out of 587 (~0.17%) over a ~4-month
window for this one store. Rare, but real, and the Walmart API does not
forbid it — nothing about the schema suggests this was an error on
Walmart's side; it is an ordinary, well-formed order with two line items.

**I have not resolved this.** Options for the Owner to choose between
(not a recommendation, just the shape of the decision):
- Import such an order as **two separate `orders` rows** sharing the same
  `walmart_order_id`, which breaks the stated `(store_id,
  walmart_order_id)` **unique** constraint as currently documented.
- Import only the **first** line and silently drop the second, which
  understates revenue, quantity, and cost for that order.
- Reject the whole order at import time as unsupported, visibly, and leave
  it for manual handling — consistent with the project's "fails visibly
  rather than guessing" philosophy elsewhere, but no AC currently describes
  this path for orders (recall the existing Decision Log entry: "a
  supported order payload" is undefined for Orders, unlike Reports where
  C-02 governs it).

This is not a hypothetical edge case invented for discussion; it is a real
order currently sitting in a real seller account.
