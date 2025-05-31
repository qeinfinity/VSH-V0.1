# AGENTS.md - Vol-Spiral Hunter V0.1

This document outlines the full specification, current status, and active tasks for the "Vol-Spiral Hunter V0.1" project. Coding agents should refer to this document as the primary source of truth for project requirements and context.

## I. CURRENT PROJECT STATUS (as of last review)

The foundational project structure has been scaffolded. The following key components have seen initial or partial implementation:

1.  **Configuration (`vol_spiral/config_loader.py`, `config.ini`):**
    *   Loading from `.ini` and `.env` (for secrets) is implemented.
    *   Color logging option added to `config.ini`.
2.  **Logging (`vol_spiral/main.py`):**
    *   Standard logging to console and file is set up.
    *   Conditional color logging for console output using `colorama` is implemented.
3.  **NTP Check (`vol_spiral/utils/ntp_checker.py`, `vol_spiral/main.py`):**
    *   NTP offset check on startup is implemented.
4.  **Ingesters (`vol_spiral/ingest/`):**
    *   `binance_ingester.py`: Basic WebSocket connection for depth and aggTrade streams implemented, parsing messages and putting them onto asyncio queues. Snapshot seeding and full sequence handling are still TODOs.
    *   `deribit_ingester.py`: Basic WebSocket connection for BTC-PERPETUAL book/trades and BTC index implemented. Dynamic options instrument selection, subscription for options, and full order book management are still TODOs.
5.  **Metrics Calculator (`vol_spiral/process/metrics_calculator.py`):**
    *   Significant progress made. Includes:
        *   `EmaSdTracker` class for EMA and SD.
        *   `OrderBook` class with `apply`, `best_prices`, `depth_within` methods. QFR event generation logic is present.
        *   `MicroState` dataclass.
        *   `parse_instrument_name` helper.
        *   Main loop consumes from queues, updates local order books.
        *   Calculation of some microstructure metrics (spread, depth, QFR proxy, aggression imbalance, velocity, acceleration) and their EMAs/SDs.
        *   Initial implementation for calculating some options metrics (ATM IV, Skew, IV Bid-Ask, Term Structure Slope) based on incoming ticker data, and their EMAs/SDs.
        *   Outputs separate `metrics` (for micro) and `opt_metrics` (for options) dictionaries to storage/alert queues.
6.  **Storage (`vol_spiral/storage/influx_writer.py`):**
    *   `InfluxWriter` class structure is present.
    *   `connect()` and `close()` methods are implemented.
    *   `write_data_loop` has an initial implementation for batching and writing, consuming from a queue. Assumes input items are suitable for `self.write_api.write()`.
7.  **Alerting Stubs (`vol_spiral/alert/rules_stubs.py`):**
    *   Anomaly check functions (`check_qfr_anomaly`, `check_depth_collapse`, etc.) have been implemented with basic logic using thresholds.
    *   `process_signals` has rudimentary Level 1/2 alert logic.
    *   `alert_processor_loop` implemented to consume from its queue, call check functions, process signals, and log alerts.
8.  **Main Orchestration (`vol_spiral/main.py`):**
    *   Initializes queues for ingesters, storage, and alerts.
    *   Creates and gathers asyncio tasks for the main components.
9.  **Testing (`tests/test_qfr_calculation.py`):**
    *   A basic passing test for a simplified QFR calculation is present.

**Known Issues / Areas for Immediate Focus Based on Current Status:**
*   Deribit ingester needs full dynamic options instrument selection and data handling.
*   Metrics calculator needs to process options data more comprehensively and consolidate its output messages.
*   Robust order book snapshot/delta sequence management in ingesters.
*   InfluxDB writer needs to correctly format the (soon-to-be-consolidated) metrics into InfluxDB points.
*   Alerting logic in `process_signals` needs to be more comprehensive.

## II. CURRENT TASK(S) FOR THE AGENT

Based on the project status, the **primary active task** is to:

