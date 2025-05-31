"""Unit tests for QFR proxy calculation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from vol_spiral_hunter.vol_spiral.process.metrics_calculator import calculate_qfr_proxy


def test_qfr_basic():
    events = [
        {"event": "add", "timestamp": 0},
        {"event": "cancel", "timestamp": 1},
        {"event": "cancel", "timestamp": 2},
        {"event": "cancel", "timestamp": 3},
    ]

    qfr = calculate_qfr_proxy(events, window_sec=5, current_ts=4)

    assert abs(qfr - 0.75) < 1e-6
