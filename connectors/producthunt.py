"""
OSINT Pipeline — ProductHunt Connector

Ingests product launches and comments from ProductHunt:
  1. GraphQL API mode (if PRODUCTHUNT_TOKEN is set) — official API
  2. Web scrape fallback — public pages via aiohttp

Focuses on tech products, SaaS, developer tools, and AI products.
Poll interval: 30 min (products launch throughout the day).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from confluent_kafka.admin import AdminClient

from connectors.kafka_publisher import KafkaPublisher
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ProductHuntConfig:
    GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
    BASE_URL = "https://www.producthunt.com"

    PRODUCTHUNT_TOKEN: Optional[str] = None

    # Categories to focus on (used for filtering scrape results)
    FOCUS_TOPICS = {
        "artificial-intelligence", "ai", "machine-learning", "developer-tools",
        "saas", "tech", "software-engineering", "productivity", "api",
        "open-source", "devops", "no-code", "automation", "analytics",
        "data-science", "cloud", "cybersecurity", "fintech",
    }

    POLL_INTERVAL = 1800          # 30 minutes
    REQUEST_DELAY = 3.0           # 3s between requests — be respectful
    MAX_PRODUCTS_PER_POLL = 20    # Cap per poll cycle (PH GraphQL complexity limit)
    MAX_COMMENTS_PER_PRODUCT = 20

    KAFKA_BOOTSTRAP = "localhost:9092"
    TOPIC_LAUNCHES = "tech.producthunt.launches"
    TOPIC_COMMENTS = "tech.producthunt.comments"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("producthunt-connector")

# Common headers for web scrape mode
SCRAPE_HEADERS = {
    "User-Agent": "OSINTPipeline/1.0 (research; +https://github.com/osint-pipeline)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------


def _make_launch_source_id(slug: str, launch_date: str) -> str:
    """Deterministic source ID for a launch, used for dedup and parent linking."""
    dedup_seed = f"ph:{slug}:{launch_date}"
    return f"ph:launch:{hashlib.sha256(dedup_seed.encode()).hexdigest()[:12]}"


def launch_to_document(product: dict) -> Optional[OsintDocument]:
    """Convert a product launch dict into an OsintDocument."""
    name = product.get("name", "").strip()
    tagline = product.get("tagline", "").strip()
    slug = product.get("slug", "")
    url = product.get("url", "")

    if not name:
        return None

    content_text = f"{name}: {tagline}" if tagline else name
    description = product.get("description", "").strip()
    if description:
        content_text = f"{content_text}\n\n{description}"

    votes = product.get("votes_count", 0)
    comments_count = product.get("comments_count", 0)
    topics = product.get("topics", [])
    maker_name = product.get("maker_name", "")
    launch_date = product.get("launch_date", "")

    created_str = product.get("created_at", "")
    if created_str:
        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Dedup key: slug + launch date
    effective_date = launch_date or created_at.strftime("%Y-%m-%d")
    source_id = _make_launch_source_id(slug, effective_date)

    doc = OsintDocument(
        source=SourcePlatform.PRODUCTHUNT,
        source_id=source_id,
        content_type=ContentType.POST,
        title=name,
        content_text=content_text,
        url=url or f"{ProductHuntConfig.BASE_URL}/posts/{slug}",
        created_at=created_at,
        entity_id=f"ph:{slug}" if slug else None,
        quality_signals=QualitySignals(
            score=float(votes) if votes else None,
            engagement_count=comments_count if comments_count else None,
        ),
        metadata={
            "votes": votes,
            "comments_count": comments_count,
            "topics": topics,
            "maker_name": maker_name,
            "product_url": url or f"{ProductHuntConfig.BASE_URL}/posts/{slug}",
            "launch_date": launch_date or created_at.strftime("%Y-%m-%d"),
            "slug": slug,
            "source_reliability": 0.7,
        },
    )
    doc.compute_dedup_hash()
    return doc


def comment_to_document(comment: dict, product: dict) -> Optional[OsintDocument]:
    """Convert a comment dict + parent product into an OsintDocument."""
    body = comment.get("body", "").strip()
    if not body:
        return None

    comment_id = comment.get("id", "")
    author = comment.get("author", "")
    slug = product.get("slug", "")

    created_str = comment.get("created_at", "")
    if created_str:
        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    source_id = f"ph:comment:{comment_id}" if comment_id else \
        f"ph:comment:{hashlib.sha256(f'{slug}:{body[:80]}'.encode()).hexdigest()[:12]}"

    doc = OsintDocument(
        source=SourcePlatform.PRODUCTHUNT,
        source_id=source_id,
        content_type=ContentType.COMMENT,
        title=f"Comment on {product.get('name', slug)}",
        content_text=body,
        url=f"{ProductHuntConfig.BASE_URL}/posts/{slug}" if slug else None,
        created_at=created_at,
        parent_id=_make_launch_source_id(slug, product.get("launch_date", "")) if slug else None,
        entity_id=f"ph:{slug}" if slug else None,
        quality_signals=QualitySignals(
            score=float(comment.get("votes", 0)),
        ),
        metadata={
            "author": author,
            "product_name": product.get("name", ""),
            "product_slug": slug,
            "product_url": f"{ProductHuntConfig.BASE_URL}/posts/{slug}",
            "comment_votes": comment.get("votes", 0),
            "source_reliability": 0.7,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# GraphQL API Fetcher (when PRODUCTHUNT_TOKEN is available)
# ---------------------------------------------------------------------------

POSTS_QUERY = """
query($postedAfter: DateTime, $first: Int) {
  posts(
    order: VOTES
    postedAfter: $postedAfter
    first: $first
  ) {
    edges {
      node {
        id
        name
        tagline
        description
        slug
        url
        votesCount
        commentsCount
        createdAt
        featuredAt
        website
        topics {
          edges {
            node {
              slug
              name
            }
          }
        }
        makers {
          name
          username
        }
      }
    }
  }
}
"""

COMMENTS_QUERY = """
query($postId: ID!, $first: Int) {
  post(id: $postId) {
    comments(first: $first, order: VOTES) {
      edges {
        node {
          id
          body
          votesCount
          createdAt
          user {
            name
            username
          }
        }
      }
    }
  }
}
"""


class GraphQLFetcher:
    """Fetches data from ProductHunt GraphQL API v2."""

    def __init__(self):
        self._seen_launches: set[str] = set()
        self._seen_comments: set[str] = set()

    def _gql_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {ProductHuntConfig.PRODUCTHUNT_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def fetch_launches(
        self, session: aiohttp.ClientSession, posted_after: str
    ) -> list[dict]:
        """Fetch recent top product launches via GraphQL."""
        products = []
        try:
            payload = {
                "query": POSTS_QUERY,
                "variables": {
                    "postedAfter": posted_after,
                    "first": ProductHuntConfig.MAX_PRODUCTS_PER_POLL,
                },
            }
            async with session.post(
                ProductHuntConfig.GRAPHQL_URL,
                headers=self._gql_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    log.warning("GraphQL posts query returned %d", resp.status)
                    return []
                data = await resp.json()

            edges = (data.get("data") or {}).get("posts", {}).get("edges", [])
            for edge in edges:
                node = edge.get("node", {})
                slug = node.get("slug", "")
                if not slug or slug in self._seen_launches:
                    continue
                self._seen_launches.add(slug)

                topics = [
                    t["node"]["slug"]
                    for t in (node.get("topics", {}).get("edges", []))
                ]
                makers = node.get("makers", [])
                maker_name = makers[0]["name"] if makers else ""

                products.append({
                    "id": node.get("id"),
                    "name": node.get("name", ""),
                    "tagline": node.get("tagline", ""),
                    "description": node.get("description", ""),
                    "slug": slug,
                    "url": node.get("url", ""),
                    "votes_count": node.get("votesCount", 0),
                    "comments_count": node.get("commentsCount", 0),
                    "created_at": node.get("createdAt", ""),
                    "launch_date": (node.get("featuredAt") or node.get("createdAt", ""))[:10],
                    "topics": topics,
                    "maker_name": maker_name,
                    "website": node.get("website", ""),
                })
        except Exception as e:
            log.error("GraphQL fetch_launches error: %s", e, exc_info=True)

        # Trim seen cache
        if len(self._seen_launches) > 5000:
            self._seen_launches = set(list(self._seen_launches)[-2500:])

        return products

    async def fetch_comments(
        self, session: aiohttp.ClientSession, post_id: str, product: dict
    ) -> list[dict]:
        """Fetch comments for a specific product via GraphQL."""
        comments = []
        try:
            payload = {
                "query": COMMENTS_QUERY,
                "variables": {
                    "postId": post_id,
                    "first": ProductHuntConfig.MAX_COMMENTS_PER_PRODUCT,
                },
            }
            async with session.post(
                ProductHuntConfig.GRAPHQL_URL,
                headers=self._gql_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("GraphQL comments query returned %d", resp.status)
                    return []
                data = await resp.json()

            post_data = (data.get("data") or {}).get("post", {})
            edges = (post_data.get("comments") or {}).get("edges", [])
            for edge in edges:
                node = edge.get("node", {})
                cid = str(node.get("id", ""))
                if cid in self._seen_comments:
                    continue
                self._seen_comments.add(cid)

                user = node.get("user", {})
                comments.append({
                    "id": cid,
                    "body": node.get("body", ""),
                    "votes": node.get("votesCount", 0),
                    "created_at": node.get("createdAt", ""),
                    "author": user.get("name", user.get("username", "")),
                })
        except Exception as e:
            log.error("GraphQL fetch_comments error: %s", e, exc_info=True)

        if len(self._seen_comments) > 10000:
            self._seen_comments = set(list(self._seen_comments)[-5000:])

        return comments


# ---------------------------------------------------------------------------
# Web Scrape Fetcher (fallback when no API token)
# ---------------------------------------------------------------------------


class WebScrapeFetcher:
    """
    Scrapes ProductHunt public pages when no API token is available.

    Targets the /posts page and individual product pages.  ProductHunt
    renders Next.js JSON data in __NEXT_DATA__ script tags that we parse.
    """

    def __init__(self):
        self._seen_launches: set[str] = set()
        self._seen_comments: set[str] = set()

    async def fetch_launches(self, session: aiohttp.ClientSession) -> list[dict]:
        """Scrape today's top launches from ProductHunt homepage."""
        products = []
        try:
            async with session.get(
                ProductHuntConfig.BASE_URL,
                headers=SCRAPE_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    log.warning("ProductHunt homepage returned %d", resp.status)
                    return []
                html = await resp.text()

            products = self._parse_launches_from_html(html)
        except Exception as e:
            log.error("Scrape fetch_launches error: %s", e, exc_info=True)

        if len(self._seen_launches) > 5000:
            self._seen_launches = set(list(self._seen_launches)[-2500:])

        return products

    def _parse_launches_from_html(self, html: str) -> list[dict]:
        """Extract product data from HTML page.

        Strategy:
        1. Try __NEXT_DATA__ JSON blob (Next.js SSR data)
        2. Fallback to application/json script tags
        3. Fallback to regex extraction of structured data
        """
        products = []

        # Strategy 1: __NEXT_DATA__
        next_data_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if next_data_match:
            try:
                next_data = json.loads(next_data_match.group(1))
                products = self._extract_from_next_data(next_data)
                if products:
                    return products
            except (json.JSONDecodeError, KeyError):
                pass

        # Strategy 2: look for JSON-LD or embedded JSON with product data
        json_blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in json_blocks:
            try:
                data = json.loads(block)
                products = self._extract_products_from_json(data)
                if products:
                    return products
            except (json.JSONDecodeError, KeyError):
                continue

        # Strategy 3: regex fallback for post links + metadata
        products = self._extract_from_regex(html)
        return products

    def _extract_from_next_data(self, data: dict) -> list[dict]:
        """Walk __NEXT_DATA__ JSON to find product nodes."""
        products = []
        # Recursively search for arrays of objects with 'slug' and 'tagline'
        self._walk_json_for_products(data, products)
        return products[:ProductHuntConfig.MAX_PRODUCTS_PER_POLL]

    def _walk_json_for_products(self, obj, products: list, depth: int = 0):
        """Recursively walk JSON to find product-like objects."""
        if depth > 15 or len(products) >= ProductHuntConfig.MAX_PRODUCTS_PER_POLL:
            return

        if isinstance(obj, dict):
            # Check if this dict looks like a product
            if "slug" in obj and "tagline" in obj and "name" in obj:
                slug = obj.get("slug", "")
                if slug and slug not in self._seen_launches:
                    self._seen_launches.add(slug)
                    topics = []
                    raw_topics = obj.get("topics", obj.get("topicIds", []))
                    if isinstance(raw_topics, list):
                        for t in raw_topics:
                            if isinstance(t, dict):
                                topics.append(t.get("slug", t.get("name", "")))
                            elif isinstance(t, str):
                                topics.append(t)

                    products.append({
                        "id": str(obj.get("id", "")),
                        "name": obj.get("name", ""),
                        "tagline": obj.get("tagline", ""),
                        "description": obj.get("description", ""),
                        "slug": slug,
                        "url": obj.get("url", f"{ProductHuntConfig.BASE_URL}/posts/{slug}"),
                        "votes_count": obj.get("votesCount", obj.get("votes_count", 0)),
                        "comments_count": obj.get("commentsCount", obj.get("comments_count", 0)),
                        "created_at": obj.get("createdAt", obj.get("created_at", "")),
                        "launch_date": (
                            obj.get("featuredAt", obj.get("createdAt", ""))
                            or ""
                        )[:10],
                        "topics": topics,
                        "maker_name": self._extract_maker(obj),
                        "website": obj.get("website", ""),
                    })

            for v in obj.values():
                self._walk_json_for_products(v, products, depth + 1)

        elif isinstance(obj, list):
            for item in obj:
                self._walk_json_for_products(item, products, depth + 1)

    def _extract_maker(self, obj: dict) -> str:
        makers = obj.get("makers", [])
        if isinstance(makers, list) and makers:
            first = makers[0]
            if isinstance(first, dict):
                return first.get("name", first.get("username", ""))
            return str(first)
        hunter = obj.get("hunter", {})
        if isinstance(hunter, dict):
            return hunter.get("name", hunter.get("username", ""))
        return ""

    def _extract_products_from_json(self, data) -> list[dict]:
        """Attempt to extract products from an arbitrary JSON blob."""
        products = []
        self._walk_json_for_products(data, products)
        return products

    def _extract_from_regex(self, html: str) -> list[dict]:
        """Last-resort regex extraction from HTML structure."""
        products = []
        # Look for /posts/SLUG patterns with nearby text
        post_links = re.findall(
            r'href="/posts/([a-z0-9-]+)"[^>]*>([^<]{2,100})</a>',
            html,
            re.IGNORECASE,
        )
        seen_slugs = set()
        for slug, name in post_links:
            slug = slug.strip()
            name = name.strip()
            if (
                slug in seen_slugs
                or slug in self._seen_launches
                or not name
                or len(name) < 3
            ):
                continue
            seen_slugs.add(slug)
            self._seen_launches.add(slug)

            products.append({
                "name": name,
                "tagline": "",
                "slug": slug,
                "url": f"{ProductHuntConfig.BASE_URL}/posts/{slug}",
                "votes_count": 0,
                "comments_count": 0,
                "created_at": "",
                "launch_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "topics": [],
                "maker_name": "",
            })

            if len(products) >= ProductHuntConfig.MAX_PRODUCTS_PER_POLL:
                break

        return products

    async def fetch_comments(
        self, session: aiohttp.ClientSession, product: dict
    ) -> list[dict]:
        """Scrape comments from a product page."""
        slug = product.get("slug", "")
        if not slug:
            return []

        comments = []
        try:
            url = f"{ProductHuntConfig.BASE_URL}/posts/{slug}"
            async with session.get(
                url,
                headers=SCRAPE_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()

            comments = self._parse_comments_from_html(html, product)
        except Exception as e:
            log.warning("Scrape fetch_comments error for %s: %s", slug, e)

        if len(self._seen_comments) > 10000:
            self._seen_comments = set(list(self._seen_comments)[-5000:])

        return comments

    def _parse_comments_from_html(self, html: str, product: dict) -> list[dict]:
        """Extract comments from product page HTML."""
        comments = []

        # Try __NEXT_DATA__ first
        next_data_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if next_data_match:
            try:
                next_data = json.loads(next_data_match.group(1))
                comments = self._walk_json_for_comments(next_data)
                if comments:
                    return comments[:ProductHuntConfig.MAX_COMMENTS_PER_PRODUCT]
            except (json.JSONDecodeError, KeyError):
                pass

        # Fallback: search any JSON blocks
        json_blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in json_blocks:
            try:
                data = json.loads(block)
                comments = self._walk_json_for_comments(data)
                if comments:
                    return comments[:ProductHuntConfig.MAX_COMMENTS_PER_PRODUCT]
            except (json.JSONDecodeError, KeyError):
                continue

        return comments

    def _walk_json_for_comments(self, obj, depth: int = 0) -> list[dict]:
        """Recursively find comment-like objects in JSON."""
        comments = []
        if depth > 15 or len(comments) >= ProductHuntConfig.MAX_COMMENTS_PER_PRODUCT:
            return comments

        if isinstance(obj, dict):
            # A comment-like object has 'body' and likely 'user' or 'id'
            if "body" in obj and ("user" in obj or "userId" in obj or "id" in obj):
                cid = str(obj.get("id", ""))
                body = obj.get("body", "").strip()
                if body and cid not in self._seen_comments:
                    if cid:
                        self._seen_comments.add(cid)
                    user = obj.get("user", {})
                    author = ""
                    if isinstance(user, dict):
                        author = user.get("name", user.get("username", ""))
                    comments.append({
                        "id": cid,
                        "body": body,
                        "votes": obj.get("votesCount", obj.get("votes", 0)),
                        "created_at": obj.get("createdAt", obj.get("created_at", "")),
                        "author": author,
                    })

            for v in obj.values():
                comments.extend(self._walk_json_for_comments(v, depth + 1))

        elif isinstance(obj, list):
            for item in obj:
                comments.extend(self._walk_json_for_comments(item, depth + 1))

        return comments


# ---------------------------------------------------------------------------
# Topic filtering
# ---------------------------------------------------------------------------


def is_relevant_product(product: dict) -> bool:
    """Check if a product matches our focus topics.

    If topics are available, require overlap with FOCUS_TOPICS.
    If no topic info, allow it through (scrape fallback may lack topics).
    """
    topics = product.get("topics", [])
    if not topics:
        return True  # No topic info — let downstream NLP filter
    topic_slugs = {t.lower().replace(" ", "-") if isinstance(t, str) else "" for t in topics}
    return bool(topic_slugs & ProductHuntConfig.FOCUS_TOPICS)


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class ProductHuntConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._use_api = bool(ProductHuntConfig.PRODUCTHUNT_TOKEN)
        if self._use_api:
            self._fetcher = GraphQLFetcher()
        else:
            self._fetcher = WebScrapeFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "ProductHunt connector started — mode=%s, poll every %ds",
            "graphql" if self._use_api else "scrape",
            ProductHuntConfig.POLL_INTERVAL,
        )

        while True:
            try:
                await self._poll_cycle(session)
            except Exception as e:
                log.error("ProductHunt poll cycle error: %s", e, exc_info=True)

            await asyncio.sleep(ProductHuntConfig.POLL_INTERVAL)

    async def _poll_cycle(self, session: aiohttp.ClientSession):
        published_launches = 0
        published_comments = 0

        # --- Fetch launches ---
        if self._use_api:
            # Fetch from last 24h
            posted_after = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
            products = await self._fetcher.fetch_launches(session, posted_after)
        else:
            products = await self._fetcher.fetch_launches(session)

        await asyncio.sleep(ProductHuntConfig.REQUEST_DELAY)

        # Filter to relevant topics
        relevant = [p for p in products if is_relevant_product(p)]
        log.info(
            "Fetched %d products, %d relevant after topic filter",
            len(products), len(relevant),
        )

        # --- Publish launches ---
        for product in relevant:
            doc = launch_to_document(product)
            if doc:
                self._publisher.publish(
                    ProductHuntConfig.TOPIC_LAUNCHES,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published_launches += 1

        # --- Fetch and publish comments for top products ---
        # Only fetch comments for top-voted products to respect rate limits
        top_products = sorted(relevant, key=lambda p: p.get("votes_count", 0), reverse=True)
        comment_targets = top_products[:10]  # Top 10 by votes

        for product in comment_targets:
            await asyncio.sleep(ProductHuntConfig.REQUEST_DELAY)

            if self._use_api:
                post_id = product.get("id", "")
                if not post_id:
                    continue
                comments = await self._fetcher.fetch_comments(session, post_id, product)
            else:
                comments = await self._fetcher.fetch_comments(session, product)

            for c in comments:
                doc = comment_to_document(c, product)
                if doc:
                    self._publisher.publish(
                        ProductHuntConfig.TOPIC_COMMENTS,
                        doc.to_kafka_key(),
                        doc.to_kafka_value(),
                    )
                    published_comments += 1

        if published_launches or published_comments:
            self._publisher.flush(timeout=5.0)
            log.info(
                "Published %d launches, %d comments | stats=%s",
                published_launches,
                published_comments,
                self._publisher.stats,
            )


# ---------------------------------------------------------------------------
# Kafka health check
# ---------------------------------------------------------------------------


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    start = time.time()
    while time.time() - start < timeout:
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.topics:
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable after {timeout}s")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_config_from_env():
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if token:
        ProductHuntConfig.PRODUCTHUNT_TOKEN = token

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        ProductHuntConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("PRODUCTHUNT_POLL_INTERVAL")
    if poll:
        ProductHuntConfig.POLL_INTERVAL = int(poll)

    max_products = os.environ.get("PRODUCTHUNT_MAX_PRODUCTS")
    if max_products:
        ProductHuntConfig.MAX_PRODUCTS_PER_POLL = int(max_products)

    extra_topics = os.environ.get("PRODUCTHUNT_TOPICS")
    if extra_topics:
        for t in extra_topics.split(","):
            t = t.strip().lower().replace(" ", "-")
            if t:
                ProductHuntConfig.FOCUS_TOPICS.add(t)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main():
    load_config_from_env()
    await wait_for_kafka(ProductHuntConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        ProductHuntConfig.KAFKA_BOOTSTRAP,
        client_id="producthunt-connector",
    )
    connector = ProductHuntConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=5)
    ) as session:
        log.info("=" * 60)
        log.info("ProductHunt Connector running")
        log.info("  Mode:       %s", "GraphQL API" if ProductHuntConfig.PRODUCTHUNT_TOKEN else "Web Scrape")
        log.info("  Topics:     %s / %s", ProductHuntConfig.TOPIC_LAUNCHES, ProductHuntConfig.TOPIC_COMMENTS)
        log.info("  Poll:       %ds", ProductHuntConfig.POLL_INTERVAL)
        log.info("  Rate limit: %.1fs between requests", ProductHuntConfig.REQUEST_DELAY)
        log.info("  Focus:      %d topic filters", len(ProductHuntConfig.FOCUS_TOPICS))
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
