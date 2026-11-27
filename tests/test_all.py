import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_FOLDER / "src"))
import acts
import config
import facts
import generator
import ingest
import router
import safety
from pipeline import Pipeline
from retriever import Retriever

config.LLM_PROVIDER = "none"


SAMPLE = """Section 1 Payment of wages
Wages must be paid on time. Delays are not allowed.
Section 2 Overtime
Overtime must be paid at double the normal rate.
"""


class IngestTests(unittest.TestCase):
    def test_header_and_sections(self):
        meta, body = ingest.parse_header("#domain: labour\n#source: Test Act\n\n" + SAMPLE)
        self.assertEqual(meta["domain"], "labour")
        chunks = ingest.chunks_from_text(body, meta, "T")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["source"], "Test Act")

    def test_long_section_is_split(self):
        long_text = "Section 1 Big\n" + " ".join(["Word sentence here."] * 200)
        chunks = ingest.chunks_from_text(long_text, {"domain": "x", "source": "s"}, "T")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk["text"].split()) <= config.MAX_CHUNK_WORDS + 20 for chunk in chunks))


ACT = """Title page and contents. 3. Ignored contents line.
BE it enacted by Parliament as follows:-
1. (1) This Act may be called the Test Act.
2. In this Act,-
(1) "wage" means pay;
(2) "worker" means a person.
5. (1) Wages shall be paid before the seventh day.
(2) Overtime at twice the rate.
STATEMENT OF OBJECTS AND REASONS
9. Not a section.
"""


class ActsTests(unittest.TestCase):
    def test_split_sections(self):
        sections = acts.split_sections(ACT)
        self.assertEqual(list(sections), ["1", "2", "5"])
        self.assertNotIn("Not a section", sections["5"])

    def test_extract_subclause(self):
        sections = acts.split_sections(ACT)
        self.assertIn('"worker"', acts.extract(sections, "2(2)"))
        self.assertNotIn('"wage"', acts.extract(sections, "2(2)"))
        self.assertIn("seventh day", acts.extract(sections, "5(1)"))
        self.assertNotIn("twice", acts.extract(sections, "5(1)"))
        self.assertEqual(acts.extract(sections, "40"), "")

    def test_every_kb_citation_has_law_text(self):
        for chunk in ingest.from_kb():
            for law in chunk["law"]:
                self.assertTrue(law["text"], f"{chunk['id']} {law['cite']}")


class FactCheckTests(unittest.TestCase):
    def test_words_and_numbers(self):
        found = facts.facts("Pay twice the rate within seven days, up to Rs 50 lakh. Call 15100. "
                              "Under Section 12 of the Act, 2005.")
        self.assertEqual(found["numbers"], {"2", "7", "50 lakh"})
        self.assertEqual(found["helplines"], {"15100"})
        self.assertEqual(found["sections"], {"12"})

    def test_catches_old_hours_error(self):
        law = "No worker shall work for more than eight hours in a day or six days in a week."
        self.assertEqual(facts.unsupported("The limit is 48 hours per week.", law, set()),
                         ["number '48'"])
        self.assertEqual(facts.unsupported("At most 8 hours a day, 6 days a week.", law, set()), [])

    def test_grouped_citations_are_not_facts(self):
        self.assertEqual(facts.unsupported("Women and children qualify [1, 2] [3-4].", "text"), [])

    def test_numbers_from_question_allowed(self):
        sources = [{"n": 1, "kind": "curated", "cite": "C", "text": "wages within 7 days", "url": ""}]
        self.assertEqual(generator.check("Unpaid for 3 months? Pay is due in 7 days [1].", sources,
                                         "salary not paid for 3 months"), [])

    def test_uncited_section_flagged(self):
        self.assertEqual(facts.unsupported("See Section 19.", "text", {"12"}), ["section 19"])


ENTRY = {"text": "Monthly wages must be paid within seven days.", "next": "Call 15100.",
         "source": "Code on Wages, 2019", "url": "",
         "law": [{"cite": "Code on Wages, s. 17(1)", "url": "u",
                  "text": "(iv) monthly basis, before the expiry of the seventh day"}]}
PASSAGES = [{"cite": "Code on Wages, s. 17", "text": "duplicate of the cited section", "url": "u"},
            {"cite": "Code on Wages, s. 16", "text": "wage period shall not exceed a month", "url": "u"}]


