"""Utility functions and async loop for calculating metrics."""

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RollingStats:
    """Tracks an exponential moving average and variance."""

    alpha: float
    ema: Optional[float] = None
    var: float = 0.0

    def update(self, value: float) -> None:
        if self.ema is None:
            self.ema = value
            self.var = 0.0
            return
        delta = value - self.ema
        self.ema += self.alpha * delta
        self.var = (1 - self.alpha) * (self.var + self.alpha * delta * delta)

    @property
    def sd(self) -> float:
        return self.var ** 0.5


def calculate_qfr_proxy(events: Iterable[dict], window_sec: float, current_ts: float) -> float:
    """Return quote fade rate proxy using cancel/add events within window."""

    adds = 0
    cancels = 0
    for evt in events:
        if current_ts - evt.get("timestamp", 0) > window_sec:
            continue
        typ = evt.get("event")
        if typ == "add":
            adds += 1
        elif typ == "cancel":
            cancels += 1
    denom = adds + cancels + 1e-9
    return cancels / denom


async def process_incoming_data(
    config,
    input_queues: Dict[str, asyncio.Queue],
    output_storage_queue: asyncio.Queue,
    output_alert_queue: asyncio.Queue,
):
    """Consume data from ingesters, compute metrics, and dispatch results."""

    logger.info("Metrics processor starting...")

    qfr_window = config.getint("metrics_params", "qfr_window_sec", fallback=5)
    ema_period = config.getint("metrics_params", "qfr_baseline_ema_sec", fallback=300)
    alpha = 2 / (ema_period + 1)

    event_logs: Dict[str, Deque[dict]] = defaultdict(deque)
    qfr_stats: Dict[str, RollingStats] = defaultdict(lambda: RollingStats(alpha))

    while True:
        got_item = False
        for name, q in input_queues.items():
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                continue

            got_item = True

            if item is None:  # Sentinel for shutdown
                continue

            symbol = item.get("symbol") or item.get("instrument")
            event_logs[symbol].append(item)

            # purge old events
            now_ts = item.get("timestamp", 0) / 1000 if item.get("timestamp") else 0
            while event_logs[symbol] and now_ts - event_logs[symbol][0].get("timestamp", 0) / 1000 > qfr_window:
                event_logs[symbol].popleft()

            qfr = calculate_qfr_proxy(event_logs[symbol], qfr_window, now_ts)
            qfr_stats[symbol].update(qfr)

            metrics = {
                "symbol": symbol,
                "qfr": qfr,
                "qfr_ema": qfr_stats[symbol].ema,
                "qfr_sd": qfr_stats[symbol].sd,
            }

            await output_storage_queue.put(metrics)
            await output_alert_queue.put(metrics)

        if not got_item:
            await asyncio.sleep(0.01)
