# -*- coding: utf-8 -*-
"""思考强度：候选字段、拒绝判定、降级重试与探测。"""
import json
import unittest

import translator as TR


class CandidateTests(unittest.TestCase):
    def names(self, level):
        return [name for name, _ in TR.reasoning_candidates(level)]

    def test_auto_sends_nothing(self):
        self.assertEqual(TR.reasoning_candidates("auto"), [])

    def test_off_disables_thinking(self):
        fragments = dict(TR.reasoning_candidates("off"))
        self.assertEqual(fragments["reasoning_effort"], {"reasoning_effort": "minimal"})
        self.assertEqual(fragments["thinking"], {"thinking": {"type": "disabled"}})
        self.assertEqual(fragments["enable_thinking"], {"enable_thinking": False})

    def test_high_carries_budget(self):
        fragments = dict(TR.reasoning_candidates("high"))
        self.assertEqual(fragments["reasoning_effort"], {"reasoning_effort": "high"})
        self.assertEqual(fragments["thinking_budget"],
                         {"thinking": {"type": "enabled", "budget_tokens": 4096}})
        self.assertEqual(fragments["enable_thinking"], {"enable_thinking": True})

    def test_unknown_level_is_noop(self):
        self.assertEqual(TR.reasoning_candidates("whatever"), [])


class RejectTests(unittest.TestCase):
    def test_unknown_argument_is_param_rejection(self):
        err = TR.PermanentError('HTTP 400 {"error":"Unrecognized request argument: reasoning_effort"}')
        self.assertTrue(TR.rejected_by_param(err, "reasoning_effort"))

    def test_other_400_is_not_param_rejection(self):
        self.assertFalse(TR.rejected_by_param(TR.PermanentError("HTTP 400 context too long"),
                                             "reasoning_effort"))

    def test_auth_error_is_not_param_rejection(self):
        self.assertFalse(TR.rejected_by_param(TR.PermanentError("HTTP 401 bad key"),
                                             "reasoning_effort"))


class ProviderRetryTests(unittest.TestCase):
    def config(self, **kw):
        base = dict(model="m", base_url="https://example.test/v1", api_key="k",
                    reasoning="high", reasoning_field="")
        base.update(kw)
        return TR.Config(**base)

    def test_falls_back_to_next_field(self):
        calls = []

        def post(url, payload, headers, timeout):
            calls.append(payload)
            if "reasoning_effort" in payload:
                raise TR.PermanentError(
                    'HTTP 400 {"error":"Unrecognized request argument: reasoning_effort"}')
            return {"choices": [{"message": {"content": json.dumps(
                {"translations": ["你好"]})}}]}

        cfg = self.config()
        provider = TR.OpenAICompatProvider(cfg)
        provider._post = post
        self.assertEqual(provider.translate(["hello"], "en", "zh"), ["你好"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(cfg.reasoning_field, "thinking")
        self.assertNotIn("reasoning_effort", calls[-1])

    def test_auto_mode_sends_original_payload(self):
        seen = {}

        def post(url, payload, headers, timeout):
            seen.update(payload)
            return {"choices": [{"message": {"content": json.dumps(
                {"translations": ["你好"]})}}]}

        cfg = self.config(reasoning="auto")
        provider = TR.OpenAICompatProvider(cfg)
        provider._post = post
        provider.translate(["hello"], "en", "zh")
        self.assertNotIn("reasoning_effort", seen)
        self.assertNotIn("thinking", seen)
        self.assertNotIn("enable_thinking", seen)

    def test_unrelated_error_is_not_swallowed(self):
        def post(url, payload, headers, timeout):
            raise TR.PermanentError("HTTP 400 model not found")

        provider = TR.OpenAICompatProvider(self.config())
        provider._post = post
        with self.assertRaises(TR.PermanentError):
            provider.translate(["hello"], "en", "zh")


class ProbeTests(unittest.TestCase):
    def test_non_openai_engine_reports_unsupported(self):
        cfg = TR.Config(engine="deepl", model="m", base_url="https://example.test")
        _, message = TR.probe_reasoning(cfg, "high")
        self.assertIn("不支持", message)

    def test_all_fields_rejected(self):
        cfg = TR.Config(model="m", base_url="https://example.test/v1", api_key="k",
                        reasoning="high")

        def post(self, url, payload, headers, timeout):
            raise TR.PermanentError('HTTP 400 unsupported parameter')

        original = TR.OpenAICompatProvider._post
        TR.OpenAICompatProvider._post = post
        try:
            field, message = TR.probe_reasoning(cfg, "high")
        finally:
            TR.OpenAICompatProvider._post = original
        self.assertEqual(field, "")
        self.assertIn("不接受", message)


if __name__ == "__main__":
    unittest.main()
