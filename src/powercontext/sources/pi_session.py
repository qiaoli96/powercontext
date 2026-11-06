"""Pi v3 session branch parsing and durable incremental event capture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _text(value: Any) -> str:
    if isinstance(value, str): return value
    if isinstance(value, list):
        return "\n".join(x["text"] for x in value if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str))
    return ""


def _visible(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    kind = record["type"]
    if kind in {"compaction", "branch_summary"}:
        role, body = kind, record.get("summary", "")
    elif kind == "custom_message":
        role, body = "custom", _text(record.get("content"))
    elif kind == "message":
        message = record.get("message") or {}
        role = message.get("role", "")
        if role == "bashExecution":
            body = "$ " + str(message.get("command", "")) + "\n" + str(message.get("output", ""))
        elif role == "toolResult":
            body = f"[tool_result {message.get('toolName', 'unknown')} {message.get('toolCallId', '')}] " + _text(message.get("content"))
        else:
            parts = []
            content = message.get("content", "")
            blocks = content if isinstance(content, list) else [{"type":"text", "text":content}] if isinstance(content, str) else []
            for block in blocks:
                if not isinstance(block, dict): continue
                if block.get("type") == "text": parts.append(str(block.get("text", "")))
                elif block.get("type") == "toolCall":
                    args = json.dumps(block.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    parts.append(f"[tool_call {block.get('name', '')} {block.get('id', '')}] {args}")
            body = "\n".join(parts)
    else:
        return None
    if not isinstance(body, str) or not body: return None
    return PiEventInput(session_id=session_id,event_id=record["id"],parent_id=record.get("parentId"),event_type=kind,role=role,content=f"{role}: {body}",record=record)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    try:
        lines=value.content.decode("utf-8-sig").splitlines()
        objects=[json.loads(line) for line in lines if line.strip()]
        header=objects[0]
        if header.get("type")!="session" or header.get("version")!=3 or not isinstance(header.get("id"),str) or not header["id"]: raise ValueError("invalid Pi session header")
        nodes={}
        for row in objects[1:]:
            if not isinstance(row,dict) or not isinstance(row.get("id"),str) or not row["id"] or row["id"] in nodes: raise ValueError("invalid or duplicate event id")
            parent=row.get("parentId")
            if parent is not None and (not isinstance(parent,str) or parent not in nodes): raise ValueError("event parent must precede child")
            nodes[row["id"]]=row
        if not nodes:
            if value.leaf_id is not None: raise ValueError("leaf not found")
            return ()
        leaf=value.leaf_id or next(reversed(nodes))
        if leaf not in nodes: raise ValueError("leaf not found")
        chain=[]; current=leaf; seen=set()
        while current is not None:
            if current in seen: raise ValueError("cycle in Pi session")
            seen.add(current); row=nodes[current]; chain.append(row); current=row.get("parentId")
        chain.reverse()
        return tuple(event for row in chain if (event:=_visible(header["id"],row)) is not None)
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("invalid Pi session") from exc


class PiEventSource(Source):
    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _identity(session_id: str, event_id: str) -> str:
    result=f"pi:{quote(session_id,safe='')}:{quote(event_id,safe='')}"
    if len(result)>MAX_SOURCE_ID_LENGTH: raise ValueError("Pi event identity exceeds source ID limit")
    return result


class _Adapter:
    input_class=PiEventInput; name="pi-event"; source_class=PiEventSource
    async def resolve(self,value,/):
        identity=_identity(value.session_id,value.event_id)
        return PiEventSource(name=identity,source_id=identity,session_id=value.session_id,event_id=value.event_id,parent_id=value.parent_id,event_type=value.event_type,role=value.role,content=value.content,record=value.record,materialization=SourceMaterialization.CAPTURED)
    async def read(self,source,/):
        return PiEventInput(session_id=source.session_id,event_id=source.event_id,parent_id=source.parent_id,event_type=source.event_type,role=source.role,content=source.content,record=source.record)


class _Projection:
    name=TEXT_EVIDENCE_PROJECTION_KEY.name; version=TEXT_EVIDENCE_PROJECTION_KEY.version; source_class=PiEventSource; output_class=TextEvidence
    def project(self,s,/): return TextEvidence(source_type="pi-event",source_id=s.name,content=s.content,metadata={"session_id":s.session_id,"event_id":s.event_id,"parent_id":s.parent_id,"event_type":s.event_type,"role":s.role})


PI_EVENT_SOURCE_DEFINITION=AdapterSourceDefinition(_Adapter(),projections=(_Projection(),))


class PiSessionConnector:
    name="pi-session-history"; version="1"; source_definitions=frozenset({"pi-event"})
    def __init__(self,path: str|Path,leaf_id: str|None=None): self.path=Path(path); self.leaf_id=leaf_id
    async def run(self,session: ConnectorRunSession,/) -> ConnectorRunCompletion:
        events=parse_pi_session(PiSessionInput(content=self.path.read_bytes(),leaf_id=self.leaf_id))
        old=session.checkpoint if isinstance(session.checkpoint,dict) else {}
        imported=dict(old.get("events",{}))
        current={}
        for event in events:
            identity=_identity(event.session_id,event.event_id)
            digest=hashlib.sha256(json.dumps(event.record,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            if identity in imported and imported[identity]!=digest: raise ValueError("an imported Pi event was modified")
            current[identity]=digest
        for event in events:
            identity=_identity(event.session_id,event.event_id)
            if identity not in imported: await session.submit(identity,"pi-event",event)
        imported.update(current)
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE,checkpoint={"events":imported})
