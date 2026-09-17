from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from apps.agent.run import build_parser
from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_grounded_vlm import _Client, _large_frame, _reply, _snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition
from uga.core.errors import ContractViolation
from uga.environment.profile import PerceptionProfile
from uga.gui.schema import GuiActionKind
from uga.perception.schema import DecisionKind, GroundedAction, NormalizedBox, TextRegion
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vision_transport import ProviderError
from uga.policy.vlm_planner import OpenAICompatibleVisionClient
from uga.time.clock import ManualClock


class GuiModelConfigurationTests(unittest.TestCase):
    def test_provider_switches_do_not_leak_between_qwen_and_glm(self) -> None:
        for model in ("Qwen/Qwen3-VL-4B-Instruct", "qwen3-vl-4b", "glm-4.6v"):
            with self.subTest(model=model):
                client = OpenAICompatibleVisionClient(
                    base_url="http://127.0.0.1:1234/v1", model=model, disable_thinking=True
                )
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = json.dumps(
                    {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
                ).encode()
                with patch("uga.policy.vlm_planner.open_vision_request",
                           return_value=response) as send:
                    client.decide(images=[b"png"], instruction="test")
                payload = json.loads(send.call_args.args[0].data)
                if model == "glm-4.6v":
                    self.assertEqual(payload["thinking"], {"type": "disabled"})
                else:
                    self.assertNotIn("thinking", payload)
        with self.assertRaises(ContractViolation):
            OpenAICompatibleVisionClient(base_url="http://127.0.0.1:1234/v1",
                                         model="Qwen3-VL-4B-Thinking", disable_thinking=True)

    def test_model_first_preserves_wait_instead_of_ocr_shortcuts(self) -> None:
        perceived = replace(_snapshot(), visible_text=(TextRegion(
            "3\u79d2\u540e\u81ea\u52a8\u7ee7\u7eed", NormalizedBox(.6, .7, .8, .8), .99
        ),))
        client = _Client([_reply("wait")])
        planner = GroundedVlmPlanner(client, prefer_ocr_task_panel=True,
                                     enable_rule_fast_paths=False, max_temporal_frames=1)
        actual = planner.decide(snapshot=perceived, frames=(_large_frame(100),), goal="story")
        self.assertEqual(actual.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(planner.last_decision_source, "model")

    def test_key_inventory_in_prompt_and_empty_inventory_in_supervisor(self) -> None:
        planner = GroundedVlmPlanner(_Client([]), available_keys=frozenset({"interact"}))
        prompt = planner._decision_instruction(_snapshot(), "goal", None, None, None)
        state = json.loads(prompt.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(state["confirmed_key_bindings"], ["interact"])
        proposed = replace(outcome(1), action=GroundedAction(
            GuiActionKind.KEY, "back", None, "go back", .95, key="back"
        ))
        supervisor = ClosedLoopSupervisor(ManualClock(100), PerceptionProfile(),
                                          available_keys=frozenset())
        verdict = supervisor.assess(proposed, snapshot(1, 100), snapshot(1, 100),
                                    frame(1, 100), frame(1, 100), "goal")
        self.assertNotEqual(verdict.disposition, DecisionDisposition.EXECUTE)
        self.assertIn("confirmed binding", verdict.reason)

    def test_always_verifier_checks_even_high_confidence_proposals(self) -> None:
        verifier = MagicMock(return_value=False)
        supervisor = ClosedLoopSupervisor(ManualClock(100), PerceptionProfile(),
                                          verifier=verifier, verify_all_actions=True)
        verdict = supervisor.assess(outcome(1), snapshot(1, 100), snapshot(1, 100),
                                    frame(1, 100), frame(1, 100), "settings")
        self.assertEqual(verifier.call_count, 1)
        self.assertNotEqual(verdict.disposition, DecisionDisposition.EXECUTE)

    def test_cli_exposes_explicit_modes_and_separate_verifier_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--profile", "game.yaml"])
        self.assertEqual(args.gui_planning_mode, "model-first")
        self.assertEqual(args.gui_verification, "ambiguous")
        args = parser.parse_args(["--profile", "game.yaml", "--gui-coordinate-space",
                                  "normalized_1000", "--gui-verification", "always",
                                  "--verifier-no-thinking", "--verifier-json-object"])
        self.assertTrue(args.verifier_no_thinking and args.verifier_json_object)
        self.assertFalse(args.vlm_no_thinking)

    def test_tool_call_responses_are_not_fallback_action_text(self) -> None:
        client = OpenAICompatibleVisionClient(base_url="http://127.0.0.1:1234/v1", model="test")
        for finish, tools in (("tool_calls", None), ("stop", [{"function": "click"}])):
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps({"choices": [{"finish_reason": finish,
                "message": {"content": _reply(), "tool_calls": tools}}]}).encode()
            with patch("uga.policy.vlm_planner.open_vision_request", return_value=response), \
                    self.assertRaises(ProviderError):
                client.decide(images=[b"png"], instruction="test")

    def test_oversized_inputs_are_rejected_before_network(self) -> None:
        client = OpenAICompatibleVisionClient(base_url="http://127.0.0.1:1234/v1", model="test")
        with patch("uga.policy.vlm_planner.open_vision_request") as send:
            for images, instruction in (([b""], "test"), ([b"x"], "x" * 65537),
                                         ([b"x" * (8 * 1024 * 1024 + 1)], "test")):
                with self.assertRaises(ContractViolation):
                    client.decide(images=images, instruction=instruction)
            send.assert_not_called()
