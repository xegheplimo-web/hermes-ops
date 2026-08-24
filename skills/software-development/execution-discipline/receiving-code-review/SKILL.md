---
name: receiving-code-review
description: Use when receiving code review feedback, before implementing suggestions, especially if feedback seems unclear or technically questionable — requires technical rigor, not performative agreement
---

# Receiving Code Review

**Attribution:** Adapted from [Superpowers](https://github.com/jessevinent/superpowers) by Jesse Vincent (MIT).  
Copyright (c) 2025 Jesse Vincent. Used under MIT License.

## Overview

Code review requires technical evaluation, not emotional performance.

**Core principle:** Verify before implementing. Ask before assuming. Technical correctness over social comfort.

## The Response Pattern

```
WHEN receiving code review feedback:

1. READ: Complete feedback without reacting
2. UNDERSTAND: Restate requirement in own words (or ask)
3. VERIFY: Check against codebase reality
4. EVALUATE: Technically sound for THIS codebase?
5. RESPOND: Technical acknowledgment or reasoned pushback
6. IMPLEMENT: One item at a time, test each
```

## Forbidden Responses

**NEVER:**
- "You're absolutely right!" (performative)
- "Great point!" / "Excellent feedback!" (performative)
- "Let me implement that now" (before verification)

**INSTEAD:**
- Restate the technical requirement
- Ask clarifying questions
- Push back with technical reasoning if wrong
- Just start working (actions > words)

## Source-Specific Handling

### From your human partner (Sếp)
- **Trusted** — implement after understanding
- **Still ask** if scope unclear
- **No performative agreement**

### From External Reviewers (Codex / CodeRabbit)

```
BEFORE implementing:
  1. Check: Technically correct for THIS codebase?
  2. Check: Breaks existing functionality?
  3. Check: Reason for current implementation?
  4. Check: Works on all platforms/versions?
  5. Check: Does reviewer understand full context?

IF suggestion seems wrong:
  Push back with technical reasoning

IF conflicts with Sếp's prior decisions:
  Stop and discuss with Sếp first
```

## YAGNI Check

```
IF reviewer suggests "implementing properly":
  grep codebase for actual usage

  IF unused: "This endpoint isn't called. Remove it (YAGNI)?"
  IF used: Then implement properly
```

## When To Push Back

Push back when:
- Suggestion breaks existing functionality
- Reviewer lacks full context
- Violates YAGNI (unused feature)
- Technically incorrect for this stack
- Conflicts with Sếp's architectural decisions

**How to push back:** Use technical reasoning, not defensiveness. Ask specific questions. Reference working tests/code.

## Integration with Hermes pipeline

- **Codex review findings** → reconcile_review.py classifies AGREE/PARTIAL/DISAGREE
- **DISAGREE findings** are never turned into tasks (architectural pushback)
- **Devin** uses this skill when responding to Codex/CodeRabbit review comments on its PRs
- **AGREE findings** become implementation tasks for Devin