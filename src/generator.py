import logging
import re

import config
import facts

log = logging.getLogger("generator")

SYSTEM_PROMPT = (
    "You explain Indian legal rights to people with little education, in simple English "
    "at about Class 8 reading level. "
    "Use ONLY the numbered SOURCES. Source [1] is a checked summary; the others are the "
    "official law text. "
    "After each sentence that states a right or a rule, cite its source like [1] or [2]. "
    "End with one short line on what to do next, taken from the SOURCES. "
    "Do not add laws, facts, phone numbers, amounts or section numbers that are not in "
    "the SOURCES. Do not give legal advice or predict case outcomes. "
    "If the SOURCES do not answer the question, reply with exactly: INSUFFICIENT. "
    "Keep the answer under 150 words."
)
INSUFFICIENT = "INSUFFICIENT"
CITATION_PATTERN = re.compile(r"\[\d+\]")


def saved_answer(entry: dict) -> str:
    text = entry["text"].strip()
    if entry.get("next"):
        return text
    return " ".join(re.split(r"(?<=[.!?])\s+", text)[:2])


def collect_sources(entries: list, passages: list) -> list:
    sources, seen = [], set()

    def add_source(kind, citation, text, url):
        sources.append({"n": len(sources) + 1, "kind": kind, "cite": citation, "text": text,
                        "url": url})

    for entry in entries:
        add_source("curated", entry["source"],
                   entry["text"] + (f"\nWhat to do next: {entry['next']}" if entry.get("next") else ""),
                   entry.get("url", ""))
        for law in entry.get("law", []):
            if law["text"] and law["cite"] not in seen:
                add_source("law", law["cite"], law["text"], law["url"])
                seen.update({law["cite"], law["cite"].split("(")[0]})
    for passage in passages:
        if passage["cite"] not in seen:
            add_source("act_search", passage["cite"], passage["text"], passage["url"])
            seen.add(passage["cite"])
    return sources


def format_sources(sources: list) -> str:
    return "\n\n".join(f"[{source['n']}] ({source['cite']})\n{source['text']}" for source in sources)


def ask_gemini(question: str, sources: list) -> str:
    from google import genai
    from google.genai import types

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(
        timeout=config.LLM_TIMEOUT * 1000,
        retry_options=types.HttpRetryOptions(attempts=1)))
    errors = []
    for model_name in config.LLM_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=f"SOURCES:\n{format_sources(sources)}\n\nQUESTION: {question}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=config.LLM_MAX_TOKENS,
                    temperature=0.1,
                    thinking_config=types.ThinkingConfig(thinking_level="low",
                                                         include_thoughts=False),
                ),
            )
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            if response.text and finish_reason == types.FinishReason.STOP:
                return response.text.strip()
            errors.append(f"{model_name}: incomplete response ({finish_reason})")
        except Exception as error:
            errors.append(f"{model_name}: {type(error).__name__} {str(error)[:80]}")
            log.info("model %s failed, trying the next one", model_name)
    raise RuntimeError("; ".join(errors))


def check(text: str, sources: list, question: str = "") -> list:
    return facts.unsupported(text, format_sources(sources) + "\n" + question,
                             facts.section_numbers(source["cite"] for source in sources if source["kind"] != "curated"))


def answer(question: str, entries: list, passages=()):
    best_entry = entries[0]
    sources = collect_sources(entries, list(passages))
    if config.LLM_PROVIDER != "gemini":
        return saved_answer(best_entry), "curated", sources, []
    try:
        gemini_reply = ask_gemini(question, sources)
    except Exception as error:
        log.warning("Gemini failed (%s: %s); using curated answer", type(error).__name__, error)
        return saved_answer(best_entry), "curated", sources, []
    if gemini_reply.strip().strip(".").upper() == INSUFFICIENT:
        log.info("Gemini found the sources insufficient")
        return None, "insufficient", sources, []
    problems = check(gemini_reply, sources, question)
    if not CITATION_PATTERN.search(gemini_reply):
        problems.append("no source cited")
    if problems:
        log.warning("fact guard rejected Gemini answer: %s", ", ".join(problems))
        return saved_answer(best_entry), "guarded", sources, problems
    return gemini_reply, "llm", sources, []
