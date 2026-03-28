"""
OSINT Pipeline — GitHub Connector

Two ingestion modes running concurrently:
  1. GRAPHQL SEARCH: Discovers trending/popular repos via GitHub's search API,
     then fetches recent issues for high-activity repos. Cursor-based pagination.
  2. REST EVENTS STREAM: Polls the public events endpoint with ETag-based
     conditional requests to avoid wasting rate limit on unchanged data.

Key patterns introduced by this connector:
  - PAT token rotation: pool of tokens with rate-limit-aware switching
  - Rate limit tracking: reads x-ratelimit-* headers after every request

Authentication: Personal Access Token(s) via GITHUB_TOKENS env var.
Unauthenticated mode works but is limited to 60 req/hr.

Rate limits (authenticated):
  - REST: 5,000 requests/hour per token
  - GraphQL: 5,000 points/hour per token
  - Search: 30 requests/minute (regardless of token count)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
from connectors.kafka_publisher import KafkaPublisher, wait_for_bus
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class GitHubConfig:
    """All tunables in one place. Override via env vars in production."""

    # API endpoints
    GRAPHQL_URL = "https://api.github.com/graphql"
    REST_BASE = "https://api.github.com"

    # PAT tokens (loaded from env)
    TOKENS: list[str] = []

    # Polling intervals (seconds)
    SEARCH_POLL_INTERVAL = 600    # 10 minutes between trending searches
    EVENTS_POLL_INTERVAL = 60     # 1 minute between event polls
    SEARCH_REQUEST_DELAY = 2.5    # seconds between search requests (30/min limit)

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "tech.github.events"

    # Search parameters
    TRENDING_MIN_STARS = 50
    TRENDING_LOOKBACK_DAYS = 7
    ISSUES_MIN_COMMENTS = 5        # Only fetch issues from repos with decent activity
    MAX_REPOS_PER_SEARCH = 100
    MAX_ISSUES_PER_REPO = 20

    # Rate limit safety
    RATE_LIMIT_BUFFER = 200        # Switch tokens when remaining drops below this


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("github-connector")


# ---------------------------------------------------------------------------
# Token Pool with Rate-Limit-Aware Rotation
# ---------------------------------------------------------------------------

class TokenPool:
    """Manages a pool of GitHub PATs with rate-limit tracking.

    Rotates tokens round-robin, skipping any that are near-exhausted.
    When all tokens are exhausted, sleeps until the earliest reset time.
    """

    def __init__(self, tokens: list[str]):
        self._tokens = tokens if tokens else [None]  # None = unauthenticated
        self._index = 0
        # Track rate limit state per token
        self._remaining: dict[int, int] = {i: 5000 for i in range(len(self._tokens))}
        self._reset_at: dict[int, float] = {i: 0.0 for i in range(len(self._tokens))}

    @property
    def current_token(self) -> Optional[str]:
        return self._tokens[self._index]

    def get_headers(self) -> dict[str, str]:
        """Return auth headers for the current token."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "OSINTPipeline/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.current_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def update_limits(self, resp_headers: dict):
        """Update rate limit tracking from response headers."""
        remaining = resp_headers.get("x-ratelimit-remaining")
        reset_at = resp_headers.get("x-ratelimit-reset")

        if remaining is not None:
            self._remaining[self._index] = int(remaining)
        if reset_at is not None:
            self._reset_at[self._index] = float(reset_at)

    def rotate(self):
        """Switch to the next available token."""
        if len(self._tokens) <= 1:
            return

        start = self._index
        for _ in range(len(self._tokens)):
            self._index = (self._index + 1) % len(self._tokens)
            if self._remaining[self._index] > GitHubConfig.RATE_LIMIT_BUFFER:
                log.info(
                    "Rotated to token %d (remaining: %d)",
                    self._index, self._remaining[self._index],
                )
                return

        # All tokens near-exhausted, stay on current
        self._index = start

    async def wait_if_exhausted(self):
        """If current token is near-exhausted, try rotating. If all exhausted, sleep."""
        if self._remaining[self._index] > GitHubConfig.RATE_LIMIT_BUFFER:
            return

        self.rotate()

        if self._remaining[self._index] > GitHubConfig.RATE_LIMIT_BUFFER:
            return

        # All tokens exhausted — find earliest reset
        earliest_reset = min(self._reset_at.values())
        wait_seconds = max(0, earliest_reset - time.time()) + 5  # 5s buffer
        if wait_seconds > 0:
            log.warning(
                "All tokens near-exhausted. Sleeping %.0fs until reset.", wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
            # Reset counters optimistically
            for i in range(len(self._tokens)):
                if self._reset_at[i] <= time.time():
                    self._remaining[i] = 5000


# ---------------------------------------------------------------------------
# GitHub → OsintDocument Converters
# ---------------------------------------------------------------------------

def repo_to_document(repo: dict) -> Optional[OsintDocument]:
    """Convert a GitHub repository (from GraphQL or REST) to OsintDocument."""
    # Handle both GraphQL (nameWithOwner) and REST (full_name) field names
    name = repo.get("nameWithOwner") or repo.get("full_name", "")
    description = repo.get("description") or ""

    if not name:
        return None

    content_text = f"{name}\n{description}".strip()

    # Star count
    stars = repo.get("stargazerCount") or repo.get("stargazers_count", 0)
    forks = repo.get("forkCount") or repo.get("forks_count", 0)

    # Language
    lang = repo.get("primaryLanguage")
    if isinstance(lang, dict):
        lang = lang.get("name")
    elif not isinstance(lang, str):
        lang = repo.get("language")

    # Topics — GraphQL nests under repositoryTopics.nodes, REST uses flat list
    topics = []
    topic_nodes = repo.get("repositoryTopics")
    if isinstance(topic_nodes, dict):
        for node in topic_nodes.get("nodes", []):
            topic_name = node.get("topic", {}).get("name")
            if topic_name:
                topics.append(topic_name)
    if not topics and isinstance(repo.get("topics"), list):
        topics = repo["topics"]

    # Created/updated timestamps
    created_at = repo.get("createdAt") or repo.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Open issues count
    open_issues = repo.get("issues", {})
    if isinstance(open_issues, dict):
        open_issues = open_issues.get("totalCount", 0)
    else:
        open_issues = repo.get("open_issues_count", 0)

    # Watchers
    watchers = repo.get("watchers", {})
    if isinstance(watchers, dict):
        watchers = watchers.get("totalCount", 0)
    else:
        watchers = repo.get("watchers_count", 0)

    url = repo.get("url") or repo.get("html_url", f"https://github.com/{name}")

    doc = OsintDocument(
        source=SourcePlatform.GITHUB,
        source_id=name,
        content_type=ContentType.REPOSITORY,
        title=name,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=name,
        quality_signals=QualitySignals(
            score=float(stars),
            engagement_count=forks + watchers,
        ),
        metadata={
            "stars": stars,
            "forks": forks,
            "open_issues": open_issues,
            "watchers": watchers,
            "language": lang,
            "topics": topics,
            "pushed_at": repo.get("pushedAt") or repo.get("pushed_at"),
        },
    )
    doc.compute_dedup_hash()
    return doc


def issue_to_document(issue: dict, repo_name: str) -> Optional[OsintDocument]:
    """Convert a GitHub issue (from GraphQL) to OsintDocument."""
    title = issue.get("title", "")
    body = issue.get("body", "")
    content_text = f"{title}\n{body}".strip() if title and body else (title or body)

    if not content_text:
        return None

    created_at = issue.get("createdAt", "")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    comment_count = issue.get("comments", {}).get("totalCount", 0)
    reaction_count = issue.get("reactions", {}).get("totalCount", 0)

    labels = [
        n.get("name") for n in issue.get("labels", {}).get("nodes", [])
        if n.get("name")
    ]

    author = issue.get("author", {})
    author_login = author.get("login", "unknown") if author else "unknown"

    doc = OsintDocument(
        source=SourcePlatform.GITHUB,
        source_id=f"{repo_name}#{issue.get('number', '')}",
        content_type=ContentType.ISSUE,
        title=title or None,
        content_text=content_text,
        url=issue.get("url", f"https://github.com/{repo_name}/issues"),
        created_at=created_at,
        entity_id=repo_name,
        quality_signals=QualitySignals(
            score=float(reaction_count),
            engagement_count=comment_count,
        ),
        metadata={
            "state": issue.get("state", "").lower(),
            "author": author_login,
            "labels": labels,
            "repo": repo_name,
        },
    )
    doc.compute_dedup_hash()
    return doc


def event_to_document(event: dict) -> Optional[OsintDocument]:
    """Convert a GitHub public event to OsintDocument."""
    event_type = event.get("type", "")
    repo_name = event.get("repo", {}).get("name", "")
    actor = event.get("actor", {}).get("login", "unknown")
    event_id = event.get("id", "")

    if not repo_name or not event_id:
        return None

    payload = event.get("payload", {})

    # Build content text from event type + payload
    content_parts = [f"{event_type}: {repo_name} by {actor}"]
    if event_type == "WatchEvent":
        content_parts.append(f"starred {repo_name}")
    elif event_type == "ForkEvent":
        forkee = payload.get("forkee", {}).get("full_name", "")
        content_parts.append(f"forked to {forkee}")
    elif event_type == "IssuesEvent":
        action = payload.get("action", "")
        issue_title = payload.get("issue", {}).get("title", "")
        content_parts.append(f"{action} issue: {issue_title}")
    elif event_type == "PushEvent":
        size = payload.get("size", 0)
        content_parts.append(f"{size} commits pushed")
    elif event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref", "")
        content_parts.append(f"created {ref_type} {ref}".strip())
    elif event_type == "ReleaseEvent":
        release = payload.get("release", {})
        tag = release.get("tag_name", "")
        content_parts.append(f"released {tag}")
    elif event_type == "PullRequestEvent":
        action = payload.get("action", "")
        pr_title = payload.get("pull_request", {}).get("title", "")
        content_parts.append(f"{action} PR: {pr_title}")

    content_text = " | ".join(content_parts)

    created_at = event.get("created_at", "")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.GITHUB,
        source_id=event_id,
        content_type=ContentType.REPOSITORY,
        title=f"{event_type} on {repo_name}",
        content_text=content_text,
        url=f"https://github.com/{repo_name}",
        created_at=created_at,
        entity_id=repo_name,
        quality_signals=QualitySignals(),
        metadata={
            "event_type": event_type,
            "actor": actor,
            "repo": repo_name,
            "payload_action": payload.get("action"),
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# GraphQL Search: Trending Repos + Issues
# ---------------------------------------------------------------------------

TRENDING_REPOS_QUERY = """
query TrendingRepos($queryString: String!, $cursor: String) {
  search(query: $queryString, type: REPOSITORY, first: 50, after: $cursor) {
    repositoryCount
    pageInfo { endCursor hasNextPage }
    nodes {
      ... on Repository {
        nameWithOwner
        description
        url
        stargazerCount
        forkCount
        primaryLanguage { name }
        repositoryTopics(first: 10) {
          nodes { topic { name } }
        }
        watchers { totalCount }
        issues(states: OPEN) { totalCount }
        createdAt
        updatedAt
        pushedAt
      }
    }
  }
}
"""

REPO_ISSUES_QUERY = """
query RepoIssues($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { endCursor hasNextPage }
      nodes {
        number
        title
        body
        url
        state
        createdAt
        author { login }
        labels(first: 5) { nodes { name } }
        comments { totalCount }
        reactions { totalCount }
      }
    }
  }
}
"""


class GraphQLSearcher:
    """Discovers trending repos and fetches their recent issues via GraphQL."""

    def __init__(self, token_pool: TokenPool):
        self._pool = token_pool
        self._seen_repos: set[str] = set()

    async def search_trending(self, session: aiohttp.ClientSession) -> list[dict]:
        """Search for trending repos created in the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=GitHubConfig.TRENDING_LOOKBACK_DAYS
        )
        query_string = (
            f"stars:>{GitHubConfig.TRENDING_MIN_STARS} "
            f"created:>{cutoff.strftime('%Y-%m-%d')}"
        )

        repos = []
        cursor = None
        pages = 0
        max_pages = GitHubConfig.MAX_REPOS_PER_SEARCH // 50 + 1

        while pages < max_pages:
            await self._pool.wait_if_exhausted()

            variables = {"queryString": query_string}
            if cursor:
                variables["cursor"] = cursor

            data = await self._graphql_request(session, TRENDING_REPOS_QUERY, variables)
            if not data:
                break

            search = data.get("data", {}).get("search", {})
            nodes = search.get("nodes", [])
            repos.extend(nodes)

            page_info = search.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            pages += 1

            await asyncio.sleep(GitHubConfig.SEARCH_REQUEST_DELAY)

        log.info("GraphQL search found %d trending repos", len(repos))
        return repos

    async def fetch_issues(
        self, session: aiohttp.ClientSession, repo_name: str,
    ) -> list[dict]:
        """Fetch recent issues for a repository."""
        parts = repo_name.split("/")
        if len(parts) != 2:
            return []
        owner, name = parts

        await self._pool.wait_if_exhausted()

        variables = {"owner": owner, "name": name}
        data = await self._graphql_request(session, REPO_ISSUES_QUERY, variables)
        if not data:
            return []

        repo_data = data.get("data", {}).get("repository")
        if not repo_data:
            return []

        issues = repo_data.get("issues", {}).get("nodes", [])
        return issues[:GitHubConfig.MAX_ISSUES_PER_REPO]

    async def _graphql_request(
        self,
        session: aiohttp.ClientSession,
        query: str,
        variables: dict,
    ) -> Optional[dict]:
        """Execute a GraphQL request with token rotation."""
        payload = json.dumps({"query": query, "variables": variables})

        try:
            async with session.post(
                GitHubConfig.GRAPHQL_URL,
                headers=self._pool.get_headers(),
                data=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                self._pool.update_limits(resp.headers)

                if resp.status == 401:
                    log.warning("Token unauthorized — rotating")
                    self._pool.rotate()
                    return None
                if resp.status == 403:
                    log.warning("Rate limited (403) — rotating token")
                    self._pool.rotate()
                    await self._pool.wait_if_exhausted()
                    return None
                if resp.status != 200:
                    log.warning("GraphQL returned %d", resp.status)
                    return None

                data = await resp.json()
                if data.get("errors"):
                    log.warning("GraphQL errors: %s", data["errors"])
                return data

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("GraphQL request failed: %s", e)
            return None


# ---------------------------------------------------------------------------
# REST Events Stream
# ---------------------------------------------------------------------------

class EventsStream:
    """Polls the GitHub public events API with ETag-based conditional requests."""

    def __init__(self, token_pool: TokenPool):
        self._pool = token_pool
        self._etag: Optional[str] = None
        self._seen_event_ids: set[str] = set()

    async def poll(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch new public events since last poll."""
        await self._pool.wait_if_exhausted()

        headers = self._pool.get_headers()
        if self._etag:
            headers["If-None-Match"] = self._etag

        url = f"{GitHubConfig.REST_BASE}/events"

        try:
            async with session.get(
                url,
                headers=headers,
                params={"per_page": "100"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                self._pool.update_limits(resp.headers)

                if resp.status == 304:
                    # No new events — doesn't count against rate limit
                    return []
                if resp.status == 403:
                    self._pool.rotate()
                    return []
                if resp.status != 200:
                    log.warning("Events API returned %d", resp.status)
                    return []

                self._etag = resp.headers.get("ETag")
                events = await resp.json()

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Events poll failed: %s", e)
            return []

        # Filter to only new events
        new_events = []
        for event in events:
            eid = event.get("id", "")
            if eid and eid not in self._seen_event_ids:
                self._seen_event_ids.add(eid)
                new_events.append(event)

        # Prevent unbounded memory growth
        if len(self._seen_event_ids) > 10000:
            self._seen_event_ids = set(list(self._seen_event_ids)[-5000:])

        return new_events


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class GitHubConnector:
    """Orchestrates GitHub data ingestion with two concurrent modes."""

    def __init__(self, publisher: KafkaPublisher, token_pool: TokenPool):
        self._publisher = publisher
        self._pool = token_pool
        self._searcher = GraphQLSearcher(token_pool)
        self._events = EventsStream(token_pool)

    async def run(self, session: aiohttp.ClientSession):
        """Run both ingestion modes concurrently."""
        tasks = [
            asyncio.create_task(self._search_loop(session)),
            asyncio.create_task(self._events_loop(session)),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _search_loop(self, session: aiohttp.ClientSession):
        """Periodically search for trending repos and fetch their issues."""
        log.info(
            "GraphQL search loop started — poll every %ds",
            GitHubConfig.SEARCH_POLL_INTERVAL,
        )

        while True:
            try:
                repos = await self._searcher.search_trending(session)
                published = 0

                for repo in repos:
                    doc = repo_to_document(repo)
                    if doc:
                        self._publisher.publish(
                            GitHubConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                    # Fetch issues for repos with significant activity
                    repo_name = repo.get("nameWithOwner", "")
                    open_issues = repo.get("issues", {})
                    if isinstance(open_issues, dict):
                        open_issues = open_issues.get("totalCount", 0)
                    else:
                        open_issues = 0

                    if open_issues >= GitHubConfig.ISSUES_MIN_COMMENTS and repo_name:
                        issues = await self._searcher.fetch_issues(session, repo_name)
                        for issue in issues:
                            idoc = issue_to_document(issue, repo_name)
                            if idoc:
                                self._publisher.publish(
                                    GitHubConfig.KAFKA_TOPIC,
                                    idoc.to_kafka_key(),
                                    idoc.to_kafka_value(),
                                )
                                published += 1

                        await asyncio.sleep(GitHubConfig.SEARCH_REQUEST_DELAY)

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info(
                        "Search loop published %d documents | Kafka: %s",
                        published, self._publisher.stats,
                    )

            except Exception as e:
                log.error("Search loop error: %s", e, exc_info=True)

            await asyncio.sleep(GitHubConfig.SEARCH_POLL_INTERVAL)

    async def _events_loop(self, session: aiohttp.ClientSession):
        """Poll the public events stream."""
        log.info(
            "Events stream started — poll every %ds",
            GitHubConfig.EVENTS_POLL_INTERVAL,
        )

        while True:
            try:
                events = await self._events.poll(session)
                published = 0

                for event in events:
                    doc = event_to_document(event)
                    if doc:
                        self._publisher.publish(
                            GitHubConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Events stream published %d documents", published)

            except Exception as e:
                log.error("Events stream error: %s", e, exc_info=True)

            await asyncio.sleep(GitHubConfig.EVENTS_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    tokens = os.environ.get("GITHUB_TOKENS", "")
    if tokens:
        GitHubConfig.TOKENS = [t.strip() for t in tokens.split(",") if t.strip()]

    # Also accept single-token var
    single = os.environ.get("GITHUB_TOKEN", "")
    if single and single not in GitHubConfig.TOKENS:
        GitHubConfig.TOKENS.append(single.strip())

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        GitHubConfig.KAFKA_BOOTSTRAP = bootstrap

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(GitHubConfig, "SEARCH_POLL_INTERVAL", "EVENTS_POLL_INTERVAL")


async def main():
    load_config_from_env()

    if not GitHubConfig.TOKENS:
        log.warning(
            "No GitHub tokens configured. Running unauthenticated (60 req/hr). "
            "Set GITHUB_TOKENS or GITHUB_TOKEN env var for higher limits."
        )

    await wait_for_kafka(GitHubConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(GitHubConfig.KAFKA_BOOTSTRAP, client_id="github-connector")
    token_pool = TokenPool(GitHubConfig.TOKENS)
    connector = GitHubConnector(publisher, token_pool)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=20),
    ) as session:
        log.info("=" * 60)
        log.info("GitHub Connector running")
        log.info("  Tokens:       %d configured", len(GitHubConfig.TOKENS))
        log.info("  Search:       trending repos (>%d stars, last %d days)",
                 GitHubConfig.TRENDING_MIN_STARS, GitHubConfig.TRENDING_LOOKBACK_DAYS)
        log.info("  Events:       public event stream (poll every %ds)",
                 GitHubConfig.EVENTS_POLL_INTERVAL)
        log.info("  Kafka topic:  %s", GitHubConfig.KAFKA_TOPIC)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("GitHub Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
