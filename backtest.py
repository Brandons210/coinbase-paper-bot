import time
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# V5 — BREAKOUT / VOLATILITY EXPANSION
# PAPER + BACKTEST ONLY
# ============================================================

DAYS_TO_TEST = 365

# 1-hour execution candles
GRANULARITY_SECONDS = 3600
CANDLES_PER_REQUEST = 250

STARTING_CASH = float(config.STARTING_CASH)

RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

# 60 / 20 / 20 chronological split
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20

MIN_TRAIN_TRADES = 8
FINALISTS = 20


# ============================================================
# IMPORTANT:
# Stage 1 tests GROSS EDGE with ZERO fees.
#
# Only if a setup survives holdout gross-positive should we
# move it into a separate realistic fee/slippage test.
# ============================================================

FEE_RATE = 0.0


# ============================================================
# PARAMETER GRID
# ============================================================

BREAKOUT_LOOKBACKS = [12, 24, 48]
TREND_EMAS = [50, 100]

VOLUME_MULTS = [0.8, 1.0, 1.2]

ATR_STOP_MULTS = [1.5, 2.0, 2.5]

ATR_TARGET_MULTS = [3.0, 4.0, 6.0]

# Exit if no target/stop after this many hours.
MAX_HOLD_BARS = [24, 48, 72]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if not values:
        return []

    multiplier = 2.0 / (period + 1)

    current = values[0]
    output = [current]

    for value in values[1:]:
        current = (
            value * multiplier
            + current * (1.0 - multiplier)
        )

        output.append(current)

    return output


def atr(candles, period=14):
    output = [0.0] * len(candles)
    true_ranges = [0.0] * len(candles)

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]

        previous_close = (
            candles[i - 1]["close"]
        )

        true_ranges[i] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

    if len(candles) <= period:
        return output

    current = (
        sum(true_ranges[1:period + 1])
        / period
    )

    output[period] = current

    for i in range(
        period + 1,
        len(candles)
    ):
        current = (
            current * (period - 1)
            + true_ranges[i]
        ) / period

        output[i] = current

    return output


# ============================================================
# COINBASE DATA
# ============================================================

