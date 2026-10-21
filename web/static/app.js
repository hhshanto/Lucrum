let supabaseClient;
let stores = [];
let catalogExport = [];
let adminStores = new Set();
let editing = null;
let activeTab = "dashboard";
const loaded = new Set();

const loginSection = document.getElementById("login-section");
const dashboardSection = document.getElementById("dashboard-section");
const storePickerSection = document.getElementById("store-picker-section");
const storeCards = document.getElementById("store-cards");
const switchStoreBtn = document.getElementById("switch-store-btn");
const logoutBtn = document.getElementById("logout-btn");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const storeSelect = document.getElementById("store-select");
const periodSelect = document.getElementById("period-select");
const profitTableBody = document.querySelector("#profit-table tbody");
const catalogTableBody = document.querySelector("#catalog-table tbody");
const purchasesTableBody = document.querySelector("#purchases-table tbody");
const damagedTableBody = document.querySelector("#damaged-table tbody");
const inventoryTableBody = document.querySelector("#inventory-table tbody");
const adjustmentsTableBody = document.querySelector("#adjustments-table tbody");
const returnsTableBody = document.querySelector("#returns-table tbody");
const orderDetailsTableBody = document.querySelector("#order-details-table tbody");
const orderDetailsTable = document.getElementById("order-details-table");
const pendingOrdersTableBody = document.querySelector("#pending-orders-table tbody");
const pendingOrdersTable = document.getElementById("pending-orders-table");
const countedSummary = document.getElementById("counted-summary");
const pendingSummary = document.getElementById("pending-summary");
const showFeesToggle = document.getElementById("show-fees-toggle");
const monthlyProfitTableBody = document.querySelector("#monthly-profit-table tbody");
const teamTableBody = document.querySelector("#team-table tbody");
const teamForm = document.getElementById("team-form");
const teamStatus = document.getElementById("team-status");
const storesTableBody = document.querySelector("#stores-table tbody");
const storeForm = document.getElementById("store-form");
const storeStatus = document.getElementById("store-status");
const orderModal = document.getElementById("order-modal");
const orderModalTitle = document.getElementById("order-modal-title");
const orderModalBody = document.getElementById("order-modal-body");
const orderModalClose = document.getElementById("order-modal-close");
const editModal = document.getElementById("edit-modal");
const editModalTitle = document.getElementById("edit-modal-title");
const editModalFields = document.getElementById("edit-modal-fields");
const editModalError = document.getElementById("edit-modal-error");
const editModalForm = document.getElementById("edit-modal-form");
const editModalClose = document.getElementById("edit-modal-close");
const editModalDelete = document.getElementById("edit-modal-delete");

// Hand-entered tables an admin may correct or remove. Synced tables (orders,
// order_lines, products, settlement_lines) are deliberately absent -- editing
// them would just be overwritten by the next sync.
const EDITABLE = {
  purchases: {
    label: "purchase",
    fields: [
      { key: "purchase_date", label: "Purchase date", type: "date", required: true },
      { key: "product_name", label: "Product name", type: "text" },
      { key: "sku", label: "My SKU", type: "text", required: true },
      { key: "source", label: "Supplier / Source", type: "text" },
      { key: "product_link", label: "Product link", type: "text" },
      { key: "order_number", label: "Order #", type: "text" },
      { key: "quantity", label: "Qty", type: "number", required: true, step: "1" },
      { key: "unit_cost", label: "Unit cost", type: "number", required: true, step: "0.01" },
      { key: "sales_tax", label: "Sales tax", type: "number", step: "0.01" },
      { key: "shipping", label: "Shipping", type: "number", step: "0.01" },
      { key: "status", label: "Status", type: "text" },
      { key: "notes", label: "Notes", type: "text" },
    ],
    invalidates: ["purchases", "inventory", "dashboard", "order-details", "profit"],
  },
  damaged_goods: {
    label: "damaged entry",
    fields: [
      { key: "damaged_date", label: "Date", type: "date", required: true },
      { key: "sku", label: "SKU", type: "text", required: true },
      { key: "quantity", label: "Qty", type: "number", required: true, step: "1" },
      { key: "reason", label: "Reason", type: "text" },
      { key: "notes", label: "Notes", type: "text" },
    ],
    invalidates: ["damaged", "inventory"],
  },
  inventory_adjustments: {
    label: "adjustment",
    fields: [
      { key: "adjusted_date", label: "Date", type: "date", required: true },
      { key: "sku", label: "SKU", type: "text", required: true },
      { key: "quantity_delta", label: "Quantity (+ or -)", type: "number", required: true, step: "1" },
      { key: "reason", label: "Reason", type: "text" },
      { key: "notes", label: "Notes", type: "text" },
    ],
    invalidates: ["inventory"],
  },
};

const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");

const syncTableBody = document.querySelector("#sync-table tbody");
const unmappedTableBody = document.querySelector("#unmapped-table tbody");
const feeMapTableBody = document.querySelector("#feemap-table tbody");
const calibrationTableBody = document.querySelector("#calibration-table tbody");
const periodsTableBody = document.querySelector("#periods-table tbody");

const purchaseForm = document.getElementById("purchase-form");
const damagedForm = document.getElementById("damaged-form");
const adjustmentForm = document.getElementById("adjustment-form");

const refreshBtn = document.getElementById("refresh-btn");
const refreshFeesBtn = document.getElementById("refresh-fees-btn");
const syncStatus = document.getElementById("sync-status");
const errorBanner = document.getElementById("error-banner");

const exportBtn = document.getElementById("export-costs-btn");
const importBtn = document.getElementById("import-costs-btn");
const importFile = document.getElementById("import-costs-file");
const importStatus = document.getElementById("import-status");
const costWarning = document.getElementById("cost-warning");

const exportPurchasesBtn = document.getElementById("export-purchases-btn");
const importPurchasesBtn = document.getElementById("import-purchases-btn");
const importPurchasesFile = document.getElementById("import-purchases-file");
const purchaseImportStatus = document.getElementById("purchase-import-status");

// ---- Tab module registry -------------------------------------------------
// Each tab declares the datasets it needs, the table bodies to show a loading
// state in, and a render function. Adding a tab = one entry here. A tab's data
// is fetched only when the tab is opened (lazy), and cached until invalidated.
const modules = {
  dashboard: {
    // ledger_sku_monthly replaces profit_by_sku: the figures come from
    // ledger_entries, so they cannot change after the fact, and each row
    // carries uncosted_lines / is_complete so the UI can say what it knows.
    tables: ["ledger_sku_monthly", "products"],
    skeleton: [[profitTableBody, 9]],
    render: (d) => renderDashboard(d.ledger_sku_monthly, d.products),
  },
  products: {
    tables: ["products", "product_costs"],
    skeleton: [[catalogTableBody, 7]],
    render: (d) => renderProducts(d.products, d.product_costs),
  },
  purchases: {
    tables: ["purchases", "products"],
    skeleton: [[purchasesTableBody, 15]],
    render: (d) => renderPurchases(d.purchases, d.products),
  },
  damaged: {
    tables: ["damaged_goods", "products"],
    skeleton: [[damagedTableBody, 7]],
    render: (d) => renderDamaged(d.damaged_goods, d.products),
  },
  inventory: {
    tables: ["inventory_levels", "inventory_adjustments"],
    skeleton: [[inventoryTableBody, 8], [adjustmentsTableBody, 6]],
    render: (d) => renderInventory(d.inventory_levels, d.inventory_adjustments),
  },
  returns: {
    // ledger_returns, not returns: the legacy view looks for refunds in
    // order_lines.status, which Walmart never sets -- a refunded line still
    // reports as Delivered. It has been empty since it was written.
    tables: ["ledger_returns"],
    skeleton: [[returnsTableBody, 6]],
    render: (d) => renderReturns(d.ledger_returns),
  },
  "order-details": {
    // ledger_order_details, not order_details: the legacy view still recomputes
    // profit from current state, so the Orders tab was disagreeing with
    // Dashboard and Profit. Same column names, so this is a drop-in swap.
    tables: ["ledger_order_details"],
    skeleton: [[orderDetailsTableBody, 21]],
    render: (d) => renderOrderDetails(d.ledger_order_details),
  },
  profit: {
    tables: ["ledger_summary"],
    skeleton: [[monthlyProfitTableBody, 9]],
    render: (d) => renderMonthlyProfit(d.ledger_summary),
  },
  // Team reads through our own server: listing emails and creating logins needs
  // the service key, which must never reach the browser.
  team: {
    skeleton: [[teamTableBody, 4]],
    load: () => apiFetch("/admin/users"),
    render: (d) => renderTeam(d),
  },
  stores: {
    skeleton: [[storesTableBody, 5]],
    load: () => apiFetch("/admin/stores"),
    render: (d) => renderStores(d),
  },
  // Health surfaces. These read views directly through RLS rather than going
  // via /admin, because none of them need the service key.
  sync: {
    tables: ["sync_health"],
    skeleton: [[syncTableBody, 6]],
    render: (d) => renderSyncHealth(d.sync_health),
  },
  fees: {
    tables: ["unmapped_amount_types", "fee_category_map", "fee_calibration"],
    skeleton: [[unmappedTableBody, 5], [feeMapTableBody, 3], [calibrationTableBody, 6]],
    render: (d) => renderFeeMap(d.unmapped_amount_types, d.fee_category_map, d.fee_calibration),
  },
  periods: {
    tables: ["period_close_readiness"],
    skeleton: [[periodsTableBody, 6]],
    render: (d) => renderPeriods(d.period_close_readiness),
  },
};

// order-details spans 22 columns (incl. the serial #) across both order tables.
modules["order-details"].skeleton = [[orderDetailsTableBody, 22], [pendingOrdersTableBody, 22]];

async function init() {
  const config = await fetch("/config").then((r) => r.json());
  supabaseClient = window.supabase.createClient(config.supabaseUrl, config.supabaseAnonKey);

  supabaseClient.auth.onAuthStateChange((_event, session) => {
    showSession(session);
  });

  const { data } = await supabaseClient.auth.getSession();
  showSession(data.session);
}

function showSession(session) {
  if (session) {
    loginSection.hidden = true;
    logoutBtn.hidden = false;
    loadDashboard(); // decides: store chooser, or straight into the only store
  } else {
    loginSection.hidden = false;
    dashboardSection.hidden = true;
    storePickerSection.hidden = true;
    logoutBtn.hidden = true;
    switchStoreBtn.hidden = true;
  }
}

switchStoreBtn.addEventListener("click", showStorePicker);

// With several stores (different owners), make the choice explicit up front
// rather than silently landing in whichever happens to be first.
function showStorePicker() {
  storeCards.innerHTML = "";
  for (const store of stores) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "store-card";

    const name = document.createElement("h3");
    name.textContent = store.name;

    const meta = document.createElement("div");
    meta.className = "store-card-meta";
    const role = document.createElement("span");
    const isAdmin = adminStores.has(store.store_id);
    role.className = "badge " + (isAdmin ? "badge-green" : "badge-gray");
    role.textContent = isAdmin ? "admin" : "member";
    meta.appendChild(role);
    if (!store.active) {
      const off = document.createElement("span");
      off.className = "badge badge-amber";
      off.textContent = "sync off";
      meta.appendChild(off);
    }

    card.append(name, meta);
    card.addEventListener("click", () => enterStore(store.store_id));
    storeCards.appendChild(card);
  }
  storePickerSection.hidden = false;
  dashboardSection.hidden = true;
  switchStoreBtn.hidden = true;
}

