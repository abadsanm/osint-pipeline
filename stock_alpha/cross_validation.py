"""
Cross-source validation — statistical agreement across independent data sources.

Measures whether multiple independent sources agree on the directional
sentiment for an entity (ticker, company, product).  This is a critical
filter: a signal confirmed by 3+ independent sources is far more
trustworthy than one that appears only on Reddit.

Core statistics:
  - Fleiss' kappa for inter-rater (inter-source) agreement
  - Chi-squared goodness-of-fit for significance testing
  - Pairwise source-independence weighting

The output is a :class:`CrossValidationResult` that downstream consumers
(the scorer, the dashboard) can use to decide whether to act on a signal.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from correlation.config import CorrelationConfig

log = logging.getLogger("stock_alpha.cross_validation")

# ---------------------------------------------------------------------------
# Try scipy for chi-squared; fall back to manual approximation
# ---------------------------------------------------------------------------

try:
    from scipy.stats import chi2 as _scipy_chi2

    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _HAS_SCIPY = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_OBSERVATIONS_PER_ENTITY = 10_000
_DEFAULT_MAX_AGE_HOURS = 72

# Sentiment classification thresholds (tighter = less data lost to neutral)
_BULLISH_THRESHOLD = 0.05
_BEARISH_THRESHOLD = -0.05

# Category indices for Fleiss' kappa
_CAT_BEARISH = 0
_CAT_NEUTRAL = 1
_CAT_BULLISH = 2
_N_CATEGORIES = 3

# All known source platforms (superset of what the pipeline currently uses)
ALL_SOURCES = list(CorrelationConfig.SOURCE_WEIGHTS.keys())

# Pairwise independence between sources.  Pairs that share data or user
# bases get a score < 1.0.  Unlisted pairs default to 1.0 (fully independent).
SOURCE_INDEPENDENCE: dict[tuple[str, str], float] = {
    ("reddit", "hacker_news"): 0.6,
    ("reddit", "twitter_x"): 0.5,
    ("reddit", "bluesky"): 0.55,
    ("hacker_news", "twitter_x"): 0.65,
    ("hacker_news", "bluesky"): 0.7,
    ("sec_edgar", "openinsider"): 0.4,  # same underlying SEC filings
    ("news", "google_trends"): 0.7,  # news drives trends
    ("twitter_x", "bluesky"): 0.5,  # similar micro-blog demographics
    ("trustpilot", "amazon"): 0.75,  # both review platforms, different audiences
    ("github", "producthunt"): 0.8,
}


def _get_independence(src_a: str, src_b: str) -> float:
    """Return the pairwise independence score for two sources."""
    if src_a == src_b:
        return 0.0
    key = (min(src_a, src_b), max(src_a, src_b))
    return SOURCE_INDEPENDENCE.get(key, 1.0)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SourceObservation:
    """A single sentiment observation from a specific source."""

    source: str
    sentiment: float  # -1 to +1
    confidence: float  # 0 to 1
    timestamp: datetime
    doc_id: str = ""


@dataclass
class CrossValidationResult:
    """Output of cross-source validation for a single entity."""

    entity: str

    # Core metrics
    validation_score: float  # 0-1, overall cross-source agreement strength
    is_statistically_significant: bool  # p < 0.05
    p_value: float  # from chi-squared test

    # Agreement metrics
    fleiss_kappa: float  # -1 to +1 inter-rater agreement
    kappa_interpretation: str  # "poor" .. "almost_perfect"

    # Consensus
    consensus_direction: str  # "bullish", "bearish", "mixed", "insufficient"
    consensus_strength: float  # 0-1

    # Source breakdown
    sources_agreeing: int
    sources_disagreeing: int
    total_sources: int
    total_observations: int

    # Per-source detail
    source_details: list[dict] = field(default_factory=list)

    # Independence check
    source_independence_score: float = 0.0

    # Suggestions
    additional_sources_needed: int = 0
    suggested_sources: list[str] = field(default_factory=list)

    # Human-readable summary
    summary: str = ""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _classify_sentiment(value: float) -> int:
    """Map a continuous sentiment to a category index."""
    if value > _BULLISH_THRESHOLD:
        return _CAT_BULLISH
    if value < _BEARISH_THRESHOLD:
        return _CAT_BEARISH
    return _CAT_NEUTRAL


def _direction_label(category: int) -> str:
    """Human label for a category index."""
    return {_CAT_BEARISH: "bearish", _CAT_NEUTRAL: "neutral", _CAT_BULLISH: "bullish"}[
        category
    ]


def _interpret_kappa(kappa: float) -> str:
    """Landis & Koch interpretation of kappa values."""
    if kappa < 0:
        return "poor"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost_perfect"


def _chi2_survival(x: float, df: int) -> float:
    """Compute P(X > x) for a chi-squared distribution with *df* degrees of freedom.

    Uses scipy when available; otherwise falls back to a Gamma-function
    based regularised incomplete gamma approximation that is accurate to
    a few percent — more than sufficient for the significance thresholds
    we care about (0.05, 0.01).
    """
    if _HAS_SCIPY:
        return float(_scipy_chi2.sf(x, df))

    # --- manual approximation via the series expansion of the regularised
    # lower incomplete gamma: gamma(s, x) / Gamma(s) ---
    # P(X <= x) = gamma(df/2, x/2) / Gamma(df/2)
    # We compute the regularised lower incomplete gamma with a series:
    #   P = exp(-t) * t^a / Gamma(a+1) * sum_{n=0..N}( t^n / prod(a+1..a+n+1) )
    # where a = df/2, t = x/2.
    if x <= 0:
        return 1.0
    a = df / 2.0
    t = x / 2.0

    # Use the series expansion for the regularised lower incomplete gamma
    term = 1.0 / a
    cumulative = term
    for n in range(1, 300):
        term *= t / (a + n)
        cumulative += term
        if abs(term) < 1e-12 * abs(cumulative):
            break

    try:
        log_p = a * math.log(t) - t - math.lgamma(a) + math.log(cumulative)
        p_lower = min(1.0, max(0.0, math.exp(log_p)))
    except (OverflowError, ValueError):
        # If x is very large the survival is ~0; if very small, ~1
        return 0.0 if x > 20 * df else 1.0

    return max(0.0, 1.0 - p_lower)


def _compute_fleiss_kappa(ratings_matrix: list[list[int]]) -> float:
    """Compute Fleiss' kappa from a ratings matrix.

    Parameters
    ----------
    ratings_matrix : list[list[int]]
        N x k matrix where N = number of subjects (one per source) and
        k = number of categories.  Each cell is the count of observations
        assigned to that category by that source.

    Returns
    -------
    float
        Fleiss' kappa in [-1, +1].  Returns 0.0 for degenerate inputs.
    """
    n_subjects = len(ratings_matrix)
    if n_subjects < 2:
        return 0.0

    k = len(ratings_matrix[0])
    if k < 2:
        return 0.0

    # n_i = total ratings by subject i (may differ across sources)
    n_per_subject = [sum(row) for row in ratings_matrix]
    total_n = sum(n_per_subject)
    if total_n == 0:
        return 0.0

    # p_j = proportion of all assignments to category j
    col_totals = [0] * k
    for row in ratings_matrix:
        for j in range(k):
            col_totals[j] += row[j]
    p_j = [col_totals[j] / total_n for j in range(k)]

    # P_bar_e = sum(p_j^2)  — expected agreement by chance
    p_bar_e = sum(pj * pj for pj in p_j)

    # P_i = (1 / (n_i * (n_i - 1))) * (sum(n_ij^2) - n_i)  for each subject
    # But when n_i = 1, P_i is undefined (single observation).  We handle
    # this by treating each source as having exactly 1 "rating" which is
    # its majority category — then Fleiss' kappa degenerates to a simpler
    # form.  For robustness, if any n_i < 2, we use a normalised version.
    if any(n < 2 for n in n_per_subject):
        # Fallback: treat each source as casting a single vote for its
        # majority category.  Then we have N raters each rating 1 subject
        # (the entity) with 1 observation.  This is really just observed
        # agreement vs expected.
        vote_counts = [0] * k
        for row in ratings_matrix:
            majority = max(range(k), key=lambda j: row[j])
            vote_counts[majority] += 1
        n_raters = n_subjects
        p_o = sum(c * (c - 1) for c in vote_counts) / max(1, n_raters * (n_raters - 1))
        if abs(1.0 - p_bar_e) < 1e-12:
            return 1.0 if p_o >= p_bar_e else 0.0
        return (p_o - p_bar_e) / (1.0 - p_bar_e)

    # Standard Fleiss' kappa
    p_i_values = []
    for i in range(n_subjects):
        ni = n_per_subject[i]
        sum_sq = sum(ratings_matrix[i][j] ** 2 for j in range(k))
        p_i = (sum_sq - ni) / max(1, ni * (ni - 1))
        p_i_values.append(p_i)

    p_bar_o = sum(p_i_values) / n_subjects  # observed agreement

    if abs(1.0 - p_bar_e) < 1e-12:
        return 1.0 if p_bar_o >= p_bar_e else 0.0

    return (p_bar_o - p_bar_e) / (1.0 - p_bar_e)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class CrossSourceValidator:
    """Measures statistical agreement across independent data sources.

    Uses Fleiss' kappa for inter-rater agreement, chi-squared test for
    heterogeneity, and a weighted credibility score based on source
    independence.

    Thread-safe: all mutations and reads are guarded by an internal lock.

    Usage::

        validator = CrossSourceValidator()
        validator.add_observation("AAPL", "reddit", 0.35, 0.8, now)
        validator.add_observation("AAPL", "sec_edgar", 0.40, 0.95, now)
        validator.add_observation("AAPL", "hacker_news", -0.10, 0.6, now)
        result = validator.validate("AAPL")
        if result:
            print(result.summary)
    """

    def __init__(self) -> None:
        self._observations: dict[str, list[SourceObservation]] = defaultdict(list)
        self._lock = threading.Lock()

    # -- mutators -----------------------------------------------------------

    def add_observation(
        self,
        entity: str,
        source: str,
        sentiment: float,
        confidence: float,
        timestamp: datetime,
        doc_id: str = "",
    ) -> None:
        """Record an observation from a specific source.

        Parameters
        ----------
        entity : str
            Entity identifier (ticker, company name, product).
        source : str
            Source platform key (must match keys in ``CorrelationConfig.SOURCE_WEIGHTS``).
        sentiment : float
            Sentiment score in ``[-1, +1]``.
        confidence : float
            Confidence of the sentiment estimate in ``[0, 1]``.
        timestamp : datetime
            When the observation was made (should be UTC).
        doc_id : str, optional
            Identifier of the originating document for traceability.
        """
        obs = SourceObservation(
            source=source,
            sentiment=max(-1.0, min(1.0, sentiment)),
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=timestamp,
            doc_id=doc_id,
        )
        with self._lock:
            obs_list = self._observations[entity]
            obs_list.append(obs)
            # Cap to prevent unbounded memory growth
            if len(obs_list) > _MAX_OBSERVATIONS_PER_ENTITY:
                self._observations[entity] = obs_list[-_MAX_OBSERVATIONS_PER_ENTITY:]

    # -- core validation ----------------------------------------------------

    def validate(
        self,
        entity: str,
        min_sources: int = 2,
        min_observations: int = 5,
    ) -> Optional[CrossValidationResult]:
        """Compute cross-source validation for an entity.

        Returns ``None`` if there are fewer than *min_sources* unique sources
        or fewer than *min_observations* total observations.

        Parameters
        ----------
        entity : str
            Entity to validate.
        min_sources : int
            Minimum number of distinct sources required.
        min_observations : int
            Minimum total observations required.
        """
        with self._lock:
            all_obs = list(self._observations.get(entity, []))

        if len(all_obs) < min_observations:
            log.debug(
                "entity=%s insufficient observations (%d < %d)",
                entity, len(all_obs), min_observations,
            )
            return None

        # Group by source
        by_source: dict[str, list[SourceObservation]] = defaultdict(list)
        for obs in all_obs:
            by_source[obs.source].append(obs)

        unique_sources = list(by_source.keys())
        if len(unique_sources) < min_sources:
            log.debug(
                "entity=%s insufficient sources (%d < %d)",
                entity, len(unique_sources), min_sources,
            )
            return None

        # ------------------------------------------------------------------
        # Step 1: Per-source consensus
        # ------------------------------------------------------------------
        source_avg_sentiment: dict[str, float] = {}
        source_direction: dict[str, int] = {}  # category index
        for src, obs_list in by_source.items():
            avg = sum(o.sentiment for o in obs_list) / len(obs_list)
            source_avg_sentiment[src] = avg
            source_direction[src] = _classify_sentiment(avg)

        # ------------------------------------------------------------------
        # Step 2: Fleiss' kappa
        # ------------------------------------------------------------------
        # Build ratings matrix: one row per source, columns = [bearish, neutral, bullish]
        ratings_matrix: list[list[int]] = []
        for src in unique_sources:
            counts = [0, 0, 0]
            for obs in by_source[src]:
                cat = _classify_sentiment(obs.sentiment)
                counts[cat] += 1
            ratings_matrix.append(counts)

        fleiss_k = _compute_fleiss_kappa(ratings_matrix)
        kappa_interp = _interpret_kappa(fleiss_k)

        # ------------------------------------------------------------------
        # Step 3: Determine consensus direction
        # ------------------------------------------------------------------
        direction_counts = [0, 0, 0]  # bearish, neutral, bullish
        for d in source_direction.values():
            direction_counts[d] += 1

        majority_cat = max(range(_N_CATEGORIES), key=lambda i: direction_counts[i])
        majority_count = direction_counts[majority_cat]
        n_sources = len(unique_sources)

        if majority_count > n_sources / 2:
            consensus_dir = _direction_label(majority_cat)
        else:
            consensus_dir = "mixed"

        consensus_strength = majority_count / n_sources

        # ------------------------------------------------------------------
        # Step 4: Chi-squared test
        # ------------------------------------------------------------------
        # Test whether the distribution of source directions differs from
        # uniform (null = each direction equally likely).
        expected = n_sources / _N_CATEGORIES
        chi2_stat = sum(
            (direction_counts[j] - expected) ** 2 / expected
            for j in range(_N_CATEGORIES)
        )
        df = _N_CATEGORIES - 1  # 2
        p_value = _chi2_survival(chi2_stat, df)
        is_significant = p_value < 0.05

        # ------------------------------------------------------------------
        # Step 5: Source independence score
        # ------------------------------------------------------------------
        # Average pairwise independence among sources that agree with consensus
        agreeing_sources = [
            src for src, d in source_direction.items() if d == majority_cat
        ]
        disagreeing_sources = [
            src for src, d in source_direction.items() if d != majority_cat
        ]

        independence_score = self._compute_independence(agreeing_sources)

        # ------------------------------------------------------------------
        # Step 6: Per-source details
        # ------------------------------------------------------------------
        source_details = []
        for src in unique_sources:
            obs_list = by_source[src]
            avg_sent = source_avg_sentiment[src]
            source_details.append({
                "source": src,
                "observation_count": len(obs_list),
                "avg_sentiment": round(avg_sent, 4),
                "direction": _direction_label(source_direction[src]),
                "agrees_with_consensus": source_direction[src] == majority_cat,
                "weight": CorrelationConfig.SOURCE_WEIGHTS.get(src, 0.3),
            })

        # Sort by weight descending so most reliable sources appear first
        source_details.sort(key=lambda d: d["weight"], reverse=True)

        # ------------------------------------------------------------------
        # Step 7: Validation score (composite)
        # ------------------------------------------------------------------
        kappa_normalized = (fleiss_k + 1.0) / 2.0  # map [-1,+1] → [0,1]

        if p_value < 0.01:
            significance_factor = 1.0
        elif p_value < 0.05:
            significance_factor = 0.7
        else:
            significance_factor = 0.3

        total_possible_sources = len(ALL_SOURCES)
        source_coverage = min(1.0, n_sources / max(1, total_possible_sources))

        validation_score = (
            0.30 * kappa_normalized
            + 0.25 * significance_factor
            + 0.25 * independence_score
            + 0.20 * source_coverage
        )
        validation_score = min(1.0, max(0.0, validation_score))

        # ------------------------------------------------------------------
        # Step 8: Suggestions
        # ------------------------------------------------------------------
        suggested = self.suggest_additional_sources(entity)
        suggested_names = [s["source"] for s in suggested]

        # Estimate how many more sources would push us to significance
        additional_needed = 0
        if not is_significant:
            # Rough heuristic: each new agreeing source adds ~1 to chi2
            # We need chi2 > 5.99 for df=2 at p<0.05
            gap = max(0.0, 5.99 - chi2_stat)
            additional_needed = max(1, math.ceil(gap / 1.5))

        # ------------------------------------------------------------------
        # Step 9: Summary
        # ------------------------------------------------------------------
        sig_marker = f"p={p_value:.3f}" if p_value >= 0.001 else f"p={p_value:.1e}"
        summary = (
            f"{len(agreeing_sources)} of {n_sources} independent sources agree: "
            f"{consensus_dir} with {kappa_interp} agreement "
            f"(\u03ba={fleiss_k:.2f}, {sig_marker})"
        )

        return CrossValidationResult(
            entity=entity,
            validation_score=round(validation_score, 4),
            is_statistically_significant=is_significant,
            p_value=round(p_value, 6),
            fleiss_kappa=round(fleiss_k, 4),
            kappa_interpretation=kappa_interp,
            consensus_direction=consensus_dir,
            consensus_strength=round(consensus_strength, 4),
            sources_agreeing=len(agreeing_sources),
            sources_disagreeing=len(disagreeing_sources),
            total_sources=n_sources,
            total_observations=len(all_obs),
            source_details=source_details,
            source_independence_score=round(independence_score, 4),
            additional_sources_needed=additional_needed,
            suggested_sources=suggested_names,
            summary=summary,
        )

    # -- suggestions --------------------------------------------------------

    def suggest_additional_sources(self, entity: str) -> list[dict]:
        """Suggest which additional sources would most strengthen validation.

        Returns up to 3 sources not yet observed that would add the most
        independent information, scored by
        ``SOURCE_WEIGHTS[source] * mean_independence_from_existing``.

        Each entry is a dict with keys: ``source``, ``score``,
        ``estimated_confidence_boost``.
        """
        with self._lock:
            all_obs = list(self._observations.get(entity, []))

        existing_sources = {obs.source for obs in all_obs}
        candidates: list[dict] = []

        for src in ALL_SOURCES:
            if src in existing_sources:
                continue

            # Mean independence from all existing sources
            if existing_sources:
                indep_scores = [
                    _get_independence(src, ex) for ex in existing_sources
                ]
                mean_indep = sum(indep_scores) / len(indep_scores)
            else:
                mean_indep = 1.0

            weight = CorrelationConfig.SOURCE_WEIGHTS.get(src, 0.3)
            score = weight * mean_indep

            # Estimate how much this source would improve the validation score.
            # Adding one fully independent, high-weight source typically boosts
            # validation_score by ~0.03-0.08 depending on current state.
            n_existing = max(1, len(existing_sources))
            coverage_boost = 1.0 / len(ALL_SOURCES)  # incremental coverage
            estimated_boost = round(0.25 * coverage_boost + 0.05 * mean_indep * weight, 4)

            candidates.append({
                "source": src,
                "score": round(score, 4),
                "independence_from_existing": round(mean_indep, 4),
                "estimated_confidence_boost": estimated_boost,
            })

        # Sort by score descending, return top 3
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:3]

    # -- maintenance --------------------------------------------------------

    def cleanup(self, max_age_hours: int = _DEFAULT_MAX_AGE_HOURS) -> None:
        """Remove observations older than *max_age_hours* and drop empty entities."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        empty: list[str] = []

        with self._lock:
            for entity, obs_list in self._observations.items():
                self._observations[entity] = [
                    o for o in obs_list if o.timestamp >= cutoff
                ]
                if not self._observations[entity]:
                    empty.append(entity)
            for e in empty:
                del self._observations[e]

        if empty:
            log.info("cleanup: dropped %d stale entities", len(empty))

    def get_tracked_entities(self) -> list[str]:
        """Return a list of entities currently being tracked."""
        with self._lock:
            return list(self._observations.keys())

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _compute_independence(sources: list[str]) -> float:
        """Average pairwise independence among a set of sources.

        Returns 1.0 when there are 0 or 1 sources (no pairs to compare).
        """
        if len(sources) < 2:
            return 1.0

        total = 0.0
        count = 0
        for i in range(len(sources)):
            for j in range(i + 1, len(sources)):
                total += _get_independence(sources[i], sources[j])
                count += 1

        return total / count if count > 0 else 1.0
