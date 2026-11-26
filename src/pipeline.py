import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import fallback
import generator
import router
import safety
from retriever import Retriever

log = logging.getLogger("pipeline")


class Pipeline:
    def __init__(self, retriever=None, use_router=True, passage_index=None):
        self.retriever = retriever or Retriever()
        self.use_router = use_router
        self.threshold = self.retriever.threshold
        self.passages = passage_index if passage_index is not None else self.load_passages()

    def load_passages(self):
        if not config.PASSAGES_PATH.exists():
            log.warning("no Act passages (run python src/ingest.py --from-kb); answers use kb only")
            return None
        model = getattr(self.retriever, "model", None)
        backend = self.retriever.backend if model is not None else "tfidf"
        return Retriever(backend=backend, path=config.PASSAGES_PATH, model=model)

    def find_passages(self, question: str, domains: set) -> list:
        if not self.passages:
            return []
        hits = self.passages.search(question, top_k=config.EXTRA_PASSAGES,
                                    where=lambda chunk: chunk["domain"] in domains)
        return [chunk for chunk, score in hits if score >= config.MIN_PASSAGE_SCORE]

    def ask(self, question: str) -> dict:
        flags = safety.check(question)
        notes = []
        if flags["self_harm"]:
            notes.append(safety.SELF_HARM_NOTE)
        elif flags["emergency"]:
            notes.append(safety.EMERGENCY_NOTE)

        def refuse(reason, hits=(), score=0.0):
            return {"fallback": True, "reason": reason, "score": score,
                    "answer": fallback.reply(reason), "notes": notes, "hits": list(hits)}

        if flags["empty"]:
            return refuse("empty")
        if self.use_router and router.classify(question) == "out_of_scope":
            return refuse("out_of_scope")

        hits = self.retriever.search(question)
        best_entry, best_score = hits[0]
        if best_score < self.threshold:
            return refuse("low_confidence", hits, best_score)

        entries = [chunk for chunk, score in hits[:config.TOP_MATCHES] if score >= self.threshold]
        passages = self.find_passages(question, {chunk["domain"] for chunk in entries})
        text, mode, sources, issues = generator.answer(question, entries, passages)
        if mode == "insufficient":
            return refuse("no_support", hits, best_score)
        return {"fallback": False, "reason": None, "score": best_score, "domain": best_entry["domain"],
                "answer": text, "mode": mode, "issues": issues,
                "sources": sources, "next": best_entry.get("next", ""),
                "source": format_source(best_entry),
                "law": best_entry.get("law", []), "verified": best_entry.get("verified", "secondary"),
                "checked": best_entry.get("checked", ""),
                "disclaimer": config.DISCLAIMER, "notes": notes, "hits": hits}


VERIFIED_LABEL = {
    "act_text": "checked against the official Act text",
    "partial": "partly checked against the official Act text",
    "secondary": "not yet checked against the official Act text",
}


def format_source(chunk: dict) -> str:
    text = f"{chunk['source']} ({chunk['version']})"
    return text + (f" {chunk['url']}" if chunk.get("url") else "")


def show(result: dict):
    for note in result["notes"]:
        print(f"!! {note}\n")
    if result["fallback"]:
        print(f"[fallback: {result['reason']} | confidence {result['score']:.2f}]\n{result['answer']}")
        return
    print(f"[{result['domain']} | confidence {result['score']:.2f} | {result['mode']}]")
    print(result["answer"])
    if result["mode"] == "llm":
        for source in result["sources"]:
            print(f"  [{source['n']}] {source['cite']}")
    elif result["next"]:
        print(f"\nNext step: {result['next']}")
    print(f"Source: {result['source']}")
    if result["law"]:
        print("Law: " + "; ".join(law["cite"] for law in result["law"]))
    print(f"Checked: {VERIFIED_LABEL[result['verified']]}")
    print(f"Note: {result['disclaimer']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bot = Pipeline()
    print(f"NyayaSaathi ({bot.retriever.backend} search). Type a question (blank to quit).")
    while True:
        question = input("\nYou: ").strip()
        if not question:
            break
        print()
        show(bot.ask(question))