**Task 1: Refine `vol_spiral/process/metrics_calculator.py` and `vol_spiral/storage/influx_writer.py` for Consolidated Metrics & Storage.**

*   **A. Modify `vol_spiral/process/metrics_calculator.py`:**
    1.  **Consolidate Output:** Instead of sending separate `metrics` (for micro) and `opt_metrics` (for options) dictionaries to the `output_storage_queue` and `output_alert_queue`, modify the logic to produce a *single, comprehensive `full_metrics_payload` dictionary per processing cycle or per relevant instrument update*.
        *   This `full_metrics_payload` should contain keys like `timestamp`, `exchange`, `instrument` (base instrument, e.g., "BTCUSDT" or "BTC-PERPETUAL", or specific option name like "BTC-27JUN25-100000-C").
        *   It should have a nested `micro` dictionary containing all relevant microstructure metrics (qfr, spread, depth_bid, depth_ask, aggr_imbalance, velocity, acceleration, and their EMAs/SDs).
        *   If the instrument is an option, or if general options market data is relevant (like a summary of ATM IV for the underlying), it should have a nested `options` dictionary. This `options` dictionary might be structured by specific instrument name (for individual option metrics) or contain summary metrics (like ATM IV for the underlying, overall skew, term structure slope for key expiries).
        *   *Example Structure (Conceptual):*
            ```python
            # For a spot/perp instrument
            full_metrics_payload = {
                "timestamp_utc_ms": 1678886400000,
                "exchange": "binance",
                "instrument": "BTCUSDT",
                "micro": {
                    "qfr": 0.6, "qfr_ema": 0.5, "qfr_sd": 0.1,
                    "spread_bps": 5.0, "spread_bps_ema": 4.5, "spread_bps_sd": 1.0,
                    # ... other micro metrics ...
                },
                "options_summary": { # Optional: if we calculate overall market options stats
                     "atm_iv_1wk": 0.55, "atm_iv_1wk_ema": 0.54, # ...
                     "skew_25d_1wk": 0.02, # ...
                }
            }

            # For an individual option instrument (when its ticker/book updates)
            full_metrics_payload_option = {
                "timestamp_utc_ms": 1678886400000,
                "exchange": "deribit",
                "instrument": "BTC-27JUN25-100000-C",
                "base_instrument": "BTC", # For easier grouping
                "options_instrument_specific": {
                    "mark_iv": 0.55, "mark_iv_ema": 0.54, "mark_iv_sd": 0.02,
                    "delta": 0.48,
                    "iv_bid_ask_spread": 0.005,
                    # ... other specific option metrics ...
                }
                # Optionally, could also include latest micro metrics for the underlying (BTC-PERPETUAL)
            }
            ```
    2.  **Triggering Calculation/Output:** Decide when to emit this `full_metrics_payload`.
        *   For spot/perp instruments: Emit after each significant book update or trade that leads to new micro metric calculations.
        *   For options: Emit instrument-specific option metrics when its ticker/book updates. Consider if/how to link these to the underlying's micro metrics (e.g., by also querying the latest micro state for the underlying when an option ticker arrives).
        *   Consider a periodic emission (e.g., every second) that sends the latest calculated state for all actively tracked instruments if no new data has arrived to trigger an update.

*   **B. Modify `vol_spiral/storage/influx_writer.py`:**
    1.  In the `write_data_loop`, update the logic to correctly parse the incoming `full_metrics_payload` dictionary (from `output_storage_queue`).
    2.  Dynamically construct InfluxDB Line Protocol strings or Point objects based on the structure of `full_metrics_payload`.
        *   **Measurement:** Could be dynamic based on data type (e.g., `micro_metrics`, `option_instrument_metrics`, `options_summary_metrics`).
        *   **Tags:** `exchange`, `instrument`, `base_instrument`, `option_type`, `expiry`, `strike` (as applicable).
        *   **Fields:** All calculated metrics, EMAs, and SDs (e.g., `qfr`, `qfr_ema`, `qfr_sd`, `mark_iv`, `mark_iv_ema`, etc.).
        *   **Timestamp:** Use the `timestamp_utc_ms` from the payload.
    3.  Ensure batch writing to InfluxDB works correctly with these structured points.

