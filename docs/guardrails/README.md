# Guardrails

Executable enforcement for the rules in `walmart-user-stories.md`.

## The problem this solves

The specification states about thirty rules. Most are **prohibitions**, and each one makes the code *more awkward in the moment*. Under deadline, or in a cold AI session six weeks from now, the convenient choice and the correct choice point in opposite directions.

Documentation loses that fight every time. These files do not.

| Layer | Catches | When it fails |
|---|---|---|
| `sql/001_invariants.sql` | Illegal rows | At write time. The violation is impossible |
| `sql/002_selftest.sql` | Illegal schema | In CI, after migrations |
| `check_invariants.py` | Illegal code | In CI, before merge |
| `golden/profit_v1_cases.json` | Wrong arithmetic | In unit tests |
| `INVARIANTS.md` → BT-xx | Wrong behaviour | In integration tests you write |

## Setup

### 1. Database constraints

Apply after your base schema migrations, and keep applying on every deploy:

```bash
psql "$DATABASE_URL" -f guardrails/sql/001_invariants.sql
```

These are triggers and check constraints. They make violations fail at write time, which is the only enforcement that cannot be argued with.

### 2. Schema self-test

Run in CI after migrations. Exits non-zero on drift:

```bash
psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f guardrails/sql/002_selftest.sql
```

This catches things a constraint cannot see: a float money column, a generated profit, a globally-unique order id, a forbidden table someone added.

### 3. Static scan

```bash
python guardrails/check_invariants.py src
python guardrails/check_invariants.py --list
python guardrails/check_invariants.py --explain SC-03
```

Zero dependencies. Point it at your source root. Exits 1 on any violation.

### 4. Golden numbers

Before implementing the calculation, fill `golden/profit_v1_cases.json` with at least twenty real orders from your spreadsheet. Then make the implementation reproduce them.

This ordering matters. If you implement first and record the output as "expected", you have tested that the code does what the code does.

## CI wiring

GitHub Actions:

```yaml
name: invariants
on: [push, pull_request]

jobs:
  invariants:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres }
        options: >-
          --health-cmd pg_isready --health-interval 10s
          --health-timeout 5s --health-retries 5
        ports: ['5432:5432']
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }

      - name: Static invariant scan
        run: python guardrails/check_invariants.py src

      - name: Apply migrations
        run: ./scripts/migrate.sh
        env:
          DATABASE_URL: postgres://postgres:postgres@localhost:5432/postgres

      - name: Apply invariant constraints
        run: psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f guardrails/sql/001_invariants.sql
        env:
          DATABASE_URL: postgres://postgres:postgres@localhost:5432/postgres

      - name: Schema self-test
        run: psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f guardrails/sql/002_selftest.sql
        env:
          DATABASE_URL: postgres://postgres:postgres@localhost:5432/postgres
```

Pre-commit hook, if you want the faster feedback:

```yaml
- repo: local
  hooks:
    - id: invariants
      name: invariant scan
      entry: python guardrails/check_invariants.py
      language: system
      pass_filenames: true
```

## Suppressing a finding

Per line, with a mandatory reason:

```python
legacy = payload["order_status"]  # guardrail:allow SC-01 third-party payload field name
```

A suppression without a reason is itself a failure. Database invariants cannot be suppressed at all — that is why the important ones live there.

Grep suppressions during review:

```bash
grep -rn "guardrail:allow" src
```

A rising count is a signal. Either the rule is wrong, or the design drifted. Investigate; do not accumulate.

## Verifying the guardrails themselves

`_selftest/` holds a deliberately-violating fixture and a clean one:

```bash
python guardrails/check_invariants.py guardrails/_selftest/src/bad.py    # expect 9 rules, exit 1
python guardrails/check_invariants.py guardrails/_selftest/src/good.py   # expect clean, exit 0
```

If `bad.py` ever comes back clean, the scanner has been broken and is giving false confidence — which is worse than having no scanner.

## Adding a rule

1. Add the invariant to `INVARIANTS.md` with an id and the policy it comes from.
2. Prefer `DDL` over `CATALOG` over `SCAN`. Earlier layers cannot be argued with.
3. Add a violating line to `_selftest/src/bad.py` and confirm it fires.
4. Confirm `good.py` still passes. A rule with false positives gets disabled within a week, which is worse than no rule.

## What this does not cover

Be clear-eyed about the limits.

- **Concurrency.** No scanner detects a missing serializable transaction. BT-04 must be a real test.
- **Semantics.** `has_ever_been_closed` can be read and then ignored. SC-03 catches the obvious shape, not the logic.
- **Design drift at a higher level.** Nothing here notices if someone builds the right rules into the wrong module.
- **The spec being wrong.** These enforce consistency with the specification, not correctness of the business rules.

The human review ritual in `INVARIANTS.md` exists because of these gaps. Six questions per pull request.
