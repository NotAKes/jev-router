"""Offline benchmark regression tests; no credentials or network are required.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_benchmark
All external calls are mocked, including paid pilot simulations.
"""
import base64
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import copy
from contextlib import ExitStack
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import zlib

from benchmark import runner as bench


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # unittest.TestCase.enterContext was added in 3.11; use stdlib ExitStack on 3.10 too.
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.enter_context = contexts.enter_context
        self.network = self.enter_context(patch.object(
            urllib.request, "urlopen", side_effect=AssertionError("Network forbidden")))
        self.tmp = Path(self.enter_context(tempfile.TemporaryDirectory()))
        self.config = bench.read_json(bench.ROOT / "config.example.json")
        for model, rate in self.config["prices"].items():
            for key in rate:
                rate[key] = 0.01 if model == "typesafe/jev" else 1.0
        self.cases = bench.load_cases(bench.ROOT / "data/cases.jsonl", "dev")
        self.enter_context(patch.object(bench.router, "KEY", "offline-test-key"))
        self.enter_context(patch.object(bench.router, "sessions", {}))
        self.enter_context(patch.object(bench.router, "vision_cache", {}))

    def response(self, text="47", usage=True):
        payload = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        if usage:
            payload["usage"] = {"prompt_tokens": 2, "completion_tokens": 3}
        return io.BytesIO(json.dumps(payload).encode())

    def request(self, model=None):
        body = bench.main_body(self.cases[0], model or self.config["cheap_model"], self.config)
        return urllib.request.Request("https://offline.invalid/v1/chat/completions",
                                      data=json.dumps(body).encode())

    def cli(self, command="run", config=None, budget="100", tty=True, confirm="PAY"):
        config_path = self.tmp / "config.json"
        config_path.write_text(json.dumps(self.config if config is None else config))
        args = ["benchmark", command, "--config", str(config_path), "--out", str(self.tmp / "output")]
        if budget is not None:
            args += ["--budget-rub", budget]
        self.output = io.StringIO()
        with patch("sys.argv", args), patch.object(bench.sys.stdin, "isatty", return_value=tty), \
                patch("builtins.input", return_value=confirm) as confirmation, \
                redirect_stdout(self.output), redirect_stderr(self.output):
            try:
                bench.main()
            except SystemExit as error:
                code = error.code
            else:
                code = 0
        return code, confirmation


