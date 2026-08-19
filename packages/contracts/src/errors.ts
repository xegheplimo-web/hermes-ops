/**
 * Validation error codes and result shapes for runtime manifest validation.
 *
 * Codes are stable strings so the policy evaluator and downstream consumers can
 * branch on them deterministically.
 */

export type ValidationErrorCode =
  | 'MALFORMED'
  | 'SCHEMA_VERSION_UNSUPPORTED'
  | 'MISSING_REQUIRED_FIELD'
  | 'INVALID_TYPE'
  | 'INVALID_REPOSITORY'
  | 'INVALID_PR_NUMBER'
  | 'INVALID_HEAD_SHA'
  | 'HEAD_SHA_MISMATCH'
  | 'INVALID_POLICY_VERSION'
  | 'INVALID_TIMESTAMP'
  | 'STALE_TIMESTAMP'
  | 'EMPTY_ARTIFACTS'
  | 'ABSOLUTE_PATH'
  | 'PATH_TRAVERSAL'
  | 'INVALID_SHA256'
  | 'DUPLICATE_ARTIFACT_PATH'
  | 'INVALID_CI_CONCLUSION'
  | 'INVALID_CODERABBIT_FINDING'
  | 'INVALID_DEVIN_METADATA'
  | 'INVALID_SOURCE_ADAPTER'
  | 'SECRET_FIELD'
  | 'INVALID_IDEMPOTENCY_KEY';

export interface ValidationError {
  readonly code: ValidationErrorCode;
  readonly message: string;
  /** Dotted path into the manifest, when applicable. */
  readonly path?: string;
}

export type ValidationResult =
  | { readonly ok: true; readonly manifest: import('./manifest.js').EvidenceManifest }
  | { readonly ok: false; readonly error: ValidationError };
