from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import main
from main import ProfileLockError, profile_lock


def test_profile_lock_writes_holder_metadata(tmp_path: Path) -> None:
    profiles_root = tmp_path
    profile_dir = profiles_root / "demo"
    profile_dir.mkdir()

    lock_path = profile_dir / "profile.yaml.lock"

    with profile_lock(profiles_root, "demo"):
        assert lock_path.is_file()
        holder = json.loads(lock_path.read_text())
        assert holder["profile"] == "demo"
        assert holder["pid"] == os.getpid()
        assert holder["hostname"]

    assert lock_path.read_text() == ""


def test_profile_lock_reports_existing_holder(tmp_path: Path) -> None:
    profiles_root = tmp_path
    profile_dir = profiles_root / "demo"
    profile_dir.mkdir()

    with profile_lock(profiles_root, "demo"):
        with pytest.raises(ProfileLockError) as exc_info:
            with profile_lock(profiles_root, "demo"):
                pass

    assert "profile 'demo' is already running" in str(exc_info.value)
    assert f"pid {os.getpid()}" in str(exc_info.value)


def test_main_reports_profile_lock_for_polling(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()
    (profile_dir / "profile.yaml").write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
instructions: test
""")

    with profile_lock(tmp_path, "demo"):
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "demo",
                "--config",
                str(config_path),
                "--profiles-root",
                str(tmp_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "profile 'demo' is already running" in result.stderr
    assert "Traceback" not in result.stderr


def test_main_reports_profile_lock_for_instruction(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()
    (profile_dir / "profile.yaml").write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
instructions: test
""")

    with profile_lock(tmp_path, "demo"):
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "demo",
                "--instruction",
                "hello",
                "--config",
                str(config_path),
                "--profiles-root",
                str(tmp_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "profile 'demo' is already running" in result.stderr
    assert "Traceback" not in result.stderr


class DummyClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requested_paths: list[str] = []

    async def __aenter__(self) -> "DummyClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, path: str) -> object:
        self.requested_paths.append(path)
        return self.response


class DummyResponse:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload or {}
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self) -> dict[str, object]:
        return self.payload


def test_parse_attach_target_accepts_issue_path() -> None:
    assert main._parse_attach_target("agentic/agentic/issues/160") == (
        "agentic",
        "agentic",
        "Issue",
        "160",
    )


def test_parse_attach_target_accepts_pull_url() -> None:
    assert main._parse_attach_target("http://gitea.ai/agentic/agentic/pulls/99") == (
        "agentic",
        "agentic",
        "Pull",
        "99",
    )


def test_parse_attach_target_rejects_invalid_target() -> None:
    with pytest.raises(ValueError, match="attach target must look like"):
        main._parse_attach_target("agentic/agentic/160")


def test_load_attach_history_loads_existing_thread_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = main.subject_message_key("agentic", "agentic", "160")
    expected_history = [SimpleNamespace(kind="message")]
    load_calls: list[tuple[Path, str]] = []
    client = DummyClient(DummyResponse())

    def client_factory(base_url: str, token: str) -> DummyClient:
        assert base_url == "http://gitea.example"
        assert token == "token"
        return client

    def fake_load_history(messages_dir: Path, loaded_key: str) -> list[object]:
        load_calls.append((messages_dir, loaded_key))
        return expected_history

    monkeypatch.setattr(main, "load_history", fake_load_history)
    deps = main.AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        http_client_factory=client_factory,
        messages_dir=tmp_path,
    )

    subject, history = main.asyncio.run(
        main._load_attach_history(deps, "agentic/agentic/issues/160")
    )

    assert subject.owner == "agentic"
    assert subject.repo == "agentic"
    assert subject.number == "160"
    assert subject.subject_type == "Issue"
    assert history == expected_history
    assert client.requested_paths == ["/api/v1/repos/agentic/agentic/issues/160"]
    assert load_calls == [(tmp_path, key)]


