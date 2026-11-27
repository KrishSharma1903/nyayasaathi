import json
import time
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_FOLDER / "src"))
import config
from facts import section_numbers, unsupported


def check_kb():
    chunks = json.loads(config.CHUNKS_PATH.read_text(encoding="utf-8"))
    rows, flagged, checked = [], 0, 0
    print("Curated answers vs the official Act text they cite\n")
    for chunk in chunks:
        if not chunk.get("law"):
            print(f"  {chunk['id']:3} SKIP  no official Act text ({chunk.get('verified')})")
            continue
        checked += 1
        law = "\n".join(item["text"] for item in chunk["law"])
        cited = section_numbers(item["cite"] for item in chunk["law"])
        problems = unsupported(chunk["text"], law, cited)
        flagged += bool(problems)
        rows.append({"id": chunk["id"], "unsupported": problems, "verified": chunk.get("verified")})
        print(f"  {chunk['id']:3} {'FLAG' if problems else 'OK  '}  {', '.join(problems)}")
    print(f"\n{flagged}/{checked} answers have facts not found in the cited Act text.")
    return {"checked": checked, "flagged": flagged, "rows": rows}


DELAY_SECONDS = 7


def check_llm():
    if config.LLM_PROVIDER != "gemini" or not config.GEMINI_API_KEY:
        sys.exit("Set GEMINI_API_KEY (and LLM_PROVIDER=gemini) in .env to check Gemini answers.")
    from pipeline import Pipeline
    bot = Pipeline()
    data = json.loads((PROJECT_FOLDER / "eval" / "questions.json").read_text(encoding="utf-8"))
    rows, counts = [], {"llm": 0, "guarded": 0, "curated": 0, "refused": 0}
    for item in [question for question in data["test"] if not question.get("oos")]:
        time.sleep(DELAY_SECONDS)
        result = bot.ask(item["q"])
        mode = "refused" if result["fallback"] else result["mode"]
        counts[mode] += 1
        rows.append({"q": item["q"], "mode": mode,
                     "answer": result["answer"], "issues": result.get("issues", [])})
        if mode == "guarded":
            print(f"GUARDED {item['q']!r}: {', '.join(result['issues'])}")
    generated = counts["llm"] + counts["guarded"]
    rate = counts["guarded"] / generated if generated else float("nan")
    print(f"\n{counts}")
    print(f"{counts['guarded']}/{generated} Gemini answers ({rate:.0%}) had facts not in their "
          "sources and were replaced by the curated answer.")
    return {"model": config.LLM_MODEL, "counts": counts, "guard_rate": rate, "rows": rows}


if __name__ == "__main__":
    check_gemini = "--llm" in sys.argv
    result = check_llm() if check_gemini else check_kb()
    results_folder = PROJECT_FOLDER / "eval" / "results"
    results_folder.mkdir(exist_ok=True)
    (results_folder / ("fact_check_llm.json" if check_gemini else "fact_check.json")).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
