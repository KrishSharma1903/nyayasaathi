import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_FOLDER / "src"))

ANSWER_MODELS = "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-flash-lite-latest"
JUDGE_MODELS = ["gemini-3.7-flash", "gemini-3.8-flash", "gemini-3-flash-preview",
                "gemini-flash-latest"]
os.environ["LLM_MODELS"] = os.environ.get("TRIAD_ANSWER_MODELS", ANSWER_MODELS)

import config
import generator
from pipeline import Pipeline

RESULTS = PROJECT_FOLDER / "eval" / "results"
CACHE = RESULTS / "rag_triad_cache.jsonl"
DELAY_SECONDS = 12
RETRIES = 3
RETRY_WAIT_SECONDS = 65
CHECKS = ["context_relevance", "groundedness", "answer_relevance"]
DOMAINS = {"labour": "Work and wages", "tenancy": "Home and rent",
           "consumer": "Shopping and services", "domestic": "Safety at home",
           "legal_aid": "Free legal help"}

JUDGE_PROMPT = """You are evaluating an assistant that explains Indian legal rights.
Score each check from 1 (very poor) to 5 (excellent).

context_relevance: Is the RETRIEVED CONTEXT relevant to the QUESTION?
  5 = contains the information needed to answer; 3 = partly relevant; 1 = unrelated.
groundedness: Can every claim in the ANSWER be supported by the RETRIEVED CONTEXT?
  5 = fully supported; 3 = mostly supported with minor unsupported details;
  1 = main claims unsupported or contradicted. Judge only against the context, not your
  own knowledge of the law.
answer_relevance: Does the ANSWER directly and appropriately answer the QUESTION?
  5 = fully answers it; 3 = partly; 1 = off-topic or evasive.

Return only JSON:
{"context_relevance": 1-5, "groundedness": 1-5, "answer_relevance": 1-5,
 "reasons": {"context_relevance": "...", "groundedness": "...", "answer_relevance": "..."}}
Keep each reason under 20 words."""


class QuotaExhausted(Exception):
    pass


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    rows = [json.loads(line) for line in CACHE.read_text(encoding="utf-8").splitlines() if line]
    return {row["q"]: row for row in rows}


def save_cache(rows: dict):
    RESULTS.mkdir(exist_ok=True)
    CACHE.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows.values()),
                     encoding="utf-8")


def questions() -> list:
    data = json.loads((PROJECT_FOLDER / "eval" / "questions.json").read_text(encoding="utf-8"))
    domain_of = {entry["id"]: entry["domain"] for entry in json.loads(config.KB_PATH.read_text(encoding="utf-8"))}
    return [{"q": item["q"], "id": item["id"], "domain": domain_of[item["id"]], "split": split}
            for split in ("dev", "test") for item in data[split] if not item.get("oos")]


def answer(bot: Pipeline, item: dict) -> dict:
    result = bot.ask(item["q"])
    if not result["fallback"] and result["mode"] == "curated":
        raise QuotaExhausted("answer models unavailable")
    used = [chunk["id"] for chunk, score in result["hits"][:config.TOP_MATCHES] if score >= bot.threshold]
    return {
        "refused": result["fallback"], "reason": result.get("reason"), "mode": result.get("mode"),
        "answer": result["answer"], "issues": result.get("issues", []),
        "retrieved_right_entry": item["id"] in used,
        "context": "" if result["fallback"] else generator.format_sources(result["sources"]),
    }


def judge(item: dict, row: dict) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(
        timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1)))
    content = (f"QUESTION:\n{item['q']}\n\nRETRIEVED CONTEXT:\n{row['context']}\n\n"
               f"ANSWER:\n{row['answer']}")
    errors = []
    for model in JUDGE_MODELS:
        try:
            response = client.models.generate_content(
                model=model, contents=content,
                config=types.GenerateContentConfig(
                    system_instruction=JUDGE_PROMPT, temperature=0,
                    response_mime_type="application/json", max_output_tokens=2048,
                    thinking_config=types.ThinkingConfig(thinking_level="low")))
            scores = json.loads(response.text)
            if all(scores.get(check) in (1, 2, 3, 4, 5) for check in CHECKS):
                return {"judge_model": model, **{check: scores[check] for check in CHECKS},
                        "reasons": scores.get("reasons", {})}
            errors.append(f"{model}: bad scores {scores}")
        except Exception as error:
            errors.append(f"{model}: {type(error).__name__} {str(error)[:60]}")
    raise QuotaExhausted("judge models unavailable: " + "; ".join(errors))