*   **C. Update `vol_spiral/alert/rules_stubs.py`:**
    1.  Modify the `alert_processor_loop` to correctly destructure the incoming `full_metrics_payload`.
    2.  Update the calls to anomaly check functions (e.g., `check_qfr_anomaly`) to pass the correct values from the `full_metrics_payload` (e.g., `payload['micro']['qfr']`, `payload['micro']['qfr_ema']`, etc.).

**Task 2 (Lower Priority for this specific agent interaction, but next in line): Fully Implement Deribit Options Ingestion.**
*   Address the TODOs in `vol_spiral/ingest/deribit_ingester.py` for dynamic instrument selection based on config (`weekly_expiries`, `monthly_expiries`, ATM/25d logic) and subscribe to book, trade, and ticker streams for all selected option instruments.

---

## III. FULL PROJECT DIRECTIVE (V0.1 - As Previously Discussed)


You are an expert Python developer specializing in real-time financial market data processing and algorithmic trading systems. Your task is to create the foundational Python codebase for a 'Vol-Spiral Hunter V0.1' application. This application will ingest real-time market data from specified cryptocurrency exchanges, process it to detect precursors of volatility spirals based on microstructure and implied volatility signals, and generate alerts.

**Phase 1 of this directive focuses on data ingestion, processing, storage (to InfluxDB), basic signal calculation, and includes stubs for alerting logic. The goal is a runnable skeleton that requires only API keys and fine-tuning.**

**I. Core Requirements & Setup:**

1. **Configuration (`config.ini` & `.env`):**
    - Implement a `config.ini` file for general application parameters. (See Appendix A for an example `config.ini` structure and default values for all thresholds and windows).
    - API keys (Binance, Deribit) and other sensitive credentials (e.g., InfluxDB token) **MUST** be managed via environment variables, loaded at runtime using the `python-dotenv` library from a `.env` file (which will be gitignored). The `config.ini` can reference these using `${ENV_VAR}` syntax if your chosen `configparser` handling supports it, or they can be loaded directly in the Python code.
2. **Logging:**
    - Implement comprehensive logging using the `logging` module. Log errors, warnings, info (e.g., successful connections, data points received), and debug messages. Output to both console and a rotating log file. Configurable log level via `config.ini`.
3. **Asynchronous Operations:**
    - Utilize `asyncio` and `aiohttp` for all network requests (API calls, WebSocket connections) to ensure non-blocking I/O.
4. **Clock Synchronization:**
    - On application startup, and repeating hourly, use the `ntplib` library to check the local machine's clock offset against a reliable NTP server. If the offset is greater than `ntp_max_offset_ms` (from `config.ini`), log a critical error and abort the application.
5. **Dependencies:**
    - Generate a `requirements.txt` file including: `aiohttp`, `pandas`, `numpy`, `influxdb-client` (async version), `python-dotenv`, `configparser` (if not using a custom solution for env var expansion), `ntplib`, `pytest`, `orjson` (for fast JSON parsing). `ujson` is an optional alternative to `orjson`.
6. **Project Structure & License:**
    - Use an MIT license (include `LICENSE.txt`).
    - Structure the project as follows (see Appendix B for directory structure).

**II. Data Ingestion Modules (Separate modules for each exchange/data type):**

*Use an `asyncio.Queue` per exchange data stream (e.g., one for Binance books, one for Binance trades, one for Deribit books, etc.) feeding into the central processor. Set queue size via `config.ini` (e.g., `ws_queue_size`) and implement a "drop oldest" policy if the queue is full to manage back-pressure.*

