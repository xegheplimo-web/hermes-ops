/**
 * EvidenceManifest v1 — the canonical, versioned evidence payload produced by
 * Hermes adapters and consumed by the deterministic policy evaluator.
 *
 * Phase 0 keeps this dependency-free and service-free. No GitHub, CodeRabbit,
 * Devin, or database types live here; those land in later phases.
 */

/** Supported manifest schema versions. Currently only v1. */
export const MANIFEST_SCHEMA_VERSION = 1 as const;

export type ManifestSchemaVersion = typeof MANIFEST_SCHEMA_VERSION;

/** Canonical repository identity. */
export interface RepositoryIdentity {
  /** Repository owner (user or org). Non-empty. */
  readonly owner: string;
  /** Repository name. Non-empty. */
  readonly name: string;
}

/** CI conclusion for a single check or the aggregate rollup. */
export type CiConclusion =
  | 'success'
  | 'failure'
  | 'neutral'
  | 'cancelled'
  | 'skipped'
  | 'timed_out'
  | 'action_required';

/** A single CI check result. */
export interface CiCheck {
  readonly name: string;
  readonly conclusion: CiConclusion;
}

/** Aggregate CI evidence. `conclusion` is the rollup; `checks` is optional detail. */
export interface CiEvidence {
  readonly conclusion: CiConclusion;
  readonly checks?: readonly CiCheck[];
}

/** Severity for a CodeRabbit finding. */
export type CodeRabbitSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';

/** A normalized CodeRabbit finding. */
export interface CodeRabbitFinding {
  readonly id: string;
  readonly severity: CodeRabbitSeverity;
  /** True when the finding has been addressed/resolved. */
  readonly resolved: boolean;
}

/** Optional CodeRabbit findings block. */
export interface CodeRabbitFindings {
  readonly findings: readonly CodeRabbitFinding[];
}

/** Optional Devin run metadata. */
export interface DevinRunMetadata {
  readonly runId: string;
  readonly status: string;
  readonly startedAt?: string;
  readonly finishedAt?: string;
}

/**
 * Source adapter descriptor — identifies which adapter produced the evidence.
 * `kind` is a closed enum for v1; `metadata` is a flat, primitive-valued bag.
 */
export type SourceAdapterKind =
  | 'github-actions'
  | 'local'
  | 'ci'
  | 'manual';

export interface SourceAdapter {
  readonly kind: SourceAdapterKind;
  readonly version: string;
  readonly metadata?: Readonly<Record<string, string | number | boolean>>;
}

/** An artifact reference: relative path plus SHA-256 content hash. */
export interface ArtifactReference {
  /** Relative, forward-slash path within the repo. No absolute paths, no `..`. */
  readonly path: string;
  /** Lowercase SHA-256 hex digest (64 hex chars). */
  readonly sha256: string;
}

/**
 * The versioned EvidenceManifest, v1.
 *
 * Bound to repository identity, an optional PR number, the head SHA the evidence
 * was gathered against, the policy version it targets, and a freshness timestamp.
 */
export interface EvidenceManifestV1 {
  readonly schemaVersion: ManifestSchemaVersion;
  readonly repository: RepositoryIdentity;
  /** Optional PR number. Positive integer when present. */
  readonly prNumber?: number;
  /** Head commit SHA (40 lowercase hex chars). */
  readonly headSha: string;
  /** Policy version this evidence was evaluated against (semver). */
  readonly policyVersion: string;
  /** ISO-8601 UTC timestamp the evidence was produced. */
  readonly timestamp: string;
  readonly artifacts: readonly ArtifactReference[];
  readonly ci: CiEvidence;
  readonly coderabbit?: CodeRabbitFindings;
  readonly devin?: DevinRunMetadata;
  readonly source: SourceAdapter;
  /**
   * Optional idempotency key. When present, the policy evaluator treats a
   * repeated key as a duplicate and fails closed.
   */
  readonly idempotencyKey?: string;
}

/** Convenience alias for the current (and only) manifest version. */
export type EvidenceManifest = EvidenceManifestV1;