class DatasetTests(OfflineTest):
    def test_counts_schema_and_embedded_assets(self):
        all_cases = []
        referenced = set()
        for split in ("dev", "test"):
            cases = bench.load_cases(bench.ROOT / "data/cases.jsonl", split)
            self.assertEqual(len(cases), 15)
            self.assertEqual(sum(bool(c.get("images")) for c in cases), 3)
            self.assertEqual(Counter(c["category"] for c in cases), {
                "math": 3, "algorithms": 3, "python": 2, "json_extraction": 2,
                "logic": 2, "vision_shapes": 1, "vision_table": 1, "vision_bars": 1})
            for case in cases:
                self.assertEqual(case["source"], "original")
                self.assertTrue(case["prompt"])
                content = case["messages"][0]["content"]
                self.assertEqual(content[0], {"type": "text", "text": case["prompt"]})
                answer = case["answer"]
                text = answer["accepted"][0] if answer["type"] == "exact" else json.dumps(answer["value"])
                self.assertTrue(bench.grade(text, answer))
                for image, block in zip(case.get("images", []), content[1:]):
                    asset = bench.ROOT / "data" / image
                    referenced.add(asset.resolve())
                    self.assertEqual(base64.b64decode(block["image_url"]["url"].split(",", 1)[1]), asset.read_bytes())
                self.assertEqual(len(content), 1 + len(case.get("images", [])))
            all_cases.extend(cases)
        self.assertEqual(len({c["id"] for c in all_cases}), 30)
        self.assertEqual(referenced, {p.resolve() for p in (bench.ROOT / "data/assets").glob("*.png")})
        self.assertEqual(len(referenced), 6)
        self.network.assert_not_called()

    def test_png_integrity_dimensions_and_pixels(self):
        for asset in (bench.ROOT / "data/assets").glob("*.png"):
            with self.subTest(asset=asset.name):
                data = asset.read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                offset, chunks, compressed = 8, [], b""
                while offset < len(data):
                    length = struct.unpack_from(">I", data, offset)[0]
                    kind = data[offset + 4:offset + 8]
                    payload = data[offset + 8:offset + 8 + length]
                    crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
                    self.assertEqual(crc, zlib.crc32(kind + payload) & 0xffffffff)
                    chunks.append(kind)
                    if kind == b"IHDR":
                        self.assertEqual(struct.unpack(">IIBBBBB", payload), (480, 300, 8, 2, 0, 0, 0))
                    if kind == b"IDAT":
                        compressed += payload
                    offset += length + 12
                self.assertEqual(offset, len(data))
                self.assertEqual(chunks[0], b"IHDR")
                self.assertEqual(chunks[-1], b"IEND")
                pixels = zlib.decompress(compressed)
                self.assertEqual(len(pixels), 300 * (480 * 3 + 1))
                self.assertTrue(all(pixels[y * 1441] <= 4 for y in range(300)))

    def test_invalid_dataset_and_assets_rejected(self):
        source = copy.deepcopy(self.cases[0])
        for mutation in ("duplicate", "split", "answer", "prompt", "empty", "traversal", "suffix", "signature"):
            with self.subTest(mutation=mutation):
                case = copy.deepcopy(source)
                if mutation == "split": case["split"] = "train"
                if mutation == "answer": case["answer"] = {"type": "exact", "accepted": []}
                if mutation == "prompt": case["prompt"] = 1
                if mutation == "empty": case["prompt"] = ""
                if mutation == "traversal": case["images"] = ["../outside.png"]
                if mutation == "suffix": case["images"] = ["image.jpg"]
                if mutation == "signature":
                    (self.tmp / "bad.png").write_bytes(b"not PNG")
                    case["images"] = ["bad.png"]
                dataset = self.tmp / "cases.jsonl"
                dataset.write_text(json.dumps(case) + ("\n" + json.dumps(case) if mutation == "duplicate" else ""))
                with self.assertRaises(ValueError):
                    bench.load_cases(dataset, "dev")
        dataset.write_text(json.dumps(dict(source, split="test")))
        with self.assertRaisesRegex(ValueError, "Empty split"):
            bench.load_cases(dataset, "dev")


class ScoringTests(OfflineTest):
    def test_exact_is_case_sensitive_and_not_markdown_tolerant(self):
        answer = {"type": "exact", "accepted": ["Yes", "да"]}
        for text in ("Yes", " \nYes\t", "да"):
            self.assertTrue(bench.grade(text, answer))
        for text in ("yes", "**Yes**", "`Yes`", "```\nYes\n```", "Yes.", "Answer: Yes"):
            self.assertFalse(bench.grade(text, answer), text)

    def test_json_strict_recursive_types_and_structure(self):
        expected = {"a": [1, True, None], "b": {"x": "v"}}
        self.assertTrue(bench.grade(' {"b":{"x":"v"},"a":[1,true,null]} ', {"type": "json", "value": expected}))
        for value in ({"a": [True, True, None], "b": {"x": "v"}},
                      {"a": [1.0, True, None], "b": {"x": "v"}},
                      {"a": [1, 1, None], "b": {"x": "v"}},
                      {"a": [True, 1, None], "b": {"x": "v"}},
                      {"a": [1, True], "b": {"x": "v"}}, {"a": [1, True, None]},
                      dict(expected, extra=0)):
            self.assertFalse(bench.grade(json.dumps(value), {"type": "json", "value": expected}))
        for text in ('```json\n{"x":1}\n```', '{"x":1} trailing', "not json", ""):
            self.assertFalse(bench.grade(text, {"type": "json", "value": {"x": 1}}))
        for left, right in ((True, 1), (False, 0), (1, 1.0), ([1, 2], [2, 1])):
            self.assertFalse(bench.strict_equal(left, right))


