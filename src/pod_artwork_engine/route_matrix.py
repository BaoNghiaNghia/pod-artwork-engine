from __future__ import annotations

from pathlib import Path

from .contracts import RouteKind
from .harness import HarnessStore, load_recipe
from .harness_engine import HarnessEngineRunner
from .harness_models import (
    BenchmarkCaseResult,
    BenchmarkRecipe,
    HarnessRunStatus,
    RouteCaseComparison,
    RouteMatrixReport,
    RouteMatrixRun,
    RouteMatrixSpec,
    RoutePreference,
)
from .settings import Settings


class RouteMatrixRunner:
    """Collect counterfactual route evidence on the same benchmark cases.

    Route overrides exist only inside the Harness. The production engine still
    uses the configured RouterPolicy unless a HarnessEngineRunner explicitly
    injects a route override.
    """

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store

    @staticmethod
    def _resolve(path: str, base: Path | None) -> Path:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute() and base is not None:
            resolved = base / resolved
        return resolved.resolve()

    @staticmethod
    def _recipe_for_route(
        base: BenchmarkRecipe,
        route: RouteKind,
    ) -> BenchmarkRecipe:
        metadata = dict(base.metadata)
        metadata["harness_route_override"] = route.value
        return base.model_copy(
            deep=True,
            update={
                "recipe_id": f"{base.recipe_id}__route_{route.value}",
                "metadata": metadata,
            },
        )

    @staticmethod
    def _best_remote(
        candidates: list[BenchmarkCaseResult],
    ) -> BenchmarkCaseResult | None:
        valid = [
            result
            for result in candidates
            if (
                result.success
                and result.quality_score is not None
                and result.route.used_remote
                and result.route.selected_route
                in {RouteKind.HYBRID, RouteKind.REMOTE_SEMANTIC}
            )
        ]
        if not valid:
            return None
        return max(
            valid,
            key=lambda result: (
                result.quality_score or 0.0,
                -result.operational.latency_ms,
            ),
        )

    def run(
        self,
        spec: RouteMatrixSpec,
        *,
        spec_base: Path | None = None,
    ) -> RouteMatrixReport:
        recipe_path = self._resolve(spec.recipe_path, spec_base)
        base_recipe = load_recipe(recipe_path)
        scorecards = {}
        results_by_route: dict[RouteKind, dict[str, BenchmarkCaseResult]] = {}
        matrix_runs: list[RouteMatrixRun] = []

        for route in spec.routes:
            recipe = self._recipe_for_route(base_recipe, route)
            scorecard = HarnessEngineRunner(
                self.settings,
                self.registry,
                self.store,
            ).run(
                spec.dataset_id,
                spec.tier,
                recipe,
                quality_mode=spec.quality_mode,
                limit=spec.limit,
                recipe_base=recipe_path.parent,
            )
            scorecards[route] = scorecard
            results = self.store.get_results(scorecard.run_id)
            results_by_route[route] = {result.pair_id: result for result in results}
            matrix_runs.append(
                RouteMatrixRun(
                    route=route,
                    run_id=scorecard.run_id,
                    scorecard_id=scorecard.scorecard_id,
                    status=scorecard.status,
                )
            )

        fingerprints = {
            scorecard.provenance.dataset_manifest_sha256
            for scorecard in scorecards.values()
            if scorecard.provenance.dataset_manifest_sha256
        }
        quality_modes = {
            scorecard.provenance.quality_mode for scorecard in scorecards.values()
        }
        router_policies = {
            (
                scorecard.provenance.router_policy_id,
                scorecard.provenance.router_policy_version,
            )
            for scorecard in scorecards.values()
        }
        qc_policies = {
            (
                scorecard.provenance.qc_policy_id,
                scorecard.provenance.qc_policy_version,
            )
            for scorecard in scorecards.values()
        }
        provider_recipes = {
            scorecard.provenance.provider_recipe_id
            for scorecard in scorecards.values()
        }
        if len(fingerprints) != 1:
            raise ValueError(
                "route matrix requires one shared dataset manifest fingerprint"
            )
        if quality_modes != {spec.quality_mode}:
            raise ValueError("route matrix quality-mode provenance mismatch")
        if len(router_policies) != 1:
            raise ValueError("route matrix must use one shared RouterPolicy")
        if len(qc_policies) != 1:
            raise ValueError("route matrix must use one shared QCPolicy")
        if len(provider_recipes) != 1:
            raise ValueError("route matrix must use one shared provider recipe")

        baseline_scorecard = scorecards[RouteKind.DETERMINISTIC]
        baseline = results_by_route[RouteKind.DETERMINISTIC]
        remote_routes = [
            route for route in spec.routes if route is not RouteKind.DETERMINISTIC
        ]
        pair_ids = sorted(
            set(baseline)
            | {
                pair_id
                for route in remote_routes
                for pair_id in results_by_route[route]
            }
        )

        comparisons: list[RouteCaseComparison] = []
        for pair_id in pair_ids:
            deterministic = baseline.get(pair_id)
            remotes = [
                result
                for route in remote_routes
                if (result := results_by_route[route].get(pair_id)) is not None
            ]
            best_remote = self._best_remote(remotes)

            if (
                deterministic is None
                or not deterministic.success
                or deterministic.quality_score is None
            ):
                comparisons.append(
                    RouteCaseComparison(
                        pair_id=pair_id,
                        artwork_identity=(
                            deterministic.artwork_identity
                            if deterministic is not None
                            else (
                                remotes[0].artwork_identity if remotes else pair_id
                            )
                        ),
                        preference=RoutePreference.UNUSABLE,
                        reasons=["deterministic baseline unavailable"],
                    )
                )
                continue

            route_evidence = deterministic.route
            if best_remote is None:
                remote_requested = any(
                    result.route.requested_override
                    in {RouteKind.HYBRID, RouteKind.REMOTE_SEMANTIC}
                    for result in remotes
                )
                reason = (
                    "remote route did not execute a remote reconstruction"
                    if remote_requested
                    else "remote counterfactual unavailable"
                )
                comparisons.append(
                    RouteCaseComparison(
                        pair_id=pair_id,
                        artwork_identity=deterministic.artwork_identity,
                        design_confidence=route_evidence.design_confidence,
                        artwork_type=route_evidence.artwork_type,
                        required_capabilities=route_evidence.required_capabilities,
                        deterministic_quality=deterministic.quality_score,
                        deterministic_latency_ms=deterministic.operational.latency_ms,
                        preference=RoutePreference.UNUSABLE,
                        reasons=[reason],
                    )
                )
                continue

            delta = best_remote.quality_score - deterministic.quality_score
            if delta >= spec.min_quality_gain:
                preference = RoutePreference.REMOTE
            elif delta <= -spec.min_quality_gain:
                preference = RoutePreference.DETERMINISTIC
            else:
                preference = RoutePreference.TIE

            comparisons.append(
                RouteCaseComparison(
                    pair_id=pair_id,
                    artwork_identity=deterministic.artwork_identity,
                    design_confidence=(
                        route_evidence.design_confidence
                        if route_evidence.design_confidence is not None
                        else best_remote.route.design_confidence
                    ),
                    artwork_type=(
                        route_evidence.artwork_type
                        if route_evidence.artwork_type is not None
                        else best_remote.route.artwork_type
                    ),
                    required_capabilities=(
                        route_evidence.required_capabilities
                        or best_remote.route.required_capabilities
                    ),
                    deterministic_quality=deterministic.quality_score,
                    remote_quality=best_remote.quality_score,
                    remote_route=best_remote.route.selected_route,
                    quality_delta=delta,
                    deterministic_latency_ms=deterministic.operational.latency_ms,
                    remote_latency_ms=best_remote.operational.latency_ms,
                    remote_used=True,
                    preference=preference,
                )
            )

        comparable = [
            item
            for item in comparisons
            if item.preference is not RoutePreference.UNUSABLE
        ]
        reasons: list[str] = []
        if any(
            scorecard.status is not HarnessRunStatus.COMPLETE
            for scorecard in scorecards.values()
        ):
            reasons.append("one or more route runs are incomplete")
        if not any(item.remote_used for item in comparisons):
            reasons.append(
                "no remote reconstruction executed; configure a working remote provider "
                "before using this matrix for router calibration"
            )

        report = RouteMatrixReport(
            matrix_id=spec.matrix_id,
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            recipe_id=base_recipe.recipe_id,
            recipe_version=base_recipe.version,
            quality_mode=spec.quality_mode,
            min_quality_gain=spec.min_quality_gain,
            dataset_manifest_sha256=(
                baseline_scorecard.provenance.dataset_manifest_sha256
            ),
            router_policy_id=baseline_scorecard.provenance.router_policy_id,
            router_policy_version=baseline_scorecard.provenance.router_policy_version,
            runs=matrix_runs,
            comparisons=comparisons,
            comparable_case_count=len(comparable),
            remote_preferred_count=sum(
                item.preference is RoutePreference.REMOTE for item in comparable
            ),
            deterministic_preferred_count=sum(
                item.preference is RoutePreference.DETERMINISTIC for item in comparable
            ),
            tie_count=sum(
                item.preference is RoutePreference.TIE for item in comparable
            ),
            incomplete_count=sum(
                item.preference is RoutePreference.UNUSABLE for item in comparisons
            ),
            requires_human_approval=True,
            auto_applied=False,
            reasons=reasons,
        )

        output_dir = self.store.route_matrix_dir(spec.matrix_id)
        self.store.save_model(output_dir / "spec.json", spec)
        self.store.save_model(output_dir / "report.json", report)
        return report


def load_route_matrix_report(path: Path) -> RouteMatrixReport:
    return RouteMatrixReport.model_validate_json(path.read_text(encoding="utf-8"))
