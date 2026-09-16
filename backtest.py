import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# V3.1 — 15 MINUTE / 90 DAY / HOLDOUT TEST
# ============================================================

DAYS_TO_TEST = 90
GRANULARITY_SECONDS = 900
CANDLES_PER_REQUEST = 250

STARTING_CASH = float(config.STARTING_CASH)

RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

# Test both assumptions separately.
FEE_MODELS = {
    "TAKER": 0.006,
    "MAKER": 0.004,
}

# Split:
# 60% training
# 20% validation
# 20% untouched holdout
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

MIN_TRAIN_TRADES = 6
FINALISTS = 25


# ============================================================
# PARAMETER GRID
# ============================================================

FAST_EMAS = [8, 12]
SLOW_EMAS = [24, 30]
TREND_EMAS = [50, 75]

RSI_MINS = [45, 50]
RSI_MAXS = [68, 72]

VOLUME_MULTS = [0.8, 1.0]

ATR_STOP_MULTS = [1.5, 2.0]

ATR_TARGET_MULTS = [
    3.0,
    4.0,
    5.0,
]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if not values:
        return []

    multiplier = 2 / (period + 1)

    current = values[0]
    output = [current]

    for value in values[1:]:
        current = (
            value * multiplier
            + current * (1 - multiplier)
        )

        output.append(current)

    return output


def rsi(values, period=14):
    output = [50.0] * len(values)

    if len(values) <= period:
        return output

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        output[period] = 100.0

    else:
        rs = avg_gain / avg_loss

        output[period] = (
            100 - (100 / (1 + rs))
        )

    for i in range(
        period + 1,
        len(values)
    ):
        change = (
            values[i] - values[i - 1]
        )

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            avg_gain * (period - 1)
            + gain
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + loss
        ) / period

        if avg_loss == 0:
            output[i] = 100.0

        else:
            rs = avg_gain / avg_loss

            output[i] = (
                100
                - (100 / (1 + rs))
            )

    return output