class GuardTests(OfflineTest):
    def test_plan_is_offline_and_accounts_for_all_strategies(self):
        result = bench.plan(self.cases, self.config)
        self.assertEqual(result["cases"], 15)
        self.assertEqual(result["model_answers"], 45)
        self.assertEqual(set(result["planning_rub"]), set(bench.STRATEGIES))
        self.assertAlmostEqual(result["total_planning_rub"], sum(result["planning_rub"].values()))
        self.assertTrue(all(x > 0 for x in result["planning_rub"].values()))
        self.assertEqual(self.cli("plan", tty=False)[0], 0)
        self.network.assert_not_called()

    def test_plan_rejects_missing_intermediate_model_rate(self):
        model = bench.router.TIERS["terra"]["model"]
        config = copy.deepcopy(self.config)
        config["prices"].pop(model)
        with self.assertRaisesRegex(ValueError, "Set token prices for " + model):
            bench.plan(self.cases, config)
        self.network.assert_not_called()

    def test_missing_prices_plan_validates_but_run_denied(self):
        config = bench.read_json(bench.ROOT / "config.example.json")
        config["prices"][config["cheap_model"]]["input_per_million_rub"] = None
        self.assertEqual(self.cli("plan", config=config)[0], 0)
        self.assertIn("Pricing not ready", self.output.getvalue())
        self.assertEqual(self.cli(config=config)[0], 2)
        self.network.assert_not_called()

    def test_paid_cli_requires_budget_tty_and_confirmation(self):
        for budget, tty, confirm, expected in ((None, True, "PAY", 2), ("0", True, "PAY", 2),
                ("-1", True, "PAY", 2), ("nan", True, "PAY", 2), ("inf", True, "PAY", 2),
                ("0.000001", True, "PAY", 2), ("100", False, "PAY", 2),
                ("100", True, "no", 0), ("100", True, "", 0)):
            with self.subTest(budget=budget, tty=tty, confirm=confirm):
                code, confirmation = self.cli(budget=budget, tty=tty, confirm=confirm)
                self.assertEqual(code, expected)
                if not tty or budget != "100": confirmation.assert_not_called()
                self.assertFalse((self.tmp / "output").exists())
        self.network.assert_not_called()

    def test_invalid_rates_rejected(self):
        for invalid in (None, True, -1, float("inf"), float("nan"), "1"):
            for model in (self.config["cheap_model"], "typesafe/jev"):
                with self.subTest(invalid=invalid, model=model):
                    config = copy.deepcopy(self.config)
                    key = next(iter(config["prices"][model]))
                    config["prices"][model][key] = invalid
                    with self.assertRaises(ValueError): bench.rate_for(config, model)
        self.assertTrue(bench.positive(0))
        self.assertFalse(bench.positive(True))

    def test_run_rejects_missing_key_or_nonempty_directory(self):
        with patch.object(bench.router, "KEY", ""):
            with self.assertRaisesRegex(ValueError, "POLZA_API_KEY"):
                bench.run(self.cases[:1], self.config, self.tmp / "new", 100)
        self.assertFalse((self.tmp / "new").exists())
        sentinel = self.tmp / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "empty output"):
            bench.run(self.cases[:1], self.config, self.tmp, 100)
        self.assertEqual(sentinel.read_text(), "keep")
        self.network.assert_not_called()


