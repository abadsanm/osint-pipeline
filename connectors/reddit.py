"""
OSINT Pipeline — Reddit Financial Subreddits Connector

Two ingestion modes (selected by configuration):

  1. OAUTH API (preferred): Uses Reddit OAuth with client credentials.
     Fetches new/hot posts and comments from configured subreddits.
     Requires client_id + client_secret from reddit.com/prefs/apps.

  2. JSON FALLBACK: Appends .json to old.reddit.com URLs.
     No auth needed but stricter rate limits and IP ban risk.
     Self-imposed 3s delay between requests.

Target subreddits (multi-pillar OSINT, ordered by category):
  Financial: wallstreetbets, stocks, investing, options, stockmarket,
             dividends, thetagang, ValueInvesting, SecurityAnalysis
  Crypto:    cryptocurrency, Bitcoin, ethereum, CryptoMarkets
  Tech:      technology, gadgets, Android, apple, Windows10,
             programming, webdev, startups, SaaS
  Consumer:  BuyItForLife, personalfinance, frugal

Rate limits:
  OAuth: 100 requests/minute
  JSON fallback: self-imposed 3s delay (~20 req/min)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
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

class RedditConfig:
    """All tunables in one place. Override via env vars in production."""

    # OAuth credentials
    CLIENT_ID: Optional[str] = None
    CLIENT_SECRET: Optional[str] = None
    USERNAME: Optional[str] = None
    PASSWORD: Optional[str] = None

    # API endpoints
    OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    OAUTH_BASE = "https://oauth.reddit.com"
    JSON_BASE = "https://old.reddit.com"

    # Subreddits to monitor
    SUBREDDITS: list[str] = [
        # Financial
        "wallstreetbets",
        "stocks",
        "investing",
        "options",
        "stockmarket",
        "dividends",
        "thetagang",
        "ValueInvesting",
        "SecurityAnalysis",
        # Crypto
        "cryptocurrency",
        "Bitcoin",
        "ethereum",
        "CryptoMarkets",
        # Semiconductors / Hardware
        "semiconductors",
        "chipdesign",
        "AMD_Stock",
        "NVDA_Stock",
        # Tech / Product
        "technology",
        "gadgets",
        "Android",
        "apple",
        "Windows10",
        "programming",
        "webdev",
        "startups",
        "SaaS",
        # Consumer
        "BuyItForLife",
        "personalfinance",
        "frugal",
    ]

    # Polling intervals
    POLL_INTERVAL = 120       # 2 minutes between full cycles
    REQUEST_DELAY_OAUTH = 0.7 # ~85 req/min (under 100/min limit)
    REQUEST_DELAY_JSON = 3.0  # Conservative for JSON fallback

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC_POSTS = "finance.reddit.posts"
    KAFKA_TOPIC_COMMENTS = "finance.reddit.comments"

    # Ingestion parameters
    POSTS_PER_SUBREDDIT = 25    # Posts to fetch per subreddit per cycle
    FETCH_COMMENTS = True       # Whether to fetch comments for hot posts
    COMMENTS_MIN_SCORE = 50     # Only fetch comments for posts above this score
    MAX_COMMENTS_PER_POST = 20  # Top-level comments to ingest per post

    # Ingestion mode
    MODE = "json"  # "oauth" or "json"

    USER_AGENT = "OSINTPipeline/1.0 (research; contact: mas-admin@assetboss.app)"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("reddit-connector")


# ---------------------------------------------------------------------------
# Reddit → OsintDocument Converters
# ---------------------------------------------------------------------------

def post_to_document(post: dict, subreddit: str) -> Optional[OsintDocument]:
    """Convert a Reddit post to OsintDocument."""
    # Handle both OAuth (data wrapper) and JSON formats
    data = post.get("data", post)

    post_id = data.get("id", "")
    title = data.get("title", "")
    selftext = data.get("selftext", "")
    content_text = f"{title}\n{selftext}".strip() if title and selftext else (title or selftext)

    if not content_text or not post_id:
        return None

    score = data.get("score", 0)
    num_comments = data.get("num_comments", 0)
    upvote_ratio = data.get("upvote_ratio")

    created_utc = data.get("created_utc")
    if isinstance(created_utc, (int, float)):
        created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    flair = data.get("link_flair_text")
    author = data.get("author", "[deleted]")
    is_self = data.get("is_self", True)
    permalink = data.get("permalink", "")

    doc = OsintDocument(
        source=SourcePlatform.REDDIT,
        source_id=post_id,
        content_type=ContentType.STORY,
        title=title or None,
        content_text=content_text,
        url=f"https://www.reddit.com{permalink}" if permalink else None,
        created_at=created_at,
        entity_id=f"r/{subreddit}",
        quality_signals=QualitySignals(
            score=float(score),
            engagement_count=num_comments,
        ),
        metadata={
            "subreddit": subreddit,
            "author": author,
            "flair": flair,
            "upvote_ratio": upvote_ratio,
            "is_self": is_self,
            "link_url": data.get("url") if not is_self else None,
        },
    )
    doc.compute_dedup_hash()
    return doc


def comment_to_document(
    comment: dict, subreddit: str, post_id: str,
) -> Optional[OsintDocument]:
    """Convert a Reddit comment to OsintDocument."""
    data = comment.get("data", comment)

    comment_id = data.get("id", "")
    body = data.get("body", "")

    if not body or not comment_id or body == "[deleted]" or body == "[removed]":
        return None

    score = data.get("score", 0)
    author = data.get("author", "[deleted]")

    created_utc = data.get("created_utc")
    if isinstance(created_utc, (int, float)):
        created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    permalink = data.get("permalink", "")

    doc = OsintDocument(
        source=SourcePlatform.REDDIT,
        source_id=comment_id,
        content_type=ContentType.COMMENT,
        title=None,
        content_text=body,
        url=f"https://www.reddit.com{permalink}" if permalink else None,
        created_at=created_at,
        parent_id=post_id,
        entity_id=f"r/{subreddit}",
        quality_signals=QualitySignals(
            score=float(score),
        ),
        metadata={
            "subreddit": subreddit,
            "author": author,
            "post_id": post_id,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# OAuth Token Manager
# ---------------------------------------------------------------------------

class RedditOAuth:
    """Manages Reddit OAuth tokens with automatic refresh."""

    def __init__(self, client_id: str, client_secret: str, username: str, password: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._token: Optional[str] = None
        self._expires_at: float = 0

    async def get_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Get a valid OAuth token, refreshing if expired."""
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        auth_str = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        try:
            async with session.post(
                RedditConfig.OAUTH_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth_str}",
                    "User-Agent": RedditConfig.USER_AGENT,
                },
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.error("OAuth token request failed: %d", resp.status)
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.error("OAuth token request error: %s", e)
            return None

        self._token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        self._expires_at = time.time() + expires_in
        log.info("OAuth token acquired — expires in %ds", expires_in)
        return self._token