def atr(candles, period=14):
    true_ranges = [0.0] * len(candles)
    output = [0.0] * len(candles)

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
        sum(
            true_ranges[
                1:period + 1
            ]
        )
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
        end_timestamp
        - GRANULARITY_SECONDS,
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
        f"of {product_name}..."
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
                    "coinbase-v31-backtest/1.0",
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

    return candles


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_strategy(
    candles,
    fee_rate,
    fast_period,
    slow_period,
    trend_period,
    rsi_min,
    rsi_max,
    volume_mult,
    atr_stop_mult,
    atr_target_mult,
):

    if len(candles) < 200:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    fast = ema(
        closes,
        fast_period
    )

    slow = ema(
        closes,
        slow_period
    )

    trend = ema(
        closes,
        trend_period
    )

    rsi_values = rsi(
        closes,
        14
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
        trend_period + 3,
        slow_period + 3,
        VOLUME_LOOKBACK + 3,
        ATR_PERIOD + 3,
        25,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        # ====================================================
        # NEXT-CANDLE ENTRY
        # ====================================================

        if (
            pending_entry is not None
            and position is None
        ):
            entry_price = (
                candle["open"]
            )

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

            if stop_distance > 0:
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

                    entry_fee = (
                        entry_value
                        * fee_rate
                    )

                    total_cost = (
                        entry_value
                        + entry_fee
                    )

                    if total_cost <= cash:
                        cash -= total_cost

                        position = {
                            "entry":
                                entry_price,

                            "qty":
                                qty,

                            "entry_fee":
                                entry_fee,

                            "stop":
                                entry_price
                                - stop_distance,

                            "target":
                                entry_price
                                + target_distance,
                        }

            pending_entry = None

        # ====================================================
        # EXIT
        # ====================================================

        if position is not None:
            exit_price = None
            reason = None

            stop_hit = (
                candle["low"]
                <= position["stop"]
            )

            target_hit = (
                candle["high"]
                >= position["target"]
            )

            # Conservative assumption.
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

            # Emergency trend failure.
            elif (
                candle["close"]
                < trend[i]
            ):
                exit_price = (
                    candle["close"]
                )

                reason = "TREND"

            if exit_price is not None:
                exit_value = (
                    position["qty"]
                    * exit_price
                )

                exit_fee = (
                    exit_value
                    * fee_rate
                )

                gross_pnl = (
                    (
                        exit_price
                        - position["entry"]
                    )
                    * position["qty"]
                )

                total_fees = (
                    position["entry_fee"]
                    + exit_fee
                )

                net_pnl = (
                    gross_pnl
                    - total_fees
                )

                cash += (
                    exit_value
                    - exit_fee
                )

                trades.append({
                    "gross":
                        gross_pnl,

                    "fees":
                        total_fees,

                    "net":
                        net_pnl,

                    "reason":
                        reason,
                })

                position = None

        # ====================================================
        # ENTRY SIGNAL
        # ====================================================

        if (
            position is None
            and pending_entry is None
            and i < len(candles) - 1
        ):
            price = (
                candle["close"]
            )

            current_atr = (
                atr_values[i]
            )

            if current_atr <= 0:
                continue

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
            # TREND
            # ----------------------------------------------

            trend_ok = (
                fast[i] > slow[i]
                and
                slow[i] > trend[i]
                and
                price > trend[i]
            )

            slope_ok = (
                slow[i]
                > slow[i - 3]
            )

            # ----------------------------------------------
            # ENTRY TYPE 1:
            # Fresh EMA crossover
            # ----------------------------------------------

            fresh_crossover = (
                fast[i - 1]
                <= slow[i - 1]
                and
                fast[i]
                > slow[i]
            )

            # ----------------------------------------------
            # ENTRY TYPE 2:
            # Pullback then reclaim fast EMA
            # ----------------------------------------------

            pullback_reclaim = (
                closes[i - 1]
                <= fast[i - 1]
                and
                closes[i]
                > fast[i]
            )

            entry_trigger = (
                fresh_crossover
                or pullback_reclaim
            )

            # ----------------------------------------------
            # MOMENTUM / VOLUME
            # ----------------------------------------------

            momentum_ok = (
                rsi_min
                <= rsi_values[i]
                <= rsi_max
            )

            volume_ok = (
                candle["volume"]
                >= avg_volume
                * volume_mult
            )

            # ----------------------------------------------
            # FEE TEST
            #
            # Instead of V3's huge multiplier,
            # target only needs a reasonable buffer
            # above estimated round-trip fees.
            # ----------------------------------------------

            target_move_pct = (
                current_atr
                * atr_target_mult
                / price
            )

            round_trip_fee_pct = (
                fee_rate * 2
            )

            # Require projected target move to
            # exceed fees by at least 0.30%.
            minimum_target_pct = (
                round_trip_fee_pct
                + 0.003
            )

            fee_ok = (
                target_move_pct
                >= minimum_target_pct
            )

            signal = (
                trend_ok
                and slope_ok
                and entry_trigger
                and momentum_ok
                and volume_ok
                and fee_ok
            )

            if signal:
                pending_entry = {
                    "atr":
                        current_atr
                }

        # ====================================================
        # EQUITY
        # ====================================================

        equity = cash

        if position is not None:
            equity += (
                position["qty"]
                * candle["close"]
            )

        peak_equity = max(
            peak_equity,
            equity
        )

        if peak_equity > 0:
            drawdown = (
                peak_equity
                - equity
            ) / peak_equity

            max_drawdown = max(
                max_drawdown,
                drawdown
            )

    # ========================================================
    # FINAL POSITION
    # ========================================================

    if position is not None:
        final_price = (
            candles[-1]["close"]
        )

        exit_value = (
            position["qty"]
            * final_price
        )

        exit_fee = (
            exit_value
            * fee_rate
        )

        gross_pnl = (
            (
                final_price
                - position["entry"]
            )
            * position["qty"]
        )

        total_fees = (
            position["entry_fee"]
            + exit_fee
        )

        net_pnl = (
            gross_pnl
            - total_fees
        )

        cash += (
            exit_value
            - exit_fee
        )

        trades.append({
            "gross":
                gross_pnl,

            "fees":
                total_fees,

            "net":
                net_pnl,

            "reason":
                "END",
        })

    # ========================================================
    # RESULTS
    # ========================================================

    total_gross = sum(
        t["gross"]
        for t in trades
    )

    total_fees = sum(
        t["fees"]
        for t in trades
    )

    total_net = sum(
        t["net"]
        for t in trades
    )

    wins = sum(
        1
        for t in trades
        if t["net"] > 0
    )

    trade_count = len(trades)

    win_rate = (
        wins
        / trade_count
        * 100
        if trade_count
        else 0.0
    )

    targets = sum(
        1
        for t in trades
        if t["reason"] == "TARGET"
    )

    stops = sum(
        1
        for t in trades
        if t["reason"] == "STOP"
    )

    trend_exits = sum(
        1
        for t in trades
        if t["reason"] == "TREND"
    )

    return_pct = (
        total_net
        / STARTING_CASH
        * 100
    )

    return {
        "trades":
            trade_count,

        "wins":
            wins,

        "win_rate":
            win_rate,

        "gross":
            total_gross,

        "fees":
            total_fees,

        "net":
            total_net,

        "return_pct":
            return_pct,

        "drawdown":
            max_drawdown * 100,

        "targets":
            targets,

        "stops":
            stops,

        "trend_exits":
            trend_exits,
    }


# ============================================================
# TRAIN → VALIDATE → HOLDOUT
# ============================================================

def optimize(
    product_name,
    candles,
    fee_name,
    fee_rate,
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
            FAST_EMAS,
            SLOW_EMAS,
            TREND_EMAS,
            RSI_MINS,
            RSI_MAXS,
            VOLUME_MULTS,
            ATR_STOP_MULTS,
            ATR_TARGET_MULTS,
        )
    )

    combinations = [
        combo
        for combo in combinations
        if combo[0] < combo[1]
        and combo[1] < combo[2]
        and combo[3] < combo[4]
    ]

    print()
    print("=" * 74)

    print(
        f"{product_name} | "
        f"{fee_name}"
    )

    print("=" * 74)

    print(
        f"Fee per side: "
        f"{fee_rate * 100:.2f}%"
    )

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

    # ========================================================
    # TRAINING
    # ========================================================

    trained = []

    for combo in combinations:
        result = run_strategy(
            training,
            fee_rate,
            *combo,
        )

        if (
            result is not None
            and result["trades"]
            >= MIN_TRAIN_TRADES
        ):
            trained.append({
                "settings": combo,
                "train": result,
            })

    if not trained:
        print(
            "No strategies produced "
            "enough training trades."
        )

        return

    trained.sort(
        key=lambda x: (
            x["train"]["return_pct"]
            - (
                x["train"]["drawdown"]
                * 0.50
            )
        ),
        reverse=True,
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    validation_candidates = []

    for candidate in trained[
        :FINALISTS
    ]:
        validation_result = (
            run_strategy(
                validation,
                fee_rate,
                *candidate["settings"],
            )
        )

        if validation_result is None:
            continue

        validation_candidates.append({
            "settings":
                candidate["settings"],

            "train":
                candidate["train"],

            "validation":
                validation_result,
        })

    if not validation_candidates:
        print(
            "No validation results."
        )

        return

    # Choose using VALIDATION.
    # Holdout remains untouched.
    validation_candidates.sort(
        key=lambda x: (
            x["validation"]
            ["return_pct"]
            -
            x["validation"]
            ["drawdown"]
            * 0.50
        ),
        reverse=True,
    )

    selected = (
        validation_candidates[0]
    )

    # ========================================================
    # FINAL UNTOUCHED HOLDOUT
    # ========================================================

    holdout_result = run_strategy(
        holdout,
        fee_rate,
        *selected["settings"],
    )

    (
        fast,
        slow,
        trend,
        rsi_min,
        rsi_max,
        volume_mult,
        atr_stop,
        atr_target,
    ) = selected["settings"]

    train = selected["train"]

    valid = (
        selected["validation"]
    )

    final = holdout_result

    print()
    print(
        "SELECTED BEFORE HOLDOUT"
    )

    print("-" * 74)

    print(
        f"EMA: "
        f"{fast}/{slow}/{trend}"
    )

    print(
        f"RSI: "
        f"{rsi_min}-{rsi_max}"
    )

    print(
        f"Volume: "
        f"{volume_mult}x"
    )

    print(
        f"ATR stop/target: "
        f"{atr_stop}x / "
        f"{atr_target}x"
    )

    print()

    print(
        f"TRAIN -> "
        f"{train['return_pct']:+.2f}% | "
        f"{train['trades']} trades | "
        f"{train['win_rate']:.1f}% win | "
        f"DD {train['drawdown']:.2f}%"
    )

    print(
        f"VALID -> "
        f"{valid['return_pct']:+.2f}% | "
        f"{valid['trades']} trades | "
        f"{valid['win_rate']:.1f}% win | "
        f"DD {valid['drawdown']:.2f}%"
    )

    print()
    print("=" * 74)

    print(
        "UNTOUCHED HOLDOUT RESULT"
    )

    print("=" * 74)

    print(
        f"HOLDOUT -> "
        f"{final['return_pct']:+.2f}% | "
        f"{final['trades']} trades | "
        f"{final['win_rate']:.1f}% win | "
        f"DD {final['drawdown']:.2f}%"
    )

    print(
        f"Money -> "
        f"Gross ${final['gross']:+.2f} | "
        f"Fees ${final['fees']:.2f} | "
        f"Net ${final['net']:+.2f}"
    )

    print(
        f"Exits -> "
        f"Targets {final['targets']} | "
        f"Stops {final['stops']} | "
        f"Trend {final['trend_exits']}"
    )

    print("=" * 74)


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "BTC/ETH STRATEGY V3.1"
    )

    print(
        "90-DAY 15-MINUTE BACKTEST"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "60% TRAIN / "
        "20% VALIDATION / "
        "20% UNTOUCHED HOLDOUT"
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

            for (
                fee_name,
                fee_rate,
            ) in FEE_MODELS.items():

                optimize(
                    product_name,
                    candles,
                    fee_name,
                    fee_rate,
                )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


if __name__ == "__main__":
    main()