def run(limit=None):
    rows = load_cache()
    todo = [item for item in questions() if "groundedness" not in rows.get(item["q"], {})
            and not rows.get(item["q"], {}).get("refused")]
    if limit:
        todo = todo[:limit]
    print(f"{len(rows)} saved, {len(todo)} to do")
    if not todo:
        return
    bot = Pipeline()
    for number, item in enumerate(todo, 1):
        row = rows.get(item["q"], {**item})
        for attempt in range(RETRIES + 1):
            try:
                if "answer" not in row:
                    row.update(answer(bot, item))
                    rows[item["q"]] = row
                    save_cache(rows)
                if not row["refused"]:
                    row.update(judge(item, row))
                    save_cache(rows)
                break
            except QuotaExhausted as error:
                if attempt == RETRIES:
                    print(f"\nStopped after {number - 1} new questions: {error}. Run again later to continue.")
                    return
                print(f"  rate limited, waiting {RETRY_WAIT_SECONDS} s (try {attempt + 1}/{RETRIES})")
                time.sleep(RETRY_WAIT_SECONDS)
        scores = "refused" if row["refused"] else " ".join(f"{row[check]}" for check in CHECKS)
        print(f"[{number}/{len(todo)}] {DOMAINS[item['domain']]:22} {scores:8} {item['q'][:55]}")
        time.sleep(DELAY_SECONDS)


def summarise(rows: list) -> dict:
    answered = [row for row in rows if not row.get("refused") and "groundedness" in row]
    summary = {"questions": len(rows), "answered": len(answered),
           "refused": sum(bool(row.get("refused")) for row in rows),
           "retrieved_right_entry": sum(row.get("retrieved_right_entry", False) for row in rows),
           "fact_guard_passed": sum(row.get("mode") == "llm" for row in answered)}
    for check in CHECKS:
        values = [row[check] for row in answered]
        summary[check] = round(sum(values) / len(values), 2) if values else None
        summary[check + "_good"] = sum(value >= 4 for value in values)
    return summary


def report():
    rows = list(load_cache().values())
    done = [row for row in rows if row.get("refused") or "groundedness" in row]
    total = len(questions())
    groups = {"All domains": done}
    for key, name in DOMAINS.items():
        groups[name] = [row for row in done if row["domain"] == key]
    summary = {name: summarise(group) for name, group in groups.items()}

    lines = [f"# RAG triad evaluation ({len(done)} of {total} in-scope questions done)", "",
             "Scores are the judge's average from 1 to 5; brackets show how many answers "
             "scored 4 or 5.", "",
             "| Domain | Questions | Context relevance | Groundedness | Answer relevance "
             "| Right entry found | Fact guard passed | Refused |",
             "|---|---|---|---|---|---|---|---|"]
    for name, stats in summary.items():
        def cell(check):
            return "-" if stats[check] is None else f"{stats[check]:.2f} ({stats[check + '_good']}/{stats['answered']})"
        lines.append(f"| {name} | {stats['questions']} | {cell('context_relevance')} | "
                     f"{cell('groundedness')} | {cell('answer_relevance')} | "
                     f"{stats['retrieved_right_entry']}/{stats['questions']} | "
                     f"{stats['fact_guard_passed']}/{stats['answered']} | {stats['refused']} |")
    weak = [row for row in done if not row.get("refused") and min(row[check] for check in CHECKS) <= 3]
    if weak:
        lines += ["", "## Answers scoring 3 or lower on any check", ""]
        for row in weak:
            worst = min(CHECKS, key=lambda check: row[check])
            lines.append(f"- **{row['q']}** ({DOMAINS[row['domain']]}): {worst.replace('_', ' ')} "
                         f"{row[worst]}, {row.get('reasons', {}).get(worst, '')}")
    refused = [row for row in done if row.get("refused")]
    if refused:
        lines += ["", "## Refused in-scope questions", ""]
        lines += [f"- **{row['q']}** ({DOMAINS[row['domain']]}): {row['reason']}" for row in refused]

    text = "\n".join(lines) + "\n"
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "rag_triad.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    if "--report" not in sys.argv:
        if config.LLM_PROVIDER != "gemini" or not config.GEMINI_API_KEY:
            sys.exit("Set GEMINI_API_KEY in .env to run the RAG triad evaluation.")
        limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        run(limit)
    report()