async function enterStore(storeId) {
  storeSelect.value = storeId;
  updateAdminContext();
  storePickerSection.hidden = true;
  dashboardSection.hidden = false;
  switchStoreBtn.hidden = stores.length < 2;
  loaded.clear();
  await buildPeriodOptions(); // months depend on the store's own history
  return loadTab(activeTab, true);
}

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError.hidden = true;

  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;

  const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
  if (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
  }
});

logoutBtn.addEventListener("click", async () => {
  await supabaseClient.auth.signOut();
});

storeSelect.addEventListener("change", async () => {
  updateAdminContext();
  await buildPeriodOptions();
  invalidateAll();
});

periodSelect.addEventListener("change", invalidateAll);

refreshBtn.addEventListener("click", syncOrders);
refreshFeesBtn.addEventListener("click", syncSettlement);
showFeesToggle.addEventListener("change", () => {
  const hide = !showFeesToggle.checked;
  orderDetailsTable.classList.toggle("hide-fees", hide);
  pendingOrdersTable.classList.toggle("hide-fees", hide);
});

// Click a row to keep it highlighted while scrolling the wide table; double-click
// for every field in one popup. Applied to both order tables.
function wireOrderRowInteractions(tbody) {
  tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || tr.parentElement !== tbody) return;
    if (tr.querySelector(".empty-cell")) return; // skip the empty / loading row
    tr.classList.toggle("row-selected");
  });
  tbody.addEventListener("dblclick", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || tr.parentElement !== tbody || !tr._order) return;
    openOrderModal(tr._order);
  });
}
wireOrderRowInteractions(orderDetailsTableBody);
wireOrderRowInteractions(pendingOrdersTableBody);

orderModalClose.addEventListener("click", closeOrderModal);
orderModal.addEventListener("click", (e) => {
  if (e.target === orderModal) closeOrderModal();
});
editModalClose.addEventListener("click", closeEditModal);
editModal.addEventListener("click", (e) => {
  if (e.target === editModal) closeEditModal();
});
editModalForm.addEventListener("submit", saveEdit);
editModalDelete.addEventListener("click", deleteEditing);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!editModal.hidden) closeEditModal();
  else if (!orderModal.hidden) closeOrderModal();
});

// Admin actions show only where the signed-in user is an admin of that store.
function updateAdminContext() {
  const active = storeSelect.value ? adminStores.has(storeSelect.value) : adminStores.size > 0;
  document.body.classList.toggle("is-admin", active);
}

function openEditModal(tableKey, row) {
  const cfg = EDITABLE[tableKey];
  if (!cfg) return;
  editing = { table: tableKey, row };
  editModalTitle.textContent = `Edit ${cfg.label}`;
  editModalError.hidden = true;
  editModalFields.innerHTML = "";
  for (const f of cfg.fields) {
    const label = document.createElement("label");
    label.textContent = f.label;
    const input = document.createElement("input");
    input.type = f.type === "number" ? "number" : f.type === "date" ? "date" : "text";
    if (f.step) input.step = f.step;
    if (f.required) input.required = true;
    input.dataset.key = f.key;
    const v = row[f.key];
    input.value = v === null || v === undefined ? "" : f.type === "date" ? String(v).slice(0, 10) : String(v);
    label.appendChild(input);
    editModalFields.appendChild(label);
  }
  editModal.hidden = false;
}

function closeEditModal() {
  editModal.hidden = true;
  editing = null;
}

async function saveEdit(e) {
  e.preventDefault();
  if (!editing) return;
  const cfg = EDITABLE[editing.table];
  const patch = {};
  for (const f of cfg.fields) {
    const input = editModalFields.querySelector(`[data-key="${f.key}"]`);
    const raw = (input.value || "").trim();
    if (f.type === "number") patch[f.key] = raw === "" ? 0 : parseFloat(raw);
    else patch[f.key] = raw === "" ? null : raw;
  }
  const { error } = await supabaseClient.from(editing.table).update(patch).eq("id", editing.row.id);
  if (error) {
    editModalError.textContent = error.message;
    editModalError.hidden = false;
    return;
  }
  const inv = cfg.invalidates;
  closeEditModal();
  await invalidate(...inv);
}

async function deleteEditing() {
  if (!editing) return;
  const cfg = EDITABLE[editing.table];
  if (!confirm(`Delete this ${cfg.label}? This can't be undone.`)) return;
  const { error } = await supabaseClient.from(editing.table).delete().eq("id", editing.row.id);
  if (error) {
    editModalError.textContent = error.message;
    editModalError.hidden = false;
    return;
  }
  const inv = cfg.invalidates;
  closeEditModal();
  await invalidate(...inv);
}

function closeOrderModal() {
  orderModal.hidden = true;
}

function openOrderModal(row) {
  const wh = (Number(row.order_qty) || 0) * (Number(row.warehouse_cost) || 0);
  const sections = [
    ["Order", [
      ["Order date", fmtDate(row.order_date)],
      ["Customer order ID", row.customer_order_id ?? ""],
      ["SKU / UPC", row.sku ?? ""],
      ["Product", row.product_name ?? ""],
      ["Status", row.order_status ?? ""],
      ["Customer", customerDetails(row) || "-"],
      ["Order qty", fmtNum(row.order_qty)],
    ]],
    ["Selling", [
      ["Unit selling price", fmtMoney(row.unit_selling_price)],
      ["Total selling price", fmtMoney(row.total_selling_price)],
      ["Extra shipping (unit)", fmtMoney(row.extra_shipping)],
      ["Total extra shipping", fmtMoney(row.total_extra_shipping)],
    ]],
    ["Costs", [
      ["Unit purchase price", fmtMoney(row.unit_purchase_price)],
      ["Total purchase price", fmtMoney(row.total_purchase_price)],
      ["Warehouse cost", fmtMoney(wh)],
      ["Total cost", fmtMoney(row.total_cost)],
    ]],
    ["Fees", [
      ["Source", row.fee_source === "settlement" ? "Walmart settlement (actual)"
                : row.fee_source === "manual" ? "Entered by you (pending settlement)"
                : "None yet - not settled, nothing entered"],
      ["Referral fees", fmtMoney(row.walmart_fees)],
      ["Label fees", fmtMoney(row.wfs_label_fees)],
      ["Refund fees", fmtMoney(row.refund_fees)],
      ["Extra service fees", fmtMoney(row.extra_service_fees)],
    ]],
    ["Profit", [
      ["Net profit", fmtMoney(row.net_profit), signClass(row.net_profit)],
      ["Margin", row.margin_pct != null ? `${fmtNum(row.margin_pct)}%` : "", signClass(row.margin_pct)],
    ]],
  ];

  orderModalTitle.textContent = row.product_name || row.sku || "Order";
  orderModalBody.innerHTML = "";
  for (const [title, pairs] of sections) {
    const h = document.createElement("h4");
    h.textContent = title;
    orderModalBody.appendChild(h);
    const dl = document.createElement("dl");
    dl.className = "modal-grid";
    for (const [label, value, cls] of pairs) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      if (cls) dd.classList.add(cls);
      dl.append(dt, dd);
    }
    orderModalBody.appendChild(dl);
  }

  // Until Walmart settles this order, let the user type the real referral/label
  // fees straight off Walmart's pending transaction page. Settlement overrides
  // these automatically once it lands.
  if (row.fee_source !== "settlement") {
    const heading = document.createElement("h4");
    heading.textContent = "Enter fees (pending settlement)";
    orderModalBody.appendChild(heading);

    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "Copy the Referral fee and Label fee from Walmart's pending transaction, as positive amounts. Real settlement data replaces these automatically when it arrives.";
    orderModalBody.appendChild(note);

    const wrap = document.createElement("div");
    wrap.className = "edit-fields";
    const makeInput = (labelText, current) => {
      const label = document.createElement("label");
      label.textContent = labelText;
      const input = document.createElement("input");
      input.type = "number";
      input.step = "0.01";
      input.min = "0";
      const n = Math.abs(Number(current) || 0);
      input.value = n ? n.toFixed(2) : "";
      label.appendChild(input);
      wrap.appendChild(label);
      return input;
    };
    const refInput = makeInput("Referral fee ($)", row.walmart_fees);
    const labInput = makeInput("Label fee ($)", row.wfs_label_fees);
    orderModalBody.appendChild(wrap);

    const err = document.createElement("p");
    err.className = "error";
    err.hidden = true;
    orderModalBody.appendChild(err);

    const actions = document.createElement("div");
    actions.className = "modal-actions";
    actions.appendChild(document.createElement("span"));
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save fees";
    save.addEventListener("click", () => saveManualFees(row, refInput.value, labInput.value, err));
    actions.appendChild(save);
    orderModalBody.appendChild(actions);
  }

  orderModal.hidden = false;
}

async function saveManualFees(row, referral, label, errEl) {
  const { error } = await supabaseClient.from("manual_order_fees").upsert({
    store_id: row.store_id,
    customer_order_id: row.customer_order_id,
    sku: row.sku,
    referral_fee: parseFloat(referral) || 0,
    label_fee: parseFloat(label) || 0,
    updated_at: new Date().toISOString(),
  });
  if (error) {
    console.error(error);
    errEl.textContent = error.message;
    errEl.hidden = false;
    return;
  }
  closeOrderModal();
  await invalidate("order-details", "profit");
}

for (const btn of tabButtons) {
  btn.addEventListener("click", () => {
    for (const b of tabButtons) b.classList.remove("active");
    btn.classList.add("active");

    activeTab = btn.dataset.tab;
    for (const panel of tabPanels) {
      panel.hidden = panel.id !== `tab-${activeTab}`;
    }
    loadTab(activeTab);
  });
}

// ---- Period filter -------------------------------------------------------
// Which column the Period filter applies to, per table. A table is only
// filterable if its rows are EVENTS that happened on a date.
//
// null = never filtered, deliberately:
//   inventory_levels -- on-hand stock is a running total, not a July event.
//     "July's inventory" would mean replaying every movement up to July 31,
//     which is a different question than the one this filter answers.
//   products / product_costs -- a catalog entry and its cost are current state.
//   monthly_profit -- the Profit tab IS the cross-month comparison; filtering it
//     to one month would leave a single row and nothing to compare it to.
const DATE_COLUMN = {
  ledger_order_details: "order_date",
  profit_by_sku: "month",
  ledger_sku_monthly: "month",
  purchases: "purchase_date",
  damaged_goods: "damaged_date",
  inventory_adjustments: "adjusted_date",
  ledger_returns: "order_date",
  products: null,
  product_costs: null,
  inventory_levels: null,
  monthly_profit: null,
  ledger_summary: null,
  // Health views describe the whole store, not a period. Filtering them to the
  // selected month would hide the very staleness they exist to report.
  sync_health: null,
  unmapped_amount_types: null,
  fee_category_map: null,
  fee_calibration: null,
  period_close_readiness: null,
};

