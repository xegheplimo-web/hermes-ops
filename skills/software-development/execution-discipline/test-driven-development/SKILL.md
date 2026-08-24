---
name: test-driven-development
description: Use when implementing any feature or bugfix, before writing implementation code — requires RED-GREEN-REFACTOR cycle with verified failing test first
---

# Test-Driven Development (TDD)

**Attribution:** Adapted from [Superpowers](https://github.com/jessevinent/superpowers) by Jesse Vincent (MIT).  
Copyright (c) 2025 Jesse Vincent. Used under MIT License.

## Overview

Write the test first. Watch it fail. Write minimal code to pass.

**Core principle:** If you didn't watch the test fail, you don't know if it tests the right thing.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over.

## Red-Green-Refactor

### RED — Write Failing Test

Write one minimal test showing what should happen:
- One behavior per test
- Clear name describing behavior
- Real code (no mocks unless unavoidable)

### Verify RED — Watch It Fail

**MANDATORY. Never skip.**
Confirm: test fails (not errors), failure message is expected, fails because feature missing.

### GREEN — Minimal Code

Write simplest code to pass the test. Don't add features, refactor other code, or "improve" beyond the test.

### Verify GREEN — Watch It Pass

**MANDATORY.**
Confirm: test passes, other tests still pass, output pristine.

### REFACTOR — Clean Up

After green only: remove duplication, improve names, extract helpers. Keep tests green.

### Repeat

Next failing test for next feature.

## Policy Exceptions (Hermes-specific)

Strict TDD applies to:
- **Features** — required
- **Bug fixes** — required (write regression test reproducing the bug first)
- **Refactoring** — required (existing tests must already cover behavior)

Pragmatic exceptions (ask Sếp):
- **Generated code** — generator validation instead
- **Configuration** — schema/validation check
- **Legacy code without test harness** — characterization test first when practical
- **UI/visual changes** — functional + screenshot verification

## Supporting Files

- `writing-good-tests.md` — Rules for writing effective, honest tests

## Integration with Hermes pipeline

- **Devin tasks:** task-plan.json acceptance criteria include TDD requirement
- **dispatch_to_devin.py prompts:** Instruct Devin to write test first, then implement
- **systematic-debugging:** Bug fix starts with failing regression test BEFORE fix