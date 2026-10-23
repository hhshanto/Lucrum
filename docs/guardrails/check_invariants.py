#!/usr/bin/env python3
"""
Walmart Store Manager - static invariant scanner.

guardrail:selfexempt -- this file defines the forbidden patterns.

Catches the prohibitions that no database constraint can see, because they
live in application code rather than in rows.

Every rule here exists because violating it is LOCALLY CONVENIENT. That is
precisely why prose did not hold: at the moment of writing, collapsing a status
or defaulting a currency always looks like the tidy choice. The scanner removes
the option.

Usage:
    python check_invariants.py [paths...]
    python check_invariants.py --list
    python check_invariants.py --explain SC-03

Exit codes:
    0  clean
    1  one or more violations
    2  bad invocation

Zero dependencies. Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_ROOTS = ["src", "app", "backend", "frontend", "api", "db", "migrations"]

SCANNED_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".sql", ".go", ".rb", ".java", ".kt", ".cs", ".php",
    ".vue", ".svelte", ".graphql", ".prisma",
}

EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".pytest_cache",
    "vendor", "target", ".mypy_cache", ".ruff_cache",
}

# A file may exempt itself entirely by carrying this marker near the top.
# Reserved for the guardrail layer itself, which necessarily contains every
# forbidden pattern in order to detect them. Do NOT use it in product code --
# use a per-line `guardrail:allow <ID> <reason>` instead, which stays reviewable.
SELF_EXEMPT = "guardrail:selfexempt"
SELF_EXEMPT_SCAN_LINES = 30

# Paths where a rule is legitimately allowed to appear.
# Keep these narrow. A widening allowlist is a design smell.
CALCULATION_MODULE = ["*calculation*", "*profit*policy*", "*domain/calculation*"]
AUTH_MODULE = ["*auth*", "*identity*", "*security*"]

SUPPRESSION = re.compile(r"guardrail:allow\s+(?P<id>[A-Z]{2}-\d{2})\s+(?P<reason>\S.*)")
BARE_SUPPRESSION = re.compile(r"guardrail:allow\s+([A-Z]{2}-\d{2})\s*$")


@dataclass
class Rule:
    id: str
    summary: str
    why: str
    pattern: re.Pattern
    allow_globs: list[str] = field(default_factory=list)
    suffixes: set[str] | None = None

    def applies_to(self, path: Path) -> bool:
        if self.suffixes and path.suffix not in self.suffixes:
            return False
        posix = path.as_posix()
        return not any(fnmatch.fnmatch(posix, g) for g in self.allow_globs)


def rx(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


RULES: list[Rule] = [
    Rule(
        id="SC-01",
        summary="Combined order status identifier",
        why=(
            "Fulfillment, refund activity, reconciliation and period protection are "
            "four independent dimensions (BR-10 interpretation). A single combined "
            "status is what killed the previous system."
        ),
        pattern=rx(r"\border[_\s]?status\b|\borderStatus\b"),
    ),
    Rule(
        id="SC-02",
        summary="Hardcoded manual cost edit limit",
        why=(
            "BR-06 leaves the numeric limit to configuration and cost-edit-v1 stores "
            "it in cost_edit_policies. The literal belongs in a seed row, never in code."
        ),
        pattern=rx(
            r"max_edits\s*[=!<>]=?\s*\d"
            r"|MAX_EDITS\s*[:=]\s*\d"
            r"|edit_limit\s*[:=]\s*\d"
            r"|edit_count\s*[<>]=?\s*\d"
        ),
    ),
    Rule(
        id="SC-03",
        summary="Period protection checked by current status",
        why=(
            "BR-08 protection follows closure HISTORY, not current status. Checking "
            "status == 'closed' lets an Admin who reopened a month edit its financials "
            "- the exact escalation BR-08 forbids. Use has_ever_been_closed."
        ),
        pattern=rx(
            r"status(_code)?\s*[=!]==?\s*['\"]closed['\"]"
            r"|['\"]closed['\"]\s*[=!]==?\s*status(_code)?"
            r"|status(_code)?\s*(==|!=)\s*MonthStatus\.CLOSED"
        ),
    ),
    Rule(
        id="SC-04",
        summary="Binary float arithmetic on money",
        why=(
            "profit-v1 requires exact decimal arithmetic with a single half-up "
            "rounding at the end. Floats silently lose cents and the loss compounds "
            "across six cost components."
        ),
        pattern=rx(
            r"parseFloat\s*\(|\.toFixed\s*\(|\bfloat\s*\(\s*(?!0\)|1\))"
            r"|\bdouble\s+precision\b|\bFLOAT8\b|\bREAL\b"
        ),
    ),
    Rule(
        id="SC-05",
        summary="Defaulted or fallback currency code",
        why=(
            "BR-12 stores a currency with every amount. An amount whose currency "
            "cannot be determined must be rejected, not assumed to be USD."
        ),
        pattern=rx(
            r"currency(_code)?\s*[:=]\s*['\"][A-Z]{3}['\"]"
            r"|(or|\?\?|\|\|)\s*['\"]USD['\"]"
            r"|DEFAULT\s+'[A-Z]{3}'"
        ),
    ),
    Rule(
        id="SC-06",
        summary="Cost component coerced to zero",
        why=(
            "profit-v1 distinguishes unknown from zero. Coalescing a missing cost to "
            "0 produces a confident wrong profit instead of an honest incomplete one."
        ),
        pattern=rx(
            r"COALESCE\s*\(\s*[\w.\"]*(cost|fee|amount|profit|recovered)[\w.\"]*\s*,\s*0"
            r"|(cost|fee|amount|profit|recovered)[\w\"'\)\]]*\s+or\s+0\b"
            r"|(cost|fee|amount|profit|recovered)[\w\"'\)\]]*\s*(\?\?|\|\|)\s*0\b"
        ),
    ),
    Rule(
        id="SC-07",
        summary="Profit assigned outside the calculation module",
        why=(
            "profit-v1 must have exactly one implementation so its version stamp "
            "means something. A second copy will diverge and the stamp becomes a lie."
        ),
        pattern=rx(r"(estimated_net_profit|settled_net_profit|_roi)\s*=(?!=)"),
        allow_globs=CALCULATION_MODULE,
    ),
    Rule(
        id="SC-08",
        summary="Manager compensation identifier",
        why="BR-09 puts compensation entirely outside this application.",
        pattern=rx(r"\b(manager_payment|compensation|payroll|commission_payout)\b"),
    ),
    Rule(
        id="SC-09",
        summary="Order line item identifier",
        why="BR-02: an order carries exactly one SKU. There are no line items.",
        pattern=rx(r"\b(order_items?|order_lines?|line_items?)\b"),
    ),
    Rule(
        id="SC-10",
        summary="Defaulted reporting date basis",
        why=(
            "period-v1 defines four bases that answer different questions. Defaulting "
            "date_basis silently picks one and the caller never learns which."
        ),
        pattern=rx(
            r"['\"]?date_basis['\"]?\s*[:=]\s*['\"]"
            r"|date_basis[\w\"'\)\]]*\s*(\?\?|\|\||or)\s*['\"]"
        ),
    ),
    Rule(
        id="SC-11",
        summary="Currency conversion code",
        why="BR-12 and period-v1: no conversion exists in this release.",
        pattern=rx(r"\b(fx_rate|exchange_rate|convert_currency|to_base_currency)\b"),
    ),
    Rule(
        id="SC-12",
        summary="Reconciliation or refund value in a fulfillment context",
        why=(
            "settlement_pending and reconciled are reconciliation values. refunded is "
            "settlement evidence. None of them is a fulfillment state."
        ),
        pattern=rx(
            r"fulfillment\w*\s*[:=].{0,40}['\"](settlement_pending|reconciled|refunded)['\"]"
            r"|['\"](settlement_pending|reconciled|refunded)['\"].{0,40}fulfillment"
        ),
    ),
    Rule(
        id="SC-13",
        summary="Credential or MFA secret serialised",
        why=(
            "access-v1: password_hash and mfa_secret must be excluded at the "
            "repository layer, never returned, logged, exported or audited."
        ),
        pattern=rx(
            r"(password_hash|mfa_secret)\s*[:=].{0,30}(json|serial|dict|dump|return|log)"
            r"|(json|serialize|to_dict|dump)\w*\(.{0,40}(password_hash|mfa_secret)"
        ),
        allow_globs=AUTH_MODULE,
    ),
    Rule(
        id="SC-14",
        summary="Variance classification gating an automatic action",
        why=(
            "settlement-v1: classification is advisory triage. No variance of any "
            "size may cause a reconciliation to confirm itself."
        ),
        pattern=rx(
            r"if\s+.{0,40}(classification|variance)\s*[=!]==?\s*['\"]minor['\"]"
            r".{0,80}(reconcil|confirm|approve)"
        ),
    ),
]

RULES_BY_ID = {r.id: r for r in RULES}


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

@dataclass
class Violation:
    rule: Rule
    path: Path
    line_no: int
    line: str


def iter_files(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.is_file():
            out.append(root)
            continue
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in SCANNED_SUFFIXES:
                continue
            if any(part in EXCLUDED_DIRS for part in p.parts):
                continue
            out.append(p)
    return sorted(set(out))


def scan_file(path: Path) -> tuple[list[Violation], list[str]]:
    violations: list[Violation] = []
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        problems.append(f"{path}: unreadable ({exc})")
        return violations, problems

    lines = text.splitlines()

    if any(SELF_EXEMPT in ln for ln in lines[:SELF_EXEMPT_SCAN_LINES]):
        return violations, problems

    for i, line in enumerate(lines, start=1):
        if BARE_SUPPRESSION.search(line):
            problems.append(
                f"{path}:{i}: suppression without a reason. "
                f"Write 'guardrail:allow <ID> <why>'."
            )

        suppressed = {
            m.group("id")
            for m in SUPPRESSION.finditer(line)
        }
        if i > 1:
            suppressed |= {
                m.group("id") for m in SUPPRESSION.finditer(lines[i - 2])
            }

        for rule in RULES:
            if rule.id in suppressed:
                continue
            if not rule.applies_to(path):
                continue
            if rule.pattern.search(line):
                violations.append(Violation(rule, path, i, line.strip()))

    return violations, problems


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def report(violations: list[Violation], problems: list[str], scanned: int) -> None:
    if problems:
        print("Suppression problems:\n")
        for p in problems:
            print(f"  {p}")
        print()

    if not violations:
        print(f"Invariant scan: clean. {scanned} file(s) checked.")
        return

    by_rule: dict[str, list[Violation]] = {}
    for v in violations:
        by_rule.setdefault(v.rule.id, []).append(v)

    print(f"Invariant scan: {len(violations)} violation(s) "
          f"across {len(by_rule)} rule(s). {scanned} file(s) checked.\n")

    for rid in sorted(by_rule):
        group = by_rule[rid]
        rule = group[0].rule
        print(f"{rid}  {rule.summary}  ({len(group)})")
        print(f"      {rule.why}")
        for v in group[:20]:
            print(f"      {v.path}:{v.line_no}: {v.line[:110]}")
        if len(group) > 20:
            print(f"      ... and {len(group) - 20} more")
        print()

    print("If a finding is genuinely correct, suppress it with a reason:")
    print("      # guardrail:allow SC-01 reading a third-party payload field")
    print("Review every suppression. A rising count means the design drifted.")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Static invariant scanner for the Walmart Store Manager rebuild."
    )
    ap.add_argument("paths", nargs="*", help="files or directories to scan")
    ap.add_argument("--list", action="store_true", help="list all rules and exit")
    ap.add_argument("--explain", metavar="ID", help="explain one rule and exit")
    args = ap.parse_args(argv)

    if args.list:
        for r in RULES:
            print(f"{r.id}  {r.summary}")
        return 0

    if args.explain:
        rule = RULES_BY_ID.get(args.explain.upper())
        if not rule:
            print(f"Unknown rule {args.explain!r}. Use --list.", file=sys.stderr)
            return 2
        print(f"{rule.id}  {rule.summary}\n")
        print(rule.why + "\n")
        print(f"Pattern: {rule.pattern.pattern}")
        if rule.allow_globs:
            print(f"Allowed in: {', '.join(rule.allow_globs)}")
        return 0

    roots = [Path(p) for p in args.paths] or [
        Path(d) for d in DEFAULT_ROOTS if Path(d).exists()
    ]
    if not roots:
        print("Nothing to scan. Pass a path, or run from the project root.",
              file=sys.stderr)
        return 2

    files = iter_files(roots)
    all_violations: list[Violation] = []
    all_problems: list[str] = []
    for f in files:
        v, p = scan_file(f)
        all_violations.extend(v)
        all_problems.extend(p)

    report(all_violations, all_problems, len(files))
    return 1 if (all_violations or all_problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
