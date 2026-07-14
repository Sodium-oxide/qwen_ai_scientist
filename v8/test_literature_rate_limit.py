from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Unit-test diagnostics must never be appended to the live science agent log.
_TEST_LOG_DIR = tempfile.TemporaryDirectory(prefix="v8-rate-limit-tests-")
os.environ["AGENT_LOG_PATH"] = str(Path(_TEST_LOG_DIR.name) / "agent.log")

import _literature_search as literature_search


class StratifiedBatchRetrievalTests(unittest.TestCase):
    def test_pubmed_query_is_compact_structured_and_omits_quality_words(self) -> None:
        query = literature_search.compact_pubmed_retrieval_query(
            "chromatin accessibility epigenetic memory reprogramming efficiency "
            "experimental theoretical mechanism measurement validation"
        )

        self.assertIn("[Title/Abstract]", query)
        self.assertIn(" AND ", query)
        for noisy_term in ("experimental", "theoretical", "mechanism", "measurement", "validation"):
            self.assertNotIn(noisy_term, query.lower())

    def test_pubmed_provider_batch_uses_only_base_and_review_requests(self) -> None:
        calls: list[tuple[str, int, int]] = []

        def fake_search(query: str, max_results: int = 10, offset: int = 0):
            calls.append((query, max_results, offset))
            return {
                "provider": "pubmed",
                "query": query,
                "status": "ok",
                "results": [],
            }

        quotas = {
            "L0_review": 3,
            "L1_milestone": 2,
            "L2_top_latest": 7,
            "L3_preprint": 0,
            "L4_regular": 16,
        }
        with patch.object(literature_search, "search_pubmed", side_effect=fake_search):
            blocks = literature_search.fetch_pubmed_stratified_batch(
                "chromatin accessibility epigenetic memory reprogramming efficiency",
                max_results=28,
                quotas=quotas,
                search_id="test_search",
            )

        self.assertEqual(len(blocks), 2)
        self.assertEqual([(limit, offset) for _, limit, offset in calls], [(50, 0), (6, 0)])
        self.assertIn("[Title/Abstract]", calls[0][0])
        self.assertNotIn("Publication Type", calls[0][0])
        self.assertIn("review[Publication Type]", calls[1][0])
        self.assertIn("systematic[sb]", calls[1][0])

    def test_pubmed_review_metadata_drives_local_review_layer(self) -> None:
        candidates = [
            {
                "provider": "pubmed",
                "title": "Chromatin barriers to somatic cell reprogramming",
                "abstract": "A broad synthesis of epigenetic memory and cell fate.",
                "year": "2025",
                "publication_types": ["Review"],
                "relevance_score": 0.92,
                "publication_quality_score": 0.85,
            }
        ]

        layers = literature_search.stratify_pubmed_candidates_locally(candidates)

        self.assertEqual(len(layers["L0_review"]), 1)
        self.assertEqual(layers["L0_review"][0]["provider_local_layer"], "L0_review")

    def test_pure_preprint_quota_never_schedules_pubmed(self) -> None:
        quotas = {
            "L0_review": 0,
            "L1_milestone": 0,
            "L2_top_latest": 0,
            "L3_preprint": 3,
            "L4_regular": 0,
        }

        self.assertFalse(
            literature_search.pubmed_stratified_batch_required(
                ["pubmed", "biorxiv", "medrxiv", "arxiv"],
                quotas,
            )
        )

    def test_pure_preprint_quota_never_schedules_semantic_scholar(self) -> None:
        quotas = {
            "L0_review": 0,
            "L1_milestone": 0,
            "L2_top_latest": 0,
            "L3_preprint": 3,
            "L4_regular": 0,
        }

        self.assertFalse(
            literature_search.semantic_scholar_stratified_batch_required(
                ["semantic_scholar", "biorxiv", "medrxiv", "arxiv"],
                quotas,
            )
        )

    def test_peer_reviewed_quota_schedules_one_semantic_scholar_batch(self) -> None:
        quotas = {
            "L0_review": 3,
            "L1_milestone": 2,
            "L2_top_latest": 7,
            "L3_preprint": 0,
            "L4_regular": 16,
        }

        self.assertTrue(
            literature_search.semantic_scholar_stratified_batch_required(
                ["semantic_scholar", "pubmed"],
                quotas,
            )
        )

    def test_same_provider_query_is_fetched_once_then_split_locally(self) -> None:
        calls: list[dict[str, int]] = []

        def fake_search(query: str, max_results: int = 10, offset: int = 0):
            calls.append({"max_results": max_results, "offset": offset})
            return {
                "provider": "semantic_scholar",
                "query": query,
                "status": "ok",
                "results": [
                    {"title": f"paper {index}", "year": "2026"}
                    for index in range(max_results)
                ],
            }

        with (
            patch.object(literature_search, "semantic_scholar_skip_block", return_value=None),
            patch.object(literature_search, "search_semantic_scholar", side_effect=fake_search),
        ):
            blocks = literature_search.fetch_stratified_layer_blocks(
                "causal mechanism",
                ["semantic_scholar"],
                {"layer": "L2_top_latest", "quota": 3, "query_suffix": ""},
                query_plan=[{"branch": "primary", "query": "causal mechanism"}],
                single_paper_serial=True,
            )

        self.assertEqual(calls, [{"max_results": 3, "offset": 0}])
        self.assertEqual(
            [block["results"][0]["title"] for block in blocks],
            ["paper 0", "paper 1", "paper 2"],
        )
        self.assertTrue(all(block["provider_request_batched"] for block in blocks))

    def test_open_provider_circuit_is_not_converted_to_skipped_block(self) -> None:
        with (
            patch.object(literature_search, "SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429", False),
            patch.object(literature_search, "semantic_scholar_circuit_open", return_value=(True, 75.0)),
        ):
            block = literature_search.semantic_scholar_skip_block(
                "causal mechanism",
            )

        self.assertIsNone(block)


