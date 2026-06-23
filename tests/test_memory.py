from __future__ import annotations

from capabilities.memory import DictMemoryStore, Memory


def test_memory_instructions_include_memory_hygiene_rules() -> None:
    instructions_builder = Memory(store=DictMemoryStore()).get_instructions()

    assert instructions_builder is not None

    instructions = instructions_builder(None)  # type: ignore[arg-type]

    assert "Only save confirmed, reusable lessons" in instructions
    assert "assumed capability limitation" in instructions