def iso_time(dt):
    return dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def get_history(product_name):
    now = datetime.now(timezone.utc)

    end_timestamp = (
        int(now.timestamp())
        // GRANULARITY_SECONDS
        * GRANULARITY_SECONDS
    )

    end_time = datetime.fromtimestamp(
        end_timestamp - GRANULARITY_SECONDS,
        timezone.utc,
    )

    start_time = (
        end_time
        - timedelta(days=DAYS_TO_TEST)
    )

    all_candles = {}

    cursor = start_time
    batches = 0

    chunk_seconds = (
        CANDLES_PER_REQUEST
        * GRANULARITY_SECONDS
    )

    print()
    print(
        f"Downloading {DAYS_TO_TEST} days "
        f"of {product_name} 1H candles..."
    )

    while cursor < end_time:
        chunk_end = min(
            cursor
            + timedelta(
                seconds=chunk_seconds
            ),
            end_time,
        )

        params = urllib.parse.urlencode({
            "start": iso_time(cursor),
            "end": iso_time(chunk_end),
            "granularity":
                GRANULARITY_SECONDS,
        })

        url = (
            "https://api.exchange.coinbase.com/"
            f"products/{product_name}/candles?"
            f"{params}"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "coinbase-v5-backtest/1.0",
                "Accept":
                    "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            raw = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        if not isinstance(raw, list):
            raise RuntimeError(
                f"Unexpected response: {raw}"
            )

        for c in raw:
            if len(c) < 6:
                continue

            candle = {
                "time": int(c[0]),
                "low": float(c[1]),
                "high": float(c[2]),
                "open": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }

            all_candles[
                candle["time"]
            ] = candle

        batches += 1

        if batches % 5 == 0:
            print(
                f"Downloaded "
                f"{batches} batches..."
            )

        cursor = chunk_end

        time.sleep(0.20)

    candles = list(
        all_candles.values()
    )

    candles.sort(
        key=lambda x: x["time"]
    )

    print(
        f"Total unique candles: "
        f"{len(candles)}"
    )

    if candles:
        first = datetime.fromtimestamp(
            candles[0]["time"],
            timezone.utc,
        )

        last = datetime.fromtimestamp(
            candles[-1]["time"],
            timezone.utc,
        )

        print(
            f"Range: {first} -> {last}"
        )

    return candles


# ============================================================
# BACKTEST
# ============================================================

def run_strategy(
    candles,
    breakout_lookback,
    trend_period,
    volume_mult,
    atr_stop_mult,
    atr_target_mult,
    max_hold_bars,
):

    if len(candles) < 300:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    trend = ema(
        closes,
        trend_period
    )

    atr_values = atr(
        candles,
        ATR_PERIOD
    )

    cash = STARTING_CASH

    position = None
    pending_entry = None

    trades = []

    peak_equity = cash
    max_drawdown = 0.0

    start_index = max(
        breakout_lookback + 5,
        trend_period + 5,
        VOLUME_LOOKBACK + 5,
        ATR_PERIOD + 5,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        # ====================================================
        # NEXT-BAR ENTRY
        # ====================================================

        if (
            pending_entry is not None
            and position is None
        ):
            entry_price = candle["open"]

            atr_at_signal = (
                pending_entry["atr"]
            )

            stop_distance = (
                atr_at_signal
                * atr_stop_mult
            )

            target_distance = (
                atr_at_signal
                * atr_target_mult
            )

            if (
                entry_price > 0
                and stop_distance > 0
            ):
                risk_dollars = (
                    cash
                    * RISK_PER_TRADE
                )

                qty_by_risk = (
                    risk_dollars
                    / stop_distance
                )

                qty_by_position = (
                    cash
                    * MAX_POSITION_PCT
                    / entry_price
                )

                qty = min(
                    qty_by_risk,
                    qty_by_position,
                )

                if qty > 0:
                    entry_value = (
                        qty * entry_price
                    )

                    if entry_value <= cash:
                        cash -= entry_value

                        position = {
                            "entry":
                                entry_price,

                            "qty":
                                qty,

                            "stop":
                                entry_price
                                - stop_distance,

                            "target":
                                entry_price
                                + target_distance,

                            "bars_held":
                                0,
                        }

            pending_entry = None

        # ====================================================
        # POSITION MANAGEMENT
        # ====================================================

        if position is not None:
            position["bars_held"] += 1

            stop_hit = (
                candle["low"]
                <= position["stop"]
            )

            target_hit = (
                candle["high"]
                >= position["target"]
            )

            exit_price = None
            reason = None

            # Conservative if both occur in same hourly bar.
            if stop_hit:
                exit_price = (
                    position["stop"]
                )

                reason = "STOP"

            elif target_hit:
                exit_price = (
                    position["target"]
                )

                reason = "TARGET"

            elif (
                position["bars_held"]
                >= max_hold_bars
            ):
                exit_price = (
                    candle["close"]
                )

                reason = "TIME"

            if exit_price is not None:
                exit_value = (
                    position["qty"]
                    * exit_price
                )

                gross_pnl = (
                    (
                        exit_price
                        - position["entry"]
                    )
                    * position["qty"]
                )

                cash += exit_value

                trades.append({
                    "pnl": gross_pnl,
                    "reason": reason,
                })

                position = None

        # ====================================================
        # BREAKOUT SIGNAL
        # ====================================================

        if (
            position is None
            and pending_entry is None
            and i < len(candles) - 1
        ):
            price = candle["close"]

            current_atr = (
                atr_values[i]
            )

            if (
                price <= 0
                or current_atr <= 0
            ):
                continue

            # Highest HIGH of PREVIOUS candles.
            # Current candle is deliberately excluded.
            prior_high = max(
                candles[j]["high"]
                for j in range(
                    i - breakout_lookback,
                    i,
                )
            )

            volume_window = [
                candles[j]["volume"]
                for j in range(
                    i - VOLUME_LOOKBACK,
                    i,
                )
            ]

            avg_volume = (
                sum(volume_window)
                / len(volume_window)
            )

            # ----------------------------------------------
            # HIGHER-TIMEFRAME-LIKE REGIME
            #
            # We're using a much slower hourly EMA as a
            # regime filter instead of another crossover.
            # ----------------------------------------------

            trend_ok = (
                price > trend[i]
                and
                trend[i] > trend[i - 6]
            )

            # ----------------------------------------------
            # TRUE BREAKOUT
            # ----------------------------------------------

            breakout_ok = (
                price > prior_high
            )

            # ----------------------------------------------
            # VOLUME EXPANSION
            # ----------------------------------------------

            volume_ok = (
                candle["volume"]
                >= avg_volume
                * volume_mult
            )

            # ----------------------------------------------
            # VOLATILITY FILTER
            #
            # Avoid extremely dead conditions.
            # ----------------------------------------------

            atr_pct = (
                current_atr / price
            )

            volatility_ok = (
                atr_pct >= 0.003
            )

            signal = (
                trend_ok
                and breakout_ok
                and volume_ok
                and volatility_ok
            )

            if signal:
                pending_entry = {
                    "atr":
                        current_atr
                }

        # ====================================================
        # EQUITY / DRAWDOWN
        # ====================================================

        equity = cash

        if position is not None:
            equity += (
                position["qty"]
                * candle["close"]
            )

        peak_equity = max(
            peak_equity,
            equity,
        )

        if peak_equity > 0:
            drawdown = (
                peak_equity
                - equity
            ) / peak_equity

            max_drawdown = max(
                max_drawdown,
                drawdown,
            )

    # ========================================================
    # CLOSE AT END OF TEST WINDOW
    # ========================================================

    if position is not None:
        final_price = (
            candles[-1]["close"]
        )

        exit_value = (
            position["qty"]
            * final_price
        )

        pnl = (
            (
                final_price
                - position["entry"]
            )
            * position["qty"]
        )

        cash += exit_value

        trades.append({
            "pnl": pnl,
            "reason": "END",
        })

    return calculate_results(
        trades,
        max_drawdown,
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_results(
    trades,
    max_drawdown,
):

    count = len(trades)

    total_pnl = sum(
        trade["pnl"]
        for trade in trades
    )

    winners = [
        trade["pnl"]
        for trade in trades
        if trade["pnl"] > 0
    ]

    losers = [
        trade["pnl"]
        for trade in trades
        if trade["pnl"] <= 0
    ]

    gross_profit = sum(winners)

    gross_loss = abs(
        sum(losers)
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )

    elif gross_profit > 0:
        profit_factor = math.inf

    else:
        profit_factor = 0.0

    win_rate = (
        len(winners)
        / count
        * 100.0
        if count
        else 0.0
    )

    avg_winner = (
        sum(winners)
        / len(winners)
        if winners
        else 0.0
    )

    avg_loser = (
        sum(losers)
        / len(losers)
        if losers
        else 0.0
    )

    expectancy = (
        total_pnl
        / count
        if count
        else 0.0
    )

    targets = sum(
        1
        for trade in trades
        if trade["reason"] == "TARGET"
    )

    stops = sum(
        1
        for trade in trades
        if trade["reason"] == "STOP"
    )

    time_exits = sum(
        1
        for trade in trades
        if trade["reason"] == "TIME"
    )

    return {
        "trades": count,

        "win_rate":
            win_rate,

        "pnl":
            total_pnl,

        "return_pct":
            total_pnl
            / STARTING_CASH
            * 100.0,

        "drawdown":
            max_drawdown
            * 100.0,

        "profit_factor":
            profit_factor,

        "avg_winner":
            avg_winner,

        "avg_loser":
            avg_loser,

        "expectancy":
            expectancy,

        "targets":
            targets,

        "stops":
            stops,

        "time_exits":
            time_exits,
    }


# ============================================================
# SCORING
# ============================================================

def score(result):
    if result["trades"] == 0:
        return -999999.0

    return (
        result["return_pct"]
        - result["drawdown"] * 0.50
    )


# ============================================================
# OUTPUT
# ============================================================

def pf_text(value):
    if math.isinf(value):
        return "INF"

    return f"{value:.2f}"


def print_result(
    label,
    result,
):

    print(
        f"{label} -> "
        f"{result['return_pct']:+.2f}% | "
        f"{result['trades']} trades | "
        f"{result['win_rate']:.1f}% win | "
        f"DD {result['drawdown']:.2f}% | "
        f"PF {pf_text(result['profit_factor'])} | "
        f"Expect ${result['expectancy']:+.2f}"
    )


# ============================================================
# TRAIN -> VALIDATE -> HOLDOUT
# ============================================================

def optimize(
    product_name,
    candles,
):

    train_end = int(
        len(candles)
        * TRAIN_RATIO
    )

    validation_end = int(
        len(candles)
        * (
            TRAIN_RATIO
            + VALIDATION_RATIO
        )
    )

    training = (
        candles[:train_end]
    )

    validation = (
        candles[
            train_end:
            validation_end
        ]
    )

    holdout = (
        candles[
            validation_end:
        ]
    )

    combinations = list(
        product(
            BREAKOUT_LOOKBACKS,
            TREND_EMAS,
            VOLUME_MULTS,
            ATR_STOP_MULTS,
            ATR_TARGET_MULTS,
            MAX_HOLD_BARS,
        )
    )

    print()
    print("=" * 78)

    print(
        f"{product_name} | "
        f"V5 GROSS-EDGE TEST"
    )

    print("=" * 78)

    print(
        f"Training candles: "
        f"{len(training)}"
    )

    print(
        f"Validation candles: "
        f"{len(validation)}"
    )

    print(
        f"Holdout candles: "
        f"{len(holdout)}"
    )

    print(
        f"Combinations: "
        f"{len(combinations)}"
    )

    print(
        "Fees: $0.00 for Stage 1"
    )

    # ========================================================
    # TRAIN
    # ========================================================

    trained = []

    for combo in combinations:
        result = run_strategy(
            training,
            *combo,
        )

        if (
            result is not None
            and result["trades"]
            >= MIN_TRAIN_TRADES
        ):
            trained.append({
                "settings":
                    combo,

                "train":
                    result,
            })

    if not trained:
        print(
            "No strategies produced "
            "enough training trades."
        )

        return

    trained.sort(
        key=lambda item:
            score(item["train"]),
        reverse=True,
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    validated = []

    for candidate in trained[
        :FINALISTS
    ]:

        result = run_strategy(
            validation,
            *candidate["settings"],
        )

        if (
            result is None
            or result["trades"] < 3
        ):
            continue

        validated.append({
            "settings":
                candidate["settings"],

            "train":
                candidate["train"],

            "validation":
                result,
        })

    if not validated:
        print(
            "No finalists produced enough "
            "validation trades."
        )

        return

    validated.sort(
        key=lambda item:
            score(
                item["validation"]
            ),
        reverse=True,
    )

    selected = validated[0]

    # ========================================================
    # UNTOUCHED HOLDOUT
    # ========================================================

    final = run_strategy(
        holdout,
        *selected["settings"],
    )

    (
        breakout_lookback,
        trend_period,
        volume_mult,
        atr_stop,
        atr_target,
        max_hold,
    ) = selected["settings"]

    train = selected["train"]
    valid = selected["validation"]

    print()
    print(
        "SELECTED BEFORE HOLDOUT"
    )

    print("-" * 78)

    print(
        f"Breakout lookback: "
        f"{breakout_lookback} hours"
    )

    print(
        f"Trend EMA: "
        f"{trend_period}"
    )

    print(
        f"Volume: "
        f"{volume_mult}x average"
    )

    print(
        f"ATR stop/target: "
        f"{atr_stop}x / "
        f"{atr_target}x"
    )

    print(
        f"Maximum hold: "
        f"{max_hold} hours"
    )

    print()

    print_result(
        "TRAIN",
        train,
    )

    print_result(
        "VALID",
        valid,
    )

    print()
    print("=" * 78)

    print(
        "UNTOUCHED HOLDOUT — GROSS EDGE"
    )

    print("=" * 78)

    print_result(
        "HOLDOUT",
        final,
    )

    print(
        f"Gross P/L: "
        f"${final['pnl']:+.2f}"
    )

    print(
        f"Average winner: "
        f"${final['avg_winner']:+.2f}"
    )

    print(
        f"Average loser: "
        f"${final['avg_loser']:+.2f}"
    )

    print(
        f"Expectancy/trade: "
        f"${final['expectancy']:+.2f}"
    )

    print(
        f"Profit factor: "
        f"{pf_text(final['profit_factor'])}"
    )

    print(
        f"Exits -> "
        f"Targets {final['targets']} | "
        f"Stops {final['stops']} | "
        f"Time {final['time_exits']}"
    )

    print("=" * 78)

    # ========================================================
    # SIMPLE PASS / FAIL
    # ========================================================

    if (
        final["trades"] >= 5
        and final["pnl"] > 0
        and final["profit_factor"] > 1.0
        and final["expectancy"] > 0
    ):
        print(
            "V5 STAGE 1: GROSS EDGE DETECTED"
        )

        print(
            "Next step: realistic "
            "fees + slippage test."
        )

    else:
        print(
            "V5 STAGE 1: NO ROBUST "
            "GROSS EDGE DETECTED"
        )

        print(
            "Do NOT increase risk."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC/ETH STRATEGY V5"
    )

    print(
        "BREAKOUT / VOLATILITY EXPANSION"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Historical period: "
        f"{DAYS_TO_TEST} days"
    )

    print(
        "1-HOUR EXECUTION"
    )

    print(
        "60% TRAIN / "
        "20% VALIDATION / "
        "20% UNTOUCHED HOLDOUT"
    )

    print(
        "STAGE 1 = ZERO-FEE "
        "GROSS EDGE TEST"
    )

    print(
        f"Risk per trade: "
        f"{RISK_PER_TRADE * 100:.1f}%"
    )

    for product_name in config.PRODUCTS:

        try:
            candles = get_history(
                product_name
            )

            optimize(
                product_name,
                candles,
            )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


if __name__ == "__main__":
    main()
