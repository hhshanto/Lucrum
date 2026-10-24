-- 002_access
--
-- US-001 — Sign in securely. Module: Access.
--
-- Four tables:
--   users                    identity, credentials, lockout bookkeeping
--   user_sessions            server-side sessions (access-v1: sign-out and
--                            password change invalidate access immediately)
--   user_store_assignments   store scope for Admin and Manager (seeded
--                            directly by tests in this story; assignment
--                            *management* is US-004)
--   access_policy            timeout and lockout values as configuration,
--                            never constants (access-v1)
--
-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction.

create extension if not exists "citext";

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

create table users (
  id                     uuid primary key default gen_random_uuid(),
  email                  citext      not null unique,
  role                   text        not null,

  -- Argon2id, or bcrypt cost >= 12 (access-v1). Never returned by any query
  -- used for display -- excluded at the repository layer, see access/repository.py.
  password_hash          text        not null,
  password_updated_at    timestamptz not null default now(),

  -- TOTP secret, required for Owner and Admin, optional for Manager.
  mfa_secret             text,
  mfa_enabled            boolean     not null default false,

  -- Lockout bookkeeping. Thresholds and durations live in access_policy,
  -- never as a literal here or in application code.
  failed_sign_in_count   integer     not null default 0,
  locked_until           timestamptz,

  last_sign_in_at        timestamptz,
  is_active              boolean     not null default true,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),

  constraint users_role_check
    check (role in ('owner', 'admin', 'manager')),

  -- access-v1: MFA is required for Owner and Admin. Structural, not just a
  -- service-layer check, so enforcement cannot be bypassed by a bad row --
  -- see service.sign_in's defence-in-depth comment for the other half.
  constraint users_role_requires_mfa
    check (role not in ('owner', 'admin') or mfa_enabled),

  -- F-09 (US-001 audit): the constraint above made `mfa_enabled` structural
  -- but left `mfa_secret` free to be null regardless, so a row could satisfy
  -- every constraint with `mfa_enabled = true, mfa_secret = null` -- an
  -- account that is permanently unsignable-in and fails with a generic
  -- "invalid authentication code" nobody could diagnose. `mfa_enabled` is a
  -- claim; this is what makes the claim complete, for every role, not only
  -- the privileged ones.
  constraint users_mfa_enabled_requires_secret
    check (not mfa_enabled or mfa_secret is not null)
);

comment on column users.password_hash is
  'Argon2id, or bcrypt cost >= 12 (access-v1). Never selected for display; excluded at the repository layer.';
comment on column users.mfa_secret is
  'TOTP secret. Never selected for display; excluded at the repository layer.';
comment on constraint users_role_requires_mfa on users is
  'access-v1: MFA required for Owner and Admin, optional for Manager.';
comment on constraint users_mfa_enabled_requires_secret on users is
  'F-09: mfa_enabled=true with no secret is structurally impossible, for every role.';

-- ---------------------------------------------------------------------------
-- user_sessions
--
-- Server-side, because access-v1 requires sign-out to invalidate access
-- immediately -- a client-side-only token cannot be revoked.
-- ---------------------------------------------------------------------------

create table user_sessions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid        not null references users(id),

  -- A hash of the session token, never the token itself (access-v1 Leakage:
  -- no token in anything that could be read off a database dump).
  token_hash     text        not null unique,

  issued_at      timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),

  -- Absolute timeout horizon, computed at issue time from the *then-current*
  -- access_policy value. Idle timeout is not stored here -- it is computed
  -- at validate time from last_seen_at plus the current policy value, so a
  -- change to the policy takes effect without touching existing sessions.
  expires_at     timestamptz not null,

  revoked_at     timestamptz,
  ip_address     text,
  user_agent     text
);

create index user_sessions_user_idx on user_sessions (user_id);

comment on column user_sessions.token_hash is
  'Hash of the session token. The token itself is never stored (access-v1 Leakage).';
comment on column user_sessions.expires_at is
  'Absolute timeout horizon, fixed at issue time from access_policy.session_absolute_timeout_minutes.';

-- ---------------------------------------------------------------------------
-- user_store_assignments
--
-- access-v1 store scope: Owner has every store implicitly; Admin and
-- Manager have only an active row here. Seeded directly by tests in this
-- story -- assignment *management* (who may create/remove a row) is US-004.
-- ---------------------------------------------------------------------------

create table user_store_assignments (
  user_id   uuid not null references users(id),
  store_id  uuid not null references stores(id),

  primary key (user_id, store_id)
);

comment on table user_store_assignments is
  'access-v1 store scope for Admin and Manager. An unassigned store must be indistinguishable from a non-existent one (US-002 AC-02).';

-- ---------------------------------------------------------------------------
-- access_policy
--
-- access-v1: "Timeout values are configuration, not constants." Extended to
-- the lockout threshold and duration, which are no less a business decision.
-- ---------------------------------------------------------------------------

create table access_policy (
  policy_key    text    primary key,
  policy_value  integer not null
);

comment on table access_policy is
  'access-v1 configuration: session timeouts and sign-in lockout. Read at validate/sign-in time, never cached as a constant.';

-- access-v1 states the timeouts explicitly: idle 30 minutes, absolute 12
-- hours. It does not state a lockout threshold or lockout duration -- both
-- are chosen here as a reasonable initial value and recorded as such, not
-- discovered. The Owner should review both; see the session report for this
-- story.
insert into access_policy (policy_key, policy_value) values
  ('session_idle_timeout_minutes',      30),
  ('session_absolute_timeout_minutes',  720),
  -- Chosen, not specified: access-v1 requires "temporary lockout after
  -- repeated failures" without a number. Five failed attempts is a common
  -- industry default balancing nuisance lockouts against brute-force risk.
  ('sign_in_failure_lockout_threshold', 5),
  -- Chosen, not specified: access-v1 does not state how long the lockout
  -- lasts. Fifteen minutes is a conservative initial value.
  ('sign_in_lockout_duration_minutes',  15);
