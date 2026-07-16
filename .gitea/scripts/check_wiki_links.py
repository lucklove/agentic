# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "scrapy>=2.11",
# ]
# ///
"""
Wiki link checker for Gitea wikis.

Crawls a wiki starting from a root URL, follows every <a href> link
(including those tagged rel="nofollow" -- which lychee silently drops
at the HTML-extract stage, see lycheeverse/lychee#548), and reports:

  * wiki page links that point to a page not in the Gitea wiki index
  * any URL the spider actually visits that returns HTTP 4xx / 5xx

Designed to be called from a Gitea Actions workflow (see
.gitea/workflows/wiki-link-check.yml). Exit code 0 means clean, 2 means
broken links were found, 3 means a setup error prevented the run.

Usage:
    uv run check_wiki_links.py <start_url> [options]

Examples:
    # Check the agentic wiki from its root:
    uv run check_wiki_links.py http://gitea.ai/autonomous/agentic/wiki

    # Check just one skill page (and any wiki page it links to):
    uv run check_wiki_links.py \
        http://gitea.ai/autonomous/agentic/wiki/Issue-Triage \
        --max-depth 2

    # Only audit wiki-internal links, skip HEAD-checks of external links:
    uv run check_wiki_links.py http://gitea.ai/autonomous/agentic/wiki \
        --no-external
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

import scrapy
from scrapy import signals
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider, Rule

LOG = logging.getLogger("check_wiki_links")


# ---------------------------------------------------------------------------
# Slug / URL helpers
# ---------------------------------------------------------------------------


def normalize_slug(raw: str) -> str:
    """Canonical form of a wiki page name for equality checks.

    Gitea wiki URLs use hyphens (Writing-Plans), API titles use spaces
    (Writing Plans), and raw-view URLs append .md. Normalize to
    lowercase hyphen form so all three compare equal.
    """
    s = raw.strip().lower().replace(" ", "-")
    if s.endswith(".md"):
        s = s[:-3]
    return s


def wiki_page_name_from_url(url: str) -> str | None:
    """Return the normalized slug for a wiki page URL, or None if not a wiki link."""
    try:
        path = urllib.parse.urlparse(url).path
    except Exception:
        return None
    marker = "/wiki/"
    idx = path.find(marker)
    if idx < 0:
        return None
    slug = path[idx + len(marker) :].split("?")[0].split("#")[0]
    if not slug:
        return None
    return normalize_slug(slug)


def split_owner_repo(start_url: str) -> tuple[str, str, str]:
    """Pull (api_base, owner, repo) out of a wiki start URL.

    Accepts URLs like http://host/owner/repo/wiki/Page; the /wiki/Page
    tail is optional and ignored -- we only need the first two path
    segments to call the wiki-pages API.
    """
    parsed = urllib.parse.urlparse(start_url)
    api_base = f"{parsed.scheme}://{parsed.netloc}"
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(
            f"start_url {start_url!r} does not contain an /owner/repo path"
        )
    return api_base, parts[0], parts[1]


def split_owner_repo_from_url(url: str) -> tuple[str, str, str] | None:
    """Pull (api_base, owner, repo) out of any /wiki/ URL.

    Returns None for URLs that don't look like a wiki page (no ``/wiki/``
    marker, or no ``/owner/repo`` prefix before the marker). This lets
    callers distinguish "definitely a wiki link in some repo" from
    "link shape is unrecognised -- can't verify, just HEAD-check it".
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    marker = "/wiki/"
    idx = parsed.path.find(marker)
    if idx < 0:
        return None
    prefix = parsed.path[:idx]
    parts = prefix.strip("/").split("/")
    if len(parts) < 2:
        return None
    api_base = f"{parsed.scheme}://{parsed.netloc}"
    return api_base, parts[0], parts[1]


# ---------------------------------------------------------------------------
# Wiki API
# ---------------------------------------------------------------------------


def fetch_known_wiki_pages(api_base: str, owner: str, repo: str) -> set[str]:
    """Pull the canonical wiki page list from the Gitea API, normalized."""
    url = f"{api_base}/api/v1/repos/{owner}/{repo}/wiki/pages"
    LOG.info("fetching wiki page index from %s", url)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 404 = wiki disabled / empty repo; the caller decides whether that is fatal.
        if e.code == 404:
            LOG.warning("wiki pages API returned 404 -- wiki may be empty or disabled")
            return set()
        raise
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach wiki API at {url}: {e}") from e
    return {normalize_slug(p["title"]) for p in data}


