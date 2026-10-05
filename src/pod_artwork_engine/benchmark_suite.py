from __future__ import annotations

from pathlib import Path

from .harness import HarnessStore, compare_scorecards, load_recipe
from .harness_engine import HarnessEngineRunner
from .harness_models import (
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkSuiteReport,
    BenchmarkSuiteSpec,
    BenchmarkTier,
    ChallengerSuiteResult,
    HarnessRunStatus,
    PromotionPolicy,
    SuiteRecommendation,
    SuiteTierResult,
)
from .settings import Settings


class BenchmarkSuiteRunner:
    """Run champion/challenger recipes through Smoke -> Regression -> Golden.

    The suite only produces evidence and a recommendation. It never rewrites
    production configuration, provider mappings, or QC policy files.
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

    def _load_recipes(
        self,
        spec: BenchmarkSuiteSpec,
        base: Path | None,
    ) -> tuple[tuple[BenchmarkRecipe, Path], list[tuple[BenchmarkRecipe, Path]]]:
        champion_path = self._resolve(spec.champion_recipe_path, base)
        champion = load_recipe(champion_path)
        challengers: list[tuple[BenchmarkRecipe, Path]] = []
        seen: set[tuple[str, str]] = set()

        for raw_path in spec.challenger_recipe_paths:
            path = self._resolve(raw_path, base)
            recipe = load_recipe(path)
            identity = (recipe.recipe_id, recipe.version)
            if identity in seen:
                continue
            if identity == (champion.recipe_id, champion.version):
                raise ValueError("challenger recipe must differ from champion recipe")
            seen.add(identity)
            challengers.append((recipe, path))

        if not challengers:
            raise ValueError("benchmark suite requires at least one unique challenger")
        return (champion, champion_path), challengers

    @staticmethod
    def _limit_for_tier(spec: BenchmarkSuiteSpec, tier: BenchmarkTier) -> int | None:
        if tier is BenchmarkTier.SMOKE:
            return spec.smoke_limit
        if tier is BenchmarkTier.REGRESSION:
            return spec.regression_limit
        return spec.golden_limit

    @staticmethod
    def _policy_for_tier(
        base: PromotionPolicy,
        tier: BenchmarkTier,
    ) -> PromotionPolicy:
        return base.model_copy(
            update={"require_golden": tier is BenchmarkTier.GOLDEN}
        )

    def _run_recipe(
        self,
        recipe: BenchmarkRecipe,
        recipe_path: Path,
        spec: BenchmarkSuiteSpec,
        tier: BenchmarkTier,
    ) -> BenchmarkScorecard:
        runner = HarnessEngineRunner(self.settings, self.registry, self.store)
        return runner.run(
            spec.dataset_id,
            tier,
            recipe,
            quality_mode=spec.quality_mode,
            limit=self._limit_for_tier(spec, tier),
            recipe_base=recipe_path.parent,
        )

    def run(
        self,
        spec: BenchmarkSuiteSpec,
        *,
        spec_base: Path | None = None,
    ) -> BenchmarkSuiteReport:
        (champion, champion_path), challenger_recipes = self._load_recipes(
            spec,
            spec_base,
        )
        states = {
            (recipe.recipe_id, recipe.version): ChallengerSuiteResult(
                recipe_id=recipe.recipe_id,
                recipe_version=recipe.version,
            )
            for recipe, _ in challenger_recipes
        }
        active = {
            (recipe.recipe_id, recipe.version)
            for recipe, _ in challenger_recipes
        }

        for tier in (
            BenchmarkTier.SMOKE,
            BenchmarkTier.REGRESSION,
            BenchmarkTier.GOLDEN,
        ):
            if not active:
                break

            try:
                champion_scorecard = self._run_recipe(
                    champion,
                    champion_path,
                    spec,
                    tier,
                )
            except ValueError as exc:
                for identity in active:
                    state = states[identity]
                    state.recommendation = SuiteRecommendation.INCOMPLETE
                    state.reasons.append(
                        f"{tier.value} champion baseline unavailable: {exc}"
                    )
                break

            baseline_valid = champion_scorecard.status is HarnessRunStatus.COMPLETE
            next_active: set[tuple[str, str]] = set()

            for recipe, recipe_path in challenger_recipes:
                identity = (recipe.recipe_id, recipe.version)
                if identity not in active:
                    continue
                state = states[identity]

                try:
                    challenger_scorecard = self._run_recipe(
                        recipe,
                        recipe_path,
                        spec,
                        tier,
                    )
                except ValueError as exc:
                    state.recommendation = SuiteRecommendation.INCOMPLETE
                    state.reasons.append(
                        f"{tier.value} challenger run unavailable: {exc}"
                    )
                    continue

                decision = compare_scorecards(
                    champion_scorecard,
                    challenger_scorecard,
                    self._policy_for_tier(spec.promotion_policy, tier),
                )
                gate_reasons = list(decision.reasons)
                if not baseline_valid:
                    gate_reasons.append("champion baseline is not complete")

                passed_gate = decision.eligible and baseline_valid
                state.tiers.append(
                    SuiteTierResult(
                        tier=tier,
                        champion_run_id=champion_scorecard.run_id,
                        challenger_run_id=challenger_scorecard.run_id,
                        decision=decision,
                        passed_gate=passed_gate,
                        reasons=gate_reasons,
                    )
                )

                if not passed_gate:
                    if (
                        champion_scorecard.status is not HarnessRunStatus.COMPLETE
                        or challenger_scorecard.status is not HarnessRunStatus.COMPLETE
                    ):
                        state.recommendation = SuiteRecommendation.INCOMPLETE
                    else:
                        state.recommendation = SuiteRecommendation.REJECTED
                    state.reasons.extend(gate_reasons)
                    continue

                if tier is BenchmarkTier.GOLDEN:
                    state.recommendation = (
                        SuiteRecommendation.ELIGIBLE_FOR_HUMAN_REVIEW
                    )
                    state.reasons.append(
                        "Golden gate passed; explicit human approval is still required"
                    )
                else:
                    next_active.add(identity)

            active = next_active

        for identity, state in states.items():
            if (
                state.recommendation is SuiteRecommendation.INCOMPLETE
                and identity in active
            ):
                state.reasons.append("suite ended before Golden evidence was produced")

        report = BenchmarkSuiteReport(
            suite_id=spec.suite_id,
            dataset_id=spec.dataset_id,
            champion_recipe_id=champion.recipe_id,
            champion_recipe_version=champion.version,
            quality_mode=spec.quality_mode,
            challengers=list(states.values()),
            requires_human_approval=True,
            auto_promoted=False,
        )
        suite_dir = self.store.suite_dir(spec.suite_id)
        self.store.save_model(suite_dir / "spec.json", spec)
        self.store.save_model(suite_dir / "report.json", report)
        return report
