"""
Kalshi API client — stub for the trading API (https://trading-api.kalshi.com/trade-api/v2/).

To activate:
  1. Create account at kalshi.com and generate an API key
  2. Set KALSHI_API_KEY environment variable (or pass directly to KalshiClient)
  3. Identify MLB game market tickers (search via list_markets())

Kalshi contract pricing: 0–100 cents. A price of 62 means the market implies 62% win probability.
"""
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv
load_dotenv()

KALSHI_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("KALSHI_API_KEY", "")
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["Authorization"] = f"Bearer {self.api_key}"
        self._session.headers["User-Agent"] = "kalshi-predictions/1.0"

    @property
    def _configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, endpoint: str, params: dict = None) -> dict:
        if not self._configured:
            raise RuntimeError(
                "Kalshi API key not set. Set the KALSHI_API_KEY environment variable."
            )
        r = self._session.get(f"{KALSHI_BASE_URL}/{endpoint}", params=params or {}, timeout=15)
        r.raise_for_status()
        time.sleep(0.1)
        return r.json()

    def list_markets(self, status: str = "open", series_ticker: str = None, limit: int = 100) -> list[dict]:
        """List available Kalshi markets. Filter by series_ticker for MLB markets."""
        params = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._get("markets", params).get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Get a specific market by its ticker (e.g. 'MLB-YANKEES-WIN-20240815')."""
        return self._get(f"markets/{ticker}").get("market", {})

    def get_orderbook(self, ticker: str) -> dict:
        """Get the current order book for a market."""
        return self._get(f"markets/{ticker}/orderbook")

    def get_mlb_markets(self) -> list[dict]:
        """Search for open MLB game markets. Kalshi series tickers vary — check their site."""
        # Common MLB series tickers on Kalshi include things like "MLB" or "MLBWINNER"
        # Adjust series_ticker to match whatever Kalshi uses at the time you activate this.
        try:
            return self.list_markets(series_ticker="MLB")
        except Exception:
            return []

    def get_implied_prob(self, ticker: str) -> Optional[float]:
        """
        Returns the market-implied win probability (0–1) for the Yes contract.
        Uses last traded price; falls back to mid-market if no trades.
        Returns None if market not found or API not configured.
        """
        if not self._configured:
            return None
        try:
            market = self.get_market(ticker)
            last = market.get("last_price")
            if last is not None:
                return last / 100.0
            # Fall back to mid-market (best yes ask + best no ask) / 2
            yes_ask = market.get("yes_ask")
            no_ask = market.get("no_ask")
            if yes_ask is not None and no_ask is not None:
                return (yes_ask / 100.0 + (1 - no_ask / 100.0)) / 2.0
        except Exception:
            pass
        return None


def implied_prob_from_price(price_cents: float) -> float:
    """Convert Kalshi yes-contract price in cents to implied probability."""
    return price_cents / 100.0


def price_from_implied_prob(prob: float) -> float:
    """Convert probability to Kalshi yes-contract price in cents."""
    return prob * 100.0
