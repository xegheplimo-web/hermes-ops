import { describe, expect, it } from 'vitest';
import {
  CLAIM_JOB_SQL,
  CLAIM_TASK_SQL,
  RECOVER_STALE_JOBS_SQL,
  RECOVER_STALE_TASKS_SQL,
  REQUEUE_OR_FAIL_JOB_SQL,
  REQUEUE_OR_FAIL_TASK_SQL,
  claimParams,
  staleRecoveryParams,
} from '../src/index.js';

describe('CLAIM_TASK_SQL — SKIP LOCKED and no unsafe interpolation', () => {
  it('uses FOR UPDATE SKIP LOCKED', () => {
    expect(CLAIM_TASK_SQL).toContain('FOR UPDATE SKIP LOCKED');
  });

  it('limits to one row', () => {
    expect(CLAIM_TASK_SQL).toContain('LIMIT 1');
  });

  it('selects only pending, available rows', () => {
    expect(CLAIM_TASK_SQL).toContain("status = 'pending'");
    expect(CLAIM_TASK_SQL).toContain('available_at <= now()');
  });

  it('transitions to running and increments attempts', () => {
    expect(CLAIM_TASK_SQL).toContain("status       = 'running'");
    expect(CLAIM_TASK_SQL).toContain('attempts     = attempts + 1');
  });

  it('sets locked_at and locked_by but does not clear available_at', () => {
    expect(CLAIM_TASK_SQL).toContain('locked_at    = now()');
    expect(CLAIM_TASK_SQL).toContain('locked_by    = $1');
    expect(CLAIM_TASK_SQL).not.toContain('available_at = NULL');
  });

  it('uses a positional parameter for the worker id (no interpolation)', () => {
    // No JS template interpolation markers in the SQL string.
    expect(CLAIM_TASK_SQL).not.toContain('${');
    // No string concatenation of user input: the only placeholder is $1.
    expect(CLAIM_TASK_SQL).toContain('$1');
    expect(CLAIM_TASK_SQL).not.toContain('$2');
  });

  it('orders by available_at then id for FIFO claiming', () => {
    expect(CLAIM_TASK_SQL).toContain('ORDER BY available_at ASC, id ASC');
  });

  it('returns the claimed row', () => {
    expect(CLAIM_TASK_SQL).toContain('RETURNING *');
  });
});

describe('CLAIM_JOB_SQL — same shape as tasks', () => {
  it('uses FOR UPDATE SKIP LOCKED', () => {
    expect(CLAIM_JOB_SQL).toContain('FOR UPDATE SKIP LOCKED');
  });
  it('transitions to running and increments attempts', () => {
    expect(CLAIM_JOB_SQL).toContain("status       = 'running'");
    expect(CLAIM_JOB_SQL).toContain('attempts     = attempts + 1');
  });
  it('uses $1 for worker id only and does not clear available_at', () => {
    expect(CLAIM_JOB_SQL).toContain('$1');
    expect(CLAIM_JOB_SQL).not.toContain('$2');
    expect(CLAIM_JOB_SQL).not.toContain('${');
    expect(CLAIM_JOB_SQL).not.toContain('available_at = NULL');
  });
});

describe('claimParams', () => {
  it('returns a single-element tuple with the worker id', () => {
    expect(claimParams('worker-1')).toEqual(['worker-1']);
  });

  it('rejects empty worker id', () => {
    expect(() => claimParams('')).toThrow(TypeError);
  });

  it('rejects non-string worker id', () => {
    expect(() => claimParams(123 as unknown as string)).toThrow(TypeError);
  });
});

describe('REQUEUE_OR_FAIL_TASK_SQL', () => {
  it('transitions to failed when available_at param is NULL', () => {
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain("WHEN $2::timestamptz IS NULL THEN 'failed'");
  });

  it('transitions to pending when available_at param is present', () => {
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain("ELSE 'pending'");
  });

  it('clears the lock', () => {
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain('locked_at    = NULL');
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain('locked_by    = NULL');
  });

  it('uses positional parameters only (no interpolation)', () => {
    expect(REQUEUE_OR_FAIL_TASK_SQL).not.toContain('${');
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain('$1');
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain('$2');
    expect(REQUEUE_OR_FAIL_TASK_SQL).toContain('$3');
  });
});

describe('RECOVER_STALE_TASKS_SQL', () => {
  it('targets rows stuck in running', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain("status = 'running'");
  });

  it('re-queues as pending with available_at = now()', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain("status       = 'pending'");
    expect(RECOVER_STALE_TASKS_SQL).toContain('available_at = now()');
  });

  it('clears the lock', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain('locked_at    = NULL');
    expect(RECOVER_STALE_TASKS_SQL).toContain('locked_by    = NULL');
  });

  it('uses a positional cutoff parameter (no interpolation)', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain('locked_at < $1');
    expect(RECOVER_STALE_TASKS_SQL).not.toContain('${');
    expect(RECOVER_STALE_TASKS_SQL).not.toContain('$2');
  });

  it('appends an observable note to last_error', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain('stale lock recovered');
  });

  it('returns recovered ids and attempts for observability', () => {
    expect(RECOVER_STALE_TASKS_SQL).toContain('RETURNING id, attempts');
  });
});

describe('RECOVER_STALE_JOBS_SQL', () => {
  it('shares the same shape as tasks recovery', () => {
    expect(RECOVER_STALE_JOBS_SQL).toContain("status = 'running'");
    expect(RECOVER_STALE_JOBS_SQL).toContain('locked_at < $1');
    expect(RECOVER_STALE_JOBS_SQL).toContain("status       = 'pending'");
  });
});

describe('staleRecoveryParams', () => {
  it('wraps a cutoff date in a single-element tuple', () => {
    const cutoff = new Date('2026-08-19T12:00:00.000Z');
    expect(staleRecoveryParams(cutoff)).toEqual([cutoff]);
  });

  it('rejects invalid dates', () => {
    expect(() => staleRecoveryParams(new Date('nope'))).toThrow(TypeError);
  });
});
