"""Validate a playbooks file against the rules the planner and runner rely on. Needs PyYAML."""

import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

import yaml

BUNDLED = Path(__file__).resolve().parent.parent / "skills" / "alethe" / "playbooks.yaml"
RUNNER = BUNDLED.parent / "scripts" / "orchestrate.py"
runner_spec = importlib.util.spec_from_file_location("orchestrate", RUNNER)
runner = importlib.util.module_from_spec(runner_spec)
runner_spec.loader.exec_module(runner)
RUNNER_CAPS = {name: rules["maxWorkers"] for name, rules in runner.STRATEGIES.items() if name != "auto"}
TIERS = {"cheap", "standard", "strong"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
BUILT_IN = {"claude", "codex", "agy", "kimi"}
DIALECT_CLIS = {"claude", "codex"}
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
PHASE_KEYS = {
    "id", "role", "who", "tier", "provider", "model", "effort", "readOnly", "workers", "isolate",
    "runner", "does", "returns", "limit", "gate", "if", "with", "after", "then", "rules",
}
QUOTA_KEYS = {"criticalThreshold", "stopAt", "whenLow", "onBothCritical", "whenSpare", "spareWithinHours", "spareMinLeft", "planEnds"}
ADAPTER_KEYS = {"command", "args", "promptTransport", "outputFormat", "modelFlag", "effortFlag", "checked"}
ADAPTER_REQUIRED = {"command", "args", "promptTransport", "outputFormat", "checked"}


def condition_pattern(providers):
    ids = "|".join(sorted(set(providers) | BUILT_IN, key=len, reverse=True))
    return re.compile(
        r"^(not )?(narrow|risky|light|standard|deep|kind:(research|code|review|command|scrap|docs)|"
        rf"({ids})-(low|spare)|both-low|all-low|budget-low|({ids})(-[a-z0-9]+)?-above:\d{{1,3}}|"
        r"(price|spent)-above:\d+(\.\d{1,2})?|"
        r"checks-failed|checks-failed-twice|report-weak|conflict|stuck|no-pass|findings-left)$"
    )


def conditions(value):
    return value if isinstance(value, list) else [value]


def check_quota(problems, quota, providers, where="quota", partial=False):
    unknown = set(quota) - QUOTA_KEYS
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
    choices = {
        "whenLow": {"switch-provider", "keep"},
        "onBothCritical": {"ask", "run-cheapest", "block"},
        "whenSpare": {"spend-strong", "prefer", "ignore"},
    }
    for key, allowed in choices.items():
        if (key in quota or not partial) and quota.get(key) not in allowed:
            problems.append(f"{where}.{key} {quota.get(key)!r}")
    for key, low, high in (("criticalThreshold", 1, 100), ("stopAt", 1, 100), ("spareWithinHours", 1, 168), ("spareMinLeft", 1, 100)):
        if key not in quota and partial:
            continue
        value = quota.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            problems.append(f"{where}.{key} {value!r}")
    if isinstance(quota.get("stopAt"), int) and isinstance(quota.get("criticalThreshold"), int) and quota["stopAt"] < quota["criticalThreshold"]:
        problems.append(f"{where}.stopAt is below criticalThreshold")
    ends = quota.get("planEnds", {})
    if not isinstance(ends, dict):
        problems.append(f"{where}.planEnds must be a map")
        return
    for provider, date in ends.items():
        if provider not in providers:
            problems.append(f"{where}.planEnds provider {provider!r} is not in providers")
        if not (isinstance(date, dt.date) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date))):
            problems.append(f"{where}.planEnds.{provider} {date!r}")


def check_tiers(problems, tiers, providers, where):
    for tier, entries in (tiers or {}).items():
        if tier not in TIERS:
            problems.append(f"{where}: tier {tier!r}")
            continue
        for provider, entry in (entries or {}).items():
            if provider not in providers:
                problems.append(f"{where}.{tier}: provider {provider!r} is not in providers")
            if set(entry or {}) - {"model", "effort"}:
                problems.append(f"{where}.{tier}.{provider}: keys {sorted(entry)}")
            if (entry or {}).get("effort") and entry["effort"] not in EFFORTS:
                problems.append(f"{where}.{tier}.{provider}: effort {entry['effort']!r}")


