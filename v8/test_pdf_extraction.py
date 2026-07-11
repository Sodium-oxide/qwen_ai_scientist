from __future__ import annotations

import unittest

import _literature_search as literature_search
import _pdf_extraction as pdf_extraction
from _project import default_literature_providers


class PdfExtractionTests(unittest.TestCase):
    def test_short_papers_are_not_reduced_to_four_pages(self) -> None:
        pages, chars = pdf_extraction.get_extraction_params(
            {"title": "Short clinical trial"},
            {"num_pages": 8},
            "clinical_trial",
        )
        self.assertEqual(pages, 8)
        self.assertGreater(chars, 8_000)

    def test_mechanism_sections_outrank_background(self) -> None:
        sections = [
            {
                "heading": "Introduction",
                "type": "introduction",
                "pages": [1, 2],
                "text": "Background context. " * 200,
            },
            {
                "heading": "Methods",
                "type": "methodology",
                "pages": [5, 6],
                "text": "CYP exposure dose toxicity intervention. " * 100,
            },
        ]
        selected = pdf_extraction.smart_extraction(sections, ["cyp", "exposure", "toxicity"], char_limit=2_400)
        by_type = {section["type"]: section for section in selected}
        self.assertIn("methodology", by_type)
        self.assertIn(5, by_type["methodology"]["pages"])
        self.assertGreater(by_type["methodology"]["score"], by_type["introduction"]["score"])

    def test_keyword_extraction_preserves_causal_evidence(self) -> None:
        text = (
            "Introduction provides broad background. "
            "The CYP2C19 variant altered pharmacokinetic exposure after dose adjustment. "
            "Higher exposure was associated with severe toxicity in the treated cohort. "
            "A separate discussion sentence closes the paper."
        )
        result = pdf_extraction.keyword_driven_extraction(text, "SH1")
        self.assertGreater(result["used_sentences"], 0)
        self.assertIn("pharmacokinetic exposure", result["extracted_text"])
        self.assertGreaterEqual(result["covered_keywords"], 2)

    def test_sh_coverage_marks_missing_evidence_for_supplement(self) -> None:
        result = pdf_extraction.validate_extraction("CYP metabolism was measured.", "SH1")
        self.assertTrue(result["needs_supplement"])
        self.assertLess(result["coverage_score"], 0.5)


class RetrievalPlanningTests(unittest.TestCase):
    def test_default_fifteen_result_budget_is_layer_capped(self) -> None:
        quotas = literature_search.stratified_literature_quotas(15)
        self.assertEqual(quotas, {
            "L3_preprint": 3,
            "L2_top_latest": 3,
            "L0_review": 1,
            "L1_milestone": 2,
            "L4_regular": 6,
        })

    def test_custom_budget_cannot_bypass_layer_caps(self) -> None:
        quotas = literature_search.normalize_stratified_layer_quotas(
            {
                "L3_preprint": 9,
                "L2_top_latest": 8,
                "L0_review": 7,
                "L1_milestone": 6,
                "L4_regular": 1,
            },
            max_results=15,
        )
        self.assertLessEqual(quotas["L3_preprint"], 3)
        self.assertLessEqual(quotas["L2_top_latest"], 3)
        self.assertLessEqual(quotas["L0_review"] + quotas["L1_milestone"], 3)
        self.assertEqual(quotas["L4_regular"], 6)

    def test_controlled_l4_backfill_caps_missing_special_layers(self) -> None:
        budget = literature_search.controlled_l4_backfill_budget(
            [
                {"layer": "L3_preprint", "unfilled_reserved_quota": 3},
                {"layer": "L2_top_latest", "unfilled_reserved_quota": 2},
                {"layer": "L4_regular", "unfilled_reserved_quota": 0},
                {"layer": "L0_review", "unfilled_reserved_quota": 1},
            ]
        )
        self.assertEqual(budget["missing_special_quota"], 6)
        self.assertEqual(budget["quota"], 3)
        self.assertEqual(budget["source_layers"], ["L3_preprint", "L2_top_latest", "L0_review"])

    def test_high_impact_review_found_through_l4_is_reclassified(self) -> None:
        result = literature_search.rank_literature_results(
            "genetic variation Treg homeostasis",
            [
                {
                    "title": "Microbiota in T-cell homeostasis and inflammatory diseases",
                    "abstract": "In this review, we focus on the microbiota-T-cell axis in homeostatic and pathogenic conditions.",
                    "venue": "Experimental &Molecular Medicine",
                    "year": "2017",
                    "citation_count": 179,
                }
            ],
        )[0]
        self.assertEqual(result["journal_quartile"], "Q1")
        self.assertTrue(literature_search.is_review_like_paper(result))
        self.assertTrue(literature_search.is_top_venue_result(result))
        result["stratified_layer"] = "L4_regular"
        reports = [
            {"layer": "L0_review", "target": 1, "selected": 0, "unfilled_reserved_quota": 1},
            {"layer": "L4_regular", "target": 6, "selected": 6, "unfilled_reserved_quota": 0},
        ]
        promoted = literature_search.promote_high_impact_l4_reviews(
            [result],
            reports,
            {"L0_review": 1, "L4_regular": 6},
        )
        self.assertEqual(promoted, [result])
        self.assertEqual(result["stratified_layer"], "L0_review")
        self.assertEqual(result["retrieved_as_layer"], "L4_regular")
        self.assertEqual(reports[0]["selected"], 1)
        self.assertEqual(reports[1]["selected"], 5)

    def test_biomedical_domains_include_pubmed_and_preprints(self) -> None:
        providers = default_literature_providers(
            domain="Personalized Medicine / Pharmacogenomics / Clinical Pharmacology"
        )
        self.assertIn("semantic_scholar", providers)
        self.assertIn("pubmed", providers)
        self.assertIn("medrxiv", providers)
        self.assertIn("biorxiv", providers)

    def test_preprint_query_drops_broad_taxonomy_terms(self) -> None:
        compact = literature_search.compact_preprint_retrieval_query(
            "biostatistics manufacturing patient-derived pharmacogenomics regulatory pharmacology",
            domain="Personalized Medicine / Precision Medicine / Pharmacogenomics / Clinical Pharmacology / Biostatistics / Regulatory Science",
        )
        self.assertIn("pharmacogenomics", compact)
        self.assertNotIn("biostatistics", compact)
        self.assertNotIn("regulatory", compact)
        self.assertLessEqual(len(compact.split()), 4)

    def test_preprint_matching_normalizes_hyphenated_terms(self) -> None:
        result = {
            "title": "Patient derived models for pharmacogenomics",
            "abstract": "A clinical pharmacology validation study.",
        }
        self.assertTrue(literature_search.preprint_result_matches_query(result, "patient-derived pharmacogenomics"))


if __name__ == "__main__":
    unittest.main()
