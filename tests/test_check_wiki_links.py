from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / ".gitea" / "scripts" / "check_wiki_links.py"


# ---------------------------------------------------------------------------
# Script loader
# ---------------------------------------------------------------------------
#
# The script imports ``scrapy`` at module load (used by the spider class).
# The helpers we want to test (normalize_slug, wiki_page_name_from_url,
# fetch_known_wiki_pages, WikiIndexCache, ...) sit ABOVE those imports, so
# we stub the ``scrapy`` family in ``sys.modules`` to let importlib load
# the module without the real (heavy) scrapy dep. We never instantiate the
# spider class in these tests, so the stubs only need to satisfy the
# class-body references (CrawlSpider base + Rule(...) literal).


class _StubBase:
    """Minimal stand-in for scrapy base classes.

    Accepts and ignores any args/kwargs -- the class body of
    WikiLinkCheckSpider calls ``LinkExtractor(allow=..., deny=...)`` and
    ``Rule(..., callback=..., follow=True)`` at class-definition time.
    The real classes return objects with their own attributes; we never
    touch those, only the class body needs to evaluate cleanly.
    """

    def __init__(self, *args, **kwargs):
        pass


def _make_stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(scope="module")
def cw():
    """Load ``check_wiki_links`` with stubbed scrapy deps."""
    sys.modules.setdefault("scrapy", _make_stub("scrapy"))
    sys.modules.setdefault(
        "scrapy.signals",
        _make_stub("scrapy.signals", spider_closed="spider_closed"),
    )
    sys.modules.setdefault(
        "scrapy.linkextractors",
        _make_stub("scrapy.linkextractors", LinkExtractor=_StubBase),
    )
    sys.modules.setdefault(
        "scrapy.spiders",
        _make_stub("scrapy.spiders", CrawlSpider=_StubBase, Rule=_StubBase),
    )

    spec = importlib.util.spec_from_file_location("check_wiki_links", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_wiki_links"] = mod  # so @dataclass can introspect it
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# normalize_slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Writing Plans", "writing-plans"),
        ("Writing-Plans", "writing-plans"),
        ("writing plans", "writing-plans"),
        ("Writing-Plans.md", "writing-plans"),
        ("  Writing Plans  ", "writing-plans"),
        ("Home", "home"),
        ("home", "home"),
        ("Foo-Bar.MD", "foo-bar"),
    ],
)
def test_normalize_slug_ascii(cw, raw, expected):
    assert cw.normalize_slug(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Commander memory \u2014 #257", "commander-memory-\u2014-#257"),
        ("Commander-Memory-%E2%80%94-%23257", "commander-memory-\u2014-#257"),
        (
            "Commander memory \u2014 tidb-management-service#4",
            "commander-memory-\u2014-tidb-management-service#4",
        ),
        (
            "Commander+memory+%E2%80%94+tidb-management-service%234.-",
            "commander-memory-\u2014-tidb-management-service#4",
        ),
        (
            "Commander memory \u2014 tidbcloud/tidb-management-service#4",
            "commander-memory-\u2014-tidbcloud/tidb-management-service#4",
        ),
        (
            "Commander+memory+%E2%80%94+tidbcloud%2Ftidb-management-service%234.-",
            "commander-memory-\u2014-tidbcloud/tidb-management-service#4",
        ),
        (
            "Commander memory \u2014 tidb-management-service#4 (round-trip test)",
            "commander-memory-\u2014-tidb-management-service#4-(round-trip-test)",
        ),
        (
            "Commander+memory+%E2%80%94+tidb-management-service%234+%28round-trip+test%29.-",
            "commander-memory-\u2014-tidb-management-service#4-(round-trip-test)",
        ),
        (
            "Commander memory \u2014 agentic/gitea#2",
            "commander-memory-\u2014-agentic/gitea#2",
        ),
        (
            "Commander-memory-%E2%80%94-agentic%2Fgitea%232",
            "commander-memory-\u2014-agentic/gitea#2",
        ),
    ],
)
def test_normalize_slug_url_encoded(cw, raw, expected):
    """API-side title and link-side slug must normalize identically."""
    assert cw.normalize_slug(raw) == expected


