from __future__ import annotations

import unittest
from unittest.mock import patch

import _gap_detection as gap_detection
import _literature_import as literature_import
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

    def test_method_section_vocabulary_covers_experimental_and_statistical_headings(self) -> None:
        self.assertEqual(pdf_extraction.classify_section("Experimental Procedures"), "methodology")
        self.assertEqual(pdf_extraction.classify_section("Statistical Analysis"), "methodology")

    def test_evidence_spans_keep_page_section_offset_and_source(self) -> None:
        page_texts = [
            {
                "page": 5,
                "layout": "two_column",
                "text": "Results\nIL-2 increases STAT5 phosphorylation in regulatory T cells.",
                "blocks": [
                    {
                        "block_index": 0,
                        "text": "Results\nIL-2 increases STAT5 phosphorylation in regulatory T cells.",
                        "bbox": [0, 0, 100, 50],
                        "offset_start": 0,
                        "offset_end": 66,
                    }
                ],
            }
        ]
        spans = pdf_extraction.build_evidence_spans(
            page_texts,
            source_url="https://example.org/paper.pdf",
        )
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span["page"], 5)
        self.assertEqual(span["section"], "Results")
        self.assertEqual(span["source_url"], "https://example.org/paper.pdf")
        self.assertGreaterEqual(span["offset_start"], 0)
        self.assertEqual(pdf_extraction.locate_evidence_span(span["text"], spans)["span_id"], span["span_id"])

    def test_two_column_order_keeps_each_column_contiguous(self) -> None:
        blocks, layout = pdf_extraction.order_pymupdf_blocks(
            [
                (0, 10, 40, 20, "Left one", 0, 0),
                (60, 10, 100, 20, "Right one", 0, 0),
                (0, 30, 40, 40, "Left two", 0, 0),
                (60, 30, 100, 40, "Right two", 0, 0),
            ],
            100,
            100,
        )
        self.assertEqual(layout, "two_column")
        self.assertEqual([block["text"] for block in blocks], ["Left one", "Left two", "Right one", "Right two"])

    def test_figures_and_formulas_are_marked_for_non_automatic_review(self) -> None:
        review = pdf_extraction.assess_complex_content_review(
            [{"page": 7, "text": "x = \u03b1 + \u03b2 + \u03b3 \u2264 1"}],
            {"captions": ["Page 7: Figure 2. Proposed causal pathway."]},
        )
        self.assertTrue(review["requires_human_or_visual_review"])
        self.assertIn("figure_caption_detected", review["reasons"])
        self.assertIn("formula_like_text_detected", review["reasons"])

    def test_rule_extraction_distinguishes_causation_association_and_methodology(self) -> None:
        spans = [
            {
                "span_id": "p5_b0_s1",
                "source_type": "body_text",
                "source_url": "https://example.org/paper.pdf",
                "page": 5,
                "section": "Results",
                "section_type": "results",
                "offset_start": 0,
                "offset_end": 90,
                "text": "IL-2 increases STAT5 phosphorylation in regulatory T cells.",
            }
        ]
        chains = literature_import.extract_causal_chains_heuristic(
            "IL-2 increases STAT5 phosphorylation in regulatory T cells.",
            evidence_spans=spans,
            source_url="https://example.org/paper.pdf",
        )
        self.assertEqual(chains[0]["relation"], "promotes")
        self.assertEqual(chains[0]["polarity"], "positive")
        self.assertEqual(chains[0]["modality"], "asserted")
        self.assertTrue(chains[0]["direct_relation"])
        self.assertEqual(chains[0]["outcome_location"]["page"], 5)
        associations = literature_import.extract_association_signals(
            "High exposure was associated with toxicity.",
        )
        self.assertFalse(associations[0]["causal_claim"])
        speculative = literature_import.extract_causal_chains_heuristic(
            "IL-2 may promote Treg stability.",
        )
        self.assertEqual(speculative[0]["modality"], "speculative")
        self.assertFalse(speculative[0]["causal_claim"])
        speculative_graph = gap_detection.build_causal_evidence_graph(
            [{"paper_id": "paper-speculative", "citation": "Speculative (2026)", "causal_chains": speculative}]
        )
        self.assertFalse(speculative_graph["edges"])
        self.assertEqual(len(speculative_graph["non_causal_claims"]), 1)
        methodology = literature_import.extract_methodology_evidence(
            "[SECTION: Methods | pages 3]\nWe conducted a randomized single-cell RNA-seq study in n=120 patients.\n"
            "[SECTION: Results | pages 4]\nThe primary endpoint was survival with a hazard ratio.",
        )
        self.assertIn("randomized trial", methodology["method"])
        self.assertIn("single-cell rna-seq", methodology["method"])
        self.assertTrue(methodology["population"])
        self.assertIn("primary endpoint", methodology["benchmark"])

    def test_shared_node_paths_and_author_gap_keep_evidence_boundaries(self) -> None:
        chains = literature_import.extract_causal_chains_heuristic(
            "IL-2 increases STAT5 phosphorylation. STAT5 phosphorylation maintains Foxp3 expression.",
        )
        graph = gap_detection.build_causal_evidence_graph(
            [{"paper_id": "paper-1", "citation": "Example (2026)", "causal_chains": chains}]
        )
        self.assertEqual(len(graph["supported_paths"]), 1)
        self.assertEqual(graph["supported_paths"][0]["intermediate"], "STAT5 phosphorylation")
        self.assertEqual(gap_detection.causal_chain_missing_requirement(graph["chains"][0]), ("", ""))
        signals = gap_detection.extract_gap_signals_from_text(
            "[SECTION: Limitations | pages 8]\nThis study was limited by the lack of an external validation cohort.",
            citation="Example (2026)",
            evidence_spans=[
                {
                    "span_id": "p8_b0_s1",
                    "source_url": "https://example.org/paper.pdf",
                    "page": 8,
                    "section": "Limitations",
                    "section_type": "discussion",
                    "offset_start": 0,
                    "offset_end": 90,
                    "text": "This study was limited by the lack of an external validation cohort.",
                }
            ],
            source_url="https://example.org/paper.pdf",
        )
        self.assertEqual(signals[0]["signal_type"], "limitation")
        self.assertEqual(signals[0]["source_location"]["page"], 8)

    def test_offline_subhypothesis_is_saved_as_retrieval_provenance(self) -> None:
        with patch.object(literature_import, "import_papergraph_record", return_value="imported") as mocked_import:
            result = literature_import.import_literature_text(
                project_id="offline-project",
                title="Offline PDF",
                text="Methods: randomized trial. Results: IL-2 increases STAT5 activation.",
                source_type="pdf",
                use_llm=False,
                sub_hypothesis="SH2",
            )
        self.assertEqual(result, "imported")
        self.assertEqual(mocked_import.call_args.kwargs["import_context"], {"query_branch": "SH2"})


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