class MeterTests(OfflineTest):
    def meter(self, budget=100):
        meter = bench.Meter(self.config, self.tmp / "requests.jsonl", budget, "test-secret")
        meter.cell = "dev-01:cheap"
        return meter

    def test_reservation_precedes_request_and_failure_persists(self):
        meter = self.meter()
        def fail(req, timeout=None):
            events = bench.rows(meter.path)
            self.assertEqual(len(events), 1)
            self.assertGreater(events[0]["reserved_rub"], 0)
            self.assertEqual(events[0]["cell"], meter.cell)
            self.assertEqual(req.get_header("Authorization"), "Bearer test-secret")
            self.assertEqual(timeout, 9)
            raise OSError("simulated failure")
        self.network.side_effect = fail
        with self.assertRaises(OSError): meter.open(self.request(), timeout=9)
        self.assertTrue(meter.blocked)
        self.assertEqual(meter.spent, bench.rows(meter.path)[0]["reserved_rub"])
        self.assertEqual(self.meter().spent, meter.spent)
        with self.assertRaisesRegex(RuntimeError, "earlier API failure"): meter.open(self.request())
        self.assertEqual(self.network.call_count, 1)
        self.assertNotIn("test-secret", meter.path.read_text())

    def test_nonfinite_or_invalid_reservations_fail_closed(self):
        for value in (float('nan'), float('inf'), -1, True, '1', None):
            with self.subTest(value=value):
                path = self.tmp / 'bad-ledger.jsonl'
                path.write_text(json.dumps({'reserved_rub': value}) + '\n')
                with self.assertRaisesRegex(ValueError, 'monetary amount'):
                    bench.Meter(self.config, path, 100, 'test-key')
        self.network.assert_not_called()

    def test_budget_blocks_before_request_or_reservation(self):
        meter = self.meter(budget=0)
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"): meter.open(self.request())
        self.assertTrue(meter.blocked)
        self.assertFalse(meter.path.exists())
        self.network.assert_not_called()

    def test_usage_billing_and_manual_jev_billing(self):
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: self.response()
        with meter.open(self.request()) as response:
            self.assertEqual(json.loads(response.read())["usage"]["prompt_tokens"], 2)
        self.network.side_effect = lambda *a, **k: io.BytesIO(b'{"usage":{"prompt_tokens":2}}')
        meter.open(self.request("typesafe/jev"))
        audits = bench.rows(meter.path.with_suffix(".usage.jsonl"))
        self.assertAlmostEqual(audits[0]["estimated_cost_rub"], 5 / 1_000_000)
        self.assertEqual(audits[0]["cost_basis"], "usage_times_manual_rates")
        self.assertAlmostEqual(audits[1]["estimated_cost_rub"], 2 * 0.01 / 1_000_000)
        self.assertEqual(audits[1]["cost_basis"], "usage_times_manual_rates")
        self.assertFalse(meter.blocked)
        self.assertEqual(len(bench.rows(meter.path)), 2)

    def test_missing_or_invalid_usage_blocks_subsequent_calls(self):
        for usage in (None, {}, {"prompt_tokens": -1, "completion_tokens": 1},
                      {"prompt_tokens": 1.0, "completion_tokens": 1}):
            with self.subTest(usage=usage):
                meter = self.meter()
                self.network.side_effect = lambda *a, **k: io.BytesIO(json.dumps({"usage": usage}).encode())
                meter.open(self.request())
                self.assertTrue(meter.blocked)
                audit = bench.rows(meter.path.with_suffix(".usage.jsonl"))[-1]
                self.assertIsNone(audit["estimated_cost_rub"])
                self.assertEqual(audit["cost_basis"], "unknown")
                calls = self.network.call_count
                with self.assertRaises(RuntimeError): meter.open(self.request())
                self.assertEqual(self.network.call_count, calls)

    def test_provider_reported_cost_takes_precedence(self):
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: io.BytesIO(
            b'{"usage":{"input_tokens":938,"output_tokens":48,"cost_rub":0.01630994}}')
        meter.open(self.request("typesafe/jev"))
        audit = bench.rows(meter.path.with_suffix(".usage.jsonl"))[0]
        self.assertEqual(audit["estimated_cost_rub"], 0.01630994)
        self.assertEqual(audit["cost_basis"], "provider_reported_rub")

    def test_jev_accepts_input_tokens_without_cost(self):
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: io.BytesIO(b'{"usage":{"input_tokens":10}}')
        meter.open(self.request("typesafe/jev"))
        audit = bench.rows(meter.path.with_suffix(".usage.jsonl"))[0]
        self.assertAlmostEqual(audit["estimated_cost_rub"], 10 * 0.01 / 1_000_000)
        self.assertFalse(meter.blocked)

    def test_jev_without_token_usage_is_unknown_not_flat_fee(self):
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: io.BytesIO(b'{"answers":{}}')
        meter.open(self.request("typesafe/jev"))
        self.assertTrue(meter.blocked)
        audit = bench.rows(meter.path.with_suffix(".usage.jsonl"))[0]
        self.assertIsNone(audit["estimated_cost_rub"])
        self.assertEqual(audit["cost_basis"], "unknown")

    def test_jev_plan_includes_real_criteria_and_state(self):
        short = bench.router.jev_body("short")
        long = bench.router.jev_body("long " * 100)
        self.assertIn("criteria", short["questions"]["tier"])
        self.assertGreater(bench.estimate_call(long, self.config), bench.estimate_call(short, self.config))
        self.network.assert_not_called()

    def test_usage_overrun_is_charged_against_global_budget(self):
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: io.BytesIO(json.dumps({"usage": {
            "prompt_tokens": 1000000, "completion_tokens": 1000000}}).encode())
        meter.open(self.request())
        self.assertFalse(meter.blocked)
        self.assertEqual(meter.spent, 2)
        self.assertEqual(self.meter().spent, 2)
        self.assertEqual(bench.rows(meter.path.with_suffix(".usage.jsonl"))[0]["estimated_cost_rub"], 2)

    def test_boolean_usage_is_not_a_valid_token_count(self):
        # bool subclasses int, but true/false are not token counts.
        meter = self.meter()
        self.network.side_effect = lambda *a, **k: io.BytesIO(
            b'{"usage":{"prompt_tokens":true,"completion_tokens":false}}')
        meter.open(self.request())
        self.assertTrue(meter.blocked)
        self.assertIsNone(bench.rows(meter.path.with_suffix(".usage.jsonl"))[0]["estimated_cost_rub"])


