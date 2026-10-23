---
description: Run the full spec-to-audit pipeline for one user story (e.g. /story US-011)
agent: slice-lead
---

Deliver story **$1** of the Walmart Store Manager rebuild, end to end.

Run the pipeline in order. Do not reorder it and do not skip a step because the story looks simple.

## 1. Establish what is required

Ask `spec-oracle` for story $1: its acceptance criteria, the policy sections that govern it, the capability matrix rows involved, and any pending configuration it depends on. Require citations.

## 2. Write the failing tests

Hand the cited requirements to `test-author`. One test per acceptance criterion, named with its id. Confirm they fail, and confirm they fail *for the right reason* — a test erroring on a missing import proves nothing.

## 3. Implement

Hand the story and the failing tests to `story-implementer`. Require it to list the modules, tables, and endpoints it expects to touch **before** editing. If that list spans more than one module, stop and split the story.

## 4. Audit

Hand the diff to `invariant-auditor`. It cannot edit, which is why its verdict is worth something.

On a blocking finding, return to step 3 with the finding. Do not argue the finding away, and do not resolve it by changing a test or a guardrail.

## 5. Report

- Each acceptance criterion and whether a passing test covers it
- Guardrail scan result
- Any suppression added, with its reason
- What is still outstanding

Do not describe the story as done unless every acceptance criterion has a passing test. Do not describe the application as working because this story passes.

$ARGUMENTS
