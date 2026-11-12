"""Bounded adaptive campaigns. Every accepted change comes from fresh verification."""

import time
from dataclasses import asdict
from pathlib import Path

from ..agent import create_provider
from ..agent.contracts import Decision, normalize_usage
from ..artifacts import digest, read_json, write_json
from ..benchmark.comparison import compare
from ..infra import run as execute_tasks
from ..memory import MemoryStore
from ..plugins import PluginRegistry
from ..skills import SkillCatalog
from .contracts import load_campaign
from .verification import ensure_unchanged, reference, verify


def run_campaign(config, *, base, out, provider=None):
    paths, protocol, protocol_hash, policy_key = load_campaign(config, base)
    registry = PluginRegistry(paths["registry"])
    inventory = read_json(paths["inventory"])
    provider = provider or create_provider(config["agent"])
    memory, out = MemoryStore(paths["memory"]), Path(out).resolve()
    catalog = SkillCatalog(memory)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "campaign.json", config)
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
        before, defects, observations = verify(
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
            proposals = registry.proposals(
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
            response = provider.decide(
                context,
                round_dir / "agent",
                timeout=remaining(budget["agent_timeout_s"]),
            )
            write_json(round_dir / "decision.json", asdict(response))
            decision = Decision.parse(asdict(response.decision), context)
            usage = normalize_usage(response.usage)
            if usage is None:
                result.update(status="USAGE_UNKNOWN", usage_complete=False)
                break
            result["total_tokens"] += usage["total_tokens"]
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
                evidence={"before": reference(accepted_report)},
                agent_usage=response.usage,
                reused_skill_id=proposal["skill_id"],
            )
            trial = {
                "round": index,
                "proposal_id": proposal["proposal_id"],
                "accepted": False,
            }
            try:
                plan, final_task = registry.compile(
                    proposal,
                    scene_id=config["scene_id"],
                    candidate=current,
                    protocol=paths["protocol"],
                    directory=round_dir / "bindings",
                    input_files=observations["input_files"],
                )
                for task in plan["tasks"]:
                    task["timeout_s"] = min(
                        task["timeout_s"], remaining(budget["wall_seconds"])
                    )
                    task["queue_timeout_s"] = min(
                        task.get("queue_timeout_s", 300),
                        remaining(budget["wall_seconds"]),
                    )
                execution = execute_tasks(
                    plan,
                    inventory,
                    paths["registry"].parent,
                    round_dir / "execution",
                    out / "cache",
                    lock_dir=config.get("lock_dir", "/tmp/simready-device-leases"),
                    wall_timeout_s=remaining(budget["wall_seconds"]),
                )
                episode["evidence"]["execution"] = reference(
                    round_dir / "execution/result.json"
                )
                ensure_unchanged(frozen)
                if execution["status"] != "SUCCESS":
                    raise ValueError("Plugin execution failed")
                candidate = (
                    Path(execution["tasks"][final_task]["artifact_dir"])
                    / "candidate.json"
                )
                after, next_defects, next_observations = verify(
                    paths["protocol"],
                    candidate,
                    round_dir / "verification",
                    protocol_hash=protocol_hash,
                    timeout=remaining(budget["verifier_timeout_s"]),
                )
                episode["evidence"]["after"] = reference(
                    round_dir / "verification/scorecard.json"
                )
                comparison = compare(before, after)
                trial.update(comparison)
                if comparison["accepted"]:
                    current, before, defects, observations = (
                        candidate,
                        after,
                        next_defects,
                        next_observations,
                    )
                    accepted_report = round_dir / "verification/scorecard.json"
                    frozen += [reference(candidate), *observations["input_files"]]
                elif comparison["reason"] == "REGRESSION" and proposal["skill_id"]:
                    catalog.retire(proposal["skill_id"])
            except (ValueError, OSError, TimeoutError) as error:
                trial["error"] = str(error)
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
    result.update(
        candidate=str(current),
        candidate_sha256=digest(current) if current.is_file() else None,
        scorecard=str(accepted_report),
        wall_seconds=time.monotonic() - started,
    )
    write_json(out / "result.json", result)
    return result