class WikiIndexCache:
    """Lazy, per-repo cache of known wiki page slugs.

    Wikis on the same host often cross-link across repos (e.g. the
    ``ng-onboarding`` wiki points at skill pages in ``nutshell-skills``).
    We can't pre-fetch every repo we might encounter, so this cache
    fetches each repo's wiki index on first request and remembers the
    result for the rest of the run.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], set[str]] = {}

    def get(self, api_base: str, owner: str, repo: str) -> set[str]:
        """Return known page slugs for (api_base, owner, repo), fetching once.

        Propagates the underlying exception on the first call so the
        caller can decide whether a fetch failure is fatal (start repo)
        or recoverable (a cross-repo link whose index can't be reached).
        """
        key = (api_base, owner, repo)
        if key not in self._cache:
            self._cache[key] = fetch_known_wiki_pages(api_base, owner, repo)
        return self._cache[key]

    def repos(self) -> list[tuple[str, str, str, int]]:
        """List every repo whose index has been fetched, with page count.

        Returned in insertion order so the report stays stable across
        runs that hit the same repos.
        """
        return [
            (api_base, owner, repo, len(pages))
            for (api_base, owner, repo), pages in self._cache.items()
        ]


# ---------------------------------------------------------------------------
# Spider
# ---------------------------------------------------------------------------


# Patterns we never want to crawl -- admin views, source view, static assets.
# These run inside Scrapy's LinkExtractor and are PCRE-compatible.
_DENY_PATTERNS = (
    r"/_fragments",
    r"/api/v\d+/",
    r"\.(?:json|xml|toml|yaml|yml|pdf|zip|tar|gz|bundle|png|jpg|jpeg|gif|svg|ico|css|js)(\?|#|$)",
    r"/archive/",
    r"/raw/",
    r"/_images/",
    r"/attachments/",
    r"/commits?/",
    r"/issues/",
    r"/pulls/",
    r"/actions/",
    r"\?ref=",
    # Admin/revision views, not real wiki pages.
    r"\?action=_pages",
    r"\?action=_revision",
    # Bare .md raw views (e.g. .../wiki/Home.md) are source view, not page.
    r"/wiki/[^/]+\.md$",
)


@dataclass
class CrawlStats:
    """Aggregated crawl results, ready to render to JSON / Markdown."""

    start_url: str
    known_pages: list[str] = field(default_factory=list)
    visited_ok: list[str] = field(default_factory=list)
    broken_http: list[dict] = field(default_factory=list)
    broken_wiki_pages: list[dict] = field(default_factory=list)
    external_links_referenced: list[str] = field(default_factory=list)
    # Repos whose wiki index was fetched during the run. Each entry is
    # ``{"api_base": ..., "owner": ..., "repo": ..., "known_pages": N}``.
    # Useful for spotting when a crawl touches multiple repos (cross-repo
    # wiki links) so the report doesn't look confusingly sparse.
    repos_scanned: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    broken_http_count: int = 0
    broken_wiki_count: int = 0

    def finalize(self) -> None:
        self.finished_at = time.time()
        self.visited_ok.sort()
        self.broken_http.sort(key=lambda x: x["url"])
        self.broken_wiki_pages.sort(key=lambda x: x["page"])
        self.external_links_referenced.sort()
        self.known_pages.sort()
        self.repos_scanned.sort(key=lambda x: (x["owner"], x["repo"]))
        self.broken_http_count = len(self.broken_http)
        self.broken_wiki_count = len(self.broken_wiki_pages)

    def is_clean(self) -> bool:
        # Compute off the lists, not the *_count fields -- those are only set
        # by finalize(), which fires in spider_closed(). If Scrapy aborts
        # before that (Ctrl-C, OOM, ERR_CONCURRENT_REQUESTS crash, ...),
        # the lists are populated but the counts stay 0 and the script
        # would incorrectly report success.
        return not self.broken_http and not self.broken_wiki_pages

    def to_dict(self) -> dict:
        return {
            "start_url": self.start_url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": (
                None
                if self.finished_at is None
                else round(self.finished_at - self.started_at, 2)
            ),
            "known_pages": self.known_pages,
            "ok_pages": self.visited_ok,
            "broken_http": self.broken_http,
            "broken_wiki_pages": self.broken_wiki_pages,
            "external_links_referenced": self.external_links_referenced,
            "repos_scanned": self.repos_scanned,
            "counts": {
                "known_pages": len(self.known_pages),
                "ok_pages": len(self.visited_ok),
                "broken_http": self.broken_http_count,
                "broken_wiki_pages": self.broken_wiki_count,
                "external_links_referenced": len(self.external_links_referenced),
                "repos_scanned": len(self.repos_scanned),
            },
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Wiki link check report")
        lines.append("")
        lines.append(f"- **start_url**: {self.start_url}")
        lines.append(f"- **known wiki pages**: {len(self.known_pages)}")
        lines.append(f"- **pages visited OK**: {len(self.visited_ok)}")
        lines.append(
            f"- **external links referenced**: {len(self.external_links_referenced)}"
        )
        if self.repos_scanned:
            repos = ", ".join(
                f"{r['owner']}/{r['repo']} ({r['known_pages']})"
                for r in self.repos_scanned
            )
            lines.append(f"- **repos scanned**: {repos}")
        if self.finished_at is not None:
            lines.append(
                f"- **duration**: {round(self.finished_at - self.started_at, 2)}s"
            )
        lines.append("")

        if self.is_clean():
            lines.append("**No broken links found.** ")
        else:
            lines.append("## Broken wiki page links")
            lines.append("")
            if self.broken_wiki_pages:
                lines.append("| Page | Referenced from |")
                lines.append("|------|-----------------|")
                for entry in self.broken_wiki_pages:
                    refs = "<br>".join(entry["referenced_from"])
                    lines.append(f"| `{entry['page']}` | {refs} |")
            else:
                lines.append("_(none)_")
            lines.append("")
            lines.append("## Broken HTTP responses")
            lines.append("")
            if self.broken_http:
                lines.append("| URL | Status | Found in |")
                lines.append("|-----|--------|----------|")
                for entry in self.broken_http:
                    lines.append(
                        f"| {entry['url']} | {entry['status']} | "
                        f"{entry.get('found_in') or '_(unknown)_'} |"
                    )
            else:
                lines.append("_(none)_")
            lines.append("")
        return "\n".join(lines)

    def print_broken(self, stream=None) -> None:
        """Print a human-readable broken-link summary to ``stream``.

        Uses Gitea Actions' ``::group::`` markers so CI run logs render
        each section as a collapsible block. Outside CI the markers are
        just harmless text on stderr. Always prints -- callers can use
        :meth:`is_clean` to decide whether to skip the call entirely.
        """
        if stream is None:
            stream = sys.stderr

        print(
            f"pages visited OK: {len(self.visited_ok)} | "
            f"external links referenced: {len(self.external_links_referenced)} | "
            f"known wiki pages: {len(self.known_pages)} | "
            f"repos scanned: {len(self.repos_scanned)}",
            file=stream,
        )
        if self.finished_at is not None:
            print(
                f"duration: {round(self.finished_at - self.started_at, 2)}s",
                file=stream,
            )
        print("", file=stream)

        if self.broken_wiki_pages:
            print(
                "::group::Broken wiki pages (referenced from -> target page)",
                file=stream,
            )
            for entry in self.broken_wiki_pages:
                page = entry.get("page", "?")
                for src in entry.get("referenced_from", []) or []:
                    print(f"  in  {src}", file=stream)
                    print(f"     -> wiki page '{page}' is missing", file=stream)
            print("::endgroup::", file=stream)
        else:
            print("No broken wiki pages found.", file=stream)

        print("", file=stream)

        if self.broken_http:
            print(
                "::group::Broken HTTP responses (referenced from -> URL, status)",
                file=stream,
            )
            for entry in self.broken_http:
                src = entry.get("found_in") or "_(unknown source)_"
                url = entry.get("url", "?")
                status = entry.get("status", "?")
                err = entry.get("error")
                tail = f"  (transport error: {err})" if err and status == -1 else ""
                print(f"  in  {src}", file=stream)
                print(f"     -> {url}  [HTTP {status}]{tail}", file=stream)
            print("::endgroup::", file=stream)
        else:
            print("No broken HTTP responses found.", file=stream)


class WikiLinkCheckSpider(CrawlSpider):
    """Crawl a wiki, classify broken links, dump a JSON summary on close."""

    name = "wiki-link-check"

    custom_settings = {
        # Treat 404/410 as ordinary responses so the callback sees them.
        "HTTPERROR_ALLOWED_CODES": [403, 404, 410, 500, 502, 503],
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 4,
        "DOWNLOAD_TIMEOUT": 20,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
        "LOG_LEVEL": "WARNING",
        "DEPTH_LIMIT": 6,
        "USER_AGENT": "wiki-link-checker/1.0 (+gitea-actions)",
    }

    rules = (
        # LinkExtractor ignores rel=nofollow by default -- exactly what we want,
        # because Gitea auto-tags wiki cross-links with rel=nofollow.
        Rule(
            LinkExtractor(allow=(r"/wiki(/|$)",), deny=_DENY_PATTERNS),
            callback="parse_page",
            follow=True,
        ),
    )

    def __init__(
        self,
        start_url: str,
        index_cache: WikiIndexCache,
        stats: CrawlStats,
        do_external_checks: bool,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.start_urls = [start_url]
        self.index_cache = index_cache
        self.stats = stats
        # Note: the attribute is `do_external_checks`, NOT `check_external`,
        # because `check_external` is also a method on this class.
        self.do_external_checks = do_external_checks
        self.start_host = urllib.parse.urlparse(start_url).netloc
        # LinkExtractor for non-crawl targets (we still HEAD-check them).
        self._head_extractor = LinkExtractor(
            allow_domains=[self.start_host],
            deny=_DENY_PATTERNS,
            unique=True,
        )
        self._broken_http: dict[str, dict] = {}
        self._broken_wiki: dict[str, set[str]] = defaultdict(set)
        self._visited_ok: set[str] = set()
        self._ext_referenced: dict[str, set[str]] = defaultdict(set)
        self._headed: set[str] = set()

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.spider_closed, signal=signals.spider_closed)
        return spider

    def parse_page(self, response):
        status = response.status
        url = response.url
        referer = (
            response.request.headers.get("Referer", b"").decode("utf-8", "ignore")
            or None
        )

        if status >= 400:
            self._broken_http[url] = {
                "url": url,
                "status": status,
                "found_in": referer,
            }
            return

        self._visited_ok.add(url)

        # Only parse HTML-ish responses; binary/non-text would crash LinkExtractor.
        ctype = response.headers.get("Content-Type", b"").decode("utf-8", "ignore")
        if not (response.text and "html" in ctype.lower()):
            return

        for link in self._head_extractor.extract_links(response):
            target = link.url
            parsed = urllib.parse.urlparse(target)
            slug = wiki_page_name_from_url(target)

            # 1) Same-host wiki page link -- verify against the *target*
            #    repo's wiki index, not the start repo's. Wikis routinely
            #    cross-link across repos on the same host (e.g. an
            #    onboarding page linking to skill pages in a separate
            #    skill repo); checking only the start repo produces
            #    false-positive "missing" reports for every cross-repo
            #    page that exists in its own repo.
            if slug is not None and parsed.netloc == self.start_host:
                triple = split_owner_repo_from_url(target)
                if triple is not None:
                    api_base, owner, repo = triple
                    try:
                        known_for_target = self.index_cache.get(api_base, owner, repo)
                    except Exception as e:
                        # Surface the fetch failure but don't crash the
                        # crawl -- mark the slug as broken so it shows
                        # up in the report alongside HTTP errors.
                        LOG.warning(
                            "wiki index fetch failed for %s/%s/%s: %s",
                            api_base,
                            owner,
                            repo,
                            e,
                        )
                        self._broken_wiki[slug].add(url)
                        continue
                    if slug not in known_for_target:
                        self._broken_wiki[slug].add(url)
                        continue
                    # Found in target repo's index -- fall through.
                # else: /wiki/ URL with no owner/repo prefix. Treat as
                # "shape unrecognised" and fall through to branch 3 so
                # the HEAD check decides whether it exists.

            # 2) Skip anchors / mailto / javascript: -- not worth HEAD-checking.
            scheme = (parsed.scheme or "").lower()
            if scheme in ("", "mailto", "javascript", "tel"):
                continue

            # 3) Other links (code-browser, repo, external) -- HEAD-check them.
            if not self.do_external_checks:
                self._ext_referenced[target].add(url)
                continue
            if target in self._headed:
                continue
            self._headed.add(target)
            # Note: dedup above means `source_page` here is only the FIRST
            # wiki page that referenced this URL -- later references are
            # silently dropped from the per-URL attribution in the report.
            # Intentional: avoids N concurrent HEAD requests for one URL.
            yield scrapy.Request(
                target,
                method="HEAD",
                callback=self.check_external,
                errback=self.check_external_err,
                meta={"source_page": url},
                dont_filter=True,
            )

    def check_external(self, response):
        status = response.status
        url = response.url
        source = response.meta.get("source_page", None)
        if status >= 400:
            self._broken_http[url] = {
                "url": url,
                "status": status,
                "found_in": source,
            }
        else:
            self._ext_referenced[url].add(source)

    def check_external_err(self, failure):
        url = failure.request.url
        source = failure.request.meta.get("source_page", None)
        # Treat connection / DNS errors as broken-but-different from HTTP 404.
        self._broken_http[url] = {
            "url": url,
            "status": -1,
            "error": str(failure.value)[:200],
            "found_in": source,
        }

    def spider_closed(self, reason):
        LOG.info("spider closed: %s", reason)
        # Flush aggregated state into the shared stats object.
        self.stats.visited_ok = list(self._visited_ok)
        self.stats.broken_http = list(self._broken_http.values())
        self.stats.broken_wiki_pages = [
            {"page": slug, "referenced_from": sorted(srcs)}
            for slug, srcs in self._broken_wiki.items()
        ]
        self.stats.external_links_referenced = sorted(self._ext_referenced.keys())
        self.stats.repos_scanned = [
            {
                "api_base": api_base,
                "owner": owner,
                "repo": repo,
                "known_pages": n,
            }
            for (api_base, owner, repo, n) in self.index_cache.repos()
        ]
        self.stats.finalize()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_wiki_links",
        description=(
            "Crawl a Gitea wiki starting from a URL and report broken "
            "internal/external links. Designed for CI use -- exits 2 if "
            "any broken link is found."
        ),
    )
    p.add_argument(
        "start_url",
        help=(
            "Wiki URL to start crawling from. Any wiki page works; the "
            "crawler follows /wiki/ links on the same host. Example: "
            "http://gitea.ai/autonomous/agentic/wiki/Issue-Triage"
        ),
    )
    p.add_argument(
        "--max-depth",
        type=int,
        default=6,
        help="Maximum link-follow depth for the wiki crawl (default: 6).",
    )
    p.add_argument(
        "--no-external",
        action="store_true",
        help=(
            "Skip HEAD checks against non-wiki URLs (code-browser, "
            "external links). Only wiki-internal consistency is checked."
        ),
    )
    p.add_argument(
        "--json-output",
        default=None,
        help=(
            "Path to write the JSON report. Optional -- when omitted, the "
            "report is only printed to stderr. Useful for local runs that "
            "want a machine-readable artifact to diff against."
        ),
    )
    p.add_argument(
        "--md-output",
        default=None,
        help=(
            "Path to write the Markdown report. Optional -- when omitted, "
            "the report is only printed to stderr."
        ),
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce scrapy logging to ERROR; only INFO from this script.",
    )
    return p


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        api_base, owner, repo = split_owner_repo(args.start_url)
    except ValueError as e:
        LOG.error("%s", e)
        return 3

    # Pre-fetch the start repo so a totally unreachable wiki fails fast
    # with a clear error, rather than 5 minutes into the crawl. Cross-repo
    # wiki indices are fetched lazily by the spider on first encounter.
    index_cache = WikiIndexCache()
    try:
        known = index_cache.get(api_base, owner, repo)
    except Exception as e:
        LOG.error("wiki index fetch failed: %s", e)
        return 3

    LOG.info("known wiki pages: %d", len(known))

    stats = CrawlStats(start_url=args.start_url, known_pages=sorted(known))

    from scrapy.crawler import CrawlerProcess

    process = CrawlerProcess(
        settings={
            "HTTPERROR_ALLOWED_CODES": [403, 404, 410, 500, 502, 503],
            "ROBOTSTXT_OBEY": False,
            "CONCURRENT_REQUESTS": 4,
            "DOWNLOAD_TIMEOUT": 20,
            "RETRY_ENABLED": True,
            "RETRY_TIMES": 2,
            "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
            "LOG_LEVEL": "ERROR" if args.quiet else "WARNING",
            "DEPTH_LIMIT": args.max_depth,
            "USER_AGENT": "wiki-link-checker/1.0 (+gitea-actions)",
        }
    )
    process.crawl(
        WikiLinkCheckSpider,
        start_url=args.start_url,
        index_cache=index_cache,
        stats=stats,
        do_external_checks=not args.no_external,
    )
    process.start()

    # Optional report files -- opt-in, default is None. CI runs typically
    # don't need them; local users can pass --json-output / --md-output.
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(stats.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        LOG.info("wrote JSON report to %s", args.json_output)

    if args.md_output:
        with open(args.md_output, "w", encoding="utf-8") as f:
            f.write(stats.to_markdown())
            f.write("\n")
        LOG.info("wrote Markdown report to %s", args.md_output)

    # Always emit the human-readable broken-link summary to stderr. CI logs
    # capture it directly, and the ::group:: markers give collapsible
    # sections in Gitea Actions without any inline workflow-side parsing.
    stats.print_broken()

    if stats.is_clean():
        LOG.info("no broken links found")
        return 0
    LOG.warning(
        "%d broken wiki pages, %d broken HTTP responses",
        stats.broken_wiki_count,
        stats.broken_http_count,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
