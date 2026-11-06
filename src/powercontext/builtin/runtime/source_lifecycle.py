# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.
"""Persistent source dependency graph and lifecycle propagation."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_GRAPHS_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.sources.journal import validate_scope_id
from powercontext.sources import SourceRef


class SourceLifecycleError(ValueError):
    """Invalid dependency registration or lifecycle event."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    node_id: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be positive")
        return value


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime, database) -> None:
        self._runtime = runtime
        self._database = database
        self._locks: dict[str, asyncio.Lock] = {}

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        scope = validate_scope_id(scope_id)
        return ScopedSourceLifecycle(self._runtime, self._database, scope, self._locks.setdefault(scope, asyncio.Lock()))


class ScopedSourceLifecycle:
    def __init__(self, runtime: BuiltinRuntime, database, scope_id: str, lock: asyncio.Lock) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)
        self._lock = lock

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(source_ref.source_type, source_ref.source_id)
        except Exception as exc:
            raise SourceLifecycleError("source reference is unavailable") from exc
        await self._register(node_id, "source", source_ref.model_dump(mode="json"), (), "all", revision)

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception as exc:
            raise SourceLifecycleError("memory citation is unavailable") from exc
        await self._register(node_id, "memory", citation.model_dump(mode="json"), parents, policy, 1)

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except Exception as exc:
            raise SourceLifecycleError("topic artifact reference is unavailable") from exc
        if artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("artifact must be Topic Memory")
        await self._register(node_id, "topic", artifact_ref.model_dump(mode="json"), parents, policy, 1)

    async def _register(self, node_id, kind, reference, parents, policy, revision) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or node_id != node_id.strip():
            raise SourceLifecycleError("node_id must be nonblank and trimmed")
        if not isinstance(parents, tuple) or len(set(parents)) != len(parents):
            raise SourceLifecycleError("parents must be a unique tuple")
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("policy must be all or any")
        if kind != "source" and (not parents or node_id in parents):
            raise SourceLifecycleError("derived nodes require distinct registered parents")
        async with self._lock:
            graph = await self._load()
            if node_id in graph["nodes"]:
                existing = graph["nodes"][node_id]
                expected = {"kind": kind, "reference": reference, "parents": list(parents), "policy": policy,
                            "revision": revision}
                if any(existing.get(key) != value for key, value in expected.items()):
                    raise SourceLifecycleError("node registration conflicts with existing identity")
                await self._sync_node(node_id, existing)
                return
            if any(parent not in graph["nodes"] for parent in parents):
                raise SourceLifecycleError("all parents must be registered first")
            parent_states = [graph["nodes"][parent]["available"] for parent in parents]
            available = True if kind == "source" else (all(parent_states) if policy == "all" else any(parent_states))
            graph["nodes"][node_id] = {"kind": kind, "reference": reference, "parents": list(parents),
                                       "policy": policy, "revision": revision, "sequence": 0,
                                       "available": available, "event": None}
            await self._save(graph)
            await self._sync_node(node_id, graph["nodes"][node_id])

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            try:
                event = SourceLifecycleEvent.model_validate(event)
            except Exception as exc:
                raise SourceLifecycleError("invalid lifecycle event") from exc
        async with self._lock:
            graph = await self._load()
            node = graph["nodes"].get(event.node_id)
            if node is None or node["kind"] != "source":
                raise SourceLifecycleError("event must reference a registered Source")
            if event.revision != node["revision"]:
                raise SourceLifecycleError("event revision does not match Source lifecycle revision")
            prior = node.get("event")
            event_data = event.model_dump(mode="json")
            if event.sequence < node["sequence"] or (event.sequence == node["sequence"] and prior != event_data):
                raise SourceLifecycleError("event sequence is stale or conflicts")
            if event.sequence == node["sequence"] and prior == event_data and node["sequence"] != 0:
                # Replay still reconciles all derived runtime state after a partial failure.
                pass
            else:
                node["sequence"] = event.sequence
                node["available"] = event.available
                node["event"] = event_data
                await self._save(graph)
            for identifier in _topological(graph["nodes"]):
                item = graph["nodes"][identifier]
                if item["kind"] == "source":
                    continue
                states = [graph["nodes"][parent]["available"] for parent in item["parents"]]
                item["available"] = all(states) if item["policy"] == "all" else any(states)
                await self._sync_node(identifier, item)
            await self._save(graph)
            return sorted(node_id for node_id, item in graph["nodes"].items() if item["available"])

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"]):
        graph = await self._load()
        node = graph["nodes"].get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown lifecycle node")
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown lifecycle purpose")
        if purpose == "generation" and not node["available"]:
            raise SourceLifecycleError("node is unavailable for generation")
        if node["kind"] == "source":
            return SourceRef.model_validate(node["reference"])
        if node["kind"] == "memory":
            return MemoryCitation.model_validate(node["reference"])
        return ArtifactRef.model_validate(node["reference"])

    async def unavailable_topic_refs(self) -> frozenset[tuple[str, int]]:
        graph = await self._load()
        return frozenset(
            (node["reference"]["artifact_id"], node["reference"]["revision"])
            for node in graph["nodes"].values() if node["kind"] == "topic" and not node["available"]
        )

    async def _sync_node(self, node_id: str, node: dict) -> None:
        if node["kind"] != "memory":
            return
        citation = MemoryCitation.model_validate(node["reference"])
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            entry = next((value for value in await service.entries(current) if value.entry_id == citation.entry_id), None)
            if entry is None:
                raise SourceLifecycleError(f"registered Memory entry disappeared: {node_id}")
            target = "active" if node["available"] else "inactive"
            records = await self._runtime.memory.for_scope(self.scope_id).list(include_inactive=True)
            record = next((value for value in records.entries if value.entry.entry_id == citation.entry_id), None)
            if record is None:
                raise SourceLifecycleError(f"registered Memory entry disappeared: {node_id}")
            if record.state == target:
                return
            if target == "inactive":
                await service.forget(current, entries=(entry,), reason="source lifecycle")
            else:
                await service.reactivate(current, entries=(entry,), reason="source lifecycle")

    async def _load(self) -> dict:
        async with self._database.transaction() as connection:
            payload = await connection.scalar(
                select(SOURCE_LIFECYCLE_GRAPHS_TABLE.c.payload).where(
                    SOURCE_LIFECYCLE_GRAPHS_TABLE.c.scope_id == self.scope_id
                )
            )
        if payload is None:
            return {"nodes": {}}
        try:
            return json.loads(bytes(payload).decode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise SourceLifecycleError("stored lifecycle graph is invalid") from exc

    async def _save(self, graph: dict) -> None:
        async with self._database.transaction() as connection:
            payload = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            statement = sqlite_insert(SOURCE_LIFECYCLE_GRAPHS_TABLE).values(scope_id=self.scope_id, payload=payload)
            await connection.execute(statement.on_conflict_do_update(
                index_elements=["scope_id"], set_={"payload": statement.excluded.payload}
            ))


def _topological(nodes: dict[str, dict]) -> tuple[str, ...]:
    output: list[str] = []
    pending = set(nodes)
    while pending:
        ready = sorted(node for node in pending if all(parent in output for parent in nodes[node]["parents"]))
        if not ready:
            raise SourceLifecycleError("dependency graph contains a cycle")
        output.extend(ready)
        pending.difference_update(ready)
    return tuple(output)


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent", "ScopedSourceLifecycle"]
