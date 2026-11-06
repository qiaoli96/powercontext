# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.
"""Coalesce concurrent embedding work and retain a bounded successful LRU."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage


class _EmbeddingModel(Protocol):
    profile: object

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult: ...


@dataclass(frozen=True, slots=True)
class _Resolved:
    vector: tuple[float, ...]
    batch_id: object
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    """Share identical in-flight texts, batch new work, and cache successes."""

    def __init__(
        self,
        model: _EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be non-negative")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self.profile = model.profile
        self.max_batch_size = max_batch_size
        self.coalesce_ms = float(coalesce_ms)
        self.cache_size = cache_size
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Resolved]] = {}
        self._pending: OrderedDict[str, asyncio.Future[_Resolved]] = OrderedDict()
        self._flush_task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        texts = tuple(texts)
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0, input_tokens=0, output_tokens=0))

        futures: dict[str, asyncio.Future[_Resolved]] = {}
        cached: dict[str, tuple[float, ...]] = {}
        loop = asyncio.get_running_loop()
        async with self._lock:
            for text in dict.fromkeys(texts):
                if not isinstance(text, str):
                    raise ValueError("embedding texts must be strings")
                if text in self._cache:
                    cached[text] = self._cache.pop(text)
                    self._cache[text] = cached[text]
                    continue
                future = self._inflight.get(text)
                if future is None:
                    future = loop.create_future()
                    future.add_done_callback(_consume_future_exception)
                    self._inflight[text] = future
                    self._pending[text] = future
                futures[text] = future
            if self._pending and (self._flush_task is None or self._flush_task.done()):
                self._flush_task = asyncio.create_task(self._flush_after_window())

        resolved: dict[str, _Resolved] = {}
        if futures:
            values = await asyncio.gather(*(asyncio.shield(futures[text]) for text in futures))
            resolved.update(zip(futures, values, strict=True))
        vectors = tuple(cached[text] if text in cached else resolved[text].vector for text in texts)
        batch_results: dict[object, InferenceUsage] = {}
        for item in resolved.values():
            batch_results[item.batch_id] = item.usage
        usage = InferenceUsage(
            requests=sum(item.requests for item in batch_results.values()),
            input_tokens=_sum_optional(item.input_tokens for item in batch_results.values()),
            output_tokens=_sum_optional(item.output_tokens for item in batch_results.values()),
        )
        return EmbeddingResult(vectors=vectors, usage=usage)

    async def _flush_after_window(self) -> None:
        if self.coalesce_ms:
            await asyncio.sleep(self.coalesce_ms / 1000)
        async with self._lock:
            pending = tuple(self._pending.items())
            self._pending.clear()
        for offset in range(0, len(pending), self.max_batch_size):
            batch = pending[offset : offset + self.max_batch_size]
            asyncio.create_task(self._run_batch(batch))

    async def _run_batch(self, batch: tuple[tuple[str, asyncio.Future[_Resolved]], ...]) -> None:
        texts = tuple(text for text, _ in batch)
        try:
            result = await self._model.embed(texts)
            if len(result.vectors) != len(texts):
                raise ValueError("embedding model returned a vector count different from the text count")
            batch_id = object()
            async with self._lock:
                for (text, future), vector in zip(batch, result.vectors, strict=True):
                    if self.cache_size:
                        self._cache[text] = vector
                        self._cache.move_to_end(text)
                        while len(self._cache) > self.cache_size:
                            self._cache.popitem(last=False)
                    if self._inflight.get(text) is future:
                        self._inflight.pop(text, None)
                    if not future.done():
                        future.set_result(_Resolved(vector, batch_id, result.usage))
        except BaseException as exc:
            async with self._lock:
                for text, future in batch:
                    if self._inflight.get(text) is future:
                        self._inflight.pop(text, None)
                    if not future.done():
                        future.set_exception(exc)


def _sum_optional(values) -> int | None:
    items = tuple(values)
    if all(value is None for value in items):
        return None
    return sum(value or 0 for value in items)


def _consume_future_exception(future: asyncio.Future[_Resolved]) -> None:
    if not future.cancelled():
        future.exception()


__all__ = ["CoalescingEmbeddingModel"]
