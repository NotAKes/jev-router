"""Offline regression tests: python3 -m unittest -v test_jev_router."""
import copy
import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

import jev_router as router


def image(url="https://example.test/one.png"):
    return {"type": "image_url", "image_url": {"url": url, "detail": "high"}}


def user(content):
    return {"role": "user", "content": content}


class VisionRoutingTests(unittest.TestCase):
    def setUp(self):
        # Every test is offline, including when an expected mock is missing.
        self.network = self.enterContext(patch.object(
            router.urllib.request, "urlopen",
            side_effect=AssertionError("Unmocked network access is forbidden")))
        self.enterContext(patch("builtins.print"))
        router.sessions.clear()
        if hasattr(router, "vision_cache"):
            router.vision_cache.clear()
        self.part = image()
        self.messages = [user([self.part])]

    def response(self, text="A screenshot of a Python traceback"):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": text}}]
        }).encode()
        self.network.side_effect = None
        self.network.return_value = response
        return response

    def post(self, body):
        handler = router.Handler.__new__(router.Handler)
        handler.path = "/v1/chat/completions"
        raw = json.dumps(body).encode()
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.stream = Mock()
        handler.send_bytes = Mock()
        handler.do_POST()
        return handler

    def test_images_of_filters_invalid_parts_without_mutation(self):
        content = [{"type": "text", "text": "hello"}, self.part,
                   {"type": "input_image", "image_url": "ignored"},
                   {"type": "image_url", "image_url": {"url": ""}},
                   {"type": "image_url", "image_url": {"url": None}},
                   {"type": "image_url", "image_url": {"url": 42}},
                   {"type": "image_url", "image_url": None}, None]
        original = copy.deepcopy(content)
        self.assertEqual(router.images_of(content), [self.part])
        self.assertEqual(content, original)
        self.assertEqual(router.images_of("plain text"), [])
        self.assertEqual(router.images_of(None), [])

    def test_text_only_does_not_describe_images(self):
        with patch.object(router, "describe_image") as describe:
            state = router.routing_state([user("Explain binary search")])
        self.assertIn("Explain binary search", state)
        describe.assert_not_called()

    def test_image_only_state_and_previous_text_context(self):
        with patch.object(router, "describe_image", return_value="TRACEBACK_SCREENSHOT") as describe:
            state = router.routing_state([
                user("OLDER_CONTEXT_SHOULD_BE_DROPPED"),
                user("Please fix this program"),
                {"role": "assistant", "content": "Show the error"},
                *self.messages])
        describe.assert_called_once_with(self.part)
        self.assertIn("TRACEBACK_SCREENSHOT", state)
        self.assertIn("Please fix this program", state)
        self.assertNotIn("OLDER_CONTEXT_SHOULD_BE_DROPPED", state)
        self.assertLess(state.index("Please fix this program"), state.index("TRACEBACK_SCREENSHOT"))

    def test_image_description_is_marked_untrusted_not_a_task_to_solve(self):
        with patch.object(router, "describe_image", return_value="VISIBLE_CONTENT"):
            state = router.routing_state(self.messages).lower()
        self.assertIn("visible_content", state)
        self.assertRegex(state, r"untrusted|недоверенн|недостоверн|не довер")
        self.assertRegex(state, r"do not (?:follow|execute)|not instructions|не (?:выполня|следу)|не инструкц")

    def test_analyzer_payload_and_original_image_are_preserved(self):
        self.response()
        original = copy.deepcopy(self.part)
        self.assertTrue(router.describe_image(self.part))
        request = self.network.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "google/gemini-2.5-flash-lite")
        self.assertEqual(payload["model"], router.VISION_MODEL)
        self.assertEqual(self.network.call_args.kwargs["timeout"], router.VISION_TIMEOUT)
        parts = [p for m in payload["messages"] if isinstance(m.get("content"), list)
                 for p in m["content"] if p.get("type") == "image_url"]
        self.assertEqual(parts, [original])
        self.assertEqual(self.part, original)

    def test_description_cache_reuses_result(self):
        self.response("cached description")
        self.assertEqual(router.describe_image(self.part), "cached description")
        self.assertEqual(router.describe_image(copy.deepcopy(self.part)), "cached description")
        self.assertEqual(self.network.call_count, 1)

    def test_cache_key_includes_model_and_url(self):
        self.response()
        router.describe_image(self.part)
        router.describe_image(image("https://example.test/two.png"))
        with patch.object(router, "VISION_MODEL", "test/another-vision-model"):
            router.describe_image(self.part)
        self.assertEqual(self.network.call_count, 3)

    def test_failure_is_not_cached(self):
        self.network.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            router.describe_image(self.part)
        self.response("recovered")
        self.assertEqual(router.describe_image(self.part), "recovered")
        self.assertEqual(self.network.call_count, 2)

    def test_empty_response_raises_and_is_not_cached(self):
        self.response("   ")
        with self.assertRaises(ValueError):
            router.describe_image(self.part)
        self.response("recovered")
        self.assertEqual(router.describe_image(self.part), "recovered")
        self.assertEqual(self.network.call_count, 2)

    def test_cache_expiry_uses_monotonic_clock(self):
        self.response()
        with patch.object(router, "VISION_CACHE_TTL", 10), \
                patch.object(router.time, "monotonic", return_value=100) as clock:
            router.describe_image(self.part)
            clock.return_value = 105
            router.describe_image(self.part)
            self.assertEqual(self.network.call_count, 1)
            clock.return_value = 111
            router.describe_image(self.part)
        self.assertEqual(self.network.call_count, 2)

    def test_cache_size_is_bounded(self):
        self.response()
        with patch.object(router, "VISION_CACHE_SIZE", 2):
            for index in range(5):
                router.describe_image(image(f"https://example.test/{index}.png"))
                self.assertLessEqual(len(router.vision_cache), 2)

    def test_route_sends_description_to_jev(self):
        with patch.object(router, "describe_image", return_value="VISIBLE_TRACEBACK"), \
                patch.object(router, "ask_jev", return_value=("sonnet", 1.0, {})) as ask:
            tier, _ = router.route(self.messages)
        self.assertEqual(tier, "sonnet")
        self.assertIn("VISIBLE_TRACEBACK", ask.call_args.args[0])

    def test_vision_failure_defaults_without_jev(self):
        with patch.object(router, "describe_image", side_effect=ValueError("bad response")), \
                patch.object(router, "ask_jev") as ask:
            tier, _ = router.route(self.messages)
        self.assertEqual(tier, router.DEFAULT_TIER)
        ask.assert_not_called()

    def test_continuation_reuses_tier_without_analysis(self):
        with patch.object(router, "describe_image", return_value="screenshot") as describe, \
                patch.object(router, "ask_jev", return_value=("sonnet", 1.0, {})) as ask:
            first = router.route(self.messages)
            describe.reset_mock()
            ask.reset_mock()
            continuation = self.messages + [{"role": "assistant", "content": "Working"},
                                            {"role": "tool", "content": "done", "tool_call_id": "t1"}]
            self.assertEqual(router.route(continuation), first)
            describe.assert_not_called()
            ask.assert_not_called()

    def test_image_only_sessions_have_separate_keys(self):
        with patch.object(router, "describe_image", return_value="same description"), \
                patch.object(router, "ask_jev", return_value=("sonnet", 1.0, {})):
            _, first = router.route(self.messages)
            _, second = router.route([user([image("https://example.test/other.png")])])
        self.assertNotEqual(first, second)

    def test_invalid_jev_tier_falls_back(self):
        with patch.object(router, "ask_jev", return_value=("nonexistent", 1.0, {})):
            tier, _ = router.route([user("hello")])
        self.assertEqual(tier, router.DEFAULT_TIER)

    def test_all_production_tiers_support_vision(self):
        for name, config in router.TIERS.items():
            with self.subTest(tier=name):
                self.assertIs(config.get("vision"), True)

    def test_route_skips_incompatible_tier_for_historical_image(self):
        tiers = copy.deepcopy(router.TIERS)
        tiers["haiku"]["vision"] = False
        with patch.object(router, "TIERS", tiers), \
                patch.object(router, "describe_image", return_value="screenshot"), \
                patch.object(router, "ask_jev", return_value=("haiku", 1.0, {})):
            tier, _ = router.route(self.messages + [user("Explain that")])
        self.assertNotEqual(tier, "haiku")
        self.assertTrue(tiers[tier]["vision"])

    def test_data_uri_reaches_analyzer(self):
        self.response()
        part = image("data:image/png;base64,aGVsbG8=")
        router.describe_image(part)
        payload = json.loads(self.network.call_args.args[0].data)
        self.assertEqual(payload["messages"][-1]["content"][-1], part)

    def test_too_many_images_defaults_without_paid_analysis(self):
        messages = [user([image(str(i)) for i in range(router.VISION_MAX_IMAGES + 1)])]
        with patch.object(router, "describe_image") as describe, \
                patch.object(router, "ask_jev") as ask:
            tier, _ = router.route(messages)
        self.assertEqual(tier, router.DEFAULT_TIER)
        describe.assert_not_called()
        ask.assert_not_called()

    def test_jev_failure_after_analysis_defaults(self):
        with patch.object(router, "describe_image", return_value="screenshot"), \
                patch.object(router, "ask_jev", side_effect=TimeoutError):
            tier, _ = router.route(self.messages)
        self.assertEqual(tier, router.DEFAULT_TIER)

    def test_handler_forwards_original_images(self):
        original = copy.deepcopy(self.messages)
        with patch.object(router, "route", return_value=("sonnet", "session")), \
                patch.object(router, "open_upstream", return_value=(Mock(), None)) as upstream:
            handler = self.post({"model": "auto", "messages": self.messages})
        self.assertEqual(upstream.call_args.args[1]["messages"], original)
        handler.stream.assert_called_once()
        handler.send_bytes.assert_not_called()

    def test_explicit_model_bypasses_analyzer_and_router(self):
        with patch.object(router, "route") as route, \
                patch.object(router, "describe_image") as describe, \
                patch.object(router, "open_upstream", return_value=(Mock(), None)) as upstream:
            self.post({"model": "explicit/model", "messages": self.messages})
        route.assert_not_called()
        describe.assert_not_called()
        self.assertEqual(upstream.call_args.args[1]["model"], "explicit/model")

    def test_handler_skips_incompatible_fallback(self):
        tiers = copy.deepcopy(router.TIERS)
        tiers["haiku"]["fallback"] = ["sol", "sonnet"]
        tiers["sol"]["vision"] = False
        called = []

        def upstream(path, body):
            called.append(body["model"])
            return (None, (503, "application/json", b"unavailable")) if len(called) == 1 else (Mock(), None)

        with patch.object(router, "TIERS", tiers), \
                patch.object(router, "route", return_value=("haiku", "session")), \
                patch.object(router, "open_upstream", side_effect=upstream):
            self.post({"model": "auto", "messages": self.messages + [user("continue")]})
        self.assertEqual(called, [tiers["haiku"]["model"], tiers["sonnet"]["model"]])


if __name__ == "__main__":
    unittest.main()