// "2026-07" -> { from: "2026-07-01", to: "2026-08-01" }; null for "All time".
// Half-open [from, to) so an order_date timestamptz late on the 31st still lands
// inside the month, which a <= "2026-07-31" bound would drop.
function periodRange() {
  const v = periodSelect.value;
  if (!v || v === "all") return null;
  const [y, m] = v.split("-").map(Number);
  const ny = m === 12 ? y + 1 : y;
  const nm = m === 12 ? 1 : m + 1;
  return {
    from: `${y}-${String(m).padStart(2, "0")}-01`,
    to: `${ny}-${String(nm).padStart(2, "0")}-01`,
  };
}

// Months from the store's first activity to now. Every month is listed, not just
// ones with rows: a month where you sold nothing is a real answer, and a gap in
// the dropdown would read as a bug.
async function buildPeriodOptions() {
  const keep = periodSelect.value;
  let earliest = null;

  try {
    const [orders, purchases] = await Promise.all([
      supabaseClient.from("orders").select("order_date")
        .eq("store_id", storeSelect.value)
        .not("order_date", "is", null)
        .order("order_date", { ascending: true }).limit(1),
      supabaseClient.from("purchases").select("purchase_date")
        .eq("store_id", storeSelect.value)
        .order("purchase_date", { ascending: true }).limit(1),
    ]);
    const dates = [orders.data?.[0]?.order_date, purchases.data?.[0]?.purchase_date]
      .filter(Boolean)
      .map((d) => String(d).slice(0, 7));
    if (dates.length) earliest = dates.sort()[0];
  } catch (_) {
    earliest = null; // no history yet, or the read failed -- "All time" still works
  }

  const options = [{ value: "all", label: "All time" }];
  if (earliest) {
    const [ey, em] = earliest.split("-").map(Number);
    const now = new Date();
    let y = now.getFullYear();
    let m = now.getMonth() + 1;
    let guard = 0; // a bad/ancient date shouldn't spin out a 10k-option dropdown
    while ((y > ey || (y === ey && m >= em)) && guard++ < 120) {
      const value = `${y}-${String(m).padStart(2, "0")}`;
      options.push({ value, label: fmtMonth(`${value}-01`) });
      m -= 1;
      if (m === 0) { m = 12; y -= 1; }
    }
  }

  periodSelect.innerHTML = "";
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label;
    periodSelect.appendChild(el);
  }
  // Keep the chosen month when switching stores, but only if it still exists.
  periodSelect.value = options.some((o) => o.value === keep) ? keep : "all";
}

// ---- Loading / invalidation ---------------------------------------------
async function runQuery(table) {
  let q = supabaseClient.from(table).select("*");
  if (storeSelect.value) q = q.eq("store_id", storeSelect.value);
  const range = periodRange();
  const dateColumn = DATE_COLUMN[table];
  if (range && dateColumn) q = q.gte(dateColumn, range.from).lt(dateColumn, range.to);
  const { data, error } = await q;
  if (error) throw error;
  return data;
}

async function loadTab(id, force = false) {
  const mod = modules[id];
  if (!mod) return;
  if (loaded.has(id) && !force) return;
  clearError();
  for (const [tbody, colspan] of mod.skeleton) {
    tbody.innerHTML = "";
    renderEmpty(tbody, colspan, "Loading...");
  }
  try {
    // A module either declares Supabase tables, or brings its own loader.
    let data;
    if (mod.load) {
      data = await mod.load();
    } else {
      const results = await Promise.all(mod.tables.map((t) => runQuery(t)));
      data = {};
      mod.tables.forEach((t, i) => { data[t] = results[i]; });
    }
    mod.render(data);
    loaded.add(id);
  } catch (err) {
    console.error(err);
    showError(`Couldn't load data: ${err.message || err}`);
    for (const [tbody, colspan] of mod.skeleton) {
      tbody.innerHTML = "";
      renderEmpty(tbody, colspan, "Couldn't load - see the message above.");
    }
  }
}

// Mark tabs stale so they reload next time they're opened; reload the active
// tab immediately if it's among them.
function invalidate(...ids) {
  for (const id of ids) loaded.delete(id);
  if (ids.includes(activeTab)) return loadTab(activeTab, true);
  return Promise.resolve();
}