1. **Binance Spot/Futures Data Ingester (`ingest/binance.py`):**
    - **Functionality:**
        - Connect to Binance Spot and/or USDT-M Futures WebSockets.
        - Subscribe to:
            - **Order Book Depth Streams:** Use `symbol@depth@100ms` (e.g., `btcusdt@depth@100ms`).
                - **Order Book Management:** On initial connect (and reconnect), fetch a REST API snapshot (e.g., `/api/v3/depth` with `limit=1000`) to seed the local order book. Subsequently, apply WebSocket delta updates. If update IDs (`u`, `U`) are present and sequence gaps are detected, log a warning and consider a re-sync via snapshot. (See Appendix C for example Binance depth update payload).
            - **Aggregated Trade Streams (`symbol@aggTrade`):** (See Appendix C for example Binance aggTrade payload).
    - **Output:** Parsed data (Python dicts or dataclasses with UTC millisecond timestamps, price, size, side, etc.) put onto the respective `asyncio.Queue`.
2. **Deribit Options & Futures Data Ingester (`ingest/deribit.py`):**
    - **Functionality:**
        - Connect to Deribit WebSockets. Authenticate using API keys for private channels if needed (though most data here is public).
        - **Instrument Selection (Options):**
            - Every `instruments_refresh_sec` (from `config.ini`), call `public/get_instruments` for `currency=BTC`, `kind=option`, `expired=false`.
            - Sort by expiration ascending. Identify the first `weekly_expiries` (from `config.ini`) unique weekly expiry dates and the first `monthly_expiries` unique monthly expiry dates occurring after the last selected weekly.
            - For each of these selected expiries:
                - Fetch all strikes.
                - Identify the ATM strike (instrument whose `mark_price` is closest to the current `deribit_price_index.btc_usd`).
                - Identify the put and call strikes whose `delta` field (from ticker data) is closest to -0.25 (put) and +0.25 (call) respectively.
            - Subscribe/re-subscribe to streams for this dynamic list of selected instruments.
        - Subscribe to:
            - **Order Book Streams (`book.{instrument_name}.100ms` with depth 10 or 20):** For selected options series and `BTC-PERPETUAL`.
                - **Order Book Management:** Use `public/get_order_book` for initial snapshot. Apply WebSocket updates using `change_id` and `prev_change_id` for sequencing. If a gap is detected, log and re-sync with a snapshot. (See Appendix C for example Deribit book update payload).
            - **Trade Streams (`trades.{instrument_name}.100ms`):** For selected options series and `BTC-PERPETUAL`. (See Appendix C for example Deribit trade payload).
            - **Ticker Streams (`ticker.{instrument_name}.100ms`):** For selected options series to get `mark_iv`, `bid_iv`, `ask_iv`, `delta`, `gamma`, `vega`, `theta`.
            - **Deribit Index Price (`deribit_price_index.btc_usd`):** For ATM strike determination.
    - **Output:** Parsed data put onto the respective `asyncio.Queue`.

**III. Data Processing & Metric Calculation Module (`process/metrics.py`):**

*This module consumes data from the ingester queues and calculates critical metrics.*

1. **Microstructure Metrics (From Binance & Deribit Perp Data):**
    - **For each monitored spot/perp symbol:**
        - **Maintain Real-time Order Book Snapshot.**
        - **Calculate Top-of-Book Bid/Ask Spread.**
        - **Calculate Order Book Depth:** Sum of quote sizes on bid and ask sides within `depth_pct_band` (from `config.ini`) of the mid-price.
        - **Calculate Quote Fade Rate (QFR) Proxy:**
            - Monitor the top `qfr_levels` (from `config.ini`) of the bid and ask sides.
            - A "cancel event" is defined as any book update message that results in a price level (that was previously in the top-N levels) having its quantity set to 0 (Binance) or an amount < 0 being effectively a removal (Deribit, or any delta update that zeros out a level). An "add event" is a new order appearing in the top-N.
            - Over a rolling `qfr_window_sec` (from `config.ini`):`QFR = (count of cancel events in window) / (count of cancel events + count of add events in window + 1e-9)` (add small epsilon to avoid division by zero).
        - **Calculate Aggressive Trade Imbalance:** Rolling sum over `aggression_window_sec` of (volume of trades hitting ask - volume of trades hitting bid).
        - **Track Large Trades:** Flag trades exceeding a configurable multiple of the rolling average trade size.
        - **Spot Price & Volume:** Calculate price velocity and acceleration.
