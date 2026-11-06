"""Canonical, independently decodable context packs and prefix deltas."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass


class ContextPackError(ValueError):
    """Invalid context pack, delta, or chunk boundary."""


_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(items: tuple[bytes, ...]) -> str:
    encoded = [base64.b64encode(item).decode("ascii") for item in items]
    return "sha256:" + hashlib.sha256(_canonical({"schema": _PACK_SCHEMA, "items": encoded})).hexdigest()


def _validate_item(item: bytes) -> None:
    if not isinstance(item, bytes):
        raise ContextPackError("pack items must be bytes")
    try:
        item.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextPackError("pack item is not valid UTF-8") from exc


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        items = tuple(items)
        for item in items:
            _validate_item(item)
        digest = _digest(items)
        count = 0
        for _ in range(8):
            payload = {"schema": _PACK_SCHEMA, "items": [base64.b64encode(item).decode("ascii") for item in items],
                       "digest": digest, "byte_count": count}
            actual = len(_canonical(payload))
            if actual == count:
                break
            count = actual
        return cls(items, digest, count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        current = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk limit cannot hold one UTF-8 character")
            if current and len(current) + len(encoded) > chunk_bytes:
                chunks.append(bytes(current))
                current.clear()
            current.extend(encoded)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _canonical({"schema": _PACK_SCHEMA,
                           "items": [base64.b64encode(item).decode("ascii") for item in self.items],
                           "digest": self.digest, "byte_count": self.byte_count})

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        try:
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
                raise ContextPackError("invalid pack fields")
            if payload["schema"] != _PACK_SCHEMA or not isinstance(payload["items"], list):
                raise ContextPackError("invalid pack schema")
            if type(payload["byte_count"]) is not int:
                raise ContextPackError("invalid pack byte count")
            items = tuple(base64.b64decode(item, validate=True) for item in payload["items"])
            pack = cls.build(items)
            if payload["digest"] != pack.digest or payload["byte_count"] != len(data) or pack.byte_count != len(data):
                raise ContextPackError("pack digest or byte count mismatch")
            if pack.to_bytes() != data:
                raise ContextPackError("pack is not canonically encoded")
            return pack
        except ContextPackError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ContextPackError("invalid pack encoding") from exc


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        while prefix < min(len(base.items), len(target.items)) and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        return _canonical({"schema": _DELTA_SCHEMA, "base_digest": self.base_digest,
                           "target_digest": self.target_digest, "prefix_items": self.prefix_items,
                           "appended": [base64.b64encode(item).decode("ascii") for item in self.appended]})

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        try:
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "schema", "base_digest", "target_digest", "prefix_items", "appended"
            } or payload["schema"] != _DELTA_SCHEMA or not isinstance(payload["appended"], list):
                raise ContextPackError("invalid delta fields")
            prefix = payload["prefix_items"]
            if type(prefix) is not int or prefix < 0:
                raise ContextPackError("invalid delta prefix")
            items = tuple(base64.b64decode(item, validate=True) for item in payload["appended"])
            for item in items:
                _validate_item(item)
            return cls(payload["base_digest"], payload["target_digest"], prefix, items)
        except ContextPackError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ContextPackError("invalid delta encoding") from exc


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest mismatch")
    if type(delta.prefix_items) is not int or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("delta prefix is out of range")
    result = ContextPack.build(base.items[:delta.prefix_items] + tuple(delta.appended))
    if result.digest != delta.target_digest:
        raise ContextPackError("delta target digest mismatch")
    return result


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
