"""Anthropic list prices, USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing (2026-11-18).

(input, cache write 5m, cache write 1h, cache read, output). Haiku 5.5 uses the up-to-100K-prompt tier.
Estimates only: they ignore batch discounts, fast mode and data residency."""
import re

PRICES = {
    "claude-fable-5-2": (8, 10, 16, 0.20, 40), "claude-mythos-5-2": (8, 10, 16, 0.20, 40),
    "claude-fable-5-1": (10, 12.5, 20, 0.25, 50), "claude-mythos-5-1": (10, 12.5, 20, 0.25, 50),
    "claude-fable-5": (10, 12.5, 20, 1, 50), "claude-mythos-5": (10, 12.5, 20, 1, 50),
    "claude-opus-5-5": (4, 5, 8, 0.20, 20),
    "claude-opus-5": (5, 6.25, 10, 0.50, 25), "claude-opus-4-8": (5, 6.25, 10, 0.50, 25),
    "claude-opus-4-7": (5, 6.25, 10, 0.50, 25), "claude-opus-4-6": (5, 6.25, 10, 0.50, 25),
    "claude-opus-4-5": (5, 6.25, 10, 0.50, 25),
    "claude-sonnet-5-5": (2, 2.5, 4, 0.10, 10), "claude-sonnet-5": (2, 2.5, 4, 0.20, 10),
    "claude-sonnet-4-6": (3, 3.75, 6, 0.30, 15), "claude-sonnet-4-5": (3, 3.75, 6, 0.30, 15),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50), "claude-haiku-4-5": (1, 1.25, 2, 0.10, 5),
}


def estimate(model, inp, out, cache_read, write_5m, write_1h):
    """USD at list price, or None for a model without a known price."""
    p = PRICES.get(re.sub(r"-\d{8}$", "", model or ""))
    if not p:
        return None
    return (inp * p[0] + write_5m * p[1] + write_1h * p[2] + cache_read * p[3] + out * p[4]) / 1e6
