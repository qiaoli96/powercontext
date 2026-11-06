"""Page-level Sources and retrieval for complete PDF documents."""
from __future__ import annotations

import hashlib
import io
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime import SearchMemoryRequest
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
    if not value.document_id or not value.document_id.strip(): raise ValueError("document_id must not be blank")
    try:
        reader=PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted:
            if reader.decrypt("") == 0: raise ValueError("encrypted PDF cannot be opened")
        pages=[]; digest=hashlib.sha256(value.content).hexdigest()
        for number,page in enumerate(reader.pages,1):
            text=page.extract_text() or ""
            pages.append(PDFPageInput(document_id=value.document_id,document_sha256=digest,page_number=number,text=text))
        if not pages: raise ValueError("PDF has no pages")
        return tuple(pages)
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("document_id"): raise
        raise ValueError("invalid or unreadable PDF document") from exc


class PDFPageSource(Source):
    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _source_id(doc: str, digest: str, page: int) -> str:
    ident=f"pdf:{quote(doc,safe='')}:{digest}:{page}"
    if len(ident)>MAX_SOURCE_ID_LENGTH: raise ValueError("PDF page source identity too long")
    return ident


class _Adapter:
    input_class=PDFPageInput; name="pdf-page"; source_class=PDFPageSource
    async def resolve(self,value,/):
        ident=_source_id(value.document_id,value.document_sha256,value.page_number)
        return PDFPageSource(name=ident,source_id=ident,document_id=value.document_id,document_sha256=value.document_sha256,page_number=value.page_number,text=value.text,materialization=SourceMaterialization.CAPTURED)
    async def read(self,source,/): return PDFPageInput(document_id=source.document_id,document_sha256=source.document_sha256,page_number=source.page_number,text=source.text)


class _Projection:
    name=TEXT_EVIDENCE_PROJECTION_KEY.name; version=TEXT_EVIDENCE_PROJECTION_KEY.version; source_class=PDFPageSource; output_class=TextEvidence
    def project(self,s,/): return TextEvidence(source_type="pdf-page",source_id=s.name,content=f"{s.document_id}\n{s.text}",metadata={"document_id":s.document_id,"document_sha256":s.document_sha256,"page_number":s.page_number})


PDF_PAGE_SOURCE_DEFINITION=AdapterSourceDefinition(_Adapter(),projections=(_Projection(),))


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if not isinstance(question,str) or not question.strip(): raise ValueError("question must not be blank")
    if limit < 0: raise ValueError("limit must not be negative")
    if limit == 0: return ()
    # Search the largest supported candidate pool before reducing to distinct PDF
    # pages. Evidence pages can rank below unrelated pages in the initial fusion.
    result=await memory.search(SearchMemoryRequest(query=question,limit=100,mode="auto"))
    citations=[]; seen=set()
    for hit in result.hits:
        citation=MemoryCitation(memory_ref=hit.memory_ref,entry_id=hit.entry_id,entry_version_id=hit.entry_version_id)
        entry=await memory.get(__import__("powercontext.builtin.runtime",fromlist=["GetMemoryEntryRequest"]).GetMemoryEntryRequest(citation=citation))
        for source in entry.entry.sources:
            key=(source.source_type,source.source_id)
            if source.source_type=="pdf-page" and key not in seen:
                seen.add(key); citations.append(citation); break
        if len(citations)>=limit: break
    return tuple(citations)
