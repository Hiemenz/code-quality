"""Tests for the optional, opt-in LLM judge. No real network calls are made
here -- the Anthropic client is mocked at the module boundary (`llm_judge.anthropic`)
so these tests run with no API key and no live model access.
"""

import unittest
from unittest import mock

from codequality import llm_judge


class _FakeToolUseBlock:
    def __init__(self, name, input_):
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class TestAvailability(unittest.TestCase):
    def test_package_not_installed_raises_clear_error(self):
        with mock.patch.object(llm_judge, "AVAILABLE", False):
            with self.assertRaises(llm_judge.LLMJudgeError) as ctx:
                llm_judge.judge("some diff")
        self.assertIn("codequality[llm]", str(ctx.exception))


class TestMissingApiKey(unittest.TestCase):
    def test_no_api_key_raises_clear_error(self):
        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            with self.assertRaises(llm_judge.LLMJudgeError) as ctx:
                llm_judge.judge("some diff")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_explicit_api_key_argument_bypasses_env_lookup(self):
        """Passing api_key= directly should not require the env var, and
        should reach the (mocked) client rather than raising early.
        """
        fake_response = _FakeResponse([
            _FakeToolUseBlock("submit_review", {
                "architecture_score": 7,
                "readability_score": 8,
                "instruction_adherence_score": None,
                "rationale": "Looks fine.",
            })
        ])
        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_response

        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client):
            result = llm_judge.judge("some diff", api_key="sk-explicit-key")

        self.assertEqual(result["architecture_score"], 7)


class TestSuccessfulReview(unittest.TestCase):
    def _mock_client(self, tool_input):
        fake_response = _FakeResponse([
            _FakeTextBlock("thinking out loud"),
            _FakeToolUseBlock("submit_review", tool_input),
        ])
        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_response
        return fake_client

    def test_parses_mocked_response_into_expected_shape(self):
        """A mocked successful tool-use response should parse into the documented dict shape."""
        tool_input = {
            "architecture_score": 6,
            "readability_score": 9,
            "instruction_adherence_score": 5,
            "rationale": "Reasonable structure, a few readability nits.",
        }
        fake_client = self._mock_client(tool_input)

        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client) as ctor:
            result = llm_judge.judge(
                "--- a.py ---\n+print('hi')\n", task_description="Add a greeting print.", model="claude-haiku-4-5"
            )

        ctor.assert_called_once_with(api_key="sk-test")
        fake_client.messages.create.assert_called_once()
        _, kwargs = fake_client.messages.create.call_args
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")
        self.assertEqual(kwargs["tool_choice"], {"type": "tool", "name": "submit_review"})

        self.assertEqual(
            result,
            {
                "model": "claude-haiku-4-5",
                "architecture_score": 6,
                "readability_score": 9,
                "instruction_adherence_score": 5,
                "rationale": "Reasonable structure, a few readability nits.",
            },
        )

    def test_default_model_used_when_not_overridden(self):
        """No model= or CODEQUALITY_LLM_MODEL means the small/cheap default model is used."""
        fake_client = self._mock_client({
            "architecture_score": 5, "readability_score": 5,
            "instruction_adherence_score": None, "rationale": "",
        })
        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client):
            result = llm_judge.judge("diff text")
        self.assertEqual(result["model"], llm_judge.DEFAULT_MODEL)

    def test_missing_tool_call_raises(self):
        """A response with no submit_review tool call is an error, not a guess."""
        fake_response = _FakeResponse([_FakeTextBlock("I refuse to use tools.")])
        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_response
        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client):
            with self.assertRaises(llm_judge.LLMJudgeError):
                llm_judge.judge("diff text")

    def test_api_failure_is_wrapped_in_llm_judge_error(self):
        """An exception from the SDK call should surface as LLMJudgeError, not propagate raw."""
        fake_client = mock.Mock()
        fake_client.messages.create.side_effect = RuntimeError("connection reset")
        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client):
            with self.assertRaises(llm_judge.LLMJudgeError) as ctx:
                llm_judge.judge("diff text")
        self.assertIn("connection reset", str(ctx.exception))


class TestInstructionAdherenceWithoutTaskDescription(unittest.TestCase):
    def test_none_task_description_forces_none_adherence_score(self):
        """Even if the model hallucinates a non-null adherence score, judge()
        must not pass it through when no task_description was given -- there
        is nothing to grade adherence against.
        """
        tool_input = {
            "architecture_score": 8,
            "readability_score": 7,
            "instruction_adherence_score": 9,  # model shouldn't have answered this
            "rationale": "n/a",
        }
        fake_response = _FakeResponse([_FakeToolUseBlock("submit_review", tool_input)])
        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_response

        with mock.patch.object(llm_judge, "AVAILABLE", True), mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(llm_judge.anthropic, "Anthropic", return_value=fake_client):
            result = llm_judge.judge("diff text", task_description=None)

        self.assertIsNone(result["instruction_adherence_score"])


if __name__ == "__main__":
    unittest.main()
