"""FastAPI server: serves the dashboard SPA, hands the browser its public
Supabase config, and exposes an authenticated /sync/orders endpoint that pulls
fresh Walmart orders into Supabase by running the existing syncer job.

Auth and data reads still happen client-side via supabase-js + RLS. The only
privileged action here is the sync, which is gated behind a valid Supabase
session token and runs with the service key inside the syncer subprocess.
"""

import os
import subprocess
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client

load_dotenv()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SYNCER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "syncer"))

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_assets(request: Request, call_next):
    """Force the browser to revalidate the SPA shell and static assets on every
    load, so a plain reload picks up new builds. The etag still yields a 304
    when nothing actually changed, so this stays cheap."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/config")
def config():
    return {
        "supabaseUrl": os.environ["SUPABASE_URL"],
        "supabaseAnonKey": os.environ["SUPABASE_ANON_KEY"],
    }


def _require_user(request: Request):
    """Reject the request unless it carries a valid Supabase session token."""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not token:
        raise HTTPException(status_code=401, detail="Sign in first.")
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    try:
        result = client.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Your session has expired — sign in again.")
    if not result or not getattr(result, "user", None):
        raise HTTPException(status_code=401, detail="Your session has expired — sign in again.")
    return result.user


def _require_store_access(request: Request, store_id: str):
    """Caller must be signed in AND have access to this specific store.

    Stores belong to different owners, so a signed-in user must never be able to
    trigger work against a store they aren't on -- that would spend another
    owner's Walmart API quota using that owner's credentials.
    """
    user = _require_user(request)
    if not store_id:
        raise HTTPException(status_code=400, detail="Select a store first.")
    svc = _service_client()
    rows = (
        svc.table("store_access")
        .select("store_id")
        .eq("user_id", user.id)
        .eq("store_id", store_id)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=403, detail="You don't have access to that store.")
    return user


@app.post("/sync/orders")
def sync_orders(request: Request, days: int = 30, store_id: str = ""):
    """Pull fresh Walmart orders + catalog for ONE store the caller has access to.

    Runs the existing `syncer/sync.py` job as a subprocess (so its local
    `secrets`/`walmart_client` imports resolve exactly as when run by hand),
    then returns its output for the browser to surface before it re-queries.
    """
    _require_store_access(request, store_id)
    days = min(max(days, 1), 180)
    try:
        proc = subprocess.run(
            [sys.executable, "sync.py", "--days", str(days), "--store-id", store_id],
            cwd=SYNCER_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Sync took too long and was stopped.")

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    failed = proc.returncode != 0 or "FAILED" in output
    return {"ok": not failed, "returncode": proc.returncode, "output": output}


@app.post("/sync/settlement")
def sync_settlement(request: Request, days: int = 30, store_id: str = ""):
    """Pull Walmart's settlement (recon) report into settlement_lines for ONE
    store the caller has access to -- the source of referral fees, label/WFS
    fees, refunds, etc. Runs the existing syncer/sync_settlement.py job.
    """
    _require_store_access(request, store_id)
    days = min(max(days, 1), 90)
    try:
        proc = subprocess.run(
            [sys.executable, "sync_settlement.py", "--days", str(days), "--store-id", store_id],
            cwd=SYNCER_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Settlement sync took too long and was stopped.")

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    failed = proc.returncode != 0 or "FAILED" in output
    return {"ok": not failed, "returncode": proc.returncode, "output": output}


def _encrypt_walmart_secret(plaintext: str) -> str:
    """Same Fernet scheme as syncer/secrets.py, inlined so the web app doesn't
    import a module named `secrets` and shadow the stdlib one."""
    from cryptography.fernet import Fernet

    key = os.environ.get("WALMART_SECRET_KEY")
    if not key:
        raise HTTPException(
            status_code=500,
            detail="WALMART_SECRET_KEY isn't configured, so the client secret can't be encrypted.",
        )
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def _service_client():
    """Service-key client. Bypasses RLS -- never expose this to the browser."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _require_admin(request: Request):
    """Caller must be signed in AND an admin of at least one store."""
    user = _require_user(request)
    svc = _service_client()
    rows = svc.table("store_access").select("store_id, role").eq("user_id", user.id).execute().data
    admin_stores = {r["store_id"] for r in rows if r.get("role") == "admin"}
    if not admin_stores:
        raise HTTPException(status_code=403, detail="Admins only.")
    return user, admin_stores, svc


@app.get("/admin/users")
def list_users(request: Request):
    """Everyone with access to a store you administer."""
    user, admin_stores, svc = _require_admin(request)
    emails = {u.id: u.email for u in svc.auth.admin.list_users()}
    stores = {s["store_id"]: s["name"] for s in svc.table("stores").select("store_id, name").execute().data}
    access = svc.table("store_access").select("store_id, user_id, role").execute().data

    users = [
        {
            "user_id": a["user_id"],
            "email": emails.get(a["user_id"], "(unknown)"),
            "store_id": a["store_id"],
            "store": stores.get(a["store_id"], "?"),
            "role": a["role"],
            "is_self": a["user_id"] == user.id,
        }
        for a in access
        if a["store_id"] in admin_stores
    ]
    users.sort(key=lambda r: (r["email"], r["store"]))
    # No orphan list: a login with no grant belongs to no store, so it can't be
    # scoped to the stores you administer -- listing them would leak other
    # owners' emails. Instead, a user with no access is told so on sign-in.
    return {"users": users}