2. **Options Metrics (From Deribit Data):**
    - For selected front-month options series:
        - **ATM Implied Volatility (Front-Month):** Use `mark_iv` from ticker for the selected ATM strike.
        - **25-Delta Skew (Front-Month):** (`mark_iv` of selected 25d Put - `mark_iv` of selected 25d Call).
        - **IV Term Structure Slope:** (e.g., (1-Month ATM `mark_iv`) - (1-Week ATM `mark_iv`)).
        - **IV Bid-Ask Spreads for Key Series:** (`ask_iv` - `bid_iv`) from ticker data.
3. **Baseline Calculations:**
    - For QFR, Depth, Spreads, Aggression Imbalance, ATM IV, Skew: Calculate dynamic rolling Exponential Moving Averages (EMAs) using EMA periods from `config.ini` (e.g., `qfr_baseline_ema_sec`).
    - Calculate rolling Standard Deviations for these metrics over the same EMA periods.

**IV. Data Storage Module (`storage/influx.py`):**

- **Functionality:**
    - Connect to InfluxDB. Batch writes every `flush_interval_ms` (from `config.ini`).
    - **Schema (Line Protocol Examples):**
        - `micro_spot,exchange=binance,symbol=BTCUSDT qfr_proxy_score=0.43,depth_bid_0.5pct=10.5,spread_bps=5.1 <timestamp_ns>`
        - `micro_perp,exchange=deribit,symbol=BTC-PERPETUAL qfr_proxy_score=0.39,depth_ask_0.5pct=12.2 <timestamp_ns>`
        - `options_metrics,exchange=deribit,symbol=BTC,expiry=20250627,strike=100000,type=call atm_iv_mark=0.55,delta_mark=0.48 <timestamp_ns>`
        - `baselines,metric_source=micro_spot,symbol=BTCUSDT,metric_name=qfr_proxy_score ema_value=0.30,sd_value=0.05 <timestamp_ns>`
    - Store all processed metrics, their baselines, and their standard deviations.

**V. Signal Generation Stubs & Alerting Logic Stubs (`alert/rules.py`):**

*Function signatures with `NotImplementedError` or print statements. Thresholds from `config.ini` should be passed.*

1. **Microstructure Anomaly Detector Functions:**
    - `check_qfr_anomaly(symbol, current_qfr, baseline_qfr, sd_qfr, qfr_sd_threshold) -> bool:`
    - `check_depth_collapse(symbol, current_depth_bid, baseline_depth_bid, depth_collapse_pct_threshold) -> bool:` (and similar for ask)
    - `check_spread_blowout(symbol, current_spread, baseline_spread, spread_widen_pct_threshold) -> bool:`
    - `check_aggression_imbalance(symbol, current_imbalance_ratio, aggression_ratio_threshold) -> bool:`
2. **IV Anomaly Detector Functions:**
    - `check_iv_spike(symbol, current_atm_iv, baseline_atm_iv, atm_iv_jump_vols_threshold) -> bool:`
    - `check_skew_shift(symbol, current_skew, baseline_skew, skew_shift_vols_threshold) -> bool:`
3. **Spiral Alert Logic Core Function:**
    - `process_signals(symbol, micro_signals: dict, iv_signals: dict, price_signals: dict) -> str: # Returns "NO_ALERT", "LEVEL_1_PRECOG", "LEVEL_2_INITIATING", "LEVEL_3_MAX_Q"`

**VI. Main Application Orchestration (`main.py`):**

- Initialize logging, load config (including `.env`), NTP check.
- Initialize InfluxDB client.
- Create `asyncio.Queue` instances.
- Create and start ingester tasks, processor task (consuming from queues), storage task (consuming from processor or a dedicated storage queue).
- The processor task, after calculating metrics, will also call the stubbed `alerter.py` functions.
- Handle graceful shutdown.