function invalidateAll() {
  loaded.clear();
  return loadTab(activeTab, true);
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function setSyncStatus(message, tone) {
  if (!message) {
    syncStatus.hidden = true;
    syncStatus.textContent = "";
    return;
  }
  syncStatus.hidden = false;
  syncStatus.textContent = message;
  syncStatus.className = "sync-status" + (tone ? ` sync-${tone}` : "");
}

function renderEmpty(tbody, colspan, message) {
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = colspan;
  td.className = "empty-cell";
  td.textContent = message;
  tr.appendChild(td);
  tbody.appendChild(tr);
}

async function syncOrders() {
  const storeId = requireStore();
  if (!storeId) return;
  clearError();
  refreshBtn.disabled = true;
  setSyncStatus("Syncing orders from Walmart...", "busy");
  try {
    const { data: { session } } = await supabaseClient.auth.getSession();
    if (!session) {
      setSyncStatus("", null);
      showError("Your session has expired - sign in again.");
      return;
    }
    const resp = await fetch(`/sync/orders?days=30&store_id=${encodeURIComponent(storeId)}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    let payload = {};
    try {
      payload = await resp.json();
    } catch (_) {
      // non-JSON response; fall through to status-based messaging
    }
    if (!resp.ok) {
      setSyncStatus("", null);
      showError(payload.detail || `Sync failed (HTTP ${resp.status}).`);
    } else if (!payload.ok) {
      setSyncStatus("Synced with warnings", "warn");
      showError("Some stores could not be synced:\n\n" + (payload.output || "No details returned."));
    } else {
      setSyncStatus("Orders up to date", "ok");
    }
    await invalidateAll();
  } catch (err) {
    console.error(err);
    setSyncStatus("", null);
    showError(`Sync request failed: ${err.message || err}`);
  } finally {
    refreshBtn.disabled = false;
  }
}

async function syncSettlement() {
  const storeId = requireStore();
  if (!storeId) return;
  clearError();
  refreshFeesBtn.disabled = true;
  setSyncStatus("Syncing fees from Walmart...", "busy");
  try {
    const { data: { session } } = await supabaseClient.auth.getSession();
    if (!session) {
      setSyncStatus("", null);
      showError("Your session has expired - sign in again.");
      return;
    }
    const resp = await fetch(`/sync/settlement?days=30&store_id=${encodeURIComponent(storeId)}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    let payload = {};
    try {
      payload = await resp.json();
    } catch (_) {
      // non-JSON response; fall through to status-based messaging
    }
    if (!resp.ok) {
      setSyncStatus("", null);
      showError(payload.detail || `Fee sync failed (HTTP ${resp.status}).`);
    } else if (!payload.ok) {
      setSyncStatus("Fees synced with warnings", "warn");
      showError("Some stores could not sync fees:\n\n" + (payload.output || "No details returned."));
    } else {
      setSyncStatus("Fees up to date", "ok");
    }
    await invalidate("order-details", "profit");
  } catch (err) {
    console.error(err);
    setSyncStatus("", null);
    showError(`Fee sync request failed: ${err.message || err}`);
  } finally {
    refreshFeesBtn.disabled = false;
  }
}

exportBtn.addEventListener("click", exportCosts);
importBtn.addEventListener("click", () => importFile.click());
importFile.addEventListener("change", () => {
  if (importFile.files && importFile.files[0]) importCosts(importFile.files[0]);
  importFile.value = "";
});

function setImportStatus(message, tone) {
  if (!message) {
    importStatus.hidden = true;
    importStatus.textContent = "";
    return;
  }
  importStatus.hidden = false;
  importStatus.textContent = message;
  importStatus.className = "sync-status" + (tone ? ` sync-${tone}` : "");
}

function noCostBadge() {
  const span = document.createElement("span");
  span.className = "badge badge-amber";
  span.textContent = "no cost";
  return span;
}

function csvEscape(value) {
  const s = String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function exportCosts() {
  if (!catalogExport.length) {
    alert("Nothing to export yet - open the Products tab first.");
    return;
  }
  const headers = ["sku", "product_name", "unit_cost", "warehouse_cost"];
  const lines = [headers.join(",")];
  for (const r of catalogExport) lines.push(headers.map((h) => csvEscape(r[h])).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "lucrum-costs.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function parseCsvLine(line) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else quoted = false;
      } else cur += c;
    } else if (c === ",") { out.push(cur); cur = ""; }
    else if (c === '"') quoted = true;
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

function parseCostsCsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (!lines.length) return [];
  const rows = lines.map(parseCsvLine);
  const header = rows[0].map((h) => h.toLowerCase());
  let iSku = 0, iUnit = 1, iWh = 2, start = 0;
  if (header.includes("sku")) {
    iSku = header.indexOf("sku");
    iUnit = header.findIndex((h) => ["unit_cost", "unitcost", "cost", "buying_price", "buy_price"].includes(h));
    iWh = header.findIndex((h) => ["warehouse_cost", "warehousecost", "wh_cost"].includes(h));
    start = 1;
  }
  const parsed = [];
  for (let i = start; i < rows.length; i++) {
    const r = rows[i];
    const sku = (r[iSku] || "").trim();
    if (!sku) continue;
    const entry = { sku };
    const unit = iUnit >= 0 ? parseFloat(r[iUnit]) : NaN;
    if (!Number.isNaN(unit)) entry.unit_cost = unit;
    if (iWh >= 0 && r[iWh] != null && r[iWh] !== "") {
      const w = parseFloat(r[iWh]);
      if (!Number.isNaN(w)) entry.warehouse_cost = w;
    }
    parsed.push(entry);
  }
  return parsed;
}

async function importCosts(file) {
  const storeId = requireStore();
  if (!storeId) return;
  clearError();
  setImportStatus("Reading CSV...", "busy");
  try {
    const rows = parseCostsCsv(await file.text());
    const valid = rows.filter((r) => "unit_cost" in r);
    const skipped = rows.length - valid.length;
    if (!valid.length) {
      setImportStatus("", null);
      showError("No usable rows. The CSV needs a 'sku' column and a 'unit_cost' column.");
      return;
    }
    // product_costs upserts must be column-homogeneous, so split by whether
    // a warehouse_cost was supplied (avoids clobbering existing warehouse costs).
    const withWh = valid.filter((r) => "warehouse_cost" in r)
      .map((r) => ({ store_id: storeId, sku: r.sku, unit_cost: r.unit_cost, warehouse_cost: r.warehouse_cost }));
    const noWh = valid.filter((r) => !("warehouse_cost" in r))
      .map((r) => ({ store_id: storeId, sku: r.sku, unit_cost: r.unit_cost }));
    for (const batch of [noWh, withWh]) {
      if (!batch.length) continue;
      const { error } = await supabaseClient.from("product_costs").upsert(batch);
      if (error) {
        setImportStatus("", null);
        showError(`Import failed: ${error.message}`);
        return;
      }
    }
    setImportStatus(`Imported ${valid.length} cost${valid.length === 1 ? "" : "s"}${skipped ? `, skipped ${skipped} row(s)` : ""}`, "ok");
    await invalidate("dashboard", "products", "order-details", "profit");
  } catch (err) {
    console.error(err);
    setImportStatus("", null);
    showError(`Import error: ${err.message || err}`);
  }
}

// ---- Purchases CSV (matches the buying spreadsheet) ---------------------
exportPurchasesBtn.addEventListener("click", exportPurchaseTemplate);
importPurchasesBtn.addEventListener("click", () => importPurchasesFile.click());
importPurchasesFile.addEventListener("change", () => {
  if (importPurchasesFile.files && importPurchasesFile.files[0]) importPurchases(importPurchasesFile.files[0]);
  importPurchasesFile.value = "";
});

function setPurchaseImportStatus(message, tone) {
  if (!message) {
    purchaseImportStatus.hidden = true;
    purchaseImportStatus.textContent = "";
    return;
  }
  purchaseImportStatus.hidden = false;
  purchaseImportStatus.textContent = message;
  purchaseImportStatus.className = "sync-status" + (tone ? ` sync-${tone}` : "");
}

function downloadCsv(text, filename) {
  const blob = new Blob([text], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportPurchaseTemplate() {
  const headers = ["Purchase Date", "Product Name", "My SKU", "Supplier / Source", "Product Link", "Order #", "Qty", "Unit Cost", "Sales Tax", "Shipping", "Status", "Notes"];
  const example = ["2026-07-01", "Widget A", "SKU-A", "Amazon", "https://example.com/item", "PO-123", "10", "12.50", "3.20", "5.00", "Received", "first batch"];
  downloadCsv(`${headers.join(",")}\n${example.map(csvEscape).join(",")}`, "lucrum-purchases-template.csv");
}

// Map a CSV header (any of your sheet's labels) to a purchases field.
// Total Cost / Landed Unit Cost map to nothing -- they're computed, not imported.
function purchaseHeaderField(h) {
  const key = h.toLowerCase().replace(/[^a-z0-9]/g, "");
  const map = {
    purchasedate: "purchase_date", date: "purchase_date",
    productname: "product_name", product: "product_name",
    mysku: "sku", sku: "sku",
    suppliersource: "source", supplier: "source", source: "source",
    productlink: "product_link", link: "product_link",
    order: "order_number", ordernumber: "order_number",
    qty: "quantity", quantity: "quantity",
    unitcost: "unit_cost", cost: "unit_cost",
    salestax: "sales_tax", tax: "sales_tax",
    shipping: "shipping",
    status: "status",
    notes: "notes",
  };
  return map[key] || null;
}

function parsePurchasesCsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (!lines.length) return [];
  const rows = lines.map(parseCsvLine);
  const fields = rows[0].map(purchaseHeaderField);
  const out = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    const rec = {};
    fields.forEach((f, idx) => {
      if (f && r[idx] != null && r[idx] !== "") rec[f] = r[idx].trim();
    });
    if (rec.sku) out.push(rec);
  }
  return out;
}

function csvDate(v) {
  if (!v) return null;
  const s = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  // Use local components (not toISOString) so a UTC+ timezone doesn't shift the day back.
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

async function importPurchases(file) {
  const storeId = requireStore();
  if (!storeId) return;
  clearError();
  setPurchaseImportStatus("Reading CSV...", "busy");
  try {
    const recs = parsePurchasesCsv(await file.text());
    const valid = recs.filter((r) => r.sku && r.quantity != null && r.unit_cost != null);
    const skipped = recs.length - valid.length;
    if (!valid.length) {
      setPurchaseImportStatus("", null);
      showError("No usable rows. The CSV needs at least My SKU, Qty, and Unit Cost columns.");
      return;
    }
    const payload = valid.map((r) => ({
      store_id: storeId,
      sku: r.sku,
      product_name: r.product_name || null,
      source: r.source || null,
      product_link: r.product_link || null,
      order_number: r.order_number || null,
      quantity: parseFloat(r.quantity) || 0,
      unit_cost: parseFloat(r.unit_cost) || 0,
      sales_tax: r.sales_tax != null ? (parseFloat(r.sales_tax) || 0) : 0,
      shipping: r.shipping != null ? (parseFloat(r.shipping) || 0) : 0,
      status: r.status || null,
      purchase_date: csvDate(r.purchase_date) || today(),
      notes: r.notes || null,
    }));
    const { error } = await supabaseClient.from("purchases").insert(payload);
    if (error) {
      setPurchaseImportStatus("", null);
      showError(`Import failed: ${error.message}`);
      return;
    }
    setPurchaseImportStatus(`Imported ${valid.length} purchase${valid.length === 1 ? "" : "s"}${skipped ? `, skipped ${skipped} row(s)` : ""}`, "ok");
    await invalidate("purchases", "inventory", "dashboard", "order-details", "profit");
  } catch (err) {
    console.error(err);
    setPurchaseImportStatus("", null);
    showError(`Import error: ${err.message || err}`);
  }
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

function requireStore() {
  // With no "All my stores" option, an empty value means the account has no
  // store_access grant at all.
  if (!storeSelect.value) {
    alert("No store selected - your account isn't linked to a store yet. Ask an admin for access.");
    return null;
  }
  return storeSelect.value;
}

async function loadDashboard() {
  const { data, error } = await supabaseClient.from("stores").select("*");
  if (error) {
    console.error(error);
    showError(`Couldn't load your stores: ${error.message || error}`);
    return;
  }

  // Which stores is this user an admin of? (degrades quietly if the role
  // migration hasn't been applied yet -- no admin UI rather than an error.)
  try {
    const { data: access, error: accessError } = await supabaseClient
      .from("store_access")
      .select("store_id, role");
    if (accessError) throw accessError;
    adminStores = new Set((access || []).filter((a) => a.role === "admin").map((a) => a.store_id));
  } catch (_) {
    adminStores = new Set();
  }

  stores = data;
  storeSelect.innerHTML = "";
  // Deliberately no "All my stores": stores belong to different owners, so an
  // aggregate across them would mix separate businesses into one number. You
  // always look at exactly one store.
  for (const store of stores) {
    const option = document.createElement("option");
    option.value = store.store_id;
    option.textContent = store.name;
    storeSelect.appendChild(option);
  }
  document.getElementById("purchase-date").value = today();
  document.getElementById("damaged-date").value = today();
  document.getElementById("adjustment-date").value = today();

  if (!stores.length) {
    // RLS returns nothing without a store_access grant -- say so, instead of
    // leaving them staring at an empty app wondering what broke.
    updateAdminContext();
    storePickerSection.hidden = true;
    dashboardSection.hidden = false;
    switchStoreBtn.hidden = true;
    showError("Your account isn't linked to a store yet. Ask an admin to grant you access.");
    return;
  }
  // One store: no choice to make. Several: ask which.
  if (stores.length === 1) enterStore(stores[0].store_id);
  else showStorePicker();
}

function storeName(storeId) {
  const store = stores.find((s) => s.store_id === storeId);
  return store ? store.name : storeId;
}

function buildNameMap(productRows) {
  const nameMap = new Map();
  for (const product of productRows) {
    nameMap.set(`${product.store_id}|${product.sku}`, product.product_name);
  }
  return nameMap;
}

// ---- Per-tab renderers ---------------------------------------------------
// ledger_sku_monthly is one row per (sku, month) so it can be filtered to a
// period. With a month selected the query already returns one row per SKU; on
// "All time" it returns one per month sold, which we add back together here.
//
// uncosted_lines carries across the roll-up: a SKU is only "complete" if EVERY
// month contributing to it was complete. Rolling up a known month with an
// unknown one must not launder the unknown away.
function rollUpBySku(rows) {
  const bySku = new Map();
  for (const row of rows) {
    const prev = bySku.get(row.sku);
    if (!prev) {
      bySku.set(row.sku, { ...row });
      continue;
    }
    prev.units          = Number(prev.units)          + Number(row.units || 0);
    prev.lines          = Number(prev.lines || 0)     + Number(row.lines || 0);
    prev.revenue        = Number(prev.revenue)        + Number(row.revenue || 0);
    prev.cogs           = Number(prev.cogs)           + Number(row.cogs || 0);
    prev.fees           = Number(prev.fees)           + Number(row.fees || 0);
    prev.net_profit     = Number(prev.net_profit)     + Number(row.net_profit || 0);
    prev.returns        = Number(prev.returns || 0)   + Number(row.returns || 0);
    prev.uncosted_lines  = Number(prev.uncosted_lines || 0)  + Number(row.uncosted_lines || 0);
    prev.unknown_lines   = Number(prev.unknown_lines || 0)   + Number(row.unknown_lines || 0);
    prev.estimated_lines = Number(prev.estimated_lines || 0) + Number(row.estimated_lines || 0);
  }
  // Round once at the end: rounding each month first would drift the total.
  for (const row of bySku.values()) {
    for (const key of ["revenue", "cogs", "fees", "net_profit", "returns"]) {
      row[key] = Math.round(Number(row[key]) * 100) / 100;
    }
    row.is_complete = Number(row.uncosted_lines || 0) === 0;
    // Effective average unit cost implied by the ledger. Null when nothing is
    // known, so the UI shows "no cost" rather than a confident $0.00.
    row.unit_cost = Number(row.units) > 0 && row.is_complete
      ? Math.round((Number(row.cogs) / Number(row.units)) * 10000) / 10000
      : null;
    // A margin over partly-unknown costs is not a fact.
    row.margin_pct = row.is_complete && Number(row.revenue) > 0
      ? Math.round((100 * Number(row.net_profit) / Number(row.revenue)) * 100) / 100
      : null;
    delete row.month; // meaningless once months are merged
  }
  return [...bySku.values()];
}

function renderDashboard(profitRows, productRows) {
  // The ledger keys everything on sku and deliberately does not carry
  // product_name -- a name is catalog state, not a financial fact, and copying
  // it into every entry would let the two drift. Join it on for display.
  const nameMap = buildNameMap(productRows);
  const named = profitRows.map((row) => ({
    ...row,
    product_name: row.product_name ?? nameMap.get(`${row.store_id}|${row.sku}`) ?? "",
  }));
  const profit = rollUpBySku(named).sort((a, b) => b.net_profit - a.net_profit);

  profitTableBody.innerHTML = "";
  for (const row of profit) {
    // Opened by sku rather than by line: this row already aggregates every
    // sale of that sku, so the drawer should too.
    const openSku = () => openEntryDrawer({
      title: row.sku,
      subtitle: `${row.product_name || ""} - every ledger entry for this SKU`.trim(),
      sku: row.sku,
    });
    // unit_cost is the EFFECTIVE cost the view used: landed cost from purchases
    // when the SKU has any, otherwise the manual Products cost. null = none.
    const tr = renderProfitRow(row, row.unit_cost ?? 0, row.unit_cost != null);
    tr.classList.add("drillable");
    tr.addEventListener("click", openSku);
    profitTableBody.appendChild(tr);
  }
  if (!profit.length) renderEmpty(profitTableBody, 9, "No sales yet - click Refresh orders to pull from Walmart.");

  // Three cost states, two of which make a total a lower bound -- but they need
  // different action, so they get different sentences. "Unknown" wants a
  // purchase logged; "estimated" wants a guess replaced. Lumping them together
  // as "no buying price" was wrong for the estimated half: a price exists
  // there, it just isn't real.
  const unknown   = profit.filter((r) => Number(r.unknown_lines || 0) > 0);
  const estimated = profit.filter((r) => Number(r.estimated_lines || 0) > 0);

  if (unknown.length || estimated.length) {
    const revenueOf = (rows) => rows.reduce((sum, r) => sum + Number(r.revenue || 0), 0);
    const linesOf = (rows, key) => rows.reduce((sum, r) => sum + Number(r[key] || 0), 0);
    const parts = [];

    if (unknown.length) {
      parts.push(
        `${unknown.length} product(s) have NO known cost `
        + `(${linesOf(unknown, "unknown_lines")} line(s), `
        + `${fmtMoney(revenueOf(unknown))} of revenue). Their profit is unknown, not zero - `
        + `log a purchase, or set a cost on the Products tab.`);
    }
    if (estimated.length) {
      parts.push(
        `${estimated.length} product(s) use an ESTIMATED placeholder cost `
        + `(${linesOf(estimated, "estimated_lines")} line(s), `
        + `${fmtMoney(revenueOf(estimated))} of revenue). These are guesses, not your `
        + `real buying prices - logging actual purchases replaces them automatically.`);
    }
    parts.push("Totals below are a lower bound and margins are hidden until costs are real.");

    costWarning.textContent = parts.join(" ");
    costWarning.hidden = false;
  } else {
    costWarning.hidden = true;
  }

  updateSummary(profit, productRows);
}

function renderProducts(productRows, costRows) {
  const costMap = new Map();
  const warehouseCostMap = new Map();
  for (const cost of costRows) {
    costMap.set(`${cost.store_id}|${cost.sku}`, cost.unit_cost);
    warehouseCostMap.set(`${cost.store_id}|${cost.sku}`, cost.warehouse_cost ?? 0);
  }

  const products = [...productRows].sort((a, b) => (a.product_name ?? "").localeCompare(b.product_name ?? ""));

  catalogTableBody.innerHTML = "";
  catalogExport = [];
  for (const row of products) {
    const key = `${row.store_id}|${row.sku}`;
    const hasCost = costMap.has(key);
    const unitCost = costMap.get(key) ?? 0;
    const warehouseCost = warehouseCostMap.get(key) ?? 0;
    catalogTableBody.appendChild(renderCatalogRow(row, unitCost, warehouseCost, hasCost));
    catalogExport.push({
      sku: row.sku,
      product_name: row.product_name ?? "",
      unit_cost: hasCost ? unitCost : "",
      warehouse_cost: warehouseCostMap.has(key) ? warehouseCost : "",
    });
  }
  if (!products.length) renderEmpty(catalogTableBody, 7, "No products yet - run a sync to pull your catalog.");
}

function renderPurchases(purchaseRows, productRows) {
  const nameMap = buildNameMap(productRows);
  const rows = [...purchaseRows].sort((a, b) => (b.purchase_date ?? "").localeCompare(a.purchase_date ?? ""));
  purchasesTableBody.innerHTML = "";
  for (const row of rows) purchasesTableBody.appendChild(renderPurchaseRow(row, nameMap));
  if (!rows.length) renderEmpty(purchasesTableBody, 15, "No purchases logged yet.");
}

function renderDamaged(damagedRows, productRows) {
  const nameMap = buildNameMap(productRows);
  const rows = [...damagedRows].sort((a, b) => (b.damaged_date ?? "").localeCompare(a.damaged_date ?? ""));
  damagedTableBody.innerHTML = "";
  for (const row of rows) damagedTableBody.appendChild(renderDamagedRow(row, nameMap));
  if (!rows.length) renderEmpty(damagedTableBody, 7, "No damaged goods logged yet.");
}

function renderInventory(inventoryRows, adjustmentRows) {
  const rows = [...inventoryRows].sort((a, b) => (a.product_name ?? "").localeCompare(b.product_name ?? ""));
  inventoryTableBody.innerHTML = "";
  for (const row of rows) inventoryTableBody.appendChild(renderInventoryRow(row));
  if (!rows.length) renderEmpty(inventoryTableBody, 8, "No inventory data yet.");

  const adj = [...(adjustmentRows || [])].sort((a, b) => (b.adjusted_date ?? "").localeCompare(a.adjusted_date ?? ""));
  adjustmentsTableBody.innerHTML = "";
  for (const row of adj) adjustmentsTableBody.appendChild(renderAdjustmentRow(row));
  if (!adj.length) renderEmpty(adjustmentsTableBody, 6, "No manual adjustments yet.");
}

function renderAdjustmentRow(row) {
  return renderRow([
    { date: row.adjusted_date },
    row.sku,
    { num: row.quantity_delta },
    row.reason,
    row.notes,
    { edit: { table: "inventory_adjustments", row } },
  ]);
}

function renderReturns(returnRows) {
  const rows = [...returnRows].sort((a, b) => (b.order_date ?? "").localeCompare(a.order_date ?? ""));
  returnsTableBody.innerHTML = "";
  for (const row of rows) {
    const tr = renderReturnRow(row);
    tr.classList.add("drillable");
    tr.addEventListener("click", () => openEntryDrawer({
      title: `${row.sku} - refunded on order ${row.customer_order_id}`,
      subtitle: row.sold_at
        ? `Sold ${fmtDate(row.sold_at)} for ${fmtMoney(row.original_revenue)}, refunded ${fmtDate(row.order_date)}`
        : "",
      customerOrderId: row.customer_order_id,
      sku: row.sku,
    }));
    returnsTableBody.appendChild(tr);
  }
  if (!rows.length) {
    renderEmpty(returnsTableBody, 6,
      "No refunds in the settlement data for this period.");
  }
}

function renderOrderDetails(orderDetailRows) {
  const rows = [...orderDetailRows].sort((a, b) => (b.order_date ?? "").localeCompare(a.order_date ?? ""));
  const counted = rows.filter((r) => r.counts_in_profit);
  const pending = rows.filter((r) => !r.counts_in_profit);

  // Make every line openable. The drawer needs the order line's own key plus
  // the customer order, because fees and refunds are keyed by the latter.
  const drillable = (tr, row) => {
    tr.classList.add("drillable");
    tr.addEventListener("click", () => openEntryDrawer({
      title: `${row.sku} - order ${row.customer_order_id}`,
      subtitle: `${row.product_name || ""} - ${row.order_status || ""}`.trim(),
      sourceId: row.source_id,
      customerOrderId: row.customer_order_id,
      sku: row.sku,
    }));
    return tr;
  };

  orderDetailsTableBody.innerHTML = "";
  counted.forEach((row, i) =>
    orderDetailsTableBody.appendChild(drillable(renderOrderDetailRow(row, i + 1), row)));
  if (!counted.length) renderEmpty(orderDetailsTableBody, 22, "No shipped or delivered orders yet.");

  pendingOrdersTableBody.innerHTML = "";
  pending.forEach((row, i) =>
    pendingOrdersTableBody.appendChild(drillable(renderOrderDetailRow(row, i + 1), row)));
  if (!pending.length) renderEmpty(pendingOrdersTableBody, 22, "Nothing pending - every order is shipped or delivered.");

  const sum = (list) => list.reduce((s, r) => s + Number(r.net_profit || 0), 0);
  countedSummary.textContent = counted.length
    ? `${counted.length} line(s) counted - net profit ${fmtMoney(sum(counted))}`
    : "";
  const cancelled = pending.filter((r) => (r.order_status || "").toLowerCase().includes("cancel"));
  const upcoming = pending.filter((r) => !(r.order_status || "").toLowerCase().includes("cancel"));
  pendingSummary.textContent = pending.length
    ? `${pending.length} line(s) excluded - ${fmtMoney(sum(upcoming))} of profit still in the pipeline`
      + (cancelled.length ? `, plus ${cancelled.length} cancelled (never counts)` : "")
    : "";
}

// Authenticated call to our own server (not Supabase).
async function apiFetch(path, options = {}) {
  const { data: { session } } = await supabaseClient.auth.getSession();
  if (!session) throw new Error("Your session has expired - sign in again.");
  const resp = await fetch(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${session.access_token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  let payload = {};
  try {
    payload = await resp.json();
  } catch (_) {
    // non-JSON response
  }
  if (!resp.ok) throw new Error(payload.detail || `Request failed (HTTP ${resp.status}).`);
  return payload;
}

function setTeamStatus(message, tone) {
  if (!message) {
    teamStatus.hidden = true;
    teamStatus.textContent = "";
    return;
  }
  teamStatus.hidden = false;
  teamStatus.textContent = message;
  teamStatus.className = "sync-status" + (tone ? ` sync-${tone}` : "");
}

function renderTeam(payload) {
  const rows = payload.users || [];
  teamTableBody.innerHTML = "";
  for (const u of rows) {
    const tr = document.createElement("tr");

    const email = document.createElement("td");
    email.textContent = u.email;

    const store = document.createElement("td");
    store.textContent = u.store;

    const roleCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + (u.role === "admin" ? "badge-green" : "badge-gray");
    badge.textContent = u.role;
    roleCell.appendChild(badge);

    const actions = document.createElement("td");
    actions.className = "team-actions";
    if (u.is_self) {
      actions.textContent = "you";
    } else {
      const flip = document.createElement("button");
      flip.type = "button";
      flip.className = "btn-xs";
      flip.textContent = u.role === "admin" ? "Make member" : "Make admin";
      flip.addEventListener("click", () => changeRole(u, u.role === "admin" ? "member" : "admin"));
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "btn-xs";
      revoke.textContent = "Revoke";
      revoke.addEventListener("click", () => revokeAccess(u));
      actions.append(flip, revoke);
    }

    tr.append(email, store, roleCell, actions);
    teamTableBody.appendChild(tr);
  }
  if (!rows.length) renderEmpty(teamTableBody, 4, "No one has access yet.");
}

teamForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const storeId = requireStore();
  if (!storeId) return;
  clearError();
  setTeamStatus("Saving...", "busy");
  try {
    const result = await apiFetch("/admin/users", {
      method: "POST",
      body: JSON.stringify({
        email: document.getElementById("team-email").value.trim(),
        password: document.getElementById("team-password").value,
        role: document.getElementById("team-role").value,
        store_id: storeId,
      }),
    });
    setTeamStatus(
      result.created ? `Created ${result.email} as ${result.role}` : `Updated ${result.email} to ${result.role}`,
      "ok",
    );
    teamForm.reset();
    await invalidate("team");
  } catch (err) {
    console.error(err);
    setTeamStatus("", null);
    showError(err.message || String(err));
  }
});

async function changeRole(u, role) {
  clearError();
  try {
    await apiFetch("/admin/users/role", {
      method: "POST",
      body: JSON.stringify({ user_id: u.user_id, store_id: u.store_id, role }),
    });
    setTeamStatus(`${u.email} is now ${role}`, "ok");
    await invalidate("team");
  } catch (err) {
    console.error(err);
    showError(err.message || String(err));
  }
}

async function revokeAccess(u) {
  if (!confirm(`Revoke ${u.email}'s access to ${u.store}?\n\nTheir login stays, but they'll see an empty app.`)) return;
  clearError();
  try {
    await apiFetch("/admin/users/revoke", {
      method: "POST",
      body: JSON.stringify({ user_id: u.user_id, store_id: u.store_id }),
    });
    setTeamStatus(`Revoked ${u.email}`, "ok");
    await invalidate("team");
  } catch (err) {
    console.error(err);
    showError(err.message || String(err));
  }
}

function setStoreStatus(message, tone) {
  if (!message) {
    storeStatus.hidden = true;
    storeStatus.textContent = "";
    return;
  }
  storeStatus.hidden = false;
  storeStatus.textContent = message;
  storeStatus.className = "sync-status" + (tone ? ` sync-${tone}` : "");
}

function renderStores(payload) {
  const rows = payload.stores || [];
  storesTableBody.innerHTML = "";
  for (const s of rows) {
    const tr = document.createElement("tr");

    const name = document.createElement("td");
    name.textContent = s.name;

    const added = document.createElement("td");
    added.textContent = fmtDate(s.added_at);

    const creds = document.createElement("td");
    const credBadge = document.createElement("span");
    credBadge.className = "badge " + (s.has_credentials ? "badge-green" : "badge-amber");
    credBadge.textContent = s.has_credentials ? "stored" : "missing";
    if (!s.has_credentials) credBadge.title = "No Walmart credentials - syncing will fail for this store.";
    creds.appendChild(credBadge);

    const syncing = document.createElement("td");
    const syncBadge = document.createElement("span");
    syncBadge.className = "badge " + (s.active ? "badge-green" : "badge-gray");
    syncBadge.textContent = s.active ? "on" : "off";
    syncing.appendChild(syncBadge);

    const actions = document.createElement("td");
    actions.className = "team-actions";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn-xs";
    toggle.textContent = s.active ? "Stop syncing" : "Start syncing";
    toggle.addEventListener("click", () => setStoreActive(s, !s.active));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-xs btn-danger-outline";
    del.textContent = "Delete";
    del.title = "Permanently delete this store and all of its data";
    del.addEventListener("click", () => deleteStore(s));
    actions.append(toggle, del);

    tr.append(name, added, creds, syncing, actions);
    storesTableBody.appendChild(tr);
  }
  if (!rows.length) renderEmpty(storesTableBody, 5, "No stores yet.");
}

storeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  setStoreStatus("Adding store...", "busy");
  try {
    const result = await apiFetch("/admin/stores", {
      method: "POST",
      body: JSON.stringify({
        name: document.getElementById("store-name").value.trim(),
        client_id: document.getElementById("store-client-id").value.trim(),
        client_secret: document.getElementById("store-client-secret").value,
      }),
    });
    setStoreStatus(`Added ${result.name} - you're admin on it`, "ok");
    storeForm.reset();
    // The store picker has a new entry now, so rebuild it.
    await loadDashboard();
  } catch (err) {
    console.error(err);
    setStoreStatus("", null);
    showError(err.message || String(err));
  }
});

async function deleteStore(store) {
  const typed = prompt(
    `Permanently delete "${store.name}"?\n\n` +
      "This removes the store AND all of its data - orders, purchases, product costs, " +
      "settlement, fees, and everyone's access to it. It cannot be undone.\n\n" +
      "If the owner is just leaving, use \"Stop syncing\" instead - that keeps the history.\n\n" +
      `Type the store name to confirm:`,
  );
  if (typed === null) return; // cancelled
  if (typed.trim() !== store.name) {
    showError("The name didn't match - nothing was deleted.");
    return;
  }
  clearError();
  setStoreStatus(`Deleting ${store.name}...`, "busy");
  try {
    const r = await apiFetch("/admin/stores/delete", {
      method: "POST",
      body: JSON.stringify({ store_id: store.store_id, confirm_name: typed.trim() }),
    });
    setStoreStatus(`Deleted ${r.name} - ${fmtNum(r.deleted)} row(s) removed`, "warn");
    await loadDashboard(); // the store list (and picker) changed
  } catch (err) {
    console.error(err);
    setStoreStatus("", null);
    showError(err.message || String(err));
  }
}

async function setStoreActive(store, active) {
  if (!active && !confirm(`Stop syncing ${store.name}? Refresh orders / fees will skip it.`)) return;
  clearError();
  try {
    await apiFetch("/admin/stores/active", {
      method: "POST",
      body: JSON.stringify({ store_id: store.store_id, active }),
    });
    setStoreStatus(`${store.name} syncing ${active ? "on" : "off"}`, "ok");
    await invalidate("stores");
  } catch (err) {
    console.error(err);
    showError(err.message || String(err));
  }
}

// Why a margin is withheld. Naming the reason makes it actionable: "unsettled"
// resolves itself when Walmart's recon file arrives, "no cost" needs a purchase
// logged. A bare "uncertain" would tell you nothing about what to do.
function marginBlockedLabel(row) {
  const unknown = Number(row.unknown_lines || 0);
  const estimated = Number(row.estimated_lines || 0);
  const lines = Number(row.lines || 0);
  if (unknown && estimated) return `${unknown} no cost, ${estimated} est.`;
  if (unknown) return `${unknown} no cost`;
  // Every line estimated means the whole month is awaiting settlement, which is
  // a different situation from a handful of stragglers.
  if (estimated && lines && estimated >= lines) return "unsettled";
  if (estimated) return `${estimated} est.`;
  return `${row.uncosted_lines || 0} uncertain`;
}

function renderMonthlyProfit(rows) {
  const sorted = [...rows].sort((a, b) => (b.month ?? "").localeCompare(a.month ?? ""));
  // This tab ignores the Period filter on purpose -- it exists to compare months.
  // Mark the month the rest of the app is scoped to, so the two views connect.
  const selected = periodSelect.value !== "all" ? periodSelect.value : null;
  monthlyProfitTableBody.innerHTML = "";
  for (const row of sorted) {
    // A month is "complete" only when every fee is settled and every cost known.
    // Until then net is a RANGE, not a figure, and the margin is withheld --
    // net_low/net_high come from the observed spread between what the workbook
    // records and what Walmart actually charged.
    const complete = row.is_complete !== false;
    const estimated = Number(row.estimated_fees || 0) > 0;
    const tr = renderRow([
      { text: fmtMonth(row.month) },
      { num: row.orders },
      { num: row.units },
      { money: row.revenue },
      { money: row.cogs },
      { money: row.fees },
      { money: row.returns, cls: Number(row.returns) ? "neg" : "muted" },
      {
        text: estimated
          ? `${fmtMoney(row.net_low)} – ${fmtMoney(row.net_high)}`
          : fmtMoney(row.net_profit),
        cls: signClass(row.net_profit),
      },
      {
        text: complete && row.margin_pct != null
          ? `${fmtNum(row.margin_pct)}%`
          : marginBlockedLabel(row),
        cls: complete && row.margin_pct != null ? signClass(row.margin_pct) : "muted",
      },
    ]);
    if (!complete) {
      const bits = [];
      if (row.unknown_lines) bits.push(`${row.unknown_lines} with no known cost`);
      if (row.estimated_lines) bits.push(`${row.estimated_lines} using an estimated cost`);
      tr.title = `${row.lines} line(s) this month: ${bits.join(", ")}. `
        + `Net profit is a lower bound; margin is hidden until costs are real.`;
    }
    if (selected && String(row.month ?? "").slice(0, 7) === selected) {
      tr.classList.add("row-current-period");
    }
    monthlyProfitTableBody.appendChild(tr);
  }
  if (!sorted.length) renderEmpty(monthlyProfitTableBody, 9, "No sales yet - click Refresh orders to pull from Walmart.");
}

// ---- Drill-down drawer ---------------------------------------------------
// Every figure in this app is a sum of ledger rows. Being able to open one and
// see "fee_referral -9.32, settlement, 2026-09-08" is what separates a number
// you can check from one you have to trust. Most of the bugs found while
// building this produced *plausible* wrong numbers, which is exactly the class
// a drill-down catches.

const entryDrawer = document.getElementById("entry-drawer");
const drawerScrim = document.getElementById("drawer-scrim");
const drawerTitle = document.getElementById("drawer-title");
const drawerSubtitle = document.getElementById("drawer-subtitle");
const drawerTableBody = document.querySelector("#drawer-table tbody");
const drawerTotal = document.getElementById("drawer-total");

const ENTRY_LABEL = {
  revenue_product: "Product revenue",
  revenue_shipping: "Shipping revenue",
  cogs: "Cost of goods",
  warehouse: "Warehouse / handling",
  fee_referral: "Walmart referral fee",
  fee_label: "Walmart shipping label",
  fee_label_3p: "Third-party label",
  fee_refund: "Refund fee",
  fee_other: "Other Walmart fee",
  return_revenue: "Refund - revenue reversed",
  return_cogs: "Refund - cost reversed",
  damage: "Damaged stock",
  correction: "Prior-period correction",
};

function closeDrawer() {
  entryDrawer.hidden = true;
  drawerScrim.hidden = true;
}

drawerScrim?.addEventListener("click", closeDrawer);
document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !entryDrawer.hidden) closeDrawer();
});