class SemanticScholarCircuitTests(unittest.TestCase):
    def test_429_request_diagnostics_exclude_api_credentials(self) -> None:
        diagnostics = literature_search.semantic_scholar_safe_request_diagnostics(
            "https://api.semanticscholar.org/graph/v1/paper/search?"
            "query=cell+fate&limit=28&offset=0&fields=title%2Cyear%2Cabstract"
        )

        self.assertEqual(diagnostics["endpoint"], "/graph/v1/paper/search")
        self.assertEqual(diagnostics["query"], "cell fate")
        self.assertEqual(diagnostics["limit"], "28")
        self.assertEqual(diagnostics["offset"], "0")
        self.assertEqual(diagnostics["field_count"], 3)
        self.assertNotIn("api", " ".join(str(value) for value in diagnostics.values()).lower())

    def test_configured_api_key_is_attached_to_search_request(self) -> None:
        captured: dict[str, object] = {}

        def fake_get_json(url: str, headers=None, timeout: float = 20.0):
            captured.update({"url": url, "headers": headers, "timeout": timeout})
            return {"total": 0, "data": []}

        with (
            patch.object(literature_search, "SEMANTIC_SCHOLAR_API_KEY", "test-key"),
            patch.object(literature_search, "semantic_scholar_skip_block", return_value=None),
            patch.object(literature_search, "semantic_scholar_get_json", side_effect=fake_get_json),
        ):
            result = literature_search.search_semantic_scholar(
                "animal migration navigation",
                max_results=3,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual((captured["headers"] or {}).get("x-api-key"), "test-key")
        self.assertIn("limit=3", str(captured["url"]))

    def test_default_policy_waits_and_retries_after_429(self) -> None:
        rate_error = RuntimeError("HTTP 429: retry_after=75 Too Many Requests")
        with (
            patch.object(literature_search, "SEMANTIC_SCHOLAR_429_COUNT", 0),
            patch.object(literature_search, "SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429", False),
            patch.object(literature_search, "SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT", 10),
            patch.object(literature_search, "semantic_scholar_cache_get", return_value=None),
            patch.object(literature_search, "semantic_scholar_cache_put"),
            patch.object(literature_search, "wait_for_semantic_scholar_circuit_if_needed") as wait_for_circuit,
            patch.object(literature_search, "register_semantic_scholar_429", return_value=75.0),
            patch.object(
                literature_search,
                "semantic_scholar_get_text",
                side_effect=[rate_error, '{"data": []}'],
            ) as get_text,
        ):
            payload = literature_search.semantic_scholar_get_json("https://example.test/search")

        self.assertEqual(payload, {"data": []})
        self.assertEqual(get_text.call_count, 2)
        self.assertEqual(wait_for_circuit.call_count, 2)

    def test_retry_limit_is_capped_at_ten(self) -> None:
        with patch.object(literature_search, "SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT", 200):
            self.assertEqual(literature_search.semantic_scholar_retry_limit(), 10)

    def test_retry_after_is_not_capped_to_three_seconds(self) -> None:
        self.assertEqual(
            literature_search.semantic_scholar_retry_after_seconds(
                "HTTP 429: retry_after=120 Too Many Requests"
            ),
            120.0,
        )
        self.assertEqual(
            literature_search.semantic_scholar_backoff_seconds(
                0,
                "HTTP 429: retry_after=120 Too Many Requests",
            ),
            120.0,
        )

    def test_rate_state_is_shared_by_api_key_scope(self) -> None:
        from config import SCIENCE_PROVIDER_RATE_DIR, SEMANTIC_SCHOLAR_RATE_SCOPE
        from _models import SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR, SEMANTIC_SCHOLAR_RATE_STATE_FILE

        self.assertEqual(SEMANTIC_SCHOLAR_RATE_STATE_FILE.parent, SCIENCE_PROVIDER_RATE_DIR)
        self.assertEqual(SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR.parent, SCIENCE_PROVIDER_RATE_DIR)
        self.assertIn(SEMANTIC_SCHOLAR_RATE_SCOPE, SEMANTIC_SCHOLAR_RATE_STATE_FILE.name)
        self.assertIn(SEMANTIC_SCHOLAR_RATE_SCOPE, SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR.name)


if __name__ == "__main__":
    unittest.main()