class RagTests(unittest.TestCase):
    def ask(self, llm_output=None, error=None):
        options = {"side_effect": error} if error else {"return_value": llm_output}
        with mock.patch.object(config, "LLM_PROVIDER", "gemini"), \
             mock.patch.object(generator, "ask_gemini", **options):
            return generator.answer("salary late", [ENTRY], PASSAGES)

    def test_sources_numbered_and_deduplicated(self):
        sources = generator.collect_sources([ENTRY], PASSAGES)
        self.assertEqual([item["cite"] for item in sources],
                         ["Code on Wages, 2019", "Code on Wages, s. 17(1)", "Code on Wages, s. 16"])
        self.assertEqual([item["n"] for item in sources], [1, 2, 3])
        self.assertIn("Call 15100", sources[0]["text"])

    def test_supported_llm_answer_is_used(self):
        gemini_output = "Your employer must pay monthly wages within 7 days [1][2]. Call 15100 for help [1]."
        text, mode, _, flags = self.ask(gemini_output)
        self.assertEqual((text, mode, flags), (gemini_output, "llm", []))

    def test_fact_guard_rejects_made_up_number(self):
        text, mode, _, flags = self.ask("Wages must be paid within 30 days under section 99 [2].")
        self.assertEqual(mode, "guarded")
        self.assertEqual(text, ENTRY["text"])
        self.assertEqual(flags, ["number '30'", "section 99"])

    def test_fact_guard_rejects_made_up_helpline(self):
        self.assertEqual(self.ask("Call 1800123456 [1].")[1], "guarded")

    def test_insufficient_returns_none(self):
        self.assertEqual(self.ask("INSUFFICIENT")[:2], (None, "insufficient"))

    def test_errors_use_curated(self):
        self.assertEqual(self.ask(error=RuntimeError("no key"))[:2], (ENTRY["text"], "curated"))

    def test_uncited_answer_is_guarded(self):
        text, mode, _, flags = self.ask("Wages are due within 7 days.")
        self.assertEqual((mode, flags), ("guarded", ["no source cited"]))

    def test_two_entries_both_become_sources(self):
        other = dict(ENTRY, source="DV Act, 2005",
                     law=[{"cite": "DV Act, s. 17(1)", "url": "u", "text": "right to reside"}])
        kinds = [(item["kind"], item["cite"]) for item in generator.collect_sources([ENTRY, other], [])]
        self.assertEqual(kinds, [("curated", "Code on Wages, 2019"),
                                 ("law", "Code on Wages, s. 17(1)"),
                                 ("curated", "DV Act, 2005"), ("law", "DV Act, s. 17(1)")])

    def test_no_provider_uses_curated(self):
        with mock.patch.object(config, "LLM_PROVIDER", "none"):
            self.assertEqual(generator.answer("q", [ENTRY], [])[1], "curated")


class RuleTests(unittest.TestCase):
    def test_router(self):
        self.assertEqual(router.classify("how to file FIR for theft"), "out_of_scope")
        self.assertIsNone(router.classify("my landlord kept my deposit"))

    def test_router_does_not_block_covered_topics(self):
        for question in ["My husband beats me and police will not file my complaint",
                         "landlord threatens to call police to evict me",
                         "Shopkeeper sold me a fake land-line phone"]:
            self.assertIsNone(router.classify(question), question)

    def test_router_cyber_and_divorce(self):
        self.assertEqual(router.classify("How do I get a divorce"), "out_of_scope")
        self.assertEqual(router.classify("My UPI got hacked and money is gone"), "out_of_scope")

    def test_safety(self):
        self.assertTrue(safety.check("")["empty"])
        self.assertTrue(safety.check("my husband beats me")["emergency"])
        self.assertTrue(safety.check("I want to die")["self_harm"])
        self.assertTrue(safety.check("I feel unsafe at home")["emergency"])
        self.assertTrue(safety.check("I am not safe at home")["emergency"])
        self.assertFalse(safety.check("the heater I bought is unsafe")["emergency"])


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ingest.save(ingest.from_kb())
        cls.bot = Pipeline(Retriever(backend="tfidf"))

    def test_answers_with_source(self):
        result = self.bot.ask("employer has not paid my salary for two months")
        self.assertFalse(result["fallback"])
        self.assertEqual(result["domain"], "labour")
        self.assertIn("Code on Wages", result["source"])
        self.assertTrue(result["sources"])
        self.assertEqual(result["verified"], "act_text")
        self.assertIn("Code on Wages, s. 17(1)", [item["cite"] for item in result["law"]])

    def test_rag_sources_include_cited_law(self):
        result = self.bot.ask("employer has not paid my salary for two months")
        self.assertEqual(result["mode"], "curated")
        self.assertEqual(result["sources"][0]["kind"], "curated")
        self.assertIn("Code on Wages, s. 17(1)", [item["cite"] for item in result["sources"]])

    def test_llm_answer_through_pipeline(self):
        with mock.patch.object(config, "LLM_PROVIDER", "gemini"), \
             mock.patch.object(generator, "ask_gemini", return_value="Wages are due soon [1]."):
            result = self.bot.ask("employer has not paid my salary for two months")
        self.assertEqual((result["mode"], result["answer"]), ("llm", "Wages are due soon [1]."))

    def test_llm_insufficient_refuses(self):
        with mock.patch.object(config, "LLM_PROVIDER", "gemini"), \
             mock.patch.object(generator, "ask_gemini", return_value="INSUFFICIENT"):
            result = self.bot.ask("employer has not paid my salary for two months")
        self.assertEqual(result["reason"], "no_support")

    def test_refuses_unrelated(self):
        self.assertTrue(self.bot.ask("how to cook paneer butter masala")["fallback"])

    def test_refuses_criminal(self):
        self.assertEqual(self.bot.ask("someone stole my phone file FIR")["reason"], "out_of_scope")

    def test_empty(self):
        self.assertEqual(self.bot.ask("  ")["reason"], "empty")

    def test_emergency_note_added(self):
        result = self.bot.ask("my husband beats me what can I do")
        self.assertTrue(result["notes"])

    def test_domestic_violence_with_police_is_answered(self):
        result = self.bot.ask("My husband beats me and police will not file my complaint")
        self.assertNotEqual(result["reason"], "out_of_scope")
        self.assertTrue(result["notes"])


if __name__ == "__main__":
    unittest.main()
