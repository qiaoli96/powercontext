"""Page-level Sources and retrieval for complete PDF documents."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PDFDocumentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page; tolerate repairable PDF syntax defects."""

    if not value.document_id or not value.document_id.strip():
        raise ValueError("document_id must not be blank")
    try:
        # Finance PDFs can be readable despite minor specification defects; pypdf's
        # tolerant mode repairs those while a failure on any page still aborts atomically.
        reader = PdfReader(io.BytesIO(value.content), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("encrypted PDF cannot be opened")
        digest = hashlib.sha256(value.content).hexdigest()
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=page.extract_text() or "",
            )
            for number, page in enumerate(reader.pages, start=1)
        )
        if not pages:
            raise ValueError("PDF has no pages")
        return pages
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("document_id"):
            raise
        raise ValueError("invalid or unreadable PDF document") from exc


class PDFPageSource(Source):
    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _source_id(doc: str, digest: str, page: int) -> str:
    ident = f"pdf:{quote(doc, safe='')}:{digest}:{page}"
    if len(ident) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page source identity too long")
    return ident


class _Adapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(self, value, /):
        ident = _source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=ident,
            source_id=ident,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source, /):
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _Projection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source, /):
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(_Adapter(), projections=(_Projection(),))

_QUERY_CHUNK_SIZE = 6
_QUERY_CHUNK_STEP = 4
_QUERY_COUNT_LIMIT = 8
_SEARCH_POOL_LIMIT = 300
_QUERY_SYNONYMS = {
    "geography": ("geographic", "region", "regions", "area", "market", "country", "countries", "territories"),
    "geographies": ("geographic", "region", "regions", "area", "market", "country", "countries", "territories"),
    "geographic": ("geography", "region", "area", "market"),
    "primarily": ("mainly", "chiefly", "principal"),
    "operate": ("operation", "operations", "business"),
    "operates": ("operation", "operations", "business"),
    "operating": ("operation", "operations", "business", "profit", "earnings"),
    "legal": ("litigation", "proceedings", "claims", "lawsuit"),
    "battles": ("litigation", "lawsuit", "dispute", "proceedings", "claims"),
    "battle": ("litigation", "lawsuit", "dispute", "proceedings", "claims"),
    "ongoing": ("continuing", "pending", "current"),
    "materially": ("material", "significant", "adverse"),
    "important": ("material", "significant"),
    "quantity": ("amount", "total", "value"),
    "cost": ("expense", "charge", "spending"),
    "costs": ("expense", "expenses", "charges", "spending"),
    "restructuring": ("reorganization", "restructuring"),
    "capex": ("capital expenditure", "capital spending"),
    "ebitda": ("operating income", "depreciation", "amortization"),
    "income": ("operating income", "income statement", "profit", "earnings"),
    "statement": ("income statement", "cash flow statement"),
    "cash": ("cash flow",),
    "flow": ("cash flow",),
    "margin": ("rate", "percentage"),
}


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search and deterministically rerank page candidates from a scoped Memory."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if limit == 0:
        return ()

    candidates: dict[tuple[str, int, str, str], Any] = {}
    reciprocal_ranks: Counter[tuple[str, int, str, str]] = Counter()
    for query in _query_variants(question):
        result = await memory.search(
            SearchMemoryRequest(query=query, limit=_SEARCH_POOL_LIMIT, mode="auto")
        )
        for rank, hit in enumerate(result.hits, start=1):
            identity = (
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(identity, hit)
            reciprocal_ranks[identity] += 1.0 / (60 + rank)

    ranked = _rank_candidates(question, candidates, reciprocal_ranks)
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str]] = set()
    for hit in ranked:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = await memory.get(GetMemoryEntryRequest(citation=citation))
        for source in entry.entry.sources:
            key = (source.source_type, source.source_id)
            if source.source_type == "pdf-page" and key not in seen:
                seen.add(key)
                citations.append(citation)
                break
        if len(citations) >= limit:
            break
    return tuple(citations)


