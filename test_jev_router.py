"""Offline regression tests: python3 -m unittest -v test_jev_router."""
import copy
from contextlib import ExitStack
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
        # unittest.TestCase.enterContext was added in 3.11; use stdlib ExitStack on 3.10 too.
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.enter_context = contexts.enter_context
        # Every test is offline, including when an expected mock is missing.
        self.network = self.enter_context(patch.object(
            router.urllib.request, "urlopen",
            side_effect=AssertionError("Unmocked network access is forbidden")))
        self.enter_context(patch("builtins.print"))
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

    def post(self, body, session_id=None):
        handler = router.Handler.__new__(router.Handler)
        handler.path = "/v1/chat/completions"
        raw = json.dumps(body).encode()
        handler.headers = {"Content-Length": str(len(raw))}
        if session_id is not None:
            handler.headers["X-Jev-Session-ID"] = session_id
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
            first = router.route(self.messages, "chat-1")
            router.remember_tier(first[1], first[0])
            describe.reset_mock()
            ask.reset_mock()
            continuation = self.messages + [{"role": "assistant", "content": "Working"},
                                            {"role": "tool", "content": "done", "tool_call_id": "t1"}]
            self.assertEqual(router.route(continuation, "chat-1"), first)
            describe.assert_not_called()
            ask.assert_not_called()

    def test_identical_prompts_in_separate_chats_do_not_share_a_tier(self):
        for first_id, second_id in ((None, None), ("chat-a", "chat-b")):
            with self.subTest(first_id=first_id), \
                    patch.object(router, "ask_jev", side_effect=[("opus", 1.0, {}), ("haiku", 1.0, {})]):
                messages = [user("hello")]
                first, key = router.route(messages, first_id)
                router.remember_tier(key, first)
                second, _ = router.route(messages, second_id)
                self.assertEqual((first, second), ("opus", "haiku"))

    def test_session_cache_is_bounded_and_expires(self):
        with patch.object(router, "SESSION_LIMIT", 2), patch.object(router.time, "monotonic") as clock:
            clock.return_value = 100
            router.remember_tier("a", "opus")
            router.remember_tier("b", "haiku")
            self.assertEqual(router.pinned_tier("a"), "opus")
            router.remember_tier("c", "sonnet")
            self.assertIsNone(router.pinned_tier("b"))
            clock.return_value += router.SESSION_TTL
            self.assertIsNone(router.pinned_tier("a"))

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
                patch.object(router, "ANTHROPIC_CACHE", False), \
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
        self.assertEqual(router.pinned_tier("session"), "sonnet")

    def test_auto_forwards_to_verified_intermediate_models(self):
        for tier, model in (("mini", "openai/gpt-6-luna-pro"),
                            ("terra", "openai/gpt-6.1-terra"), ("sol-61", "openai/gpt-6.1-sol")):
            with self.subTest(tier=tier), patch.object(router, "ask_jev", return_value=(tier, 1.0, {})), \
                    patch.object(router, "open_upstream", return_value=(Mock(), None)) as upstream:
                handler = self.post({"model": "auto", "messages": [user("review this change")]})
                upstream.assert_called_once()
                self.assertEqual(upstream.call_args.args[1]["model"], model)
                handler.stream.assert_called_once()

    def test_handler_passes_explicit_session_id(self):
        messages = [user("hello")]
        with patch.object(router, "route", return_value=("haiku", "chat-a")) as route, \
                patch.object(router, "open_upstream", return_value=(Mock(), None)):
            self.post({"model": "auto", "messages": messages}, session_id="chat-a")
        route.assert_called_once_with(messages, "chat-a")
        self.assertEqual(router.pinned_tier("chat-a"), "haiku")

    def test_handler_does_not_retry_auth_and_billing_errors(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                error = (status, "application/json", b"upstream error")
                with patch.object(router, "route", return_value=("haiku", "chat-a")), \
                        patch.object(router, "open_upstream", return_value=(None, error)) as upstream:
                    handler = self.post({"model": "auto", "messages": [user("hello")]})
                upstream.assert_called_once()
                handler.send_bytes.assert_called_once_with(*error)
                self.assertIsNone(router.pinned_tier("chat-a"))


class ReasoningReplayTests(unittest.TestCase):
    """OpenCode присылает reasoning прошлых ответов, а он привязан к модели."""

    ITEM_ID = "rs_0b119948a311a07f016afca925a0b481978df6706e9ea9683a"

    def history(self):
        # Форма из живой ошибки 400: reasoning_details + текстовая part + reasoning_opaque.
        return [
            {"role": "user", "content": "Привет"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Готов помочь."},
                {"type": "reasoning", "text": "endpoint_slug: openai/gpt-6.1-sol-20260929|azure"},
            ], "reasoning_details": [{
                "type": "reasoning.encrypted", "data": "gAAAAABq_Kkmm7QG3BVV",
                "id": self.ITEM_ID, "format": "azure-openai-responses-v1", "index": 0}],
             "reasoning_opaque": "gAAAAABq_Kkmm7QG3BVV"},
            {"role": "user", "content": "настрой провайдера"},
        ]

    def test_encrypted_reasoning_and_reasoning_parts_are_removed(self):
        request = {"model": "auto", "messages": self.history()}
        original = copy.deepcopy(request)
        clean = router.sanitize_reasoning(request)
        self.assertEqual(request, original)                     # исходник не меняется
        self.assertNotIn("reasoning_details", json.dumps(clean))
        self.assertNotIn(self.ITEM_ID, json.dumps(clean))
        self.assertEqual(clean["messages"][1]["content"],
                         [{"type": "text", "text": "Готов помочь."}])
        self.assertEqual([m["role"] for m in clean["messages"]], ["user", "assistant", "user"])

    def test_parts_with_reasoning_id_or_encrypted_content_are_removed(self):
        messages = [{"role": "assistant", "content": [
            {"type": "text", "text": "ok"},
            {"id": self.ITEM_ID},
            {"type": "text", "text": "blob", "encrypted_content": "gAAAA"},
            {"type": "thinking", "thinking": "..."}]}]
        clean = router.sanitize_reasoning({"messages": messages})
        self.assertEqual(clean["messages"][0]["content"], [{"type": "text", "text": "ok"}])

    def test_plain_reasoning_fields_are_stripped(self):
        messages = [{"role": "assistant", "content": "ответ", "reasoning_content": "думал",
                     "reasoning_text": "думал"}]
        clean = router.sanitize_reasoning({"messages": messages})
        self.assertEqual(clean["messages"], [{"role": "assistant", "content": "ответ"}])

    def test_message_with_only_reasoning_is_dropped(self):
        messages = [user("go"), {"role": "assistant", "content": "", "reasoning_content": "думал"},
                    user("дальше")]
        clean = router.sanitize_reasoning({"messages": messages})
        self.assertEqual([m["role"] for m in clean["messages"]], ["user", "user"])

    def test_tool_call_message_keeps_tool_calls(self):
        messages = [{"role": "assistant", "content": None, "reasoning_content": "думал",
                     "tool_calls": [{"id": "call_1", "type": "function"}]}]
        clean = router.sanitize_reasoning({"messages": messages})
        self.assertEqual(clean["messages"], [{"role": "assistant", "content": None,
                                              "tool_calls": [{"id": "call_1", "type": "function"}]}])

    def test_untouched_requests_are_returned_as_is(self):
        request = {"model": "auto", "messages": [user("привет")]}
        self.assertIs(router.sanitize_reasoning(request), request)
        body = {"model": "auto"}
        self.assertIs(router.sanitize_reasoning(body), body)

    def test_keep_reasoning_flag_switches_off(self):
        request = {"messages": self.history()}
        with patch.object(router, "KEEP_REASONING", True):
            self.assertIs(router.sanitize_reasoning(request), request)

    def test_handler_sends_sanitized_history_upstream(self):
        handler = router.Handler.__new__(router.Handler)
        handler.path = "/v1/chat/completions"
        raw = json.dumps({"model": "auto", "messages": self.history()}).encode()
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.stream, handler.send_bytes = Mock(), Mock()
        with patch.object(router, "route", return_value=("sol", "session")), \
                patch.object(router, "ANTHROPIC_CACHE", False), \
                patch.object(router, "open_upstream", return_value=(Mock(), None)) as upstream:
            handler.do_POST()
        sent = upstream.call_args.args[1]
        self.assertEqual(sent["model"], router.TIERS["sol"]["model"])
        self.assertNotIn(self.ITEM_ID, json.dumps(sent))
        handler.stream.assert_called_once()


