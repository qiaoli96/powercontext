import html
import re
import uuid
from dataclasses import dataclass

from pyseekdb import (
    Client,
    FulltextIndexConfig,
    Schema,
)

from .parser import Paragraph


class SearchError(Exception):
    """Raised when the seekdb query itself fails (as opposed to returning zero hits)."""


@dataclass
class SearchResult:
    file: str
    section: str
    content: str
    snippet: str


def _query_terms(query: str) -> list[str]:
    return [w for w in re.split(r"\s+", query) if len(w) >= 2]


def _passes_filter(doc: str, terms: list[str]) -> bool:
    if not terms:
        return True
    lower_doc = doc.lower()
    return any(t.lower() in lower_doc for t in terms)


def _highlight_snippet(text: str, query: str, window: int = 120) -> str:
    """Extract a snippet around the first match and highlight query terms."""
    words = [w for w in re.split(r"\s+", query) if w]
    if not words:
        return html.escape(text[:window]) + ("..." if len(text) > window else "")

    lower_text = text.lower()
    best_pos = 0
    for w in words:
        idx = lower_text.find(w.lower())
        if idx != -1:
            best_pos = idx
            break

    start = max(0, best_pos - window // 2)
    end = min(len(text), best_pos + window // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    snippet = html.escape(snippet)
    for w in words:
        snippet = re.sub(
            rf"({re.escape(html.escape(w))})",
            r"<mark>\1</mark>",
            snippet,
            flags=re.IGNORECASE,
        )
    return snippet


class SeekDB:
    """Full-text search only using pyseekdb."""

    def __init__(self, db_path: str, collection_name: str = "research_paragraphs"):
        # Initialize client with explicit no-embedding configuration
        self.client = Client(
            path=db_path,
            embedding_function=None,  # Disable default embedding function
        )
        
        # Create collection with full-text index only
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            schema=Schema(
                fulltext_index=FulltextIndexConfig(analyzer="ik"),
            ),
        )

    def add_paragraphs(self, paragraphs: list[Paragraph]):
        """Add paragraphs to the collection with placeholder vectors."""
        ids = [str(uuid.uuid4()) for _ in paragraphs]
        documents = [p.content for p in paragraphs]
        metadatas = [{"file": p.file, "section": p.section} for p in paragraphs]
        
        # Provide placeholder vectors (384-dim zero vectors) if required by the API
        # Since we're using full-text only, these won't be used for search
        embeddings = [[0.0] * 384 for _ in paragraphs]
        
        try:
            # Try without embeddings first
            self.collection.add(ids=ids, documents=documents, metadatas=metadatas)
        except TypeError:
            # If embeddings are required, provide placeholder vectors
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        
        self.collection.refresh_index()

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Search using full-text index, then post-filter to remove false positives."""
        try:
            results = self.collection.get(
                where_document={"$contains": query},
                limit=limit,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            raise SearchError(f"seekdb query failed: {e}") from e

        if not results or not results.get("ids"):
            return []

        terms = _query_terms(query)
        hits: list[SearchResult] = []
        for i, doc_id in enumerate(results["ids"]):
            meta = results["metadatas"][i] if results.get("metadatas") else {}
            doc = results["documents"][i] if results.get("documents") else ""
            if not _passes_filter(doc, terms):
                continue
            hits.append(SearchResult(
                file=meta.get("file", ""),
                section=meta.get("section", ""),
                content=doc,
                snippet=_highlight_snippet(doc, query),
            ))
        return hits

    def clear(self):
        """Clear all documents from the collection."""
        try:
            self.client.delete_collection(self.collection.name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection.name,
                schema=Schema(
                    fulltext_index=FulltextIndexConfig(analyzer="ik"),
                ),
            )
        except Exception:
            pass

    def count(self) -> int:
        """Return the number of documents in the collection."""
        try:
            return self.collection.count()
        except Exception:
            return 0

    def close(self):
        """Close the client connection."""
        pass
