import logging
import os
from pathlib import Path

PROJECT_FOLDER = Path(__file__).resolve().parent.parent


def load_env_file(path: Path):
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


load_env_file(PROJECT_FOLDER / ".env")

logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(levelname)s: %(message)s")
for logger_name in ("ingest", "retriever", "generator", "pipeline"):
    logging.getLogger(logger_name).setLevel(os.getenv("NS_LOG_LEVEL", "INFO"))

RAW_DIR = PROJECT_FOLDER / "data" / "raw"
KB_PATH = PROJECT_FOLDER / "data" / "kb.json"
CHUNKS_PATH = PROJECT_FOLDER / "data" / "processed" / "chunks.json"
PASSAGES_PATH = PROJECT_FOLDER / "data" / "processed" / "act_chunks.json"
INDEX_DIR = PROJECT_FOLDER / "index"
FEEDBACK_PATH = PROJECT_FOLDER / "data" / "feedback.csv"

MAX_CHUNK_WORDS = 180
TOP_K = 5

BACKEND = os.getenv("NS_BACKEND", "auto")
EMBED_MODEL = os.getenv("EMBED_MODEL",
                        "sentence-transformers/all-MiniLM-L6-v2")
KEYWORD_WEIGHT = 0.5

THRESHOLD = {
    "tfidf": float(os.getenv("NS_THRESHOLD_TFIDF", "0.12")),
    "embed": float(os.getenv("NS_THRESHOLD_EMBED", "0.35")),
    "hybrid": float(os.getenv("NS_THRESHOLD_HYBRID", "0.23")),
}

TOP_MATCHES = 2
EXTRA_PASSAGES = 2
MIN_PASSAGE_SCORE = 0.25

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini" if GEMINI_API_KEY else "none").strip().lower()
LLM_MODELS = [model_name.strip() for model_name in os.getenv(
    "LLM_MODELS", os.getenv("LLM_MODEL", "gemini-3.5-flash,gemini-3.1-flash-lite")).split(",") if model_name.strip()]
LLM_MODEL = LLM_MODELS[0]
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "25"))

DISCLAIMER = ("This is general legal information, not legal advice. "
              "Rules can differ by State. Please verify with a lawyer or legal aid.")