def test_normalize_slug_strips_trailing_dot_dash(cw):
    """Legacy `.-` artifact in some wiki sub_urls must be stripped."""
    assert cw.normalize_slug("foo-bar.-") == "foo-bar"
    assert (
        cw.normalize_slug("Commander+memory+%E2%80%94+tidb-management-service%234.-")
        == "commander-memory-\u2014-tidb-management-service#4"
    )


def test_normalize_slug_preserves_internal_dots(cw):
    """Internal dots are preserved (only trailing `.md` / `.-` is stripped)."""
    assert cw.normalize_slug("foo.bar") == "foo.bar"
    assert cw.normalize_slug("foo.bar.-") == "foo.bar"


# ---------------------------------------------------------------------------
# wiki_page_name_from_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "http://gitea.ai/agentic/agentic/wiki/Writing-Plans",
            "writing-plans",
        ),
        (
            "http://gitea.ai/agentic/agentic/wiki/Commander+memory+%E2%80%94+tidb-management-service%234.-",
            "commander-memory-\u2014-tidb-management-service#4",
        ),
        (
            "http://gitea.ai/agentic/agentic/wiki/Commander-Memory-%E2%80%94-%23257",
            "commander-memory-\u2014-#257",
        ),
        (
            "http://gitea.ai/agentic/agentic/wiki/Writing-Plans.md",
            "writing-plans",
        ),
    ],
)
def test_wiki_page_name_from_url(cw, url, expected):
    assert cw.wiki_page_name_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        # No `/wiki/` substring anywhere -- definitely not a wiki page.
        "http://example.com/no/wiki-here",
        # `/wiki/` marker exists but nothing after it (root of wiki).
        "http://gitea.ai/agentic/agentic/wiki/",
    ],
)
def test_wiki_page_name_from_url_non_wiki_returns_none(cw, url):
    assert cw.wiki_page_name_from_url(url) is None


# ---------------------------------------------------------------------------
# Cross-form equality -- the issue #265 reproducer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_title,link_slug",
    [
        ("Writing Plans", "writing-plans"),
        ("Writing Plans Template", "writing-plans-template"),
        ("Requesting Code Review", "requesting-code-review"),
        ("Systematic Debugging", "systematic-debugging"),
        (
            "Systematic Debugging ArgoCD Failure Patterns",
            "systematic-debugging-argocd-failure-patterns",
        ),
        ("Commander Memory \u2014 #257", "Commander-Memory-%E2%80%94-%23257"),
        (
            "Commander memory \u2014 agentic/gitea#2",
            "Commander-memory-%E2%80%94-agentic%2Fgitea%232",
        ),
        (
            "Commander memory \u2014 tidb-management-service#4",
            "Commander+memory+%E2%80%94+tidb-management-service%234.-",
        ),
        (
            "Commander memory \u2014 tidbcloud/tidb-management-service#4",
            "Commander+memory+%E2%80%94+tidbcloud%2Ftidb-management-service%234.-",
        ),
        (
            "Commander memory \u2014 tidb-management-service#4 (round-trip test)",
            "Commander+memory+%E2%80%94+tidb-management-service%234+%28round-trip+test%29.-",
        ),
    ],
)
def test_cross_form_equality(cw, api_title, link_slug):
    """Every (api_title, link_slug) pair from the issue's 16 broken pages
    must normalize to the same canonical form."""
    assert cw.normalize_slug(api_title) == cw.normalize_slug(link_slug)


