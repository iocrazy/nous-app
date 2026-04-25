"""Unit tests for UuidIntMapper — LLM-facing int ↔ UUID translation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.services.memory.uuid_int_mapper import UuidIntMapper


@dataclass
class _Stub:
    id: UUID


@pytest.mark.unit
def test_from_uuids_preserves_order():
    a, b, c = uuid4(), uuid4(), uuid4()
    mapper = UuidIntMapper.from_uuids([a, b, c])
    assert mapper.to_int(a) == 0
    assert mapper.to_int(b) == 1
    assert mapper.to_int(c) == 2


@pytest.mark.unit
def test_from_records_extracts_id():
    a, b = _Stub(id=uuid4()), _Stub(id=uuid4())
    mapper = UuidIntMapper.from_records([a, b])
    assert mapper.to_int(a.id) == 0
    assert mapper.to_int(b.id) == 1


@pytest.mark.unit
def test_to_uuid_lookup():
    a, b = uuid4(), uuid4()
    mapper = UuidIntMapper.from_uuids([a, b])
    assert mapper.to_uuid(0) == a
    assert mapper.to_uuid(1) == b


@pytest.mark.unit
def test_to_uuid_out_of_range_raises():
    mapper = UuidIntMapper.from_uuids([uuid4()])
    with pytest.raises(IndexError):
        mapper.to_uuid(5)
    with pytest.raises(IndexError):
        mapper.to_uuid(-1)


@pytest.mark.unit
def test_to_int_unknown_uuid_raises():
    mapper = UuidIntMapper.from_uuids([uuid4()])
    with pytest.raises(ValueError):
        mapper.to_int(uuid4())


@pytest.mark.unit
def test_resolve_int_refs_simple():
    a, b, c = uuid4(), uuid4(), uuid4()
    mapper = UuidIntMapper.from_uuids([a, b, c])
    text = "I would keep [0] and [2]"
    assert mapper.resolve_int_refs(text) == [a, c]


@pytest.mark.unit
def test_resolve_int_refs_preserves_appearance_order():
    a, b, c = uuid4(), uuid4(), uuid4()
    mapper = UuidIntMapper.from_uuids([a, b, c])
    text = "Best to worst: [2], [0], [1]"
    assert mapper.resolve_int_refs(text) == [c, a, b]


@pytest.mark.unit
def test_resolve_int_refs_drops_out_of_range():
    a, b = uuid4(), uuid4()
    mapper = UuidIntMapper.from_uuids([a, b])
    text = "I want [0], [5], [99]"
    assert mapper.resolve_int_refs(text) == [a]


@pytest.mark.unit
def test_resolve_int_refs_empty_text_returns_empty():
    mapper = UuidIntMapper.from_uuids([uuid4()])
    assert mapper.resolve_int_refs("") == []


@pytest.mark.unit
def test_resolve_int_refs_no_brackets_returns_empty():
    mapper = UuidIntMapper.from_uuids([uuid4()])
    assert mapper.resolve_int_refs("I want number 0 and 1") == []


@pytest.mark.unit
def test_len_reports_size():
    mapper = UuidIntMapper.from_uuids([uuid4(), uuid4(), uuid4()])
    assert len(mapper) == 3


@pytest.mark.unit
def test_empty_mapper():
    mapper = UuidIntMapper.from_uuids([])
    assert len(mapper) == 0
    assert mapper.resolve_int_refs("[0]") == []