class RunAndReportTests(OfflineTest):
    def test_successful_text_and_image_pilot_and_report(self):
        cases = [self.cases[0], next(c for c in self.cases if c.get("images"))]
        directory = self.tmp / "pilot"
        def fake_provider(req, timeout=None):
            reservations = bench.rows(directory / "requests.jsonl")
            self.assertEqual(len(reservations), self.network.call_count)
            body = json.loads(req.data)
            self.assertEqual(body["model"], reservations[-1]["model"])
            self.assertEqual(req.get_header("Authorization"), "Bearer offline-test-key")
            if body["model"] == "typesafe/jev":
                return io.BytesIO(b'{"answers":{"tier":{"choice":"haiku","confidence":1}},"usage":{"prompt_tokens":2}}')
            if body["model"] == bench.router.VISION_MODEL:
                return self.response("A synthetic chart")
            current = next(c for c in cases if reservations[-1]["cell"].startswith(c["id"] + ":"))
            answer = current["answer"]
            return self.response(answer["accepted"][0] if answer["type"] == "exact" else json.dumps(answer["value"]))
        self.network.side_effect = fake_provider
        with redirect_stdout(io.StringIO()): bench.run(cases, self.config, directory, 100)
        results = bench.rows(directory / "results.jsonl")
        self.assertEqual(len(results), 6)
        self.assertTrue(all(r["status"] == "ok" and r["correct"] for r in results))
        self.assertEqual([r["chosen_tier"] for r in results], [None, None, "haiku"] * 2)
        self.assertEqual(self.network.call_count, 9)  # Six answers, two Jev, one image analyzer.
        manifest = bench.read_json(directory / "manifest.json")
        self.assertEqual(manifest["case_ids"], [c["id"] for c in cases])
        self.assertEqual(manifest["cases_sha256"], bench.fingerprint(cases))
        self.assertEqual(len(manifest["runner_sha256"]), 64)
        summary = bench.report(directory)
        self.assertEqual(summary["expected_cases_per_strategy"], 2)
        for strategy, stats in summary["strategies"].items():
            self.assertTrue(stats["complete"], strategy)
            self.assertEqual(stats["completed_cells"], 2)
            self.assertEqual(stats["accuracy_observed"], 1)
            self.assertEqual(stats["accuracy_text"], 1)
            self.assertEqual(stats["accuracy_image"], 1)
            self.assertEqual(stats["unknown_cost_calls"], 0)
            self.assertGreater(stats["known_estimated_cost_rub"], 0)
        before = self.network.call_count
        self.assertEqual(self.cli("report")[0], 2)  # Missing output, never network.
        self.assertEqual(self.network.call_count, before)

    def test_main_body_has_shared_instruction_without_mutating_case(self):
        original = copy.deepcopy(self.cases[0])
        for model in (self.config["cheap_model"], self.config["strong_model"]):
            body = bench.main_body(self.cases[0], model, self.config)
            self.assertEqual(body["messages"][0], {"role": "system", "content": bench.ANSWER_INSTRUCTION})
            self.assertEqual(body["messages"][1:], original["messages"])
            self.assertEqual(body["max_tokens"], 2048)
        self.assertEqual(self.cases[0], original)
        self.network.assert_not_called()

    def test_retrospective_analysis_is_offline_and_preserves_originals(self):
        case = self.cases[0]
        directory = self.tmp / "old-pilot"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({
            "case_ids": [case["id"]], "cases_sha256": bench.fingerprint([case])}))
        for strategy in bench.STRATEGIES:
            bench.append_json(directory / "results.jsonl", {
                "id": case["id"], "strategy": strategy, "category": case["category"],
                "status": "ok", "text": "**47**", "correct": False,
                "has_images": False, "finish_reason": "length" if strategy == "cheap" else "stop",
                "latency_s": 1.0, "model": "offline"})
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        summary = bench.analyze(directory, [case])
        self.assertTrue(summary["analysis"]["retrospective"])
        for stats in summary["strategies"].values():
            self.assertEqual(stats["accuracy_observed"], 0)
            self.assertEqual(stats["accuracy_normalized"], 1)
            self.assertEqual(stats["wrapper_only_failures"], 1)
        self.assertEqual(summary["strategies"]["cheap"]["truncated_answers"], 1)
        cell = summary["per_case"][0]["strategies"]["cheap"]
        self.assertTrue(cell["truncated"])
        self.assertTrue(cell["wrapper_only_failure"])
        self.assertEqual(cell["text"], "**47**")
        self.assertIsNone(bench.report(directory)["strategies"]["cheap"]["accuracy_normalized"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            bench.analyze(directory, self.cases)
        self.network.assert_not_called()

    def test_explicit_resume_skips_all_paid_completed_cells(self):
        cases = self.cases[:1]
        directory = self.tmp / "resume"
        def fake(req, timeout=None):
            if json.loads(req.data)["model"] == "typesafe/jev":
                return io.BytesIO(b'{"answers":{"tier":{"choice":"haiku","confidence":1}},"usage":{"input_tokens":2}}')
            return self.response("47")
        self.network.side_effect = fake
        with redirect_stdout(io.StringIO()):
            bench.run(cases, self.config, directory, 100)
        paid_calls = self.network.call_count
        original = (directory / "results.jsonl").read_bytes()
        with redirect_stdout(io.StringIO()):
            bench.run(cases, self.config, directory, 100, resume=True)
        self.assertEqual(self.network.call_count, paid_calls)
        self.assertEqual((directory / "results.jsonl").read_bytes(), original)
        self.assertEqual(bench.rows(directory / "continuations.jsonl")[0]["completed_cells_skipped"], 3)
        changed = copy.deepcopy(self.config)
        changed["max_tokens"] += 1
        with self.assertRaisesRegex(ValueError, "protocol mismatch"):
            bench.run(cases, changed, directory, 100, resume=True)
        bench.append_json(directory / "requests.jsonl", {
            "cell": "dev-01:router", "model": "unknown", "reserved_rub": 1})
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            bench.run(cases, self.config, directory, 100, resume=True)
        self.assertEqual(self.network.call_count, paid_calls)

    def test_run_stops_after_failure_or_missing_usage_and_reports_partial(self):
        for failure in ("network", "usage", "routing"):
            with self.subTest(failure=failure):
                directory = self.tmp / failure
                self.network.reset_mock()
                def fake_provider(req, timeout=None):
                    if failure == "network" or (failure == "routing" and json.loads(req.data)["model"] == "typesafe/jev"):
                        raise OSError("offline simulated failure")
                    return self.response(usage=failure != "usage")
                self.network.side_effect = fake_provider
                with redirect_stdout(io.StringIO()): bench.run(self.cases[:1], self.config, directory, 100)
                results = bench.rows(directory / "results.jsonl")
                self.assertEqual(len(results), 3 if failure == "routing" else 1)
                self.assertEqual(self.network.call_count, 3 if failure == "routing" else 1)
                self.assertEqual(results[-1]["strategy"], "router" if failure == "routing" else "cheap")
                self.assertEqual(results[-1]["status"], "ok" if failure == "usage" else "error")
                self.assertEqual(len(bench.rows(directory / "requests.jsonl")), self.network.call_count)
                summary = bench.report(directory)
                self.assertFalse(summary["strategies"]["router"]["complete"])
                if failure != "routing":
                    self.assertIsNone(summary["strategies"]["strong"]["accuracy_observed"])
                if failure == "usage":
                    self.assertEqual(summary["strategies"]["cheap"]["unknown_cost_calls"], 1)
                self.assertIn("incomplete", summary["warning"])

    def test_report_command_is_offline(self):
        directory = self.tmp / "output"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({"case_ids": ["dev-01"]}))
        self.assertEqual(self.cli("report")[0], 0)
        report = json.loads(self.output.getvalue())
        for stats in report["strategies"].values():
            self.assertFalse(stats["complete"])
            self.assertEqual(stats["completed_cells"], 0)
            self.assertIsNone(stats["accuracy_observed"])
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
