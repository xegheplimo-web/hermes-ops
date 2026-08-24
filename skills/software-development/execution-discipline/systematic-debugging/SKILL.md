---
name: systematic-debugging
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes — requires root cause investigation before any fix attempt
---

# Systematic Debugging

**Attribution:** Adapted from [Superpowers](https://github.com/jessevinent/superpowers) by Jesse Vincent (MIT).  
Copyright (c) 2025 Jesse Vincent. Used under MIT License.

## Overview

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue:
- Test failures
- Bugs in production
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

## The Four Phases

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings
   - They often contain the exact solution
   - Read stack traces completely
   - Note line numbers, file paths, error codes

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps?
   - Does it happen every time?
   - If not reproducible → gather more data, don't guess

3. **Check Recent Changes**
   - What changed that could cause this?
   - Git diff, recent commits
   - New dependencies, config changes
   - Environmental differences

4. **Gather Evidence in Multi-Component Systems**

   **WHEN system has multiple components (CI → build → signing, API → service → database):**

   **BEFORE proposing fixes, add diagnostic instrumentation:**
   ```
   For EACH component boundary:
     - Log what data enters component
     - Log what data exits component
     - Verify environment/config propagation
     - Check state at each layer

   Run once to gather evidence showing WHERE it breaks
   THEN analyze evidence to identify failing component
   THEN investigate that specific component
   ```

5. **Trace Data Flow**

   **WHEN error is deep in call stack:**

   See `root-cause-tracing.md` in this directory for the complete backward tracing technique.

### Phase 2: Pattern Analysis

1. **Find Working Examples** — Locate similar working code in same codebase
2. **Compare Against References** — Read reference implementation COMPLETELY
3. **Identify Differences** — What's different between working and broken?
4. **Understand Dependencies** — What other components does this need?

### Phase 3: Hypothesis and Testing

1. **Form Single Hypothesis** — "I think X is the root cause because Y"
2. **Test Minimally** — Smallest possible change to test hypothesis; one variable at a time
3. **Verify Before Continuing** — Did it work? Yes → Phase 4. Didn't work? Form NEW hypothesis

### Phase 4: Implementation

1. **Create Failing Test Case** — Simplest possible reproduction; automated test if possible
2. **Implement Single Fix** — Address the root cause; ONE change at a time
3. **Verify Fix** — Test passes now? No other tests broken?
4. **If Fix Doesn't Work (3+ failed attempts):** STOP and question the architecture — is this pattern fundamentally sound?

## Integration with Hermes pipeline

When a Devin task fails:
1. Devin applies `systematic-debugging` → root cause → hypothesis → minimal fix
2. Devin creates regression test (TDD) before fix
3. Devin applies `verification-before-completion` before reporting done
4. Hermes verify step validates the fix via CI

## Supporting Files

- `root-cause-tracing.md` — Trace bugs backward through call stack
- `defense-in-depth.md` — Add validation at multiple layers