def _query_variants(question: str) -> tuple[str, ...]:
    """Keep the original query and add bounded, generic finance-term expansions."""

    variants = [question]
    terms = tuple(dict.fromkeys(analyze_fts_query(question).split()))
    if not terms:
        return tuple(variants)

    if len(terms) > _QUERY_CHUNK_SIZE:
        for start in range(0, len(terms), _QUERY_CHUNK_STEP):
            chunk_terms = terms[start : start + _QUERY_CHUNK_SIZE]
            chunk = " ".join(_expanded_query_terms(chunk_terms))
            if chunk and chunk not in variants:
                variants.append(chunk)
            if len(variants) >= 3:
                break

    concepts = sorted(
        (term for term in terms if term in _QUERY_SYNONYMS),
        key=lambda term: (-len(_QUERY_SYNONYMS[term]), -len(term), terms.index(term)),
    )
    for term in concepts:
        aliases = _QUERY_SYNONYMS[term]
        phrase_queries: list[str] = []
        single_terms: list[str] = []
        for phrase in (term, *aliases):
            phrase_tokens = analyze_text(phrase).split()
            if len(phrase_tokens) > 1:
                phrase_queries.append(" ".join(phrase_tokens))
            else:
                single_terms.extend(phrase_tokens)
        # Preserve useful finance phrases such as "operating income" and
        # "cash flow statement"; pair only the standalone synonyms.
        phrase_queries.extend(
            " ".join(single_terms[offset : offset + 2])
            for offset in range(0, len(single_terms), 2)
        )
        for alias_query in phrase_queries:
            if alias_query and alias_query not in variants:
                variants.append(alias_query)
            if len(variants) >= _QUERY_COUNT_LIMIT:
                break
        if len(variants) >= _QUERY_COUNT_LIMIT:
            break

    return tuple(variants)


def _expanded_query_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    expanded: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for candidate in (term, *_QUERY_SYNONYMS.get(term, ())):
            for token in analyze_text(candidate).split():
                if token not in seen:
                    seen.add(token)
                    expanded.append(token)
    return tuple(expanded)


def _rank_candidates(
    question: str,
    candidates: dict[tuple[str, int, str, str], Any],
    reciprocal_ranks: Counter[tuple[str, int, str, str]],
) -> tuple[Any, ...]:
    if not candidates:
        return ()
    query_terms = tuple(dict.fromkeys(analyze_fts_query(question).split()))
    if not query_terms:
        return tuple(candidates.values())

    term_weights: dict[str, float] = {}
    for term in query_terms:
        term_weights[term] = max(term_weights.get(term, 0.0), 1.0)
        for alias in _QUERY_SYNONYMS.get(term, ()):
            for token in analyze_text(alias).split():
                term_weights[token] = max(term_weights.get(token, 0.0), 0.65)

    token_sets = {
        identity: frozenset(analyze_text(hit.text).split())
        for identity, hit in candidates.items()
    }
    document_frequency = Counter(
        term for token_set in token_sets.values() for term in token_set if term in term_weights
    )
    document_count = max(1, len(token_sets))
    weights = {
        term: math.log1p((document_count + 0.5) / (document_frequency.get(term, 0) + 0.5))
        * (1.5 if any(character.isdigit() for character in term) else 1.0)
        for term in term_weights
    }

    def score(identity: tuple[str, int, str, str]) -> tuple[float, float, str, str]:
        matched = token_sets[identity]
        lexical_score = sum(weights[term] * term_weights[term] for term in term_weights if term in matched)
        # EBITDA questions commonly need two separate statement pages: the
        # operating-income line and D&A from the cash-flow statement. Reward the
        # income-statement line so the cash-flow page cannot crowd it out.
        if "ebitda" in query_terms and (
            {"operating", "income"}.issubset(matched)
            or {"operating", "profit"}.issubset(matched)
        ):
            lexical_score += 4.0
        # A margin question needs both the numerator and the revenue denominator.
        # Statement pages contain the exact figures but may not repeat the word
        # "margin", so give the P&L page a modest tie-break when it has both lines.
        if "margin" in question.casefold() and "revenue" in matched and (
            "income" in matched or "profit" in matched
        ):
            lexical_score += 3.0
        # Reciprocal rank preserves the backend's own relevance signal when lexical
        # overlap is tied, while the first component rewards rare, query-specific terms.
        return (
            -lexical_score,
            -reciprocal_ranks[identity],
            identity[2],
            identity[3],
        )

    ordered = sorted(candidates, key=score)
    return tuple(candidates[identity] for identity in ordered)