@app.post("/admin/users")
async def add_user(request: Request):
    """Create the account if needed, then grant it a role on a store."""
    user, admin_stores, svc = _require_admin(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    role = body.get("role") or "member"
    store_id = body.get("store_id")

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required.")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be admin or member.")
    if store_id not in admin_stores:
        raise HTTPException(status_code=403, detail="You don't administer that store.")

    existing = next((u for u in svc.auth.admin.list_users() if (u.email or "").lower() == email), None)
    if existing:
        user_id = existing.id
    else:
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="New accounts need a password of at least 8 characters.")
        created = svc.auth.admin.create_user({"email": email, "password": password, "email_confirm": True})
        user_id = created.user.id

    svc.table("store_access").upsert({"store_id": store_id, "user_id": user_id, "role": role}).execute()
    return {"ok": True, "email": email, "role": role, "created": existing is None}


@app.post("/admin/users/role")
async def set_user_role(request: Request):
    user, admin_stores, svc = _require_admin(request)
    body = await request.json()
    user_id, store_id, role = body.get("user_id"), body.get("store_id"), body.get("role")

    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be admin or member.")
    if store_id not in admin_stores:
        raise HTTPException(status_code=403, detail="You don't administer that store.")
    # Don't let an admin demote themselves and lock everyone out.
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You can't change your own role.")

    svc.table("store_access").update({"role": role}).eq("store_id", store_id).eq("user_id", user_id).execute()
    return {"ok": True}


@app.post("/admin/users/revoke")
async def revoke_user(request: Request):
    """Remove data access. The login still exists, it just sees nothing."""
    user, admin_stores, svc = _require_admin(request)
    body = await request.json()
    user_id, store_id = body.get("user_id"), body.get("store_id")

    if store_id not in admin_stores:
        raise HTTPException(status_code=403, detail="You don't administer that store.")
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You can't revoke your own access.")

    svc.table("store_access").delete().eq("store_id", store_id).eq("user_id", user_id).execute()
    return {"ok": True}


@app.get("/admin/stores")
def list_stores(request: Request):
    user, admin_stores, svc = _require_admin(request)
    stores = svc.table("stores").select("store_id, name, active, added_at").execute().data
    creds = {c["store_id"] for c in svc.table("store_credentials").select("store_id").execute().data}
    out = [
        {**s, "has_credentials": s["store_id"] in creds}
        for s in stores
        if s["store_id"] in admin_stores
    ]
    out.sort(key=lambda s: s["name"] or "")
    return {"stores": out}


@app.post("/admin/stores")
async def add_store(request: Request):
    """Register a Walmart store, encrypt its secret, and grant the creator admin.

    The grant matters: without it RLS hides the new store from its own creator,
    so it would never appear in the store picker.
    """
    user, _admin_stores, svc = _require_admin(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    client_id = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()

    if not name or not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Store name, client ID and client secret are all required.")

    encrypted = _encrypt_walmart_secret(client_secret)  # before insert: fail early if the key is missing

    store = svc.table("stores").insert({"name": name}).execute()
    store_id = store.data[0]["store_id"]
    svc.table("store_credentials").insert({
        "store_id": store_id,
        "client_id": client_id,
        "encrypted_client_secret": encrypted,
    }).execute()
    svc.table("store_access").insert({"store_id": store_id, "user_id": user.id, "role": "admin"}).execute()
    return {"ok": True, "store_id": store_id, "name": name}


@app.post("/admin/stores/active")
async def set_store_active(request: Request):
    """Active stores are the ones the syncer pulls orders/fees for."""
    user, admin_stores, svc = _require_admin(request)
    body = await request.json()
    store_id = body.get("store_id")
    active = bool(body.get("active"))
    if store_id not in admin_stores:
        raise HTTPException(status_code=403, detail="You don't administer that store.")
    svc.table("stores").update({"active": active}).eq("store_id", store_id).execute()
    return {"ok": True}


@app.post("/admin/stores/delete")
async def delete_store(request: Request):
    """Permanently delete a store and everything under it.

    Every table FKs to stores(store_id) ON DELETE CASCADE, so this one delete
    removes the store's orders, purchases, costs, settlement, fee map and all
    access grants. There is no undo -- hence the exact-name confirmation, which
    is re-checked here rather than trusted from the browser.
    """
    user, admin_stores, svc = _require_admin(request)
    body = await request.json()
    store_id = body.get("store_id")
    confirm_name = (body.get("confirm_name") or "").strip()

    if store_id not in admin_stores:
        raise HTTPException(status_code=403, detail="You don't administer that store.")
    rows = svc.table("stores").select("name").eq("store_id", store_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Store not found.")
    name = rows[0]["name"]
    if confirm_name != name:
        raise HTTPException(status_code=400, detail="The name didn't match. Nothing was deleted.")

    # Count first, so the answer can say honestly what went.
    breakdown = {}
    for table in (
        "orders", "order_lines", "products", "product_costs", "purchases",
        "damaged_goods", "inventory_adjustments", "settlement_lines",
        "manual_order_fees", "fee_category_map", "store_credentials", "store_access",
    ):
        try:
            breakdown[table] = (
                svc.table(table).select("*", count="exact").eq("store_id", store_id).limit(0).execute().count or 0
            )
        except Exception:
            breakdown[table] = 0

    svc.table("stores").delete().eq("store_id", store_id).execute()  # cascades
    return {"ok": True, "name": name, "deleted": sum(breakdown.values()), "breakdown": breakdown}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