async function openEntryDrawer({ title, subtitle, sourceId, customerOrderId, sku }) {
  drawerTitle.textContent = title;
  drawerSubtitle.textContent = subtitle || "";
  drawerTableBody.innerHTML = "";
  drawerTotal.textContent = "";
  entryDrawer.hidden = false;
  drawerScrim.hidden = false;

  try {
    let query = supabaseClient.from("ledger_entries").select("*");
    if (storeSelect.value) query = query.eq("store_id", storeSelect.value);
    if (sourceId && customerOrderId) {
      // A line's own entries are keyed by "{po}:{line}", but its fees and
      // refunds arrive from settlement keyed by customer order -- so both have
      // to be asked for, or the drawer would show revenue with no fees.
      query = query.or(`source_id.eq.${sourceId},source_id.like.*:${customerOrderId}:*`);
      if (sku) query = query.or(`sku.eq.${sku},sku.is.null`);
    } else if (sourceId) {
      query = query.eq("source_id", sourceId);
    } else if (sku) {
      query = query.eq("sku", sku);
    }
    const { data, error } = await query.order("entry_date").limit(500);
    if (error) throw error;

    const rows = data || [];
    for (const entry of rows) {
      const estimated = entry.source_kind === "manual"
        && ["fee_referral", "fee_label"].includes(entry.entry_type);
      const tr = renderRow([
        { text: entry.entry_date },
        { text: ENTRY_LABEL[entry.entry_type] || entry.entry_type },
        { money: entry.amount, cls: signClass(entry.amount) },
        { text: entry.source_kind + (estimated ? " (estimate)" : "") },
      ]);
      if (estimated) tr.classList.add("entry-estimated");
      // The note carries the calibration ratio and the cost basis, which is
      // where an unexpected figure usually explains itself.
      if (entry.note) tr.title = entry.note;
      if (entry.effective_for && entry.effective_for !== entry.entry_date) {
        tr.title = `${tr.title ? tr.title + " - " : ""}effective for ${entry.effective_for}`;
      }
      drawerTableBody.appendChild(tr);
    }
    if (!rows.length) {
      renderEmpty(drawerTableBody, 4, "No ledger entries - this line has not realized yet.");
    }
    const net = rows.reduce((s, r) => s + Number(r.amount || 0), 0);
    drawerTotal.textContent = fmtMoney(net);
    drawerTotal.className = signClass(net);
  } catch (err) {
    renderEmpty(drawerTableBody, 4, `Could not load entries: ${err.message || err}`);
  }
}