**VII. Testing (`tests/` directory):**

- Include at least one `pytest` test:
    - `test_qfr_calculation.py`: Feeds synthetic order book update data (Python dicts mocking exchange payloads) to the QFR calculation logic and asserts that specific sequences of adds/cancels produce expected QFR proxy scores and that an anomaly is triggered if it crosses a threshold.

**VIII. Documentation:**

- Well-commented code. Type hinting.
- `README.md`: Setup (`.env`, `config.ini`), dependencies, running the app, brief overview of modules.

---

**Appendix A: Example `config.ini` (Defaults based on your suggestions)**

```
iniCopy code
; Vol-Spiral Hunter V0.1 Configuration
; API Keys should be set as Environment Variables (e.g., BINANCE_API_KEY, DERIBIT_CLIENT_ID, INFLUXDB_TOKEN)

[binance]
; api_key and api_secret loaded from environment variables
symbols     = BTCUSDT,ETHUSDT        ; Comma-separated spot/perp symbols for Binance

[deribit]
; client_id and client_secret loaded from environment variables
instruments_refresh_sec = 60         ; How often to refresh the list of Deribit option instruments
deribit_perp_symbol = BTC-PERPETUAL  ; Perpetual futures symbol on Deribit

[influxdb]
url                = http://localhost:8086 ; Example: https://us-east-1-1.aws.cloud2.influxdata.com
org                = your-org
bucket             = vol_spiral
; token loaded from environment variable INFLUXDB_TOKEN
flush_interval_ms  = 500             ; Batch write metrics to InfluxDB every 0.5 seconds

[metrics_params]
; Microstructure Parameters
qfr_levels               = 5           ; Top-N book levels to watch for QFR calculation
qfr_window_sec           = 5           ; Rolling window for QFR calculation
qfr_baseline_ema_sec     = 300         ; EMA period for QFR baseline
qfr_sd_threshold         = 2.5         ; SD multiplier for QFR anomaly
depth_pct_band           = 0.005       ; 0.5% from mid-price for depth sum calculation
depth_baseline_ema_sec   = 300         ; EMA period for depth baseline
depth_collapse_pct_threshold = 40      ; Percentage drop for depth collapse alert
spread_baseline_ema_sec  = 60          ; EMA period for spread baseline
spread_widen_pct_threshold = 200       ; Percentage widening for spread blowout alert (relative to baseline)
aggression_window_sec    = 180         ; Rolling window for aggressive trade imbalance
aggression_ratio_threshold = 0.75      ; Ratio (e.g., 75% of aggressive vol on one side) for imbalance alert
price_accel_window_sec   = 10          ; Window for price velocity/acceleration

; Implied Volatility Parameters
atm_iv_baseline_ema_sec  = 300         ; EMA period for ATM IV baseline
atm_iv_jump_vols_threshold = 3         ; Absolute jump in vols for IV spike alert
skew_baseline_ema_sec    = 300         ; EMA period for Skew baseline
skew_shift_vols_threshold  = 2         ; Absolute jump in vols for Skew shift alert

[options_selection]
weekly_expiries          = 4           ; Number of front-month weekly expiries to track
monthly_expiries         = 2           ; Number of front-month serial monthly expiries to track

[system]
ws_queue_size            = 10000       ; Max size for internal asyncio.Queues
ntp_max_offset_ms        = 100         ; Abort if local clock drift from NTP is greater than this
log_level                = INFO        ; DEBUG, INFO, WARNING, ERROR, CRITICAL

```

**Appendix B: Project Directory Structure**

