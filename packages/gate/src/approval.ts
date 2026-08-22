/**
 * Human Approval Gate — explicit sign-off before CRITICAL-risk actions proceed.
 *
 * The gate is stateful by design: a pending request stays pending until a valid
 * {@link HumanApprovalToken} is supplied via {@link resolveHumanApproval}.
 * In the CLI, the token is passed as a `--approval` JSON string.
 */

/** Current status of a human approval request. */
export type HumanApprovalStatus = 'approved' | 'pending' | 'rejected';

/**
 * A cryptographically-shaped token that constitutes human sign-off.
 * In phase 0 the signature is not verified cryptographically — the presence
 * of a well-shaped token is sufficient. A future phase can add verification.
 */
export interface HumanApprovalToken {
  /** ISO-8601 timestamp of when the human signed. */
  readonly signedAt: string;
  /** Name or identifier of the human approver. */
  readonly approver: string;
  /** Free-text reason for the approval. */
  readonly reason: string;
  /** Opaque signature string (reserved for future cryptographic verification). */
  readonly signature: string;
}

/** In-memory store: taskId → status. Replaced by durable storage in phase 1. */
const store = new Map<string, HumanApprovalStatus>();

/**
 * Request human approval for a task.
 * Returns `'pending'`. The request is stored for later resolution.
 */
export const requestHumanApproval = (
  taskId: string,
  _risk: string,
): HumanApprovalStatus => {
  store.set(taskId, 'pending');
  return 'pending';
};

/**
 * Resolve a pending human approval request.
 *
 * Basic validation:
 * - The task must have a pending request.
 * - The token must have all required fields populated.
 * - The signature must be non-empty.
 *
 * On success returns `'approved'`; on failure returns `'rejected'`.
 */
export const resolveHumanApproval = (
  taskId: string,
  token: HumanApprovalToken,
): HumanApprovalStatus => {
  const status = store.get(taskId);
  if (status !== 'pending') return 'rejected';

  // Basic structural validation.
  if (
    !token.signedAt ||
    !token.approver ||
    !token.reason ||
    !token.signature
  ) {
    store.set(taskId, 'rejected');
    return 'rejected';
  }

  store.set(taskId, 'approved');
  return 'approved';
};

/**
 * Determine whether a human approval gate is required for the given risk level.
 *
 * Phase 0 rule: only `'critical'` requires human approval. LOW / MED bypass
 * entirely. This is a pure function — no side effects, no state.
 */
export const isHumanApprovalRequired = (risk: string): boolean => {
  return risk === 'critical';
};

/** Clear all stored approval state (for test teardown). */
export const resetApprovalStore = (): void => {
  store.clear();
};