# ---------------------------------------------------------------------------
# Reddit Fetcher (supports both OAuth and JSON modes)
# ---------------------------------------------------------------------------

class RedditFetcher:
    """Fetches posts and comments from Reddit subreddits."""

    def __init__(self, oauth: Optional[RedditOAuth] = None):
        self._oauth = oauth
        self._seen_post_ids: set[str] = set()
        self._seen_comment_ids: set[str] = set()

    async def fetch_posts(
        self,
        session: aiohttp.ClientSession,
        subreddit: str,
        sort: str = "new",
        limit: int = 25,
    ) -> list[dict]:
        """Fetch posts from a subreddit."""
        if self._oauth and RedditConfig.MODE == "oauth":
            return await self._fetch_oauth(session, subreddit, sort, limit)
        return await self._fetch_json(session, subreddit, sort, limit)

    async def fetch_comments(
        self,
        session: aiohttp.ClientSession,
        subreddit: str,
        post_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Fetch top-level comments for a post."""
        if self._oauth and RedditConfig.MODE == "oauth":
            url = f"{RedditConfig.OAUTH_BASE}/r/{subreddit}/comments/{post_id}"
            headers = await self._oauth_headers(session)
            if not headers:
                return []
        else:
            url = f"{RedditConfig.JSON_BASE}/r/{subreddit}/comments/{post_id}.json"
            headers = {"User-Agent": RedditConfig.USER_AGENT}

        try:
            async with session.get(
                url,
                headers=headers,
                params={"limit": str(limit), "sort": "top"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("Comments fetch returned %d for %s", resp.status, post_id)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Comments fetch failed for %s: %s", post_id, e)
            return []

        # Reddit returns [post_listing, comment_listing]
        if not isinstance(data, list) or len(data) < 2:
            return []

        comments_listing = data[1].get("data", {}).get("children", [])

        new_comments = []
        for item in comments_listing:
            if item.get("kind") != "t1":
                continue
            cid = item.get("data", {}).get("id", "")
            if cid and cid not in self._seen_comment_ids:
                self._seen_comment_ids.add(cid)
                new_comments.append(item)

        return new_comments[:limit]

    async def _fetch_oauth(
        self, session: aiohttp.ClientSession, subreddit: str, sort: str, limit: int,
    ) -> list[dict]:
        headers = await self._oauth_headers(session)
        if not headers:
            return []

        url = f"{RedditConfig.OAUTH_BASE}/r/{subreddit}/{sort}"

        try:
            async with session.get(
                url,
                headers=headers,
                params={"limit": str(limit)},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    log.warning("OAuth token expired — will refresh next request")
                    self._oauth._token = None
                    return []
                if resp.status != 200:
                    log.warning("Reddit OAuth returned %d for r/%s", resp.status, subreddit)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Reddit OAuth fetch failed for r/%s: %s", subreddit, e)
            return []

        return self._extract_new_posts(data)

    async def _fetch_json(
        self, session: aiohttp.ClientSession, subreddit: str, sort: str, limit: int,
    ) -> list[dict]:
        url = f"{RedditConfig.JSON_BASE}/r/{subreddit}/{sort}.json"

        try:
            async with session.get(
                url,
                headers={"User-Agent": RedditConfig.USER_AGENT},
                params={"limit": str(limit)},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    log.warning("Reddit rate limited (429) — backing off")
                    await asyncio.sleep(30)
                    return []
                if resp.status != 200:
                    log.warning("Reddit JSON returned %d for r/%s", resp.status, subreddit)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Reddit JSON fetch failed for r/%s: %s", subreddit, e)
            return []

        return self._extract_new_posts(data)

    def _extract_new_posts(self, listing: dict) -> list[dict]:
        children = listing.get("data", {}).get("children", [])
        new_posts = []
        for item in children:
            post_id = item.get("data", {}).get("id", "")
            if post_id and post_id not in self._seen_post_ids:
                self._seen_post_ids.add(post_id)
                new_posts.append(item)

        # Prevent unbounded growth
        if len(self._seen_post_ids) > 20000:
            self._seen_post_ids = set(list(self._seen_post_ids)[-10000:])
        if len(self._seen_comment_ids) > 20000:
            self._seen_comment_ids = set(list(self._seen_comment_ids)[-10000:])

        return new_posts

    async def _oauth_headers(self, session: aiohttp.ClientSession) -> Optional[dict]:
        token = await self._oauth.get_token(session)
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": RedditConfig.USER_AGENT,
        }


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class RedditConnector:
    """Orchestrates Reddit ingestion from configured subreddits."""

    def __init__(self, publisher: KafkaPublisher, fetcher: RedditFetcher):
        self._publisher = publisher
        self._fetcher = fetcher

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "Reddit connector started — mode=%s, subreddits=%s, poll=%ds",
            RedditConfig.MODE, RedditConfig.SUBREDDITS, RedditConfig.POLL_INTERVAL,
        )

        delay = (
            RedditConfig.REQUEST_DELAY_OAUTH
            if RedditConfig.MODE == "oauth"
            else RedditConfig.REQUEST_DELAY_JSON
        )

        while True:
            for subreddit in RedditConfig.SUBREDDITS:
                try:
                    await self._ingest_subreddit(session, subreddit, delay)
                except Exception as e:
                    log.error("Error ingesting r/%s: %s", subreddit, e, exc_info=True)

            await asyncio.sleep(RedditConfig.POLL_INTERVAL)

    async def _ingest_subreddit(
        self, session: aiohttp.ClientSession, subreddit: str, delay: float,
    ):
        # Fetch new posts
        posts = await self._fetcher.fetch_posts(
            session, subreddit, sort="new", limit=RedditConfig.POSTS_PER_SUBREDDIT,
        )

        published = 0
        hot_post_ids = []

        for post in posts:
            doc = post_to_document(post, subreddit)
            if doc:
                self._publisher.publish(
                    RedditConfig.KAFKA_TOPIC_POSTS,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published += 1

                # Track high-scoring posts for comment fetching
                data = post.get("data", post)
                score = data.get("score", 0)
                if score >= RedditConfig.COMMENTS_MIN_SCORE and RedditConfig.FETCH_COMMENTS:
                    hot_post_ids.append(data.get("id", ""))

        # Fetch comments for high-engagement posts
        for post_id in hot_post_ids:
            await asyncio.sleep(delay)
            comments = await self._fetcher.fetch_comments(
                session, subreddit, post_id, RedditConfig.MAX_COMMENTS_PER_POST,
            )
            for comment in comments:
                cdoc = comment_to_document(comment, subreddit, post_id)
                if cdoc:
                    self._publisher.publish(
                        RedditConfig.KAFKA_TOPIC_COMMENTS,
                        cdoc.to_kafka_key(),
                        cdoc.to_kafka_value(),
                    )
                    published += 1

        if published:
            self._publisher.flush(timeout=5.0)
            log.info("r/%s: published %d documents", subreddit, published)

        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")

    if all([client_id, client_secret, username, password]):
        RedditConfig.CLIENT_ID = client_id
        RedditConfig.CLIENT_SECRET = client_secret
        RedditConfig.USERNAME = username
        RedditConfig.PASSWORD = password
        RedditConfig.MODE = "oauth"

    subreddits = os.environ.get("REDDIT_SUBREDDITS", "")
    if subreddits:
        RedditConfig.SUBREDDITS = [s.strip() for s in subreddits.split(",") if s.strip()]

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        RedditConfig.KAFKA_BOOTSTRAP = bootstrap

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(RedditConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()

    await wait_for_kafka(RedditConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(RedditConfig.KAFKA_BOOTSTRAP, client_id="reddit-connector")

    oauth = None
    if RedditConfig.MODE == "oauth" and RedditConfig.CLIENT_ID:
        oauth = RedditOAuth(
            RedditConfig.CLIENT_ID, RedditConfig.CLIENT_SECRET,
            RedditConfig.USERNAME, RedditConfig.PASSWORD,
        )

    fetcher = RedditFetcher(oauth)
    connector = RedditConnector(publisher, fetcher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("Reddit Financial Connector running")
        log.info("  Mode:       %s", RedditConfig.MODE)
        log.info("  Subreddits: %s", RedditConfig.SUBREDDITS)
        log.info("  Comments:   %s (min score: %d)",
                 "enabled" if RedditConfig.FETCH_COMMENTS else "disabled",
                 RedditConfig.COMMENTS_MIN_SCORE)
        log.info("  Kafka:      %s, %s", RedditConfig.KAFKA_TOPIC_POSTS, RedditConfig.KAFKA_TOPIC_COMMENTS)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("Reddit Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