def check_group(problems, where, group):
    runner = next((phase.get("runner") for phase in group if phase.get("runner")), None)
    if runner not in RUNNER_CAPS:
        problems.append(f"{where}: runner {runner!r}")
        return
    total = sum(phase.get("workers", 1) for phase in group)
    if total > RUNNER_CAPS[runner]:
        problems.append(f"{where}: {total} workers exceed the {runner} cap of {RUNNER_CAPS[runner]}")
    if runner == "research" and any(not phase.get("readOnly") for phase in group):
        problems.append(f"{where}: research runs read-only workers only")
    if runner == "parallel" and any(not phase.get("readOnly") and not phase.get("isolate") for phase in group):
        problems.append(f"{where}: parallel needs isolate on every writer")
    if runner == "rescue" and total != 1:
        problems.append(f"{where}: rescue resumes exactly one worker")


def check_providers(problems, data, providers):
    if not providers or len(set(providers)) != len(providers):
        problems.append(f"providers {providers!r}")
    for provider in providers:
        if not PROVIDER_ID.match(str(provider)):
            problems.append(f"providers: id {provider!r}")
        if provider in BUILT_IN:
            continue
        adapter = (data.get("adapters") or {}).get(provider)
        if not adapter:
            problems.append(f"{provider}: a custom provider needs a checked adapter")
        elif set(adapter) - ADAPTER_KEYS or not ADAPTER_REQUIRED <= set(adapter):
            problems.append(f"adapters.{provider}: keys {sorted(adapter)}")
        elif adapter["promptTransport"] not in ("stdin", "argument") or adapter["outputFormat"] not in ("text", "json", "jsonl"):
            problems.append(f"adapters.{provider}: transport or format")
    for provider, models in (data.get("models") or {}).items():
        if provider not in providers:
            problems.append(f"models.{provider}: not in providers")
        for model in models:
            if isinstance(model, dict) and (set(model) - {"id", "cost"} or "id" not in model):
                problems.append(f"models.{provider}: {model!r}")
    for route, spec in (data.get("routes") or {}).items():
        if route != "router9":
            problems.append(f"routes.{route}: unknown route")
        if not isinstance(spec.get("port"), int) or not isinstance(spec.get("running"), bool):
            problems.append(f"routes.{route}: port and running")
        if any(p not in DIALECT_CLIS or p not in providers for p in spec.get("carries", [])):
            problems.append(f"routes.{route}.carries {spec.get('carries')!r}")


def check_profiles(problems, data, providers):
    profiles = data.get("profiles", {})
    if data.get("profile", "base") not in profiles:
        problems.append(f"profile {data.get('profile')!r} is not in profiles")
    for key, profile in profiles.items():
        if set(profile) - {"name", "summary", "order", "quota", "tiers"} or not profile.get("name") or not profile.get("summary"):
            problems.append(f"profiles.{key}: keys {sorted(profile)}")
        if "order" in profile and (set(profile["order"]) != set(providers) or len(profile["order"]) != len(providers)):
            problems.append(f"profiles.{key}.order must list every provider once")
        check_quota(problems, profile.get("quota", {}), providers, f"profiles.{key}.quota", partial=True)
        check_tiers(problems, profile.get("tiers"), providers, f"profiles.{key}.tiers")
    return profiles


