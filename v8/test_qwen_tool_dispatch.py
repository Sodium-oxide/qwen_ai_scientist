from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autogen_collab
import main
import qwen_adapter
import tools


FAILURE_SHAPED_RESPONSE = """已经创建项目，现在继续执行。

[assistant requested tool]
{"id":"toolu_qwen_case_0","name":"create_autogen_groupchat","input":{"project_id":"sci_real","max_round":12}}

[assistant requested tool]
{"id":"toolu_qwen_case_1","name":"run_boxue_research_round","input":{"project_id":"sci_real","plan_id":"","execution_mode":"pipeline"}}
"""


class QwenToolDispatchTests(unittest.TestCase):
    def test_research_compact_catalog_keeps_workflow_start_tools(self) -> None:
        selected = qwen_adapter.select_context_tools(
            [{"role": "user", "content": "Boxue AutoGen 科研闭环"}],
            tools.TOOLS,
        )
        names = {tool["name"] for tool in selected}
        self.assertIn("create_autogen_groupchat", names)
        self.assertIn("run_autogen_research_flow", names)
        self.assertIn("run_boxue_research_round", names)

    def test_mixed_prose_and_multiple_tool_markers_parse_all_calls(self) -> None:
        selected = qwen_adapter.select_context_tools(
            [{"role": "user", "content": "Boxue AutoGen 科研闭环"}],
            tools.TOOLS,
        )
        blocks = qwen_adapter.parse_qwen_content(FAILURE_SHAPED_RESPONSE, selected)
        self.assertEqual(
            [block.get("name") for block in blocks],
            ["create_autogen_groupchat", "run_boxue_research_round"],
        )
        self.assertTrue(all(block.get("type") == "tool_use" for block in blocks))

    def test_unknown_first_tool_does_not_hide_later_allowed_tool(self) -> None:
        allowed = [{"name": "run_boxue_research_round"}]
        blocks = qwen_adapter.parse_qwen_content(FAILURE_SHAPED_RESPONSE, allowed)
        self.assertEqual([block.get("name") for block in blocks], ["run_boxue_research_round"])

    def test_tool_markers_are_not_safe_final_text(self) -> None:
        self.assertTrue(main.contains_unparsed_tool_request(FAILURE_SHAPED_RESPONSE))
        self.assertTrue(
            main.contains_unparsed_tool_request(
                '{"tool_uses":[{"name":"run_boxue_research_round","input":{}}]}'
            )
        )
        self.assertFalse(main.contains_unparsed_tool_request("研究流程已经完成。"))

    def test_agent_loop_retries_unparsed_tool_text_instead_of_finalizing(self) -> None:
        unparsed = type(
            "Response",
            (),
            {"content": [{"type": "text", "text": FAILURE_SHAPED_RESPONSE}]},
        )()
        completed = type(
            "Response",
            (),
            {"content": [{"type": "text", "text": "显式停止：没有待执行工具。"}]},
        )()
        with (
            patch.object(main, "get_client", return_value=object()),
            patch.object(main, "create_response", side_effect=[unparsed, completed]) as create_response,
            patch.object(main, "assemble_tool_pool", return_value=([], {})),
            patch.object(main, "consume_cron_queue", return_value=[]),
            patch.object(main, "collect_background_notifications", return_value=[]),
            patch.object(main, "compact_messages", side_effect=lambda messages: messages),
            patch.object(main, "trigger_hook", return_value=None),
            patch.object(main, "extract_memories"),
            patch.object(main, "validate_before_final", return_value=""),
        ):
            final_text = main.run_agent_locked("运行科研流程")

        self.assertEqual(create_response.call_count, 2)
        self.assertEqual(final_text, "显式停止：没有待执行工具。")

    def test_one_batch_cannot_mix_new_and_stale_project_ids(self) -> None:
        tool_response = type(
            "Response",
            (),
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "create_project",
                        "name": "create_research_project",
                        "input": {"title": "T", "domain": "D", "objective": "O"},
                    },
                    {
                        "type": "tool_use",
                        "id": "decompose_old",
                        "name": "decompose_research_objective",
                        "input": {"project_id": "sci_old"},
                    },
                ]
            },
        )()
        completed = type(
            "Response",
            (),
            {"content": [{"type": "text", "text": "已报告项目 ID 冲突。"}]},
        )()
        created_result = main.tool_result(
            "create_project",
            json.dumps({"project_id": "sci_new", "status": "created"}),
        )
        with (
            patch.object(main, "get_client", return_value=object()),
            patch.object(main, "create_response", side_effect=[tool_response, completed]),
            patch.object(main, "assemble_tool_pool", return_value=([], {})),
            patch.object(main, "consume_cron_queue", return_value=[]),
            patch.object(main, "collect_background_notifications", return_value=[]),
            patch.object(main, "compact_messages", side_effect=lambda messages: messages),
            patch.object(main, "trigger_hook", return_value=None),
            patch.object(main, "extract_memories"),
            patch.object(main, "validate_before_final", return_value=""),
            patch.object(main, "run_tool", return_value=created_result) as run_tool,
        ):
            final_text = main.run_agent_locked("创建并运行科研项目")

        self.assertEqual(final_text, "已报告项目 ID 冲突。")
        run_tool.assert_called_once()


class ToolPlaceholderTests(unittest.TestCase):
    def test_legacy_project_id_placeholder_uses_previous_real_result(self) -> None:
        previous_results = [
            {
                "type": "tool_result",
                "tool_use_id": "create_project",
                "content": json.dumps({"project_id": "sci_real", "status": "created"}),
            }
        ]
        resolved = main.resolve_tool_placeholders(
            {
                "project_id": "<project_id>",
                "nested": {"project_id": "<project_id>"},
                "items": ["<project_id>", "literal"],
            },
            previous_results,
        )
        self.assertEqual(resolved["project_id"], "sci_real")
        self.assertEqual(resolved["nested"]["project_id"], "sci_real")
        self.assertEqual(resolved["items"], ["sci_real", "literal"])

    def test_unresolved_project_id_is_not_invented(self) -> None:
        self.assertEqual(
            main.resolve_tool_placeholders(
                {"project_id": "<project_id>"},
                [{"content": json.dumps({"status": "ok"})}],
            )["project_id"],
            "<project_id>",
        )


class AutoGenProjectValidationTests(unittest.TestCase):
    def test_groupchat_rejects_unresolved_project_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            with patch.object(autogen_collab, "AUTOGEN_DIR", target):
                with self.assertRaisesRegex(ValueError, "unresolved project_id"):
                    autogen_collab.create_autogen_groupchat("<project_id>")
            self.assertEqual(list(target.iterdir()), [])

    def test_groupchat_validates_real_project_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            with (
                patch.object(autogen_collab, "AUTOGEN_DIR", target),
                patch.object(
                    autogen_collab,
                    "ensure_autogen_project_exists",
                    return_value={"project_id": "sci_real"},
                ) as validate,
            ):
                payload = json.loads(autogen_collab.create_autogen_groupchat("sci_real"))
            validate.assert_called_once_with("sci_real")
            self.assertEqual(payload["project_id"], "sci_real")
            self.assertTrue((target / f"{payload['groupchat_id']}.json").exists())

    def test_research_flow_rejects_groupchat_from_another_project(self) -> None:
        with self.assertRaisesRegex(ValueError, "GroupChat project mismatch"):
            autogen_collab.ensure_groupchat_matches_project(
                {"groupchat_id": "agc_old", "project_id": "sci_old"},
                "sci_new",
                "agc_old",
            )


if __name__ == "__main__":
    unittest.main()