# ---------------------------------------------------------------------------
# fetch_known_wiki_pages -- pagination
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stand-in for the object returned by urllib.request.urlopen.

    Real urlopen returns an HTTPResponse that supports `with` and `.read()`;
    the script does `with urlopen(...) as r: data = json.loads(r.read())`.
    Our stub must satisfy that shape.
    """

    def __init__(self, payload):
        self._buf = io.StringIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._buf.close()

    def read(self):
        return self._buf.getvalue()


class _UrlOpenRecorder:
    """Stub `urllib.request.urlopen` that replays a sequence of JSON
    payloads and records the URLs requested."""

    def __init__(self, pages):
        self._pages = pages
        self.requested_urls = []
        self._idx = 0

    def __call__(self, url, timeout=0):
        self.requested_urls.append(url)
        assert self._idx < len(
            self._pages
        ), f"unexpected extra fetch at idx={self._idx}, url={url}"
        payload = json.dumps(self._pages[self._idx])
        self._idx += 1
        return _FakeResponse(payload)


def test_fetch_known_wiki_pages_paginates(cw, monkeypatch):
    """Walk `?page=N&limit=50` until a short page terminates the loop."""
    # Page 1 + page 2 are both FULL (50 entries each), forcing a page=3 fetch.
    page1 = [{"title": f"Page-{i}"} for i in range(50)]
    page2 = [{"title": f"Page-{i}"} for i in range(50, 100)]
    page3 = [{"title": f"Page-{i}"} for i in range(100, 115)]  # 15 < 50

    recorder = _UrlOpenRecorder([page1, page2, page3])
    monkeypatch.setattr(cw.urllib.request, "urlopen", recorder)

    seen = cw.fetch_known_wiki_pages("http://gitea.ai", "owner", "repo")

    assert seen == {f"page-{i}" for i in range(115)}
    assert recorder.requested_urls == [
        "http://gitea.ai/api/v1/repos/owner/repo/wiki/pages?page=1&limit=50",
        "http://gitea.ai/api/v1/repos/owner/repo/wiki/pages?page=2&limit=50",
        "http://gitea.ai/api/v1/repos/owner/repo/wiki/pages?page=3&limit=50",
    ]


def test_fetch_known_wiki_pages_short_page_terminates(cw, monkeypatch):
    """A short final page (< page_size) terminates pagination without
    fetching an empty page=3 round-trip."""
    page1 = [{"title": f"Page-{i}"} for i in range(50)]
    page2 = [{"title": f"Page-{i}"} for i in range(50, 65)]  # 15 < 50

    recorder = _UrlOpenRecorder([page1, page2])
    monkeypatch.setattr(cw.urllib.request, "urlopen", recorder)

    seen = cw.fetch_known_wiki_pages("http://gitea.ai", "owner", "repo")

    assert seen == {f"page-{i}" for i in range(65)}
    assert recorder.requested_urls == [
        "http://gitea.ai/api/v1/repos/owner/repo/wiki/pages?page=1&limit=50",
        "http://gitea.ai/api/v1/repos/owner/repo/wiki/pages?page=2&limit=50",
    ]


def test_fetch_known_wiki_pages_single_short_page(cw, monkeypatch):
    """A wiki that fits in one short page makes a single fetch."""
    page1 = [{"title": "Home"}, {"title": "Foo Bar"}]
    recorder = _UrlOpenRecorder([page1])
    monkeypatch.setattr(cw.urllib.request, "urlopen", recorder)

    seen = cw.fetch_known_wiki_pages("http://gitea.ai", "o", "r")

    assert seen == {"home", "foo-bar"}
    assert recorder.requested_urls == [
        "http://gitea.ai/api/v1/repos/o/r/wiki/pages?page=1&limit=50",
    ]


def test_fetch_known_wiki_pages_404_returns_empty(cw, monkeypatch):
    """A 404 means the wiki is disabled or the repo has no wiki pages;
    must short-circuit to an empty set, not raise."""

    def _raise_404(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.StringIO(""))

    monkeypatch.setattr(cw.urllib.request, "urlopen", _raise_404)

    seen = cw.fetch_known_wiki_pages("http://gitea.ai", "o", "r")
    assert seen == set()