class CachingTests(unittest.TestCase):
    def test_anthropic_cache_marks_system_copy_only_when_enabled(self):
        request = {"model": "anthropic/claude-haiku-5.5", "messages": [
            {"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}]}
        original = copy.deepcopy(request)
        with patch.object(router, "ANTHROPIC_CACHE", False):
            self.assertIs(router.with_anthropic_cache(request), request)
        with patch.object(router, "ANTHROPIC_CACHE", True):
            cached = router.with_anthropic_cache(request)
            self.assertEqual(request, original)
            self.assertEqual(cached["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})
            self.assertEqual(cached["messages"][1]["content"][0]["cache_control"], {"type": "ephemeral"})
            openai = dict(request, model="openai/gpt-6.1-sol")
            self.assertIs(router.with_anthropic_cache(openai), openai)

    def test_anthropic_cache_marks_last_message_too(self):
        request = {"model": "anthropic/claude-sonnet-5.5", "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"}]}
        original = copy.deepcopy(request)
        with patch.object(router, "ANTHROPIC_CACHE", True):
            cached = router.with_anthropic_cache(request)
        self.assertEqual(request, original)
        self.assertEqual(cached["messages"][3]["content"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(cached["messages"][1], request["messages"][1])

    def test_anthropic_cache_skips_empty_tool_call_message(self):
        request = {"model": "anthropic/claude-sonnet-5.5", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": []}]}
        with patch.object(router, "ANTHROPIC_CACHE", True):
            cached = router.with_anthropic_cache(request)
        self.assertEqual(cached["messages"][0]["content"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertIsNone(cached["messages"][1]["content"])

    def test_tier_is_not_downgraded_within_session(self):
        with patch.object(router, "describe_image", return_value="x"), \
                patch.object(router, "ask_jev", side_effect=[("opus", 1.0, {}), ("haiku", 1.0, {})]):
            first, key = router.route([user("big task")], "big")
            router.remember_tier(key, first)
            tier, _ = router.route([user("big task"), {"role": "assistant", "content": "ok"},
                                    user("small follow-up")], "big")
        self.assertEqual(tier, "opus")

    def test_tier_can_be_upgraded_within_session(self):
        with patch.object(router, "ask_jev", side_effect=[("haiku", 1.0, {}), ("opus", 1.0, {})]):
            first, key = router.route([user("task")], "hard")
            router.remember_tier(key, first)
            tier, _ = router.route([user("task"), {"role": "assistant", "content": "ok"},
                                    user("harder")], "hard")
        self.assertEqual(tier, "opus")


if __name__ == "__main__":
    unittest.main()


class TierDisablingTests(unittest.TestCase):
    def setUp(self):
        self.saved = (copy.deepcopy(router.TIERS), list(router.ORDER), router.DEFAULT_TIER)

    def tearDown(self):
        router.TIERS.clear()
        router.TIERS.update(self.saved[0])
        router.ORDER[:] = self.saved[1]
        router.DEFAULT_TIER = self.saved[2]

    def test_new_middle_tiers_exist_and_are_vision_capable(self):
        for name in ("mini", "flash"):
            self.assertIn(name, router.TIERS)
            self.assertTrue(router.TIERS[name]["vision"])
        self.assertEqual(router.TIERS["mini"]["model"], "openai/gpt-6-luna-pro")
        self.assertEqual(router.ORDER.index("haiku") + 1, router.ORDER.index("mini"))
        self.assertLess(router.ORDER.index("flash"), router.ORDER.index("sol"))

    def test_gpt_62_models_are_available_and_ordered(self):
        expected = {
            "sol": "openai/gpt-6.2-sol",
            "sol-pro": "openai/gpt-6.2-sol-pro",
            "astra": "openai/gpt-6.2-astra",
            "astra-pro": "openai/gpt-6.2-astra-pro",
        }
        for tier, model in expected.items():
            with self.subTest(tier=tier):
                self.assertEqual(router.TIERS[tier]["model"], model)
                self.assertTrue(router.TIERS[tier]["vision"])
                self.assertIn(tier, router.jev_body("task")["questions"]["tier"]["criteria"])
        self.assertEqual(set(router.ORDER), set(router.TIERS))
        for lower, higher in zip(expected, list(expected)[1:]):
            self.assertLess(router.ORDER.index(lower), router.ORDER.index(higher))
        for config in router.TIERS.values():
            for fallback in config["fallback"]:
                self.assertIn(fallback, router.TIERS)

    def test_gpt_62_pro_and_astra_tiers_can_be_disabled(self):
        router.apply_disabled(["sol-pro", "astra", "astra-pro"])
        self.assertIn("sol", router.TIERS)
        for tier in ("sol-pro", "astra", "astra-pro"):
            self.assertNotIn(tier, router.ORDER)
            self.assertNotIn(tier, router.jev_body("task")["questions"]["tier"]["criteria"])

    def test_disabling_removes_tier_from_choices_fallbacks_and_default(self):
        router.apply_disabled(["mini", "flash"])
        self.assertNotIn("mini", router.TIERS)
        self.assertNotIn("mini", router.ORDER)
        self.assertNotIn("flash", router.ORDER)
        self.assertNotIn("mini", router.TIERS["haiku"]["fallback"])
        self.assertIn(router.DEFAULT_TIER, router.TIERS)

    def test_disabling_default_tier_moves_default(self):
        router.apply_disabled(["sonnet"])
        self.assertNotIn(router.DEFAULT_TIER, ["sonnet"])
        self.assertIn(router.DEFAULT_TIER, router.TIERS)

    def test_unknown_or_all_tiers_rejected(self):
        with self.assertRaises(SystemExit):
            router.apply_disabled(["gpt-9000"])
        with self.assertRaises(SystemExit):
            router.apply_disabled(list(router.TIERS))

    def test_flags_and_env_are_combined(self):
        names = router.parse_disabled(["--disable", "mini,flash", "--disable=opus"],
                                      {"JEV_DISABLE_TIERS": "haiku, mini, opus, opus"})
        self.assertEqual(sorted(names), ["flash", "haiku", "mini", "opus"])
        self.assertEqual(router.parse_disabled([], {}), [])

    def test_legacy_disable_names_remain_valid(self):
        router.apply_disabled(["flash-plus", "flash-max", "sol-lite", "sol-61"])
        self.assertNotIn("terra", router.ORDER)
        self.assertNotIn("sol-61", router.ORDER)
        self.assertNotIn("terra", router.TIERS["mini"]["fallback"])
        self.assertIn("sol", router.ORDER)

    def test_invalid_cli_flags_fail_instead_of_starting_router(self):
        for argv in (["--disable"], ["--disabl=opus"], ["unexpected"]):
            with self.subTest(argv=argv), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    router.parse_disabled(argv, {})
                self.assertEqual(error.exception.code, 2)
