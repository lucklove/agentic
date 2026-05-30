"""VibeGuard-style privacy capability for Pydantic AI agents.

The capability redacts sensitive values before provider requests and restores
placeholders only at local boundaries where cleartext is explicitly needed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

__all__ = ["PrivacyCapability"]


_BUILTIN_PATTERNS: dict[str, tuple[str, str, str]] = {
    "email": (r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", "i", "EMAIL"),
    "china_phone": (r"(?<!\d)1[3-9]\d{9}(?!\d)", "", "CHINA_PHONE"),
    "china_id": (r"(?<!\d)\d{17}[\dXx](?!\d)", "", "CHINA_ID"),
    "uuid": (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        "",
        "UUID",
    ),
    "ipv4": (r"(?:\d{1,3}\.){3}\d{1,3}", "", "IPV4"),
    "mac": (r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", "i", "MAC"),
}


@dataclass(frozen=True)
class _KeywordRule:
    value: str
    category: str


@dataclass(frozen=True)
class _RegexRule:
    regex: re.Pattern[str]
    category: str


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    category: str


def _sanitize_category(value: Any) -> str:
    raw = str(value or "").strip().upper()
    safe = re.sub(r"_+", "_", re.sub(r"[^A-Z0-9_]", "_", raw))
    return safe or "TEXT"


def _compile_flags(flags: str) -> int:
    compiled = 0
    if "i" in flags:
        compiled |= re.IGNORECASE
    if "m" in flags:
        compiled |= re.MULTILINE
    if "s" in flags:
        compiled |= re.DOTALL
    return compiled


def _peel_inline_flags(pattern: str, flags: str) -> tuple[str, str]:
    while True:
        if pattern.startswith("(?i)"):
            pattern = pattern[4:]
            flags += "" if "i" in flags else "i"
            continue
        if pattern.startswith("(?m)"):
            pattern = pattern[4:]
            flags += "" if "m" in flags else "m"
            continue
        break
    return pattern, flags


def _parse_ttl(value: Any) -> timedelta | None:
    if value is None:
        return timedelta(hours=1)
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))
    text = str(value).strip().lower()
    if text in {"", "none", "off", "disabled"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?", text)
    if not match:
        return timedelta(hours=1)
    amount = float(match.group(1))
    unit = match.group(2) or "s"
    if unit == "ms":
        return timedelta(milliseconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(seconds=amount)


@dataclass
class _PatternSet:
    keywords: list[_KeywordRule]
    regex: list[_RegexRule]
    exclude: set[str]

    @classmethod
    def from_spec(cls, spec: dict[str, Any] | None) -> _PatternSet:
        spec = spec or {}
        keywords = []
        for item in spec.get("keywords", []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "")).strip()
            if value:
                keywords.append(
                    _KeywordRule(value, _sanitize_category(item.get("category")))
                )

        regex_rules = []
        for item in spec.get("regex", []):
            if not isinstance(item, dict):
                continue
            pattern = str(item.get("pattern", "")).strip()
            if not pattern:
                continue
            flags = str(item.get("flags", ""))
            pattern, flags = _peel_inline_flags(pattern, flags)
            regex_rules.append(
                _RegexRule(
                    re.compile(pattern, _compile_flags(flags)),
                    _sanitize_category(item.get("category")),
                )
            )

        for name in spec.get("builtin", []):
            builtin = _BUILTIN_PATTERNS.get(str(name).strip())
            if builtin is None:
                continue
            pattern, flags, category = builtin
            regex_rules.append(
                _RegexRule(re.compile(pattern, _compile_flags(flags)), category)
            )

        return cls(
            keywords=keywords,
            regex=regex_rules,
            exclude={str(item) for item in spec.get("exclude", [])},
        )


@dataclass
class _PlaceholderSession:
    prefix: str = "__VG_"
    ttl: timedelta | None = timedelta(hours=1)
    max_mappings: int = 100000
    secret: bytes = field(default_factory=lambda: os.urandom(32))
    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    created: dict[str, datetime] = field(default_factory=dict)

    def cleanup(self) -> None:
        if self.ttl is None:
            return
        now = datetime.now(timezone.utc)
        expired = [
            key for key, created in self.created.items() if now - created > self.ttl
        ]
        for placeholder in expired:
            original = self.forward.pop(placeholder, None)
            self.created.pop(placeholder, None)
            if original is not None:
                self.reverse.pop(original, None)

    def _evict_oldest(self) -> None:
        if not self.created:
            return
        placeholder = min(self.created, key=self.created.__getitem__)
        original = self.forward.pop(placeholder, None)
        self.created.pop(placeholder, None)
        if original is not None:
            self.reverse.pop(original, None)

    def get_or_create(self, original: str, category: str) -> str:
        existing = self.reverse.get(original)
        if existing:
            return existing

        self.cleanup()
        if self.max_mappings > 0:
            while len(self.forward) >= self.max_mappings:
                self._evict_oldest()

        safe_category = _sanitize_category(category)
        digest = hmac.new(self.secret, original.encode(), hashlib.sha256).hexdigest()[
            :12
        ]
        base = f"{self.prefix}{safe_category}_{digest}__"
        placeholder = base
        suffix = 2
        while placeholder in self.forward and self.forward[placeholder] != original:
            placeholder = f"{base[:-2]}_{suffix}__"
            suffix += 1

        now = datetime.now(timezone.utc)
        self.forward[placeholder] = original
        self.reverse[original] = placeholder
        self.created[placeholder] = now
        return placeholder

    def placeholder_regex(self) -> re.Pattern[str]:
        return re.compile(
            rf"{re.escape(self.prefix)}[A-Za-z0-9_]+_[a-f0-9A-F]{{12}}(?:_\d+)?__"
        )


@dataclass
class PrivacyCapability(AbstractCapability[Any]):
    """Profile-configurable provider-boundary privacy capability."""

    patterns: _PatternSet
    session: _PlaceholderSession = field(default_factory=_PlaceholderSession)
    restore_tool_names: set[str] = field(default_factory=set)
    restore_final_output: bool = True
    enabled: bool = True

    @classmethod
    def from_spec(cls, opts: dict[str, Any]) -> PrivacyCapability:
        session_spec = opts.get("session") or {}
        return cls(
            patterns=_PatternSet.from_spec(opts.get("patterns")),
            session=_PlaceholderSession(
                prefix=str(opts.get("placeholder_prefix", "__VG_")),
                ttl=_parse_ttl(session_spec.get("ttl", "1h")),
                max_mappings=int(session_spec.get("max_mappings", 100000)),
            ),
            restore_tool_names={str(name) for name in opts.get("restore_tools", [])},
            restore_final_output=bool(opts.get("restore_final_output", True)),
            enabled=bool(opts.get("enabled", True)),
        )

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self.enabled:
            return request_context
        messages = [
            self._redact_message(message) for message in request_context.messages
        ]
        return ModelRequestContext(
            model=request_context.model,
            messages=messages,
            model_settings=request_context.model_settings,
            model_request_parameters=request_context.model_request_parameters,
        )

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            return args
        tool_name = getattr(tool_def, "name", call.tool_name)
        if self.restore_tool_names and tool_name not in self.restore_tool_names:
            return args
        if not self.restore_tool_names:
            return args
        return self.restore_object(args)

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        if not self.enabled:
            return result
        return self.redact_object(result)

    async def before_output_process(
        self, ctx: RunContext[Any], *, output_context: Any, output: Any
    ) -> Any:
        if not self.enabled or not self.restore_final_output:
            return output
        return self.restore_object(output)

    async def after_output_validate(
        self, ctx: RunContext[Any], *, output_context: Any, output: Any
    ) -> Any:
        if not self.enabled or not self.restore_final_output:
            return output
        return self.restore_object(output)

    def redact_text(self, value: str) -> str:
        if not self.enabled or not value:
            return value
        matches: list[_Match] = []
        for rule in self.patterns.keywords:
            start = 0
            while True:
                index = value.find(rule.value, start)
                if index < 0:
                    break
                end = index + len(rule.value)
                original = value[index:end]
                if original not in self.patterns.exclude:
                    matches.append(_Match(index, end, rule.category))
                start = end

        for regex_rule in self.patterns.regex:
            for match in regex_rule.regex.finditer(value):
                if not match.group(0):
                    continue
                original = value[match.start() : match.end()]
                if original not in self.patterns.exclude:
                    matches.append(
                        _Match(match.start(), match.end(), regex_rule.category)
                    )

        if not matches:
            return value

        matches.sort(key=lambda m: (m.start, -(m.end - m.start)))
        selected: list[_Match] = []
        covered_until = -1
        for sensitive_match in matches:
            if sensitive_match.start < covered_until:
                continue
            selected.append(sensitive_match)
            covered_until = sensitive_match.end

        output = value
        for sensitive_match in reversed(selected):
            original = output[sensitive_match.start : sensitive_match.end]
            placeholder = self.session.get_or_create(original, sensitive_match.category)
            output = (
                output[: sensitive_match.start]
                + placeholder
                + output[sensitive_match.end :]
            )
        return output

    def restore_text(self, value: str) -> str:
        if not self.enabled or not value:
            return value

        def replace(match: re.Match[str]) -> str:
            return self.session.forward.get(match.group(0), match.group(0))

        return self.session.placeholder_regex().sub(replace, value)

    def redact_object(self, value: Any) -> Any:
        return self._map_object(value, self.redact_text)

    def restore_object(self, value: Any) -> Any:
        return self._map_object(value, self.restore_text)

    def _redact_message(self, message: ModelMessage) -> ModelMessage:
        if isinstance(message, ModelRequest):
            return dataclasses.replace(
                message,
                parts=tuple(self._redact_part(part) for part in message.parts),
                instructions=(
                    self.redact_text(message.instructions)
                    if message.instructions
                    else None
                ),
            )
        if isinstance(message, ModelResponse):
            return dataclasses.replace(
                message, parts=tuple(self._redact_part(part) for part in message.parts)
            )
        return self.redact_object(message)

    def _redact_part(self, part: Any) -> Any:
        if isinstance(part, SystemPromptPart | TextPart):
            return dataclasses.replace(part, content=self.redact_text(part.content))
        if isinstance(part, UserPromptPart):
            return dataclasses.replace(part, content=self.redact_object(part.content))
        if isinstance(part, ToolCallPart):
            return dataclasses.replace(part, args=self.redact_object(part.args))
        if isinstance(part, ToolReturnPart):
            return dataclasses.replace(part, content=self.redact_object(part.content))
        if isinstance(part, RetryPromptPart):
            return dataclasses.replace(part, content=self.redact_object(part.content))
        if isinstance(part, TextContent):
            return dataclasses.replace(part, content=self.redact_text(part.content))
        return self.redact_object(part)

    def _map_object(self, value: Any, text_mapper: Any) -> Any:
        if isinstance(value, str):
            return text_mapper(value)
        if isinstance(value, list):
            return [self._map_object(item, text_mapper) for item in value]
        if isinstance(value, tuple):
            return tuple(self._map_object(item, text_mapper) for item in value)
        if isinstance(value, dict):
            return {
                self._map_object(key, text_mapper): self._map_object(item, text_mapper)
                for key, item in value.items()
            }
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            changes = {}
            for field_info in dataclasses.fields(value):
                if not field_info.init:
                    continue
                field_value = getattr(value, field_info.name)
                mapped = self._map_object(field_value, text_mapper)
                if mapped != field_value:
                    changes[field_info.name] = mapped
            if changes:
                return dataclasses.replace(value, **changes)
        return value