// ---- Health surfaces -----------------------------------------------------

// Settlement arrives roughly fortnightly, so it is legitimately stale most of
// the time. Judging every feed by one threshold would cry wolf constantly.
const STALE_HOURS = { orders: 48, products: 48, settlement: 24 * 18 };

function renderSyncHealth(rows) {
  const sorted = [...(rows || [])].sort((a, b) => (a.feed || "").localeCompare(b.feed || ""));
  syncTableBody.innerHTML = "";
  for (const row of sorted) {
    const hours = Number(row.hours_since_run || 0);
    const stale = hours > (STALE_HOURS[row.feed] ?? 48);
    const failed = row.last_status === "failed";
    const tr = renderRow([
      { text: row.feed },
      { text: failed ? "failed" : stale ? "stale" : "ok", cls: failed || stale ? "neg" : "pos" },
      { text: row.last_run_at ? `${fmtDate(row.last_run_at)} (${fmtNum(hours)}h ago)` : "never" },
      { text: row.watermark ? fmtDate(row.watermark) : "-", cls: row.watermark ? "" : "muted" },
      { num: row.failures_7d },
      { text: row.last_error || "-", cls: row.last_error ? "neg" : "muted" },
    ]);
    if (row.last_error) tr.title = row.last_error;
    syncTableBody.appendChild(tr);
  }
  if (!sorted.length) {
    renderEmpty(syncTableBody, 6, "No sync has run yet for this store.");
  }
}

