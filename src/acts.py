import json
import logging
import re
from pathlib import Path

import config

log = logging.getLogger("acts")

ACTS_PATH = config.PROJECT_FOLDER / "data" / "acts.json"

ENACTMENT_START = re.compile(r"BE it enacted by Parliament", re.I)
ACT_BODY_END = re.compile(r"^\s*(STATEMENT OF OBJECTS|NOTES ON CLAUSES|FINANCIAL MEMORANDUM|"
                          r"MEMORANDUM REGARDING|THE (FIRST )?SCHEDULE\s*$)", re.M)
SECTION_HEADING = re.compile(r"^\s*(\d{1,3})([A-Z]?)\.\s*(?=\(1\)|[A-Z\"“])")
NOISE_LINE = re.compile(r"^\s*(SEC\. \d+\]|THE GAZETTE OF INDIA|[~#$@%!+]\s*$)")


def load_registry() -> dict:
    return json.loads(ACTS_PATH.read_text(encoding="utf-8"))


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def split_sections(text: str) -> dict:
    start = ENACTMENT_START.search(text)
    body = text[start.end():] if start else text
    end = ACT_BODY_END.search(body)
    if end:
        body = body[:end.start()]

    sections, section_number, section_lines, last_number = {}, None, [], 0
    for line in body.splitlines():
        line = re.sub(r"^[~#$@%!+]\s*", "", line)
        if NOISE_LINE.match(line):
            continue
        heading = SECTION_HEADING.match(line)
        if heading and last_number < int(heading.group(1)) <= last_number + 10 or (heading and int(heading.group(1)) == last_number and heading.group(2)):
            if section_number:
                sections.setdefault(section_number, "\n".join(section_lines).strip())
            section_number, section_lines, last_number = heading.group(1) + heading.group(2), [], int(heading.group(1))
        if section_number:
            section_lines.append(line.rstrip())
    if section_number:
        sections.setdefault(section_number, "\n".join(section_lines).strip())
    return {number: re.sub(r"[ \t]+", " ", section_text) for number, section_text in sections.items()}


def load_sections() -> dict:
    sections_by_act = {}
    for act_key, act_info in load_registry().items():
        path = config.PROJECT_FOLDER / act_info["file"]
        if not path.exists():
            log.warning("missing %s (%s); its citations will have no law text", path.name, act_key)
            continue
        sections_by_act[act_key] = split_sections(read_pdf(path))
        log.info("%s: %d sections", act_key, len(sections_by_act[act_key]))
    return sections_by_act


MAX_LAW_CHARS = 1500


def base_section(section: str) -> str:
    return re.match(r"\d+[A-Z]?", section).group(0)


def extract(sections: dict, section: str) -> str:
    text = sections.get(base_section(section), "")
    subclause = re.match(r"\d+[A-Z]?\((\w+)\)", section)
    if text and subclause:
        label = subclause.group(1)
        start = re.search(rf"(^|\s|-)\({label}\)\s", text, re.M)
        if start:
            rest = text[start.start():]
            next_subclause = None
            if label.isdigit():
                next_subclause = re.search(rf"\n\s*\({int(label) + 1}\)\s", rest)
            text = rest[:next_subclause.start()] if next_subclause else rest
    text = text.strip()
    return text if len(text) <= MAX_LAW_CHARS else text[:MAX_LAW_CHARS].rsplit(" ", 1)[0] + " ..."


def cite(registry: dict, citation: dict) -> str:
    return f"{registry[citation['act']]['short']}, s. {citation['section']}"
