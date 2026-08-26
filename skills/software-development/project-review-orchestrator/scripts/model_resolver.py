#!/usr/bin/env python3
"""Model / role allocation resolver for the Hermes Open Design pipeline.

Loads ``model-roles.json`` and resolves a stage or (stage + risk) to a
concrete model, executor, provider and fallback chain.  Fallbacks are filtered
by the provider's valid model set so the resolver never returns a Devin model
id that Devin cannot actually run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelAssignment:
    """Resolved model/role assignment for a pipeline stage."""

    stage: str
    executor: str
    provider: str
    preferred: str
    primary: str
    fallbacks: tuple[str, ...]
    notes: str

    @property
    def all_models(self) -> list[str]:
        """Primary runnable model followed by all runnable fallbacks."""
        return [self.primary] + list(self.fallbacks)


class ModelResolver:
    """Load the model/role config and resolve stage/risk to a concrete model."""

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or self._default_config_path()
        self._config = self._load(self._config_path)

    @staticmethod
    def _default_config_path() -> str:
        return str(Path(__file__).resolve().parent / "model-roles.json")

    def _load(self, path: str) -> dict[str, Any]:
        if not Path(path).exists():
            raise FileNotFoundError(
                f"Hermes model-roles config not found: {path}. "
                "Set HERMES_MODEL_CONFIG or place model-roles.json next to model_resolver.py."
            )
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def fallback_policy(self) -> dict[str, Any]:
        return self._config.get("fallback_policy", {})

    def resolve(self, stage_or_risk: str, risk: str | None = None) -> ModelAssignment:
        """Resolve a stage name or risk level to a concrete assignment."""
        stage = self._stage_for(stage_or_risk, risk)
        cfg = self._config["stages"][stage]

        provider = cfg.get("provider", "devin")
        valid = set(self._config.get("provider_valid_models", {}).get(provider, []))

        candidates = [cfg["primary_model"]] + list(cfg.get("fallback_chain", []))

        # Filter to provider-valid models when the provider has a restricted set.
        if valid:
            concrete = [m for m in candidates if m in valid]
        else:
            concrete = candidates

        # Failsafe: if nothing is valid for the provider, keep the primary.
        if not concrete and candidates:
            concrete = [candidates[0]]

        preferred = candidates[0]
        primary = concrete[0]
        fallbacks = tuple(concrete[1:])

        return ModelAssignment(
            stage=stage,
            executor=cfg.get("executor", "devin"),
            provider=provider,
            preferred=preferred,
            primary=primary,
            fallbacks=fallbacks,
            notes=cfg.get("notes", ""),
        )

    def resolve_for_task(self, risk: str, task_type: str | None = None) -> ModelAssignment:
        """Resolve the Devin implementer model for a task by risk.

        Never raises on an unmapped risk level: an unknown risk is treated as the
        most conservative mapped level so a mid-pipeline dispatch cannot crash on
        a risk name the config has not caught up with. Escalating (rather than
        defaulting down) keeps an unknown risk from being handled too cheaply.
        """
        try:
            return self.resolve(risk)
        except ValueError:
            risk_map = self._config.get("risk_to_stage", {})
            for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                if level in risk_map:
                    return self.resolve(level)
            raise

    def _stage_for(self, stage_or_risk: str, risk: str | None = None) -> str:
        stages = self._config.get("stages", {})

        # Explicit stage wins.
        if stage_or_risk in stages:
            return stage_or_risk

        # If a risk is supplied explicitly, use the risk→stage map.
        if risk:
            return self._config.get("risk_to_stage", {}).get(
                risk.upper(), "normal_task"
            )

        # stage_or_risk may itself be a risk level.
        upper = stage_or_risk.upper()
        if upper in self._config.get("risk_to_stage", {}):
            return self._config["risk_to_stage"][upper]

        raise ValueError(f"Unknown stage or risk: {stage_or_risk}")


# Convenience singleton / default resolver
_default_resolver: ModelResolver | None = None


def _get_default_resolver() -> ModelResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = ModelResolver(
            os.getenv("HERMES_MODEL_CONFIG") or None
        )
    return _default_resolver


def resolve(stage_or_risk: str, risk: str | None = None) -> ModelAssignment:
    """Resolve using the default resolver (env-configurable)."""
    return _get_default_resolver().resolve(stage_or_risk, risk)


def resolve_for_task(risk: str, task_type: str | None = None) -> ModelAssignment:
    """Resolve a task risk to a Devin model assignment."""
    return _get_default_resolver().resolve_for_task(risk, task_type)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Resolve model/role assignment.")
    parser.add_argument("--stage", default="orchestrator", help="Stage name or risk")
    parser.add_argument("--risk", help="Optional risk override")
    parser.add_argument("--config", help="Path to model-roles.json")
    args = parser.parse_args()

    resolver = ModelResolver(args.config)
    assignment = resolver.resolve(args.stage, args.risk)
    print(
        json.dumps(
            {
                "stage": assignment.stage,
                "executor": assignment.executor,
                "provider": assignment.provider,
                "preferred": assignment.preferred,
                "primary": assignment.primary,
                "fallbacks": list(assignment.fallbacks),
                "notes": assignment.notes,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