const FEE_CATEGORY_LABEL = {
  ignore: "ignored - already counted in the order",
  walmart_fee: "Walmart referral / commission",
  wfs_label_fee: "Walmart shipping label",
  refund_fee: "refund processing fee",
  extra_service_fee: "other Walmart fee",
  return_revenue: "reverses revenue (refund)",
};

function renderFeeMap(unmapped, mapping, calibration) {
  unmappedTableBody.innerHTML = "";
  for (const row of unmapped || []) {
    const tr = renderRow([
      { text: row.transaction_type },
      { text: row.amount_type },
      { num: row.rows },
      { money: row.total_amount, cls: "neg" },
      { text: fmtDate(row.last_seen) },
    ]);
    tr.classList.add("row-missing-cost");
    unmappedTableBody.appendChild(tr);
  }
  if (!(unmapped || []).length) {
    renderEmpty(unmappedTableBody, 5, "Empty - every settlement row is mapped.");
  }

  const sorted = [...(mapping || [])].sort((a, b) =>
    (a.transaction_type || "").localeCompare(b.transaction_type || "") ||
    (a.amount_type || "").localeCompare(b.amount_type || ""));
  feeMapTableBody.innerHTML = "";
  for (const row of sorted) {
    feeMapTableBody.appendChild(renderRow([
      { text: row.transaction_type },
      { text: row.amount_type || "(blank)" },
      {
        text: FEE_CATEGORY_LABEL[row.category] || row.category,
        cls: row.category === "ignore" ? "muted" : "",
      },
    ]));
  }
  if (!sorted.length) renderEmpty(feeMapTableBody, 3, "No mappings configured.");

  calibrationTableBody.innerHTML = "";
  for (const row of calibration || []) {
    // A ratio above 1 means the workbook overstates the fee, so the real charge
    // is lower and profit is higher than the spreadsheet suggests.
    const over = Number(row.ratio_median || 1) - 1;
    calibrationTableBody.appendChild(renderRow([
      { text: row.entry_type === "fee_referral" ? "Referral" : "Label" },
      { num: row.pairs },
      { text: fmtNum(row.ratio_p10) },
      { text: fmtNum(row.ratio_median) },
      { text: fmtNum(row.ratio_p90) },
      {
        text: `${over >= 0 ? "over" : "under"}states by ${fmtNum(Math.abs(over) * 100)}%`,
        cls: Math.abs(over) > 0.05 ? "neg" : "muted",
      },
    ]));
  }
  if (!(calibration || []).length) {
    renderEmpty(calibrationTableBody, 6,
      "No settled lines to compare against yet - estimates post uncalibrated.");
  }
}

function renderPeriods(rows) {
  const sorted = [...(rows || [])].sort((a, b) => (b.month ?? "").localeCompare(a.month ?? ""));
  periodsTableBody.innerHTML = "";
  for (const row of sorted) {
    // Name what is blocking rather than just refusing, so the next action is
    // obvious: estimated fees resolve themselves, unknown costs do not.
    const blockers = [];
    if (!row.settlement_ingested) blockers.push("no settlement yet");
    if (!row.fees_all_mapped) blockers.push(`${row.unmapped_types} unmapped fee type(s)`);
    if (row.unknown_lines) blockers.push(`${row.unknown_lines} line(s) with no known cost`);
    if (row.estimated_entries) blockers.push(`${row.estimated_entries} estimated fee(s) awaiting settlement`);

    const tick = (ok) => ({ text: ok ? "yes" : "no", cls: ok ? "pos" : "neg" });
    const tr = renderRow([
      { text: fmtMonth(row.month) },
      { text: row.status, cls: row.status === "closed" ? "pos" : "muted" },
      tick(row.settlement_ingested),
      tick(row.fees_final),
      tick(row.costs_known),
      {
        text: row.status === "closed" ? "closed"
          : blockers.length ? blockers.join("; ")
          : "ready to close",
        cls: row.status === "closed" ? "muted" : blockers.length ? "" : "pos",
      },
    ]);
    periodsTableBody.appendChild(tr);
  }
  if (!sorted.length) renderEmpty(periodsTableBody, 6, "No ledger entries yet.");
}

function updateSummary(profitRows, productRows) {
  const totalRevenue = profitRows.reduce((sum, row) => sum + Number(row.revenue), 0);
  const totalCogs = profitRows.reduce((sum, row) => sum + Number(row.cogs), 0);
  const totalProfit = profitRows.reduce((sum, row) => sum + Number(row.net_profit), 0);

  document.getElementById("stat-products").textContent = fmtNum(productRows.length);
  document.getElementById("stat-revenue").textContent = fmtMoney(totalRevenue);
  document.getElementById("stat-cogs").textContent = fmtMoney(totalCogs);

  // The only summary figure whose colour means anything: are we up or down?
  // When any SKU is uncosted the total is a LOWER BOUND, so it is prefixed with
  // "≥". A bare number here would claim a precision the data does not have.
  const unknownLines = profitRows.reduce((sum, r) => sum + Number(r.unknown_lines || 0), 0);
  const estimatedLines = profitRows.reduce((sum, r) => sum + Number(r.estimated_lines || 0), 0);
  const incomplete = unknownLines > 0 || estimatedLines > 0;

  const profitEl = document.getElementById("stat-profit");
  profitEl.textContent = (incomplete ? "≥ " : "") + fmtMoney(totalProfit);
  const why = [];
  if (unknownLines) why.push(`${unknownLines} line(s) have no known cost`);
  if (estimatedLines) why.push(`${estimatedLines} line(s) use an estimated cost`);
  profitEl.title = incomplete
    ? `Lower bound: ${why.join(" and ")}. The real figure is not yet knowable.`
    : "";
  profitEl.className = `stat-value ${signClass(totalProfit)}`.trim();
}

// ---- Low-level row renderers --------------------------------------------
function renderProfitRow(row, unitCost, hasCost) {
  const tr = document.createElement("tr");

  const skuCell = document.createElement("td");
  skuCell.textContent = row.sku ?? "";
  if (!storeSelect.value) {
    skuCell.title = storeName(row.store_id);
  }

  const nameCell = document.createElement("td");
  nameCell.textContent = row.product_name ?? "";
  nameCell.title = row.product_name ?? "";
  nameCell.classList.add("truncate");

  const unitsCell = document.createElement("td");
  unitsCell.textContent = fmtNum(row.units);

  const revenueCell = document.createElement("td");
  revenueCell.textContent = fmtMoney(row.revenue);

  const unknown = Number(row.unknown_lines || 0);
  const estimated = Number(row.estimated_lines || 0);

  const costCell = document.createElement("td");
  if (hasCost) {
    costCell.textContent = fmtMoney(unitCost);
    costCell.title = "Effective cost implied by the ledger (COGS / units).";
  } else {
    costCell.appendChild(noCostBadge());
    tr.classList.add("row-missing-cost");
  }

  const cogsCell = document.createElement("td");
  cogsCell.textContent = fmtMoney(row.cogs);

  const feesCell = document.createElement("td");
  feesCell.textContent = fmtMoney(row.fees);
  feesCell.title = "Walmart referral and label fees, third-party labels, and refund fees.";

  // Returns were folded invisibly into net before this -- a SKU could look
  // mediocre when it was actually selling well and being sent back.
  const returnsCell = document.createElement("td");
  const returns = Number(row.returns || 0);
  returnsCell.textContent = returns ? fmtMoney(returns) : "-";
  if (returns) {
    returnsCell.classList.add("neg");
    returnsCell.title = "Revenue reversed by customer refunds, dated to the refund.";
  } else {
    returnsCell.classList.add("muted");
  }

  const profitCell = document.createElement("td");
  // Unknown cost means profit is UNKNOWN, not zero -- so mark it as a bound
  // rather than printing a confident figure.
  profitCell.textContent = (unknown ? "≥ " : "") + fmtMoney(row.net_profit);
  const profitSign = signClass(row.net_profit);
  if (profitSign) profitCell.classList.add(profitSign);
  if (unknown) {
    profitCell.title = `Lower bound - ${unknown} line(s) have no known cost.`;
  } else if (estimated) {
    profitCell.title = `${estimated} line(s) use estimated fees or costs; this will firm up when Walmart settles.`;
    profitCell.classList.add("estimated");
  }

  tr.append(skuCell, nameCell, unitsCell, revenueCell, costCell, cogsCell,
            feesCell, returnsCell, profitCell);
  return tr;
}

function renderCatalogRow(row, unitCost, warehouseCost, hasCost) {
  const tr = document.createElement("tr");

  const skuCell = document.createElement("td");
  skuCell.textContent = row.sku ?? "";
  if (!storeSelect.value) {
    skuCell.title = storeName(row.store_id);
  }

  const nameCell = document.createElement("td");
  nameCell.textContent = row.product_name ?? "";
  nameCell.title = row.product_name ?? "";
  nameCell.classList.add("truncate");

  const priceCell = document.createElement("td");
  priceCell.textContent = row.price != null && row.price !== "" ? fmtMoney(row.price) : "";

  const statusCell = document.createElement("td");
  if (row.published_status) {
    statusCell.appendChild(renderStatusBadge(row.published_status));
  }

  const costCell = document.createElement("td");
  costCell.appendChild(renderCostInput(row.store_id, row.sku, unitCost, hasCost));

  const warehouseCostCell = document.createElement("td");
  warehouseCostCell.appendChild(renderWarehouseCostInput(row.store_id, row.sku, unitCost, warehouseCost));

  const actionsCell = document.createElement("td");
  actionsCell.className = "col-actions";
  if (hasCost && adminStores.has(row.store_id)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-xs";
    btn.textContent = "Clear";
    btn.title = "Remove the manual cost for this SKU";
    btn.addEventListener("click", () => clearProductCost(row.store_id, row.sku));
    actionsCell.appendChild(btn);
  }

  tr.append(skuCell, nameCell, priceCell, statusCell, costCell, warehouseCostCell, actionsCell);
  return tr;
}