```
Copy code
vol_spiral_hunter/
├── vol_spiral/
│   ├── __init__.py
│   ├── main.py
│   ├── config_loader.py  # Handles loading config.ini & .env
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── base_ingester.py # Optional base class
│   │   ├── binance_ingester.py
│   │   └── deribit_ingester.py
│   ├── process/
│   │   ├── __init__.py
│   │   └── metrics_calculator.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── influx_writer.py
│   └── alert/
│       ├── __init__.py
│       └── rules_stubs.py
├── tests/
│   ├── __init__.py
│   └── test_qfr_calculation.py
├── config.ini.example       # Example config, user copies to config.ini
├── .env.example             # Example .env, user copies to .env
├── README.md
├── requirements.txt
├── LICENSE.txt
└── run.sh                   # Optional simple run script
```**Appendix C: Example Minimal JSON Payloads (Conceptual for LLM)**

*   **Binance Depth Update (`symbol@depth@100ms`):**
    ```json
    {
      "e": "depthUpdate", // Event type
      "E": 1672515782136, // Event time
      "s": "BTCUSDT",     // Symbol
      "U": 157,           // First update ID in event
      "u": 160,           // Final update ID in event
      "b": [              // Bids to be updated
        ["20000.00", "0.5"], // Price level, Quantity
        ["19990.00", "0"]    // Quantity 0 means remove
      ],
      "a": [              // Asks to be updated
        ["20010.00", "1.2"]
      ]
    }
    ```
*   **Binance AggTrade (`symbol@aggTrade`):**
    ```json
    {
      "e": "aggTrade",  // Event type
      "E": 1672515782136, // Event Time
      "s": "BTCUSDT",     // Symbol
      "a": 12345,         // Aggregate trade ID
      "p": "20005.00",    // Price
      "q": "0.01",        // Quantity
      "f": 100,           // First trade ID
      "l": 105,           // Last trade ID
      "T": 1672515782130, // Trade time
      "m": true,          // Is the buyer the market maker?
      "M": true           // Ignore
    }
    ```
*   **Deribit Book Update (`book.{instrument_name}.100ms`):**
    ```json
    {
      "jsonrpc": "2.0",
      "method": "subscription",
      "params": {
        "channel": "book.BTC-PERPETUAL.10.100ms",
        "data": {
          "type": "change", // or "snapshot"
          "timestamp": 1672515782136,
          "instrument_name": "BTC-PERPETUAL",
          "change_id": 3456,
          "prev_change_id": 3455,
          "bids": [
            ["change", 20000.0, 0.5], // type, price, amount (amount < 0 or "remove" for deletion)
            ["delete", 19990.0, 0]
          ],
          "asks": [
            ["new", 20010.0, 1.2]
          ]
        }
      }
    }
    ```
*   **Deribit Trade (`trades.{instrument_name}.100ms`):**
    ```json
    {
      "jsonrpc": "2.0",
      "method": "subscription",
      "params": {
        "channel": "trades.BTC-PERPETUAL.100ms",
        "data": [
          {
            "trade_seq": 5678,
            "trade_id": "BTC-12345",
            "timestamp": 1672515782136,
            "instrument_name": "BTC-PERPETUAL",
            "price": 20005.0,
            "amount": 100.0, // USD for perps, contracts for options
            "direction": "buy", // or "sell"
            "index_price": 20004.5
          }
        ]
      }
    }
    ```
*   **Deribit Ticker (`ticker.{instrument_name}.100ms` for options):**
    ```json
    {
      "jsonrpc": "2.0",
      "method": "subscription",
      "params": {
        "channel": "ticker.BTC-27JUN25-100000-C.100ms",
        "data": {
          "timestamp": 1672515782136,
          "instrument_name": "BTC-27JUN25-100000-C",
          "best_bid_price": 0.1050, "best_ask_price": 0.1055,
          "mark_price": 0.1052,
          "mark_iv": 55.25, "bid_iv": 55.00, "ask_iv": 55.50,
          "greeks": {"delta": 0.48, "gamma": 0.00012, "vega": 25.5, "theta": -5.2},
          "stats": {"volume_24h": 100, "open_interest": 5000}
          // ... other fields
        }
      }
    }
    ```

---


```
