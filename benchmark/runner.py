"""Small, serial, opt-in benchmark. No network calls during planning or reporting."""

import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.request
from unittest.mock import patch

import jev_router as router
from benchmark.scoring import SCORING_VERSION, grade, normalized_grade, strict_equal

ROOT = Path(__file__).resolve().parent
STRATEGIES = ("cheap", "strong", "router")
ANSWER_INSTRUCTION = (
    "Верни только ответ в формате, указанном пользователем. "
    "Не добавляй пояснений, Markdown, блоков кода или префиксов. "
    "Если требуется JSON, верни только JSON без обёртки."
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_cases(path, split):
    path = Path(path).resolve()
    cases, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if case["id"] in seen or case["split"] not in ("dev", "test"):
            raise ValueError("Duplicate id or invalid split")
        seen.add(case["id"])
        answer = case["answer"]
        if answer["type"] == "exact":
            if not answer["accepted"] or not all(isinstance(x, str) for x in answer["accepted"]):
                raise ValueError("Invalid exact answer")
        elif answer["type"] != "json" or "value" not in answer:
            raise ValueError("Invalid answer schema")
        if not isinstance(case["prompt"], str):
            raise ValueError("Invalid prompt")
        content = [{"type": "text", "text": case["prompt"]}] if case["prompt"] else []
        for image in case.get("images", []):
            asset = (path.parent / image).resolve()
            if not asset.is_relative_to(path.parent) or asset.suffix.lower() != ".png":
                raise ValueError("Only local PNG assets inside dataset directory are allowed")
            data = asset.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG signature")
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(data).decode()}})
        if not content:
            raise ValueError("Empty task")
        case["messages"] = [{"role": "user", "content": content}]
        if case["split"] == split:
            cases.append(case)
    if not cases:
        raise ValueError("Empty split")
    return cases


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as out:
        out.write(json.dumps(value, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def rate_for(config, model):
    rate = config["prices"].get(model)
    if model == "typesafe/jev":
        if not rate or not positive(rate.get("input_per_million_rub")):
            raise ValueError("Set prices.typesafe/jev.input_per_million_rub")
    elif not rate or not all(positive(rate.get(k)) for k in ("input_per_million_rub", "output_per_million_rub")):
        raise ValueError("Set token prices for " + model)
    return rate


def estimate_call(body, config):
    model = body["model"]
    rate = rate_for(config, model)
    if model == "typesafe/jev":
        return len(json.dumps(body, ensure_ascii=False).encode()) * rate["input_per_million_rub"] / 1_000_000
    # Byte count is a conservative heuristic for text, NOT a provider tokenization guarantee.
    image_count = sum(len(router.images_of(m.get("content"))) for m in body.get("messages", []))
    inputs = len(json.dumps(body, ensure_ascii=False).encode()) + image_count * config["image_input_tokens"]
    outputs = body.get("max_tokens", config["max_tokens"])
    return (inputs * rate["input_per_million_rub"] + outputs * rate["output_per_million_rub"]) / 1_000_000


def main_body(case, model, config):
    return {"model": model, "messages": [{"role": "system", "content": ANSWER_INSTRUCTION}] + case["messages"],
            "max_tokens": config["max_tokens"],
            "temperature": 0, "stream": False}


def plan(cases, config):
    costs = {strategy: 0.0 for strategy in STRATEGIES}
    for case in cases:
        for strategy, key in (("cheap", "cheap_model"), ("strong", "strong_model")):
            costs[strategy] += estimate_call(main_body(case, config[key], config), config)
        # Reserve worst direct fallback chain, plus analyzer and Jev. Cold cache.
        main = max(sum(estimate_call(main_body(case, router.TIERS[t]["model"], config), config)
                       for t in [tier] + router.TIERS[tier]["fallback"]
                       if router.compatible(t, router.has_images(case["messages"])) and
                       router.TIERS[t]["model"] in config.get("prices", {}))
                   for tier in router.ORDER if router.compatible(tier, router.has_images(case["messages"])))
        analysis = sum(estimate_call({"model": router.VISION_MODEL, "messages": [
            {"role": "system", "content": router.VISION_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": "Опиши это изображение для маршрутизации задачи."}, image]}],
            "max_tokens": 450}, config) for image in router.images_of(case["messages"][0]["content"]))
        # Use the router's real criteria and maximum description length, without any vision call.
        with patch.object(router, "describe_image", return_value="я" * 3000):
            state = router.routing_state(case["messages"])
        costs["router"] += main + analysis + estimate_call(router.jev_body(state), config)
    return {"cases": len(cases), "model_answers": len(cases) * len(STRATEGIES),
            "planning_rub": costs, "total_planning_rub": sum(costs.values()),
            "warning": "Heuristic, not a billing quote or a hard spending cap; all rates are a catalog snapshot."}


class Meter:
    def __init__(self, config, path, budget, key):
        self.config, self.path, self.budget, self.key = config, path, budget, key
        if not positive(budget):
            raise ValueError("Invalid budget")
        reservations = rows(path)
        audits = rows(path.with_suffix(".usage.jsonl"))
        # Conservative charge: never release reserved money, but include known overruns.
        if len(audits) > len(reservations):
            raise ValueError("Usage ledger longer than reservation ledger")
        if any(not positive(r.get("reserved_rub")) for r in reservations) or any(
                a.get("estimated_cost_rub") is not None and not positive(a["estimated_cost_rub"])
                for a in audits):
            raise ValueError("Invalid monetary amount in API ledger")
        self.spent = sum(max(row["reserved_rub"],
                             (audits[i].get("estimated_cost_rub") or 0) if i < len(audits) else 0)
                         for i, row in enumerate(reservations))
        self.real_open = urllib.request.urlopen
        self.blocked = False
        self.cell = None

    def open(self, req, timeout=None):
        if self.blocked:
            raise RuntimeError("Run stopped after earlier API failure")
        body = json.loads(req.data)
        reserve = estimate_call(body, self.config)
        if not positive(reserve) or not positive(self.spent) or not positive(self.spent + reserve) or self.spent + reserve > self.budget:
            self.blocked = True
            raise RuntimeError("Planning budget exhausted or invalid ledger")
        # Write reservation BEFORE the request, so failures/crashes cannot erase possible spend.
        event = {"cell": self.cell, "model": body["model"], "reserved_rub": reserve, "time": time.time()}
        append_json(self.path, event)
        self.spent += reserve
        started = time.monotonic()
        try:
            authorized = urllib.request.Request(req.full_url, data=req.data, headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
            with self.real_open(authorized, timeout=timeout) as response:
                data = response.read()
            payload = json.loads(data)
            usage = payload.get("usage")
            # Separate audit trail. These amounts are estimates from supplied rates, not invoices.
            audit = {"cell": self.cell, "model": body["model"], "usage": usage,
                     "latency_s": time.monotonic() - started}
            if isinstance(usage, dict) and positive(usage.get("cost_rub")):
                audit["estimated_cost_rub"] = usage["cost_rub"]
                audit["cost_basis"] = "provider_reported_rub"
            elif (body["model"] == "typesafe/jev" and isinstance(usage, dict) and
                    type(usage.get("input_tokens")) is int and usage["input_tokens"] >= 0):
                rate = rate_for(self.config, body["model"])
                audit["estimated_cost_rub"] = usage["input_tokens"] * rate["input_per_million_rub"] / 1_000_000
                audit["cost_basis"] = "usage_times_manual_rates"
            elif (body["model"] == "typesafe/jev" and isinstance(usage, dict) and
                    type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] >= 0):
                rate = rate_for(self.config, body["model"])
                audit["estimated_cost_rub"] = usage["prompt_tokens"] * rate["input_per_million_rub"] / 1_000_000
                audit["cost_basis"] = "usage_times_manual_rates"
            elif body["model"] == "typesafe/jev":
                audit["estimated_cost_rub"] = None
                audit["cost_basis"] = "unknown"
                audit["planning_estimate_rub"] = reserve
                self.blocked = True
            elif (isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                               for k in ("prompt_tokens", "completion_tokens"))):
                rate = rate_for(self.config, body["model"])
                audit["estimated_cost_rub"] = (usage["prompt_tokens"] * rate["input_per_million_rub"] +
                                                usage["completion_tokens"] * rate["output_per_million_rub"]) / 1_000_000
                audit["cost_basis"] = "usage_times_manual_rates"
            else:
                audit["estimated_cost_rub"] = None
                audit["cost_basis"] = "unknown"
                self.blocked = True
            append_json(self.path.with_suffix(".usage.jsonl"), audit)
            if audit["estimated_cost_rub"] is not None:
                self.spent += max(0, audit["estimated_cost_rub"] - reserve)
                if self.spent > self.budget:
                    self.blocked = True
            return io.BytesIO(data)
        except Exception:
            self.blocked = True
            raise


def run(cases, config, directory, budget, resume=False):
    directory = Path(directory)
    if not resume and directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a new empty output directory; automatic paid retries/resume are disabled")
    if not router.KEY:
        raise ValueError("Set POLZA_API_KEY")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"config": config, "cases_sha256": fingerprint(cases),
                "router_sha256": hashlib.sha256(Path(router.__file__).read_bytes()).hexdigest(),
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "case_ids": [c["id"] for c in cases], "started": time.time(), "budget_rub": budget,
                "answer_instruction": ANSWER_INSTRUCTION, "scoring_version": SCORING_VERSION,
                "scoring_sha256": hashlib.sha256((ROOT / "scoring.py").read_bytes()).hexdigest()}
    completed = set()
    if resume:
        original = read_json(directory / "manifest.json")
        for key in ("config", "cases_sha256", "router_sha256", "case_ids", "answer_instruction",
                    "scoring_version", "scoring_sha256", "budget_rub"):
            if original.get(key) != manifest.get(key):
                raise ValueError("Resume protocol mismatch: " + key)
        saved = rows(directory / "results.jsonl")
        requests = rows(directory / "requests.jsonl")
        audits = rows(directory / "requests.usage.jsonl")
        for row in saved:
            cell = row["id"] + ":" + row["strategy"]
            if row["status"] != "ok" or cell in completed:
                raise ValueError("Resume refuses failed or duplicate cells; no automatic paid retries")
            completed.add(cell)
        if len(requests) != len(audits) or any(
                req["cell"] != audit["cell"] or req["model"] != audit["model"] or
                req["cell"] not in completed or not positive(audit.get("estimated_cost_rub"))
                for req, audit in zip(requests, audits)):
            raise ValueError("Ambiguous or incomplete API ledger; resume refused")
        append_json(directory / "continuations.jsonl", {
            "time": time.time(), "runner_sha256": manifest["runner_sha256"],
            "completed_cells_skipped": len(completed),
            "note": "Explicit continuation, unchanged evaluation protocol; reconciled global spending ledger"})
    else:
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    meter = Meter(config, directory / "requests.jsonl", budget, router.KEY)
    router.sessions.clear()
    router.vision_cache.clear()
    with patch.object(router.urllib.request, "urlopen", side_effect=meter.open):
        for case in cases:
            for strategy in STRATEGIES:
                meter.cell = case["id"] + ":" + strategy
                if meter.cell in completed:
                    continue
                start = time.monotonic()
                result = {"id": case["id"], "strategy": strategy, "category": case["category"],
                          "has_images": router.has_images(case["messages"])}
                try:
                    tier = None
                    if strategy == "router":
                        with redirect_stdout(io.StringIO()):
                            tier, _ = router.route(case["messages"])
                        if meter.blocked:
                            raise RuntimeError("Stopped after routing API failure or missing usage")
                        chain = [t for t in [tier] + router.TIERS[tier]["fallback"]
                                 if router.compatible(t, result["has_images"])]
                        models = [router.TIERS[t]["model"] for t in chain]
                    else:
                        models = [config[strategy + "_model"]]
                    result["chosen_tier"] = tier
                    # Stop on API failure instead of automatically making more paid attempts.
                    # Fallback is tested offline; compare successful single-shot routing here.
                    result["model"] = models[0]
                    body = main_body(case, models[0], config)
                    req = urllib.request.Request(router.POLZA + "/v1/chat/completions", data=json.dumps(body).encode())
                    with urllib.request.urlopen(req, timeout=120) as response:
                        answer = json.loads(response.read())
                    choice = answer["choices"][0]
                    result["text"] = router.text_of(choice["message"].get("content"))
                    result["finish_reason"] = choice.get("finish_reason")
                    result["correct"] = grade(result["text"], case["answer"])
                    result["correct_normalized"] = normalized_grade(result["text"], case["answer"])
                    result["truncated"] = result["finish_reason"] == "length"
                    result["scoring_version"] = SCORING_VERSION
                    result["status"] = "ok"
                except Exception as error:
                    result.update(status="error", correct=False, correct_normalized=False, error=type(error).__name__)
                    meter.blocked = True
                result["latency_s"] = time.monotonic() - start
                append_json(directory / "results.jsonl", result)
                print(meter.cell, result["status"], "correct=" + str(result["correct"]))
                if meter.blocked:
                    print("Stopped safely. See saved outputs; no automatic retries.")
                    return


def report(directory, evaluated_results=None):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    results = rows(directory / "results.jsonl") if evaluated_results is None else evaluated_results
    usage = rows(directory / "requests.usage.jsonl")
    reservations = rows(directory / "requests.jsonl")
    expected = len(manifest["case_ids"])
    output = {"expected_cases_per_strategy": expected, "strategies": {}}
    for strategy in STRATEGIES:
        selected = [r for r in results if r["strategy"] == strategy]
        costs = [r for r in usage if r["cell"].endswith(":" + strategy)]
        done = len(selected) == expected and all(r["status"] == "ok" for r in selected)
        def accuracy(subset, key="correct"):
            if not subset or any(key not in r for r in subset):
                return None
            return sum(r[key] for r in subset) / len(subset)
        output["strategies"][strategy] = {
            "completed_cells": len(selected), "complete": done,
            "accuracy_observed": accuracy(selected),
            "accuracy_normalized": accuracy(selected, "correct_normalized"),
            "accuracy_normalized_text": accuracy([r for r in selected if not r["has_images"]], "correct_normalized"),
            "accuracy_normalized_image": accuracy([r for r in selected if r["has_images"]], "correct_normalized"),
            "wrapper_only_failures": sum(not r["correct"] and r.get("correct_normalized", False) for r in selected),
            "truncated_answers": sum(r.get("finish_reason") == "length" for r in selected),
            "accuracy_text": accuracy([r for r in selected if not r["has_images"]]),
            "accuracy_image": accuracy([r for r in selected if r["has_images"]]),
            "mean_latency_s": sum(r["latency_s"] for r in selected) / len(selected) if selected else None,
            "known_estimated_cost_rub": sum(r["estimated_cost_rub"] or 0 for r in costs),
            "unknown_cost_calls": sum(r["estimated_cost_rub"] is None for r in costs) +
                                  sum(r["cell"].endswith(":" + strategy) for r in reservations) - len(costs),
            "reserved_planning_rub": sum(r["reserved_rub"] for r in reservations
                                         if r["cell"].endswith(":" + strategy)),
        }
    output["per_case"] = []
    for case_id in manifest["case_ids"]:
        comparison = {"id": case_id, "strategies": {}}
        for strategy in STRATEGIES:
            matches = [r for r in results if r["id"] == case_id and r["strategy"] == strategy]
            if not matches:
                comparison["strategies"][strategy] = None
                continue
            row = dict(matches[0])
            cell = case_id + ":" + strategy
            costs = [u for u in usage if u["cell"] == cell]
            row["known_cost_rub"] = sum(u["estimated_cost_rub"] or 0 for u in costs)
            row["unknown_cost_calls"] = (sum(u["estimated_cost_rub"] is None for u in costs) +
                                          sum(r["cell"] == cell for r in reservations) - len(costs))
            row["truncated"] = row.get("finish_reason") == "length"
            row["wrapper_only_failure"] = (not row["correct"] and row.get("correct_normalized", False)
                                               if "correct_normalized" in row else None)
            comparison["strategies"][strategy] = row
        output["per_case"].append(comparison)
    output["warning"] = ("Do not compare incomplete strategies. Costs are estimates, not invoices; "
                         "failed requests may be billed and have no usage. See requests.jsonl reservations.")
    return output


def analyze(directory, cases):
    """Derived offline analysis only; never mutate original results or strict grades."""
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if fingerprint(cases) != manifest["cases_sha256"]:
        raise ValueError("Dataset fingerprint differs from original run; analysis refused")
    answers = {c["id"]: c["answer"] for c in cases}
    evaluated = []
    for result in rows(directory / "results.jsonl"):
        result = dict(result)
        if result["status"] == "ok":
            # Strict scoring semantics must remain unchanged from the saved run.
            if grade(result["text"], answers[result["id"]]) != result["correct"]:
                raise ValueError("Strict score differs from original; analysis refused")
            result["correct_normalized"] = normalized_grade(result["text"], answers[result["id"]])
        else:
            result["correct_normalized"] = False
        evaluated.append(result)
    summary = report(directory, evaluated)
    summary["analysis"] = {"scoring_version": SCORING_VERSION,
                           "retrospective": True, "original_results_unchanged": True,
                           "scoring_sha256": hashlib.sha256((ROOT / "scoring.py").read_bytes()).hexdigest(),
                           "note": "Exploratory normalization chosen after dev pilot, not a new paid run."}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "report", "analyze"), nargs="?", default="plan")
    parser.add_argument("--cases", type=Path, default=ROOT / "data/cases.jsonl")
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.json")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--out", type=Path, default=ROOT / "runs/pilot")
    parser.add_argument("--budget-rub", type=float)
    parser.add_argument("--resume", action="store_true", help="Explicitly continue only completed, audited cells; never retry failures")
    args = parser.parse_args()
    try:
        if args.command == "report":
            print(json.dumps(report(args.out), ensure_ascii=False, indent=2))
            return
        cases = load_cases(args.cases, args.split)
        if args.command == "analyze":
            print(json.dumps(analyze(args.out, cases), ensure_ascii=False, indent=2))
            return
        config = read_json(args.config)
        if not isinstance(config["max_tokens"], int) or not 1 <= config["max_tokens"] <= 4096:
            raise ValueError("max_tokens must be 1..4096")
        if not positive(config["image_input_tokens"]):
            raise ValueError("Invalid image_input_tokens")
        print("Tasks:", len(cases), "| image tasks:", sum(bool(c.get("images")) for c in cases))
        try:
            estimate = plan(cases, config)
        except ValueError as error:
            print("Pricing not ready:", error)
            if args.command == "run":
                raise
            print("Offline validation finished. Fill manual prices before estimating or running.")
            return
        print(json.dumps(estimate, ensure_ascii=False, indent=2))
        if args.command == "plan":
            return
        if not positive(args.budget_rub) or args.budget_rub <= 0:
            raise ValueError("run requires positive --budget-rub")
        if estimate["total_planning_rub"] > args.budget_rub:
            raise ValueError("Plan exceeds supplied budget")
        if not sys.stdin.isatty():
            raise ValueError("Interactive confirmation required; no non-interactive paid runs")
        print("PAID API calls. Heuristic budget, NOT a hard billing cap. Outputs saved locally.")
        if input("Type PAY to confirm: ").strip() != "PAY":
            print("Cancelled; no API requests.")
            return
        run(cases, config, args.out, args.budget_rub, resume=args.resume)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, str(error) + "\n")