def test_load_attach_history_allows_closed_or_merged_subjects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DummyClient(DummyResponse({"state": "closed"}))

    monkeypatch.setattr(main, "load_history", lambda messages_dir, loaded_key: [])
    deps = main.AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        http_client_factory=lambda base_url, token: client,
        messages_dir=Path("/tmp/messages"),
    )

    subject, history = main.asyncio.run(
        main._load_attach_history(deps, "agentic/agentic/pulls/160")
    )

    assert subject.subject_type == "Pull"
    assert history == []
    assert client.requested_paths == ["/api/v1/repos/agentic/agentic/pulls/160"]


def test_load_attach_history_propagates_missing_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "GET",
        "http://gitea.example/api/v1/repos/agentic/agentic/issues/404",
    )
    response = httpx.Response(404, request=request)
    client = DummyClient(
        DummyResponse(
            error=httpx.HTTPStatusError("missing", request=request, response=response)
        )
    )

    monkeypatch.setattr(main, "load_history", lambda messages_dir, loaded_key: [])
    deps = main.AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        http_client_factory=lambda base_url, token: client,
        messages_dir=Path("/tmp/messages"),
    )

    with pytest.raises(httpx.HTTPStatusError):
        main.asyncio.run(main._load_attach_history(deps, "agentic/agentic/issues/404"))


def test_main_rejects_attach_without_instruction(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()
    (profile_dir / "profile.yaml").write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
instructions: test
""")

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "demo",
            "--attach",
            "agentic/agentic/issues/160",
            "--config",
            str(config_path),
            "--profiles-root",
            str(tmp_path),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--attach requires --instruction" in result.stderr


def test_run_instruction_attaches_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class DummyResult:
        output = "done"

        def all_messages(self) -> list[object]:
            return saved_messages

    class DummyAgent:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object, object, object]] = []

        async def __aenter__(self) -> "DummyAgent":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def run(
            self,
            message: str,
            deps: object,
            message_history: object = None,
            usage_limits: object = None,
        ):
            self.calls.append((message, deps, message_history, usage_limits))
            return DummyResult()

    dummy_agent = DummyAgent()
    runtime = SimpleNamespace(
        agent=dummy_agent,
        deps=main.AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
            profile_name="demo",
            messages_dir=tmp_path,
        ),
        request_limit=42,
    )
    subject = main.NotificationSubject(
        owner="agentic",
        repo="agentic",
        number="160",
        subject_type="Issue",
    )
    history = [SimpleNamespace(kind="message")]
    saved_messages = [SimpleNamespace(kind="saved")]
    save_calls: list[tuple[Path, str, list[object]]] = []

    async def fake_build_runtime(*args):
        return runtime

    async def fake_load_attach_history(deps, attach):
        return subject, history

    def fake_save_history(messages_dir: Path, key: str, messages: list[object]) -> None:
        save_calls.append((messages_dir, key, messages))

    monkeypatch.setattr(main, "_build_runtime", fake_build_runtime)
    monkeypatch.setattr(main, "_load_attach_history", fake_load_attach_history)
    monkeypatch.setattr(main, "save_history", fake_save_history)
    monkeypatch.setattr(
        main,
        "profile_lock",
        lambda profiles_root, profile_name: main.contextlib.nullcontext(),
    )
    monkeypatch.setattr(main.logfire, "configure", lambda **kwargs: None)
    monkeypatch.setattr(main.logfire, "instrument_pydantic_ai", lambda: None)

    main.asyncio.run(
        main.run_instruction(
            "demo",
            "hello",
            tmp_path / "agentic.yaml",
            tmp_path,
            attach="agentic/agentic/issues/160",
        )
    )

    assert len(dummy_agent.calls) == 1
    message, deps, loaded_history, usage_limits = dummy_agent.calls[0]
    assert message == "hello"
    assert loaded_history == history
    assert deps.notification_subject == subject
    assert usage_limits is not None
    assert getattr(usage_limits, "request_limit") == 42
    assert save_calls == [
        (
            tmp_path,
            main.subject_message_key("agentic", "agentic", "160"),
            saved_messages,
        )
    ]
