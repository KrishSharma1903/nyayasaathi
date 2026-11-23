import hashlib
import json
import logging

import numpy as np

import config

log = logging.getLogger("retriever")


def load_chunks(path=None):
    path = path or config.CHUNKS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run: python src/ingest.py "
            "(or python src/ingest.py --from-kb)")
    return json.loads(path.read_text(encoding="utf-8"))


class Retriever:
    def __init__(self, backend=None, path=None, model=None):
        self.chunks = load_chunks(path)
        self.texts = [chunk["index_text"] for chunk in self.chunks]
        choice = backend or config.BACKEND
        self.backend = None
        if choice in ("auto", "hybrid", "embed"):
            try:
                self.build_embedding_index(model)
                self.backend = "embed" if choice == "embed" else "hybrid"
            except Exception as error:
                if choice != "auto":
                    raise
                log.warning("embeddings unavailable (%s: %s); using TF-IDF", type(error).__name__, error)
        if self.backend != "embed":
            self.build_tfidf()
        if self.backend is None:
            self.backend = "tfidf"

    @property
    def threshold(self):
        return config.THRESHOLD[self.backend]

    def build_embedding_index(self, model=None):
        import faiss
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(config.EMBED_MODEL)
        self.model = model
        vectors = self.cached_embeddings()
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)

    def cached_embeddings(self):
        cache_key = hashlib.sha1("\n".join([config.EMBED_MODEL, *self.texts]).encode("utf-8")).hexdigest()
        path = config.INDEX_DIR / f"emb_{cache_key[:16]}.npy"
        if path.exists():
            return np.load(path)
        vectors = self.encode(self.texts)
        config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, vectors)
        return vectors

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True,
                                 show_progress_bar=False).astype("float32")

    def embedding_scores(self, question):
        scores, positions = self.index.search(self.encode([question]), len(self.chunks))
        all_scores = np.zeros(len(self.chunks), dtype="float32")
        all_scores[positions[0]] = scores[0]
        return all_scores

    def build_tfidf(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(self.texts)

    def tfidf_scores(self, question):
        from sklearn.metrics.pairwise import cosine_similarity
        return cosine_similarity(self.vectorizer.transform([question]), self.matrix)[0]

    def search(self, question, top_k=None, where=None):
        top_k = min(top_k or config.TOP_K, len(self.chunks))
        if self.backend == "embed":
            similarity = self.embedding_scores(question)
        elif self.backend == "tfidf":
            similarity = self.tfidf_scores(question)
        else:
            weight = config.KEYWORD_WEIGHT
            similarity = (1 - weight) * self.embedding_scores(question) + weight * self.tfidf_scores(question)
        if where is not None:
            similarity = np.where([where(chunk) for chunk in self.chunks], similarity, -1.0)
        order = [index for index in np.argsort(similarity)[::-1][:top_k] if similarity[index] > -1.0]
        return [(self.chunks[index], float(similarity[index])) for index in order]
