"""
Shared statistics helpers used by tournament.py, gauntlet.py, sprt_gauntlet.py.

Provides:
  - Glicko-2 rating system (volatility-aware, gives RD confidence intervals)
  - Bo5 win-probability helpers (per-game, per-pair)
  - Paired-game Bernoulli helpers (for paired-seed evaluation)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ── Glicko-2 ─────────────────────────────────────────────────────────────────

# Standard Glicko-2 constants. tau controls volatility change rate (0.3-1.2 sane).
GLICKO2_SCALE = 173.7178
GLICKO2_DEFAULT_RATING = 1500.0
GLICKO2_DEFAULT_RD = 350.0
GLICKO2_DEFAULT_VOL = 0.06
GLICKO2_TAU = 0.5
GLICKO2_EPSILON = 1e-6


@dataclass
class Glicko2Rating:
    rating: float = GLICKO2_DEFAULT_RATING
    rd: float = GLICKO2_DEFAULT_RD
    vol: float = GLICKO2_DEFAULT_VOL

    def conf95(self) -> tuple[float, float]:
        """Approximate 95% confidence interval on the rating."""
        return (self.rating - 1.96 * self.rd, self.rating + 1.96 * self.rd)


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def glicko2_update(player: Glicko2Rating, opponents: list[tuple[Glicko2Rating, float]]) -> Glicko2Rating:
    """
    Update player's rating from a list of (opponent_rating, score) results.
    score is 1.0=win, 0.5=draw, 0.0=loss.

    If opponents is empty, only RD inflates (volatility-aware unrated period).
    """
    mu = (player.rating - GLICKO2_DEFAULT_RATING) / GLICKO2_SCALE
    phi = player.rd / GLICKO2_SCALE
    sigma = player.vol

    if not opponents:
        # No games this period: inflate RD via volatility
        phi_star = math.sqrt(phi * phi + sigma * sigma)
        return Glicko2Rating(
            rating=player.rating,
            rd=min(GLICKO2_SCALE * phi_star, GLICKO2_DEFAULT_RD),
            vol=sigma,
        )

    v_inv = 0.0
    delta_sum = 0.0
    for opp, score in opponents:
        mu_j = (opp.rating - GLICKO2_DEFAULT_RATING) / GLICKO2_SCALE
        phi_j = opp.rd / GLICKO2_SCALE
        g_j = _g(phi_j)
        E_j = _E(mu, mu_j, phi_j)
        v_inv += g_j * g_j * E_j * (1 - E_j)
        delta_sum += g_j * (score - E_j)

    if v_inv <= 0:
        return Glicko2Rating(rating=player.rating, rd=player.rd, vol=sigma)

    v = 1.0 / v_inv
    delta = v * delta_sum

    # Volatility update via iterative bracket (Glicko-2 spec)
    a = math.log(sigma * sigma)
    tau2 = GLICKO2_TAU * GLICKO2_TAU

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / tau2

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * GLICKO2_TAU) < 0:
            k += 1
            if k > 100:
                break
        B = a - k * GLICKO2_TAU

    fA = f(A)
    fB = f(B)
    iters = 0
    while abs(B - A) > GLICKO2_EPSILON and iters < 100:
        C = A + (A - B) * fA / (fB - fA) if (fB - fA) != 0 else (A + B) / 2
        fC = f(C)
        if fC * fB <= 0:
            A = B
            fA = fB
        else:
            fA = fA / 2.0
        B = C
        fB = fC
        iters += 1

    new_sigma = math.exp(A / 2.0)

    phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * delta_sum

    return Glicko2Rating(
        rating=new_mu * GLICKO2_SCALE + GLICKO2_DEFAULT_RATING,
        rd=new_phi * GLICKO2_SCALE,
        vol=new_sigma,
    )


def glicko2_expected_score(player: Glicko2Rating, opponent: Glicko2Rating) -> float:
    """Expected score for player against opponent (single-game probability of winning)."""
    mu = (player.rating - GLICKO2_DEFAULT_RATING) / GLICKO2_SCALE
    mu_o = (opponent.rating - GLICKO2_DEFAULT_RATING) / GLICKO2_SCALE
    phi_o = opponent.rd / GLICKO2_SCALE
    return _E(mu, mu_o, phi_o)


# ── Bo9 win probability ──────────────────────────────────────────────────────

def bo9_win_probability(p: float) -> float:
    """
    Probability of winning a best-of-9 match given per-game win prob p (no draws).
    A bo9 ends as soon as one side reaches 5 wins (so 5..9 games are played).

    Closed form: P(win bo9) = sum_{k=0..4} C(4+k, k) * p^5 * (1-p)^k
    """
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    q = 1.0 - p
    # C(4,0)=1, C(5,1)=5, C(6,2)=15, C(7,3)=35, C(8,4)=70
    coeffs = (1, 5, 15, 35, 70)
    return p ** 5 * sum(c * q ** k for k, c in enumerate(coeffs))


def bo9_from_pair_outcomes(sweeps: int, splits: int, losses: int) -> float | None:
    """
    Estimate Bo9 win probability using paired-seed outcomes (both sides on same map+seed).
    Each pair has 3 decisive outcomes: sweep (2-0), split (1-1), or 0-2.
    Treat per-game win rate as (sweeps + 0.5*splits) / (sweeps+splits+losses), then
    plug into bo9_win_probability().

    Pairs reduce variance from map+seed noise. Returns None if no decisive pairs.
    """
    n = sweeps + splits + losses
    if n == 0:
        return None
    p = (sweeps + 0.5 * splits) / n
    return bo9_win_probability(p)


def bo9_from_per_game(wins: int, losses: int) -> float | None:
    """Bo9 prob from per-game wins/losses. Returns None if no decisive games."""
    n = wins + losses
    if n == 0:
        return None
    return bo9_win_probability(wins / n)


# Backwards-compat aliases (in case anything still imports the old names)
bo5_win_probability = bo9_win_probability
bo5_from_pair_outcomes = bo9_from_pair_outcomes
bo5_from_per_game = bo9_from_per_game


# ── Paired-pair statistics (sweep / split / 0-2) ────────────────────────────

@dataclass
class PairCounts:
    """Counts of paired-game outcomes (one pair = both sides on same map+seed)."""
    sweeps: int = 0   # 2-0 for main bot
    splits: int = 0   # 1-1
    losses: int = 0   # 0-2 for main bot
    with_draw: int = 0   # at least one game ended in a draw (excluded from above)

    @property
    def decisive(self) -> int:
        return self.sweeps + self.splits + self.losses

    @property
    def per_game_wr(self) -> float:
        d = self.decisive
        if d == 0:
            return 0.0
        return (self.sweeps + 0.5 * self.splits) / d

    @property
    def pair_wr(self) -> float:
        """Win rate at the *pair* level (sweep counts as win, split as half)."""
        return self.per_game_wr

    @property
    def bo9_prob(self) -> float | None:
        return bo9_from_pair_outcomes(self.sweeps, self.splits, self.losses)

    # Backwards-compat
    @property
    def bo5_prob(self) -> float | None:
        return self.bo9_prob
