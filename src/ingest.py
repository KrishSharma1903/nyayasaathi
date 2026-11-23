import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

log = logging.getLogger("ingest")

SECTION_HEADING = re.compile(r"^\s*((Section|Sec\.|Article|Rule)\s+\d+[A-Za-z]?\b|\d+[A-Za-z]?\.\s+[A-Z])")


def read_file(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if extension in (".html", ".htm"):
        from bs4 import BeautifulSoup
        page = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        for tag in page(["script", "style", "nav", "footer"]):
            tag.decompose()
        return page.get_text("\n")
    if extension == ".pdf":
        from pypdf import PdfReader
        return "\n".join((pdf_page.extract_text() or "") for pdf_page in PdfReader(str(path)).pages)
    raise ValueError(f"unsupported file type: {path.name}")


def parse_header(text: str):
    meta, body = {}, []
    in_header = True
    for line in text.splitlines():
        match = re.match(r"^#(\w+):\s*(.*)$", line)
        if in_header and match:
            meta[match.group(1).lower()] = match.group(2).strip()
        else:
            in_header = False
            body.append(line)
    return meta, "\n".join(body)


def clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(body: str):
    blocks, current = [], []
    for line in body.splitlines():
        if SECTION_HEADING.match(line) and current:
            blocks.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def pack(block: str, max_words: int):
    if len(block.split()) <= max_words:
        return [block]
    pieces, current, word_count = [], [], 0
    for part in re.split(r"\n\s*\n|(?<=[.;])\s+", block):
        part_words = len(part.split())
        if current and word_count + part_words > max_words:
            pieces.append(" ".join(current))
            current, word_count = [], 0
        current.append(part.strip())
        word_count += part_words
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunks_from_text(text: str, meta: dict, prefix: str):
    body = clean(text)
    chunks = []
    for section_number, section in enumerate(split_sections(body), 1):
        first_line = section.splitlines()[0][:90]
        for piece_number, piece in enumerate(pack(section, config.MAX_CHUNK_WORDS), 1):
            chunks.append({
                "id": f"{prefix}-{section_number}.{piece_number}",
                "domain": meta.get("domain", "unknown"),
                "title": first_line,
                "text": piece,
                "index_text": f"{first_line} {piece}",
                "source": meta.get("source", prefix),
                "url": meta.get("url", ""),
                "version": meta.get("version", "unknown"),
            })
    return chunks


def ingest_raw():
    chunks = []
    files = sorted(path for path in config.RAW_DIR.glob("*") if path.is_file() and not path.name.startswith("_"))
    for file in files:
        meta, body = parse_header(read_file(file))
        if "domain" not in meta or "source" not in meta:
            log.warning("skip %s: missing #domain or #source header", file.name)
            continue
        chunks += chunks_from_text(body, meta, file.stem[:12].upper())
        log.info("ingested %s", file.name)
    return chunks


def from_kb():
    import acts
    registry, sections = acts.load_registry(), acts.load_sections()
    entries = []
    for entry in json.loads(config.KB_PATH.read_text(encoding="utf-8")):
        law = []
        for citation in entry.get("citations", []):
            text = acts.extract(sections.get(citation["act"], {}), citation["section"])
            if not text:
                log.warning("%s: %s s.%s not found in the Act text", entry["id"], citation["act"], citation["section"])
            law.append({"cite": acts.cite(registry, citation), "text": text,
                        "url": registry[citation["act"]]["url"]})
        entries.append({
            "id": entry["id"], "domain": entry["domain"], "title": entry["title"],
            "text": entry["answer"], "next": entry["next"],
            "index_text": f"{entry['title']} {entry['text']} {entry['answer']}",
            "source": entry["source"], "url": entry.get("url", ""), "version": entry["version"],
            "law": law, "verified": entry.get("verified", "secondary"),
            "verify_note": entry.get("verify_note", ""), "checked": entry.get("checked", ""),
        })
    return entries


def build_passages():
    import acts
    registry, passages = acts.load_registry(), []
    for act_key, sections in acts.load_sections().items():
        act_info = registry[act_key]
        for number, text in sections.items():
            for piece_number, piece in enumerate(pack(text, config.MAX_CHUNK_WORDS), 1):
                passages.append({
                    "id": f"{act_key}-s{number}.{piece_number}", "act": act_key, "section": number,
                    "domain": act_info["domain"], "cite": f"{act_info['short']}, s. {number}",
                    "text": piece, "index_text": f"{act_info['title']} section {number} {piece}",
                    "url": act_info["url"],
                })
    return passages


def save_passages(passages):
    config.PASSAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.PASSAGES_PATH.write_text(json.dumps(passages, indent=1, ensure_ascii=False),
                                    encoding="utf-8")
    log.info("saved %d Act passages -> %s", len(passages), config.PASSAGES_PATH)


def save(chunks):
    config.CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CHUNKS_PATH.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("saved %d chunks -> %s", len(chunks), config.CHUNKS_PATH)


if __name__ == "__main__":
    save(from_kb() if "--from-kb" in sys.argv else ingest_raw())
    save_passages(build_passages())