async function clearProductCost(storeId, sku) {
  if (!confirm(`Remove the manual cost for ${sku}? Profit will fall back to landed cost from purchases, if any.`)) return;
  const { error } = await supabaseClient
    .from("product_costs")
    .delete()
    .eq("store_id", storeId)
    .eq("sku", sku);
  if (error) {
    console.error(error);
    showError(`Couldn't clear cost: ${error.message || error}`);
    return;
  }
  await invalidate("dashboard", "products", "order-details", "profit");
}

function renderStatusBadge(status) {
  const span = document.createElement("span");
  span.className = "badge";
  span.textContent = status.toLowerCase().replace(/_/g, " ");

  if (status === "PUBLISHED") {
    span.classList.add("badge-green");
  } else if (status === "UNPUBLISHED") {
    span.classList.add("badge-gray");
  } else {
    span.classList.add("badge-amber");
  }

  return span;
}

const moneyFmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const numFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  return Number.isFinite(n) ? moneyFmt.format(n) : String(v);
}

function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  return Number.isFinite(n) ? numFmt.format(n) : String(v);
}

function fmtDate(v) {
  if (!v) return "";
  const d = new Date(`${String(v).slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? String(v)
    : d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

function fmtMonth(v) {
  if (!v) return "";
  const d = new Date(`${String(v).slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? String(v)
    : d.toLocaleDateString("en-US", { year: "numeric", month: "short" });
}

// "" for zero/non-numeric, else "pos"/"neg" for coloring gains vs losses.
function signClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

// Build a <td> from a cell spec. Plain string/number -> text. Object forms:
// {money}, {num}, {date} formatted; {truncate} ellipsized w/ tooltip; {text} raw.
// Optional {cls} adds a class (e.g. "col-fees" or a sign color).
function cell(spec) {
  const td = document.createElement("td");
  if (spec === null || spec === undefined) return td;
  if (typeof spec !== "object") {
    td.textContent = spec;
    return td;
  }
  if ("edit" in spec) {
    const { table, row } = spec.edit;
    td.classList.add("col-actions");
    if (adminStores.has(row.store_id)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-secondary btn-xs";
      btn.textContent = "Edit";
      btn.addEventListener("click", () => openEditModal(table, row));
      td.appendChild(btn);
    }
    return td;
  }
  if ("link" in spec) {
    if (spec.link) {
      const a = document.createElement("a");
      a.href = spec.link;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = "open";
      td.appendChild(a);
    }
    if (spec.cls) td.classList.add(spec.cls);
    return td;
  }
  // Long text lives in an inner block: a <td> ignores max-width under
  // table-layout:auto (the wide tables), so the column would stretch to the
  // longest product title. The full text stays in the tooltip.
  if ("truncate" in spec) {
    const text = spec.truncate ?? "";
    td.title = text;
    td.classList.add("truncate");
    const inner = document.createElement("span");
    inner.className = "truncate-inner";
    inner.textContent = text;
    td.appendChild(inner);
    if (spec.cls) td.classList.add(spec.cls);
    return td;
  }
  let text = "";
  if ("money" in spec) text = fmtMoney(spec.money);
  else if ("num" in spec) text = fmtNum(spec.num);
  else if ("date" in spec) text = fmtDate(spec.date);
  else if ("text" in spec) text = spec.text ?? "";
  if (spec.cls) td.classList.add(spec.cls);
  td.textContent = text;
  return td;
}

function renderRow(values) {
  const tr = document.createElement("tr");
  for (const value of values) tr.appendChild(cell(value));
  return tr;
}

function renderPurchaseRow(row, nameMap) {
  const qty = Number(row.quantity) || 0;
  const totalCost = qty * (Number(row.unit_cost) || 0) + (Number(row.sales_tax) || 0) + (Number(row.shipping) || 0);
  const landed = qty > 0 ? totalCost / qty : 0;
  return renderRow([
    { date: row.purchase_date },
    row.order_number,
    { truncate: row.product_name || nameMap.get(`${row.store_id}|${row.sku}`) || "" },
    row.sku,
    row.source,
    { link: row.product_link },
    { num: row.quantity },
    { money: row.unit_cost },
    { money: row.sales_tax },
    { money: row.shipping },
    { money: totalCost },
    { money: landed },
    row.status,
    row.notes,
    { edit: { table: "purchases", row } },
  ]);
}

function renderDamagedRow(row, nameMap) {
  return renderRow([
    { date: row.damaged_date },
    row.sku,
    { truncate: nameMap.get(`${row.store_id}|${row.sku}`) || "" },
    { num: row.quantity },
    row.reason,
    row.notes,
    { edit: { table: "damaged_goods", row } },
  ]);
}

function renderInventoryRow(row) {
  return renderRow([
    row.sku,
    { truncate: row.product_name || "" },
    { num: row.purchased },
    { num: row.sold },
    { num: row.returned },
    { num: row.damaged },
    { num: row.adjusted },
    { num: row.on_hand },
  ]);
}

function renderReturnRow(row) {
  return renderRow([
    { date: row.order_date },
    row.sku,
    { truncate: row.product_name || "" },
    { num: row.quantity },
    { money: row.product_revenue },
    row.status,
  ]);
}

function renderCostInput(storeId, sku, unitCost, hasCost) {
  const input = document.createElement("input");
  input.type = "number";
  input.step = "0.01";
  input.min = "0";
  if (hasCost) {
    input.value = unitCost;
  } else {
    input.value = "";
    input.placeholder = "no cost";
    input.classList.add("missing-cost");
  }
  input.addEventListener("change", () => saveUnitCost(storeId, sku, input.value));
  return input;
}

async function saveUnitCost(storeId, sku, value) {
  const unitCost = parseFloat(value) || 0;
  const { error } = await supabaseClient
    .from("product_costs")
    .upsert({ store_id: storeId, sku, unit_cost: unitCost });

  if (error) {
    console.error(error);
    showError(`Couldn't save unit cost: ${error.message || error}`);
    return;
  }

  await invalidate("dashboard", "products", "order-details", "profit");
}

function renderWarehouseCostInput(storeId, sku, unitCost, warehouseCost) {
  const input = document.createElement("input");
  input.type = "number";
  input.step = "0.01";
  input.min = "0";
  input.value = warehouseCost;
  input.addEventListener("change", () => saveWarehouseCost(storeId, sku, unitCost, input.value));
  return input;
}

async function saveWarehouseCost(storeId, sku, unitCost, value) {
  const warehouseCost = parseFloat(value) || 0;
  const { error } = await supabaseClient
    .from("product_costs")
    .upsert({ store_id: storeId, sku, unit_cost: unitCost, warehouse_cost: warehouseCost });

  if (error) {
    console.error(error);
    showError(`Couldn't save warehouse cost: ${error.message || error}`);
    return;
  }

  await invalidate("products", "order-details", "profit");
}

function customerDetails(row) {
  return [row.customer_name, row.customer_city, row.customer_state]
    .filter((part) => part)
    .join(", ");
}

function renderOrderDetailRow(row, serial) {
  const totalWarehouseCost = (Number(row.order_qty) || 0) * (Number(row.warehouse_cost) || 0);

  const tr = renderRow([
    { num: serial },
    { date: row.order_date },
    row.customer_order_id,
    row.sku,
    { truncate: row.product_name || "" },
    { truncate: customerDetails(row) },
    { num: row.order_qty },
    { money: row.unit_selling_price },
    { money: row.extra_shipping },
    { money: row.unit_purchase_price },
    { money: row.total_selling_price },
    { money: row.total_extra_shipping },
    { money: row.total_purchase_price },
    { money: row.walmart_fees, cls: "col-fees" },
    { money: row.wfs_label_fees, cls: "col-fees" },
    { money: totalWarehouseCost },
    { money: row.total_cost },
    { money: row.net_profit, cls: signClass(row.net_profit) },
    { text: row.margin_pct != null ? `${fmtNum(row.margin_pct)}%` : "", cls: signClass(row.margin_pct) },
    row.order_status,
    { money: row.refund_fees, cls: "col-fees" },
    { money: row.extra_service_fees, cls: "col-fees" },
  ]);
  tr._order = row;
  return tr;
}

purchaseForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const storeId = requireStore();
  if (!storeId) return;

  const { error } = await supabaseClient.from("purchases").insert({
    store_id: storeId,
    sku: document.getElementById("purchase-sku").value.trim(),
    product_name: document.getElementById("purchase-name").value.trim() || null,
    source: document.getElementById("purchase-source").value.trim() || null,
    product_link: document.getElementById("purchase-link").value.trim() || null,
    order_number: document.getElementById("purchase-order").value.trim() || null,
    quantity: parseFloat(document.getElementById("purchase-qty").value),
    unit_cost: parseFloat(document.getElementById("purchase-cost").value),
    sales_tax: parseFloat(document.getElementById("purchase-tax").value) || 0,
    shipping: parseFloat(document.getElementById("purchase-shipping").value) || 0,
    status: document.getElementById("purchase-status").value.trim() || null,
    purchase_date: document.getElementById("purchase-date").value,
    notes: document.getElementById("purchase-notes").value.trim() || null,
  });

  if (error) {
    console.error(error);
    alert(error.message);
    return;
  }

  purchaseForm.reset();
  document.getElementById("purchase-date").value = today();
  // purchases now drive COGS via landed cost, so profit views are stale too
  await invalidate("purchases", "inventory", "dashboard", "order-details", "profit");
});

damagedForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const storeId = requireStore();
  if (!storeId) return;

  const { error } = await supabaseClient.from("damaged_goods").insert({
    store_id: storeId,
    sku: document.getElementById("damaged-sku").value.trim(),
    quantity: parseFloat(document.getElementById("damaged-qty").value),
    reason: document.getElementById("damaged-reason").value.trim() || null,
    damaged_date: document.getElementById("damaged-date").value,
    notes: document.getElementById("damaged-notes").value.trim() || null,
  });

  if (error) {
    console.error(error);
    alert(error.message);
    return;
  }

  damagedForm.reset();
  document.getElementById("damaged-date").value = today();
  await invalidate("damaged", "inventory");
});

adjustmentForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const storeId = requireStore();
  if (!storeId) return;

  const { error } = await supabaseClient.from("inventory_adjustments").insert({
    store_id: storeId,
    sku: document.getElementById("adjustment-sku").value.trim(),
    quantity_delta: parseFloat(document.getElementById("adjustment-qty").value),
    reason: document.getElementById("adjustment-reason").value.trim() || null,
    adjusted_date: document.getElementById("adjustment-date").value,
    notes: document.getElementById("adjustment-notes").value.trim() || null,
  });

  if (error) {
    console.error(error);
    alert(error.message);
    return;
  }

  adjustmentForm.reset();
  document.getElementById("adjustment-date").value = today();
  await invalidate("inventory");
});

init();