def check_playbook(problems, key, playbook, providers, condition):
    phases = playbook["phases"]
    ids = [phase["id"] for phase in phases]
    if len(ids) != len(set(ids)):
        problems.append(f"{key}: duplicate phase ids")
    if phases[0].get("who") != "lead":
        problems.append(f"{key}: the first phase should be the planner (who: lead)")
    by_id = {phase["id"]: phase for phase in phases}
    for phase in phases:
        where = f"{key}.{phase['id']}"
        unknown = set(phase) - PHASE_KEYS
        if unknown:
            problems.append(f"{where}: unknown keys {sorted(unknown)}")
        if phase.get("tier") not in TIERS:
            problems.append(f"{where}: tier {phase.get('tier')!r}")
        if phase.get("provider", "auto") not in set(providers) | {"auto"}:
            problems.append(f"{where}: provider {phase.get('provider')!r}")
        if "effort" in phase and phase["effort"] not in EFFORTS:
            problems.append(f"{where}: effort {phase['effort']!r}")
        for token in conditions(phase["if"]) if "if" in phase else []:
            if not condition.match(str(token)):
                problems.append(f"{where}: condition {token!r}")
        for rule in phase.get("rules", []):
            for token in conditions(rule.get("if")):
                if not condition.match(str(token)):
                    problems.append(f"{where}: rule condition {token!r}")
            use = rule.get("use", {})
            if not use or set(use) - {"tier", "provider", "model", "effort"}:
                problems.append(f"{where}: rule use {use!r}")
            if use.get("tier") and use["tier"] not in TIERS:
                problems.append(f"{where}: rule tier {use['tier']!r}")
            if use.get("effort") and use["effort"] not in EFFORTS:
                problems.append(f"{where}: rule effort {use['effort']!r}")
        if phase.get("with") and phase["with"] not in by_id:
            problems.append(f"{where}: with -> {phase['with']}")
        if phase.get("after"):
            target = by_id.get(phase["after"])
            if not target:
                problems.append(f"{where}: after -> {phase['after']}")
            elif "gate" not in target and not target.get("after"):
                problems.append(f"{where}: after points at a phase without a gate")
        if phase.get("then") not in (None, "next", "recheck", "done"):
            problems.append(f"{where}: then {phase.get('then')!r}")
    steps = []
    for phase in [p for p in phases if not p.get("after")]:
        host = next((s for s in steps if any(q["id"] == phase.get("with") for q in s)), None) if phase.get("with") else None
        if host:
            host.append(phase)
        else:
            steps.append([phase])
    for step in steps:
        workers = [phase for phase in step if phase.get("who") != "lead"]
        routed = any("if" in phase for phase in step)
        groups = [[phase] for phase in workers] if routed else ([workers] if workers else [])
        for group in groups:
            check_group(problems, f"{key}.{group[0]['id']}", group)
    for phase in phases:
        if phase.get("after") and phase.get("who") != "lead":
            check_group(problems, f"{key}.{phase['id']}", [phase])


def check(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = []
    if data.get("version") != 1:
        problems.append("version must be 1")
    providers = data.get("providers", ["claude", "codex"])
    check_providers(problems, data, providers)
    setup = data.get("setup")
    if setup is not None and (
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(setup.get("date"))) or not isinstance(setup.get("notes", []), list)
    ):
        problems.append(f"setup {setup!r}")
    for tier in TIERS:
        if tier not in data.get("tiers", {}):
            problems.append(f"tiers.{tier} missing")
    check_tiers(problems, data.get("tiers"), providers, "tiers")
    check_quota(problems, data.get("quota", {}), providers)
    profiles = check_profiles(problems, data, providers)
    condition = condition_pattern(providers)
    for key, playbook in data["playbooks"].items():
        check_playbook(problems, key, playbook, providers, condition)
    for item in data.get("pickOrder", []):
        if item["playbook"] not in data["playbooks"]:
            problems.append(f"pickOrder -> {item['playbook']}")
    phases = sum(len(playbook["phases"]) for playbook in data["playbooks"].values())
    print(f"{path}: {len(providers)} providers, {len(profiles)} profiles, {len(data['playbooks'])} playbooks, {phases} phases")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    paths = [Path(arg) for arg in sys.argv[1:]] or [BUNDLED]
    sys.exit(max(check(path) for path in paths))
