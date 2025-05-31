"""Asynchronous metrics calculator for Vol-Spiral Hunter.

This module consumes parsed messages from the exchange ingesters via asyncio
queues and calculates both microstructure and options metrics.  The results are
forwarded to storage and alert queues.

The ingesters are expected to put normalised dictionaries on their output
queues.  Example message formats:

- Order book update::
    {
        "type": "book",
        "exchange": "binance" | "deribit",
        "instrument": "BTCUSDT" | "BTC-PERPETUAL" | "BTC-27JUN25-100000-C",
        "bids": [[price, size], ...],
        "asks": [[price, size], ...],
        "timestamp": 1672515782136
    }

- Trade::
    {
        "type": "trade",
        "exchange": "binance" | "deribit",
        "instrument": "BTCUSDT",
        "price": 20000.0,
        "size": 0.5,
        "side": "buy" | "sell",
        "timestamp": 1672515782136
    }

- Ticker (for options)::
    {
        "type": "ticker",
        "exchange": "deribit",
        "instrument": "BTC-27JUN25-100000-C",
        "mark_iv": 55.0,
        "bid_iv": 54.8,
        "ask_iv": 55.2,
        "delta": 0.45,
        "timestamp": 1672515782136
    }

The exact payloads may vary slightly; this processor only requires the fields
shown above.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers for EMA and SD calculations
# ---------------------------------------------------------------------------
class EmaSdTracker:
    """Tracks exponential moving average and standard deviation."""

    def __init__(self, period: int) -> None:
        self.alpha = 2 / float(period + 1)
        self.ema: float | None = None
        self.var: float | None = None

    def update(self, value: float) -> Tuple[float, float]:
        if self.ema is None:
            self.ema = value
            self.var = 0.0
        else:
            diff = value - self.ema
            self.ema += self.alpha * diff
            # Exponential moving variance
            self.var = (1 - self.alpha) * (self.var + self.alpha * diff * diff)
        return self.ema, (self.var or 0.0) ** 0.5


# ---------------------------------------------------------------------------
# Order book model
# ---------------------------------------------------------------------------
@dataclass
class OrderBook:
    bids: Dict[float, float] = field(default_factory=dict)
    asks: Dict[float, float] = field(default_factory=dict)

    def apply(self, bids: List[Tuple[float, float]] | None, asks: List[Tuple[float, float]] | None) -> List[str]:
        """Apply updates to the book and return QFR events."""
        events: List[str] = []
        if bids:
            for price, qty in bids:
                price = float(price)
                qty = float(qty)
                prev = self.bids.get(price, 0.0)
                if qty <= 0:
                    if price in self.bids:
                        del self.bids[price]
                        if prev > 0:
                            events.append("cancel")
                else:
                    if prev == 0:
                        events.append("add")
                    self.bids[price] = qty
        if asks:
            for price, qty in asks:
                price = float(price)
                qty = float(qty)
                prev = self.asks.get(price, 0.0)
                if qty <= 0:
                    if price in self.asks:
                        del self.asks[price]
                        if prev > 0:
                            events.append("cancel")
                else:
                    if prev == 0:
                        events.append("add")
                    self.asks[price] = qty
        return events

    def best_prices(self) -> Tuple[float | None, float | None]:
        if not self.bids or not self.asks:
            return None, None
        return max(self.bids.keys()), min(self.asks.keys())

    def depth_within(self, pct_band: float) -> Tuple[float, float]:
        bid_depth = 0.0
        ask_depth = 0.0
        best_bid, best_ask = self.best_prices()
        if best_bid is None or best_ask is None:
            return 0.0, 0.0
        mid = (best_bid + best_ask) / 2
        bid_limit = mid * (1 - pct_band)
        ask_limit = mid * (1 + pct_band)
        for price, qty in self.bids.items():
            if price >= bid_limit:
                bid_depth += qty
        for price, qty in self.asks.items():
            if price <= ask_limit:
                ask_depth += qty
        return bid_depth, ask_depth


# ---------------------------------------------------------------------------
# Metrics processor
# ---------------------------------------------------------------------------
@dataclass
class MicroState:
    book: OrderBook = field(default_factory=OrderBook)
    qfr_events: Deque[Tuple[int, str]] = field(default_factory=deque)
    trades: Deque[Tuple[int, float, str]] = field(default_factory=deque)
    prices: Deque[Tuple[int, float]] = field(default_factory=deque)


def parse_instrument_name(name: str) -> Tuple[str, str, float, str]:
    """Parse Deribit style instrument name into parts."""
    try:
        base, exp, strike, option_type = name.split("-")
        return base, exp, float(strike), "call" if option_type.upper().startswith("C") else "put"
    except Exception:  # pragma: no cover - best effort parsing
        return "", "", 0.0, ""


async def process_incoming_data(config, input_queues: Dict[str, asyncio.Queue], output_storage_queue: asyncio.Queue, output_alert_queue: asyncio.Queue) -> None:
    logger.info("Metrics processor starting...")

    params = config["metrics_params"] if config.has_section("metrics_params") else {}
    qfr_window = int(params.get("qfr_window_sec", 5)) * 1000
    depth_band = float(params.get("depth_pct_band", 0.005))
    aggression_window = int(params.get("aggression_window_sec", 180)) * 1000
    accel_window = int(params.get("price_accel_window_sec", 10)) * 1000

    # EMA trackers
    ema_trackers = defaultdict(lambda: defaultdict(lambda: EmaSdTracker(50)))
    # instrument -> MicroState
    micro: Dict[str, MicroState] = defaultdict(MicroState)
    # expiry -> { instrument_name: ticker_info }
    option_tickers: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)

    queues = {name: q for name, q in input_queues.items()}

    while True:
        get_tasks = {asyncio.create_task(q.get()): name for name, q in queues.items()}
        done, pending = await asyncio.wait(get_tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            src = get_tasks[task]
            msg = task.result()
            instrument = msg.get("instrument") or msg.get("symbol")
            ts = int(msg.get("timestamp", 0))
            state = micro[instrument]

            if msg.get("type") == "book":
                events = state.book.apply(msg.get("bids"), msg.get("asks"))
                for ev in events:
                    state.qfr_events.append((ts, ev))
                while state.qfr_events and state.qfr_events[0][0] < ts - qfr_window:
                    state.qfr_events.popleft()
            elif msg.get("type") == "trade":
                state.trades.append((ts, float(msg.get("size", 0.0)), msg.get("side", "")))
                while state.trades and state.trades[0][0] < ts - aggression_window:
                    state.trades.popleft()
                state.prices.append((ts, float(msg.get("price", 0.0))))
                while state.prices and state.prices[0][0] < ts - accel_window:
                    state.prices.popleft()
            elif msg.get("type") == "ticker":
                base, exp, strike, opt_type = parse_instrument_name(instrument)
                option_tickers[exp][instrument] = {
                    "mark_iv": float(msg.get("mark_iv", 0.0)),
                    "bid_iv": float(msg.get("bid_iv", 0.0)),
                    "ask_iv": float(msg.get("ask_iv", 0.0)),
                    "delta": float(msg.get("delta", 0.0)),
                    "type": opt_type,
                }

            # ------------------------------------------------------------------
            # Calculate microstructure metrics when we have a book
            # ------------------------------------------------------------------
            best_bid, best_ask = state.book.best_prices()
            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2
                spread = (best_ask - best_bid) / mid * 10000  # bps
                depth_bid, depth_ask = state.book.depth_within(depth_band)
                cancel_count = sum(1 for t, ev in state.qfr_events if ev == "cancel")
                add_count = sum(1 for t, ev in state.qfr_events if ev == "add")
                qfr = cancel_count / (cancel_count + add_count + 1e-9)

                buy_vol = sum(size for t, size, side in state.trades if side == "buy")
                sell_vol = sum(size for t, size, side in state.trades if side == "sell")
                imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol + 1e-9)

                velocity = acceleration = 0.0
                if len(state.prices) >= 2:
                    (t1, p1), (t2, p2) = state.prices[-2], state.prices[-1]
                    velocity = (p2 - p1) / max((t2 - t1), 1)
                if len(state.prices) >= 3:
                    (t0, p0), (t1, p1), (t2, p2) = state.prices[-3], state.prices[-2], state.prices[-1]
                    v1 = (p1 - p0) / max((t1 - t0), 1)
                    v2 = (p2 - p1) / max((t2 - t1), 1)
                    acceleration = (v2 - v1) / max((t2 - t0), 1)

                metrics = {
                    "exchange": msg.get("exchange"),
                    "instrument": instrument,
                    "micro": {
                        "spread_bps": spread,
                        "depth_bid": depth_bid,
                        "depth_ask": depth_ask,
                        "qfr": qfr,
                        "aggr_imbalance": imbalance,
                        "velocity": velocity,
                        "acceleration": acceleration,
                    },
                }

                # Update EMAs for micro metrics
                for m_name, value in metrics["micro"].items():
                    ema, sd = ema_trackers[m_name][instrument].update(value)
                    metrics["micro"][f"{m_name}_ema"] = ema
                    metrics["micro"][f"{m_name}_sd"] = sd

                await output_storage_queue.put(metrics)
                await output_alert_queue.put(metrics)

            # ------------------------------------------------------------------
            # Calculate option metrics whenever ticker data updates
            # ------------------------------------------------------------------
            if msg.get("type") == "ticker":
                exp_data = option_tickers[exp]
                if not exp_data:
                    continue
                # determine ATM and 25d instruments
                atm = min(exp_data.items(), key=lambda kv: abs(kv[1].get("delta", 0)))
                call_25 = min((kv for kv in exp_data.items() if kv[1]["type"] == "call"), key=lambda kv: abs(kv[1].get("delta", 0.25) - 0.25))
                put_25 = min((kv for kv in exp_data.items() if kv[1]["type"] == "put"), key=lambda kv: abs(kv[1].get("delta", -0.25) + 0.25))

                atm_iv = atm[1]["mark_iv"]
                skew = put_25[1]["mark_iv"] - call_25[1]["mark_iv"]
                iv_bid_ask = msg.get("ask_iv", 0.0) - msg.get("bid_iv", 0.0)

                # Term structure slope using first two expiries if available
                slope = 0.0
                expiries = sorted(option_tickers.keys())
                if len(expiries) >= 2:
                    e1, e2 = expiries[0], expiries[1]
                    iv1 = min(option_tickers[e1].values(), key=lambda v: abs(v.get("delta", 0)))
                    iv2 = min(option_tickers[e2].values(), key=lambda v: abs(v.get("delta", 0)))
                    slope = iv2["mark_iv"] - iv1["mark_iv"]

                opt_metrics = {
                    "exchange": "deribit",
                    "instrument": instrument,
                    "options": {
                        "atm_iv": atm_iv,
                        "skew_25d": skew,
                        "term_slope": slope,
                        "iv_bid_ask_spread": iv_bid_ask,
                    },
                }

                for m_name, value in opt_metrics["options"].items():
                    ema, sd = ema_trackers[m_name][instrument].update(value)
                    opt_metrics["options"][f"{m_name}_ema"] = ema
                    opt_metrics["options"][f"{m_name}_sd"] = sd

                await output_storage_queue.put(opt_metrics)
                await output_alert_queue.put(opt_metrics)

        # End for task
    # End while True
