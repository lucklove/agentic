from __future__ import annotations

import re
from datetime import datetime, timezone

from capabilities.memory import DictMemoryStore, Memory, MemoryEntry


def test_memory_instructions_include_memory_hygiene_rules() -> None:
    instructions_builder = Memory(store=DictMemoryStore()).get_instructions()

    assert instructions_builder is not None

    instructions = instructions_builder(None)  # type: ignore[arg-type]

    assert "Only save confirmed, reusable lessons" in instructions
    assert "assumed capability limitation" in instructions


# ── `instructions_sort` tests for `Memory.build_instructions` ─────────────────


def _entry(
    key: str,
    *,
    importance: float | None = None,
    updated_at: str | None = None,
    read_only: bool = False,
) -> MemoryEntry:
    """Build a `MemoryEntry` with explicit `importance` / `updated_at` / `read_only`."""
    return MemoryEntry(
        key=key,
        content=f"content for {key}",
        importance=importance,
        updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
        read_only=read_only,
    )


# Note: build_instructions inspects ctx.messages via dedup_recent_saves. Tests
# that inject entries disable dedup so ctx=None is safe.


def test_build_instructions_default_score_uses_additive_recency_and_importance() -> (
    None
):
    """Default `instructions_sort="score"` ranks by importance + recency_scorer."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    # Old + low importance: recency ~ 0, importance 0.1 → score ~ 0.1
    store.put(_entry("old_low", importance=0.1, updated_at=old_iso))
    # Fresh + no importance: recency ~ 0.5 (weight=0.5), importance 0 → score ~ 0.5
    store.put(_entry("fresh_none", importance=None, updated_at=now_iso))

    memory = Memory(store=store, dedup_recent_saves=False)
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys.index("fresh_none") < keys.index("old_low"), keys


def test_build_instructions_recency_mode_ignores_importance() -> None:
    """`instructions_sort="recency"` sorts purely by recency_scorer; importance ignored."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    # High importance but very old: recency ~ 0, importance ignored
    store.put(_entry("old_high", importance=10.0, updated_at=old_iso))
    # Low importance but fresh: recency ~ 0.5 (weight=0.5), importance ignored
    store.put(_entry("fresh_low", importance=0.0, updated_at=now_iso))

    memory = Memory(store=store, instructions_sort="recency", dedup_recent_saves=False)
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys.index("fresh_low") < keys.index("old_high"), keys


def test_build_instructions_importance_mode_ignores_recency() -> None:
    """`instructions_sort="importance"` sorts purely by importance; recency ignored."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    # Fresh but importance=0
    store.put(_entry("fresh_zero", importance=0.0, updated_at=now_iso))
    # Old but importance=10
    store.put(_entry("old_ten", importance=10.0, updated_at=old_iso))

    memory = Memory(
        store=store, instructions_sort="importance", dedup_recent_saves=False
    )
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys.index("old_ten") < keys.index("fresh_zero"), keys


def test_build_instructions_insertion_mode_keeps_dict_order() -> None:
    """`instructions_sort="insertion"` preserves legacy dict-insertion order."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    # Insert in this order; `beta` would otherwise rank first under score/recency
    store.put(_entry("alpha", importance=0.0, updated_at=old_iso))
    store.put(_entry("beta", importance=10.0, updated_at=now_iso))
    store.put(_entry("gamma", importance=0.0, updated_at=old_iso))

    memory = Memory(
        store=store, instructions_sort="insertion", dedup_recent_saves=False
    )
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys == ["alpha", "beta", "gamma"], keys


def test_build_instructions_pinned_entries_stay_first_under_all_sort_modes() -> None:
    """`read_only=True` entries are emitted first regardless of `instructions_sort`."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    for sort in ("score", "recency", "importance", "insertion"):
        store = DictMemoryStore()
        # Pinned but old: would otherwise rank last under score/recency
        store.put(
            _entry("pinned_old", importance=0.0, updated_at=old_iso, read_only=True)
        )
        # Non-pinned but fresh + important: would otherwise rank first under score
        store.put(_entry("non_pinned_fresh", importance=10.0, updated_at=now_iso))

        memory = Memory(
            store=store,
            instructions_sort=sort,  # type: ignore[arg-type]
            dedup_recent_saves=False,
        )
        out = memory.build_instructions(None)  # type: ignore[arg-type]

        keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
        assert keys.index("pinned_old") < keys.index("non_pinned_fresh"), (sort, keys)


def test_build_instructions_handles_entries_without_importance() -> None:
    """`importance=None` entries rank by recency alone under score mode."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    store.put(_entry("fresh_no_imp", importance=None, updated_at=now_iso))
    store.put(_entry("old_no_imp", importance=None, updated_at=old_iso))

    memory = Memory(store=store, instructions_sort="score", dedup_recent_saves=False)
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys.index("fresh_no_imp") < keys.index("old_no_imp"), keys


def test_build_instructions_handles_no_recency_scorer() -> None:
    """When `recency_scorer=None`, score mode ranks purely by importance."""
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    store = DictMemoryStore()
    store.put(_entry("low_imp_fresh", importance=0.1, updated_at=now_iso))
    store.put(_entry("high_imp_old", importance=0.9, updated_at=old_iso))

    memory = Memory(
        store=store,
        instructions_sort="score",
        recency_scorer=None,
        dedup_recent_saves=False,
    )
    out = memory.build_instructions(None)  # type: ignore[arg-type]

    keys = re.findall(r"^- \[([^\]]+)\]", out, flags=re.MULTILINE)
    assert keys.index("high_imp_old") < keys.index("low_imp_fresh"), keys


def test_from_spec_accepts_instructions_sort() -> None:
    """`from_spec` accepts `instructions_sort` and threads it into the instance."""
    assert Memory.from_spec().instructions_sort == "score"
    assert Memory.from_spec(instructions_sort="recency").instructions_sort == "recency"
    assert (
        Memory.from_spec(instructions_sort="importance").instructions_sort
        == "importance"
    )
    assert (
        Memory.from_spec(instructions_sort="insertion").instructions_sort == "insertion"
    )
