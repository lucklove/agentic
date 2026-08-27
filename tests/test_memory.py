from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from capabilities.memory import DictMemoryStore, Memory, MemoryEntry, SqliteMemoryStore


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


# ── SqliteMemoryStore tests (issue #287) ───────────────────────────────────


def _sqlite_entry(
    key: str,
    content: str,
    *,
    namespace: tuple[str, ...] = ("global",),
    tags: list[str] | None = None,
    metadata: dict[str, object] | None = None,
    importance: float | None = None,
    expires_at: str | None = None,
    read_only: bool = False,
) -> MemoryEntry:
    """Build a `MemoryEntry` with explicit field overrides for SQLite tests."""
    return MemoryEntry(
        key=key,
        content=content,
        namespace=namespace,
        tags=tags or [],
        metadata=metadata or {},
        importance=importance,
        expires_at=expires_at,
        read_only=read_only,
    )


def test_sqlite_store_round_trip(tmp_path) -> None:
    """`put` then `get` returns the stored entry intact."""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    entry = _sqlite_entry("k1", "hello world")
    store.put(entry)
    assert store.get("k1") == entry


def test_sqlite_store_get_missing_returns_none(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    assert store.get("missing") is None


def test_sqlite_store_delete_removes_and_is_idempotent(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "hello"))
    assert store.delete("k1") is True
    assert store.get("k1") is None
    assert store.delete("k1") is False


def test_sqlite_store_put_overwrites_existing(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "first"))
    store.put(_sqlite_entry("k1", "second"))
    assert store.get("k1").content == "second"


def test_sqlite_store_list_all_returns_all(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "x"))
    store.put(_sqlite_entry("b", "y"))
    store.put(_sqlite_entry("c", "z"))
    assert {e.key for e in store.list_all()} == {"a", "b", "c"}


def test_sqlite_store_list_all_filters_by_namespace(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "x", namespace=("agents", "planner")))
    store.put(_sqlite_entry("b", "y", namespace=("agents", "worker")))
    store.put(_sqlite_entry("c", "z", namespace=("global",)))
    result = store.list_all(namespace=("agents",))
    assert {e.key for e in result} == {"a", "b"}


def test_sqlite_store_list_all_filters_by_metadata(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "x", metadata={"kind": "bug"}))
    store.put(_sqlite_entry("b", "y", metadata={"kind": "feature"}))
    assert {e.key for e in store.list_all(filter={"kind": "bug"})} == {"a"}


def test_sqlite_store_search_porter_stem_matches_inflections(tmp_path) -> None:
    """FTS5 with `tokenize='porter'` matches `save` -> `saving`/`saved`/`saves`."""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "I am saving money wisely"))
    for q in ("save", "saved", "saving", "saves"):
        assert [e.key for e in store.search(q)] == ["k1"], q


def test_sqlite_store_search_empty_query_returns_empty(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "hello"))
    assert store.search("") == []
    assert store.search("   ") == []


def test_sqlite_store_search_no_match_returns_empty(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "hello"))
    assert store.search("completely_unrelated_token") == []


def test_sqlite_store_search_filters_by_namespace(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "python rocks", namespace=("agents", "planner")))
    store.put(_sqlite_entry("b", "python rocks", namespace=("agents", "worker")))
    result = store.search("python", namespace=("agents", "planner"))
    assert {e.key for e in result} == {"a"}


def test_sqlite_store_search_adds_importance_boost(tmp_path) -> None:
    """Entries with `importance` set rank above equal-bm25 entries."""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "python programming", importance=10.0))
    store.put(_sqlite_entry("b", "python programming"))
    results = store.search("python")
    assert [e.key for e in results] == ["a", "b"]


def test_sqlite_store_search_adds_recency_scorer(tmp_path) -> None:
    """`recency_scorer` is added on top of the FTS bm25 base score."""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "python programming"))
    store.put(_sqlite_entry("b", "python programming"))

    def scorer(entry: MemoryEntry) -> float:
        return 1.0 if entry.key == "b" else 0.0

    results = store.search("python", recency_scorer=scorer)
    assert results[0].key == "b"


def test_sqlite_store_search_skips_expired_entries(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "python", expires_at="2000-01-01T00:00:00+00:00"))
    store.put(_sqlite_entry("b", "python"))
    assert [e.key for e in store.search("python")] == ["b"]


def test_sqlite_store_delete_removes_from_fts_index(tmp_path) -> None:
    """Deleting an entry must remove it from the FTS index too."""
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("k1", "I am saving money"))
    store.delete("k1")
    assert store.search("save") == []


def test_sqlite_store_list_namespaces_unique(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "x", namespace=("agents", "planner")))
    store.put(_sqlite_entry("b", "y", namespace=("agents", "worker")))
    store.put(_sqlite_entry("c", "z", namespace=("global",)))
    assert store.list_namespaces() == [
        ("agents", "planner"),
        ("agents", "worker"),
        ("global",),
    ]


def test_sqlite_store_list_namespaces_filters_by_prefix(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "mem.db")
    store.put(_sqlite_entry("a", "x", namespace=("agents", "planner")))
    store.put(_sqlite_entry("b", "y", namespace=("agents", "worker")))
    store.put(_sqlite_entry("c", "z", namespace=("global",)))
    assert store.list_namespaces(prefix=("agents",)) == [
        ("agents", "planner"),
        ("agents", "worker"),
    ]


def test_from_spec_accepts_sqlite_backend(tmp_path) -> None:
    """`Memory.from_spec(backend="sqlite")` returns a `SqliteMemoryStore`."""
    memory = Memory.from_spec(backend="sqlite", path=str(tmp_path / "mem.db"))
    assert isinstance(memory.store, SqliteMemoryStore)


def test_from_spec_rejects_unknown_backend() -> None:
    """Unknown backends still raise (now including the new `"sqlite"` option)."""
    with pytest.raises(ValueError, match="Unknown memory backend"):
        Memory.from_spec(backend="postgres")
