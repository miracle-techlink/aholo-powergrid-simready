"""Bounded adaptive campaigns. Every accepted change comes from fresh verification."""

import time
from dataclasses import asdict
from pathlib import Path

from simready.agents import create_decision_provider
from simready.agents.contracts import DecisionProvider, RepairDecision
from simready.agents.token_usage import normalize_usage
from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import ensure_unchanged, reference
from simready.core.types import JsonObject, PathLike
from simready.evaluation.verification import verify_candidate
from simready.experience import EpisodeStore, SkillLibrary
from simready.repair import RepairPluginRegistry
from simready.workflows.config import load_workflow_config
from simready.workflows.repair_step import run_repair_trial


def run_repair_workflow(
    config: JsonObject,
    *,
    base_dir: PathLike,
    output_dir: PathLike,
    decision_provider: DecisionProvider | None = None,
) -> JsonObject:
    """Run the bounded development repair loop and return its evidence manifest."""
    base, out, provider = base_dir, output_dir, decision_provider
    paths, protocol, protocol_hash, policy_key = load_workflow_config(config, base)
    registry = RepairPluginRegistry(paths["registry"])
    inventory = read_json(paths["inventory"])
    provider = provider or create_decision_provider(config["agent"])
    memory, out = EpisodeStore(paths["memory"]), Path(out).resolve()
    catalog = SkillLibrary(memory)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "workflow.json", config)
    budget = config["budget"]
    started = time.monotonic()
    current = paths["candidate"]
    accepted_report = out / "initial/scorecard.json"
    result = {
        "status": "RUNNING",
        "scene_id": config["scene_id"],
        "rounds": [],
        "agent_calls": 0,
        "total_tokens": 0,
        "usage_complete": True,
        "release_authorized": False,
    }
    frozen = [
        reference(paths[key])
        for key in ("protocol", "registry", "inventory", "candidate")
    ]
    frozen += registry.input_files

    def remaining(cap):
        seconds = min(cap, budget["wall_seconds"] - (time.monotonic() - started))
        if seconds <= 0:
            raise TimeoutError("Campaign wall budget exhausted")
        return seconds

    try:
        before, defects, observations = verify_candidate(
            paths["protocol"],
            current,
            out / "initial",
            protocol_hash=protocol_hash,
            timeout=remaining(budget["verifier_timeout_s"]),
        )
        frozen += observations["input_files"]
        for index in range(budget["max_rounds"]):
            ensure_unchanged(frozen)
            if before["status"] == "PASS":
                result["status"] = "DEVELOPMENT_PASS"
                break
            remaining(budget["wall_seconds"])
            if (
                result["agent_calls"] >= budget["max_agent_calls"]
                or result["total_tokens"] >= budget["max_total_tokens"]
            ):
                result["status"] = "BUDGET_EXHAUSTED"
                break
            kinds = sorted({d["kind"] for d in defects["defects"]})
            experiences = memory.retrieve(config["family"], policy_key, kinds)
            skills = catalog.retrieve(
                config["family"], policy_key, kinds, registry.versions
            )
            proposals = registry.propose_repairs(
                protocol,
                config["family"],
                defects["defects"],
                skills,
                memory.failed_actions(
                    config["scene_id"],
                    digest(current),
                    family=config["family"],
                    policy_key=policy_key,
                ),
            )
            if not proposals:
                result["status"] = "NEEDS_EVIDENCE_OR_NEW_STRATEGY"
                break
            round_dir = out / f"round_{index:03d}"
            round_dir.mkdir()
            context = {
                "scene_id": config["scene_id"],
                "family": config["family"],
                "profile": protocol["profile"],
                "round": index,
                "defects": defects["defects"],
                "proposals": proposals,
                "memories": [
                    {
                        key: e[key]
                        for key in (
                            "episode_id",
                            "scene_id",
                            "plugin_id",
                            "plugin_version",
                            "parameters",
                            "outcome",
                            "comparison",
                        )
                    }
                    for e in experiences
                ],
                "active_skills": skills,
                "remaining_rounds": budget["max_rounds"] - index,
            }
            write_json(round_dir / "context.json", context)
            result["agent_calls"] += 1
            result["usage_complete"] = False
            response = provider.decide(
                context,
                round_dir / "agent",
                timeout=remaining(budget["agent_timeout_s"]),
            )
            write_json(round_dir / "decision.json", asdict(response))
            usage = normalize_usage(response.usage)
            if usage is None:
                result.update(status="USAGE_UNKNOWN", usage_complete=False)
                break
            result["usage_complete"] = True
            result["total_tokens"] += usage["total_tokens"]
            decision = RepairDecision.parse(asdict(response.decision), context)
            if result["total_tokens"] > budget["max_total_tokens"]:
                result["status"] = "BUDGET_EXHAUSTED"
                break
            if decision.action != "repair":
                result["status"] = decision.action.upper()
                break
            proposal = next(
                p for p in proposals if p["proposal_id"] == decision.proposal_id
            )
            episode = {
                key: proposal[key]
                for key in (
                    "action_key",
                    "plugin_id",
                    "plugin_version",
                    "parameters",
                    "defect_kinds",
                )
            }
            episode.update(
                scene_id=config["scene_id"],
                family=config["family"],
                role=config["role"],
                policy_key=policy_key,
                candidate_sha256=digest(current),
                evidence={
                    "before": reference(accepted_report),
                    "protocol": reference(paths["protocol"]),
                },
                agent_usage=response.usage,
                reused_skill_id=proposal["skill_id"],
            )
            trial = {
                "round": index,
                "proposal_id": proposal["proposal_id"],
                "accepted": False,
            }
            outcome = run_repair_trial(
                proposal,
                registry=registry,
                config=config,
                paths=paths,
                current=current,
                before=before,
                observations=observations,
                protocol_hash=protocol_hash,
                round_dir=round_dir,
                cache_dir=out / "cache",
                remaining=remaining,
                frozen=frozen,
                inventory=inventory,
            )
            episode["evidence"].update(outcome.evidence)
            if outcome.error is not None:
                trial["error"] = outcome.error
            elif outcome.comparison is not None:
                trial.update(outcome.comparison)
                if outcome.comparison["accepted"]:
                    current, before, defects, observations = (
                        outcome.candidate,
                        outcome.scorecard,
                        outcome.defects,
                        outcome.observations,
                    )
                    accepted_report = round_dir / "verification/scorecard.json"
                    frozen += [reference(current), *observations["input_files"]]
                elif (
                    outcome.comparison["reason"] == "REGRESSION"
                    and proposal["skill_id"]
                ):
                    catalog.retire(proposal["skill_id"])
            episode["trial"] = trial
            recorded = memory.append(episode)
            trial["episode_id"] = recorded["episode_id"]
            skill = catalog.distill(recorded["episode_id"])
            if (
                skill
                and config.get("auto_promote_skills", False)
                and catalog.eligible(skill)
                and skill["status"] != "retired"
            ):
                skill = catalog.promote(skill["skill_id"])
            trial["skill"] = skill
            result["rounds"].append(trial)
            write_json(round_dir / "result.json", trial)
            write_json(out / "progress.json", result)
        else:
            result["status"] = (
                "DEVELOPMENT_PASS" if before["status"] == "PASS" else "BUDGET_EXHAUSTED"
            )
        ensure_unchanged(frozen)
    except TimeoutError as error:
        result.update(status="BUDGET_EXHAUSTED", error=str(error))
    except Exception as error:
        result.update(status="FAILED", error=f"{type(error).__name__}: {error}")
    except BaseException as error:
        result.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        result.update(
            candidate=str(current),
            candidate_sha256=digest(current) if current.is_file() else None,
            scorecard=str(accepted_report),
            wall_seconds=time.monotonic() - started,
        )
        write_json(out / "result.json", result)
    return result
