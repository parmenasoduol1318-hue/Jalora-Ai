"""Pluggable vector index for long-term memory (spec §12–13).

SQL (SQLite JSON column + Python cosine) is the default offline store.
ChromaDB is used when available and selected via JALORA_MEMORY_VECTORSTORE
(auto | sql | chroma). The SQL rows remain the source of truth; Chroma is a
derived index that is rebuilt lazily whenever it is out of sync.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("jalora.vector_store")

_COLLECTION = "jalora_memories"


class VectorStore(Protocol):
    name: str

    def index(self, memory) -> None:
        """Upsert one Memory row into the index."""

    def remove(self, memory_id: str) -> None:
        """Drop one id from the index (missing ids are ignored)."""

    def search(self, user_id: str, query_vec: list[float], k: int,
               rows: list, query_text: str = "") -> list[tuple[str, float]] | None:
        """Return [(memory_id, score)] sorted best-first, or None on store
        failure so the caller can fall back to SQL cosine."""


# --------------------------------------------------------------------------- #
# SQL fallback: cosine over the rows already loaded from the database          #
# --------------------------------------------------------------------------- #
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _keyword_score(query: str, content: str) -> float:
    tokens = query.lower().split()
    if not tokens:
        return 0.0
    overlap = sum(1 for t in tokens if t in content.lower())
    return min(1.0, overlap / len(tokens))


class SqlVectorStore:
    name = "sql"

    def index(self, memory) -> None:  # rows already live in SQL
        return None

    def remove(self, memory_id: str) -> None:
        return None

    def search(self, user_id: str, query_vec: list[float], k: int,
               rows: list, query_text: str = "") -> list[tuple[str, float]] | None:
        scored: list[tuple[str, float]] = []
        for row in rows:
            if row.embedding:
                scored.append((row.id, _cosine(query_vec, row.embedding)))
            else:
                scored.append((row.id, _keyword_score(query_text, row.content)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


# --------------------------------------------------------------------------- #
# ChromaDB index                                                               #
# --------------------------------------------------------------------------- #
_clients: dict[str, object] = {}
_store_cache: dict[str, VectorStore] = {}


def chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        return True
    except Exception:  # pragma: no cover - import failure path
        return False


class ChromaVectorStore:
    name = "chroma"

    def __init__(self, path: Path):
        import chromadb

        self._path = str(path)
        if self._path not in _clients:
            path.mkdir(parents=True, exist_ok=True)
            _clients[self._path] = chromadb.PersistentClient(path=self._path)
        self._client = _clients[self._path]

    def _collection(self):
        return self._client.get_or_create_collection(
            _COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    def index(self, memory) -> None:
        if not memory.embedding:
            return
        self._collection().upsert(
            ids=[memory.id],
            embeddings=[memory.embedding],
            documents=[memory.content],
            metadatas=[{
                "user_id": memory.user_id,
                "kind": memory.kind,
                "source": memory.source,
                "created_at": memory.created_at.isoformat(),
            }],
        )

    def remove(self, memory_id: str) -> None:
        self._collection().delete(ids=[memory_id])

    def _indexed_count(self, user_id: str) -> int:
        got = self._collection().get(where={"user_id": user_id}, include=[])
        ids = got["ids"] if isinstance(got, dict) else got.ids
        return len(ids)

    def search(self, user_id: str, query_vec: list[float], k: int,
               rows: list, query_text: str = "") -> list[tuple[str, float]] | None:
        expected = sum(1 for r in rows if r.embedding)
        if expected and self._indexed_count(user_id) != expected:
            # lazily (re)build the derived index from the SQL source of truth
            payload = [
                (r.id, r.user_id, r.kind, r.source, r.content, r.embedding,
                 r.created_at.isoformat())
                for r in rows if r.embedding
            ]
            if payload:
                self._collection().upsert(
                    ids=[p[0] for p in payload],
                    embeddings=[p[5] for p in payload],
                    documents=[p[4] for p in payload],
                    metadatas=[{"user_id": p[1], "kind": p[2], "source": p[3],
                                "created_at": p[6]} for p in payload],
                )
        got = self._collection().query(
            query_embeddings=[query_vec],
            n_results=max(1, k),
            where={"user_id": user_id},
            include=["distances"],
        )
        ids = got["ids"] if isinstance(got, dict) else got.ids
        distances = got["distances"] if isinstance(got, dict) else got.distances
        out: list[tuple[str, float]] = []
        for memory_id, distance in zip(ids[0], distances[0]):
            out.append((memory_id, 1.0 - float(distance)))
        return out[:k]


# --------------------------------------------------------------------------- #
# Resolution                                                                   #
# --------------------------------------------------------------------------- #
def reset() -> None:
    """Drop cached clients/stores (tests)."""
    _clients.clear()
    _store_cache.clear()


def get_vector_store() -> VectorStore:
    from app.config import get_settings

    settings = get_settings()
    choice = (settings.memory_vectorstore or "auto").strip().lower()
    key = f"{choice}:{settings.chroma_dir}"
    if key in _store_cache:
        return _store_cache[key]
    store: VectorStore
    if choice == "sql":
        store = SqlVectorStore()
    elif choice == "chroma":
        store = ChromaVectorStore(Path(settings.chroma_dir))
    elif chroma_available():
        try:
            store = ChromaVectorStore(Path(settings.chroma_dir))
        except Exception:
            logger.warning("chroma unavailable, falling back to sql store",
                           exc_info=True)
            store = SqlVectorStore()
    else:
        store = SqlVectorStore()
    _store_cache[key] = store
    return store


def safe_index(memory) -> None:
    try:
        get_vector_store().index(memory)
    except Exception:
        logger.warning("vector index upsert failed for %s", memory.id,
                       exc_info=True)


def safe_remove(memory_id: str) -> None:
    try:
        get_vector_store().remove(memory_id)
    except Exception:
        logger.warning("vector index delete failed for %s", memory_id,
                       exc_info=True)
