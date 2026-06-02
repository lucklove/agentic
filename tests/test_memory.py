from __future__ import annotations

from capabilities.memory import DictMemoryStore, Memory


def test_memory_instructions_include_memory_hygiene_rules() -> None:
    instructions_builder = Memory(store=DictMemoryStore()).get_instructions()

    assert instructions_builder is not None

    instructions = instructions_builder(None)  # type: ignore[arg-type]

    assert "If run_code fails but you later identify the cause" in instructions
    assert "save that lesson to memory" in instructions
    assert (
        "If you discover an existing memory is outdated or no longer useful"
        in instructions
    )
    assert "correct it or delete it" in instructions
