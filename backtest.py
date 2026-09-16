import time
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# V4 — 1 HOUR SWING STRATEGY
# ============================================================

DAYS_TO_TEST = 365

# Coinbase Exchange supports 1-hour candles.
GRANULARITY_SECONDS = 3600
CANDLES_PER_REQUEST = 250

STARTING_CASH = float(config.STARTING_CASH)

# PAPER/BACKTEST ONLY
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

# Keep both assumptions separate.
FEE_MODELS = {
    "TAKER": 0.006,
    "MAKER": 0.004,
}

# 60% train / 20% validation / 20% untouched holdout
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20

ATR_PERIOD = 14
RSI_PERIOD = 14
VOLUME_LOOKBACK = 20

MIN_TRAIN_TRADES = 8
FINALISTS = 20


# ============================================================
# PARAMETER GRID
#
# Intentionally small. We don't want thousands of combinations
# mining one historical period for a lucky result.
# ============================================================

FAST_EMAS = [12, 20]
SLOW_EMAS = [40, 50]

RSI_MINS = [45, 50]

VOLUME_MULTS = [0.8, 1.0]

ATR_STOP_MULTS = [2.0, 2.5]

ATR_TARGET_MULTS = [4.0, 6.0]

TRAIL_START_MULTS = [2.0, 3.0]
TRAIL_DISTANCE_MULTS = [1.5, 2.0]


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


def rsi(values, period=14):
    output = [50.0] * len(values)

    if len(values) <= period:
        return output

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        output[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        output[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0.0)
        loss = max(-change, 0.0)

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
            output[i] = 100.0 - (100.0 / (1.0 + rs))

    return output


def atr(candles, period=14):
    output = [0.0] * len(candles)
    true_ranges = [0.0] * len(candles)

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

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

    for i in range(period + 1, len(candles)):
        current = (
            current * (period - 1)
            + true_ranges[i]
        ) / period

        output[i] = current

    return output


# ============================================================
# COINBASE HISTORICAL DATA
# ============================================================

def iso_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_history(product_name):
    now = datetime.now(timezone.utc)

    end_timestamp = (
        int(now.timestamp())
        // GRANULARITY_SECONDS
        * GRANULARITY_SECONDS
    )

    # Last fully completed candle.
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
            cursor + timedelta(seconds=chunk_seconds),
            end_time,
        )

        params = urllib.parse.urlencode({
            "start": iso_time(cursor),
            "end": iso_time(chunk_end),
            "granularity": GRANULARITY_SECONDS,
        })

        url = (
            "https://api.exchange.coinbase.com/"
            f"products/{product_name}/candles?"
            f"{params}"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "coinbase-v4-paper-backtest/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            raw = json.loads(
                response.read().decode("utf-8")
            )

        if not isinstance(raw, list):
            raise RuntimeError(
                f"Unexpected Coinbase response: {raw}"
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

            all_candles[candle["time"]] = candle

        batches += 1

        if batches % 5 == 0:
            print(
                f"Downloaded {batches} batches..."
            )

        cursor = chunk_end

        # Be polite to the public endpoint.
        time.sleep(0.20)

    candles = list(all_candles.values())

    candles.sort(
        key=lambda x: x["time"]
    )

    print(
        f"Total unique candles: {len(candles)}"
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
# BACKTEST ENGINE
# ============================================================

def run_strategy(
    candles,
    fee_rate,
    fast_period,
    slow_period,
    rsi_min,
    volume_mult,
    atr_stop_mult,
    atr_target_mult,
    trail_start_mult,
    trail_distance_mult,
):
    if len(candles) < 300:
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

    rsi_values = rsi(
        closes,
        RSI_PERIOD
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
        slow_period + 5,
        VOLUME_LOOKBACK + 5,
        ATR_PERIOD + 5,
        RSI_PERIOD + 5,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        # ====================================================
        # EXECUTE PREVIOUS SIGNAL AT CURRENT OPEN
        # ====================================================

        if (
            pending_entry is not None
            and position is None
        ):
            entry_price = candle["open"]

            atr_at_signal = pending_entry["atr"]

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
                and target_distance > 0
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

                    entry_fee = (
                        entry_value
                        * fee_rate
                    )

                    total_entry_cost = (
                        entry_value
                        + entry_fee
                    )

                    if total_entry_cost <= cash:
                        cash -= total_entry_cost

                        position = {
                            "entry": entry_price,
                            "qty": qty,
                            "entry_fee": entry_fee,
                            "initial_atr": atr_at_signal,
                            "stop":
                                entry_price
                                - stop_distance,
                            "target":
                                entry_price
                                + target_distance,
                            "highest":
                                entry_price,
                            "trailing_active":
                                False,
                        }

            pending_entry = None

        # ====================================================
        # MANAGE OPEN POSITION
        # ====================================================

        if position is not None:
            # Track highest price reached.
            position["highest"] = max(
                position["highest"],
                candle["high"],
            )

            # Activate trailing stop only after the trade
            # has moved substantially in our favor.
            trail_trigger_price = (
                position["entry"]
                + (
                    position["initial_atr"]
                    * trail_start_mult
                )
            )

            if (
                position["highest"]
                >= trail_trigger_price
            ):
                position["trailing_active"] = True

            # Ratchet trailing stop upward only.
            if position["trailing_active"]:
                proposed_trail = (
                    position["highest"]
                    - (
                        position["initial_atr"]
                        * trail_distance_mult
                    )
                )

                position["stop"] = max(
                    position["stop"],
                    proposed_trail,
                )

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

            # Conservative same-candle assumption:
            # if both are touched, count the stop first.
            if stop_hit:
                exit_price = position["stop"]

                if position["trailing_active"]:
                    reason = "TRAIL"
                else:
                    reason = "STOP"

            elif target_hit:
                exit_price = position["target"]
                reason = "TARGET"

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
                    "gross": gross_pnl,
                    "fees": total_fees,
                    "net": net_pnl,
                    "reason": reason,
                })

                position = None

        # ====================================================
        # SIGNAL
        # ====================================================

        if (
            position is None
            and pending_entry is None
            and i < len(candles) - 1
        ):
            price = candle["close"]

            current_atr = atr_values[i]

            if (
                price <= 0
                or current_atr <= 0
            ):
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
            # PRIMARY TREND
            # ----------------------------------------------

            trend_ok = (
                fast[i] > slow[i]
                and
                price > slow[i]
            )

            # Require both averages to be rising.
            slope_ok = (
                fast[i] > fast[i - 3]
                and
                slow[i] > slow[i - 3]
            )

            # ----------------------------------------------
            # PULLBACK / RECLAIM
            #
            # Price briefly comes back toward the fast EMA,
            # then closes back above it while the larger
            # trend remains bullish.
            # ----------------------------------------------

            reclaim = (
                closes[i - 1]
                <= fast[i - 1] * 1.002
                and
                closes[i]
                > fast[i]
            )

            # Also allow a fresh fast/slow crossover.
            crossover = (
                fast[i - 1]
                <= slow[i - 1]
                and
                fast[i]
                > slow[i]
            )

            trigger_ok = (
                reclaim
                or crossover
            )

            # ----------------------------------------------
            # MOMENTUM
            # ----------------------------------------------

            momentum_ok = (
                rsi_values[i] >= rsi_min
                and
                rsi_values[i] <= 72
            )

            # ----------------------------------------------
            # VOLUME
            # ----------------------------------------------

            volume_ok = (
                candle["volume"]
                >= avg_volume * volume_mult
            )

            # ----------------------------------------------
            # FEE-AWARE MOVE SIZE
            #
            # Don't enter unless the planned target is
            # comfortably larger than round-trip fees.
            # ----------------------------------------------

            projected_target_pct = (
                current_atr
                * atr_target_mult
                / price
            )

            round_trip_fee_pct = (
                fee_rate * 2.0
            )

            fee_ok = (
                projected_target_pct
                >= round_trip_fee_pct
                + 0.005
            )

            signal = (
                trend_ok
                and slope_ok
                and trigger_ok
                and momentum_ok
                and volume_ok
                and fee_ok
            )

            if signal:
                pending_entry = {
                    "atr": current_atr
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
    # CLOSE POSITION AT END OF WINDOW
    # ========================================================

    if position is not None:
        final_price = candles[-1]["close"]

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
            "gross": gross_pnl,
            "fees": total_fees,
            "net": net_pnl,
            "reason": "END",
        })

    return calculate_results(
        trades,
        max_drawdown,
    )


# ============================================================
# PERFORMANCE STATISTICS
# ============================================================

def calculate_results(
    trades,
    max_drawdown,
):
    trade_count = len(trades)

    total_gross = sum(
        trade["gross"]
        for trade in trades
    )

    total_fees = sum(
        trade["fees"]
        for trade in trades
    )

    total_net = sum(
        trade["net"]
        for trade in trades
    )

    winners = [
        trade
        for trade in trades
        if trade["net"] > 0
    ]

    losers = [
        trade
        for trade in trades
        if trade["net"] <= 0
    ]

    gross_profit = sum(
        trade["net"]
        for trade in winners
    )

    gross_loss = abs(
        sum(
            trade["net"]
            for trade in losers
        )
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

    if winners:
        average_winner = (
            sum(
                trade["net"]
                for trade in winners
            )
            / len(winners)
        )
    else:
        average_winner = 0.0

    if losers:
        average_loser = (
            sum(
                trade["net"]
                for trade in losers
            )
            / len(losers)
        )
    else:
        average_loser = 0.0

    if trade_count:
        win_rate = (
            len(winners)
            / trade_count
            * 100.0
        )

        expectancy = (
            total_net
            / trade_count
        )
    else:
        win_rate = 0.0
        expectancy = 0.0

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

    trails = sum(
        1
        for trade in trades
        if trade["reason"] == "TRAIL"
    )

    return {
        "trades": trade_count,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": win_rate,

        "gross": total_gross,
        "fees": total_fees,
        "net": total_net,

        "return_pct":
            total_net
            / STARTING_CASH
            * 100.0,

        "drawdown":
            max_drawdown
            * 100.0,

        "profit_factor":
            profit_factor,

        "average_winner":
            average_winner,

        "average_loser":
            average_loser,

        "expectancy":
            expectancy,

        "targets": targets,
        "stops": stops,
        "trails": trails,
    }


# ============================================================
# SCORING
# ============================================================

def score_result(result):
    # Require actual activity.
    if result["trades"] == 0:
        return -999999.0

    # Return rewarded, drawdown penalized.
    return (
        result["return_pct"]
        - (
            result["drawdown"]
            * 0.50
        )
    )


# ============================================================
# TRAIN -> VALIDATE -> UNTOUCHED HOLDOUT
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

    training = candles[:train_end]

    validation = candles[
        train_end:
        validation_end
    ]

    holdout = candles[
        validation_end:
    ]

    combinations = list(
        product(
            FAST_EMAS,
            SLOW_EMAS,
            RSI_MINS,
            VOLUME_MULTS,
            ATR_STOP_MULTS,
            ATR_TARGET_MULTS,
            TRAIL_START_MULTS,
            TRAIL_DISTANCE_MULTS,
        )
    )

    combinations = [
        combo
        for combo in combinations
        if combo[0] < combo[1]
    ]

    print()
    print("=" * 76)

    print(
        f"{product_name} | "
        f"{fee_name} FEE MODEL"
    )

    print("=" * 76)

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
    # TRAIN
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
            "No strategies produced enough "
            "training trades."
        )
        return

    trained.sort(
        key=lambda item:
            score_result(
                item["train"]
            ),
        reverse=True,
    )

    # ========================================================
    # VALIDATE TRAINING FINALISTS
    # ========================================================

    validated = []

    for candidate in trained[:FINALISTS]:
        result = run_strategy(
            validation,
            fee_rate,
            *candidate["settings"],
        )

        if result is None:
            continue

        # Require at least some independent activity.
        if result["trades"] < 2:
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

    # Selection ends HERE.
    # Holdout data is not used for selection.
    validated.sort(
        key=lambda item:
            score_result(
                item["validation"]
            ),
        reverse=True,
    )

    selected = validated[0]

    # ========================================================
    # ONE FINAL HOLDOUT TEST
    # ========================================================

    holdout_result = run_strategy(
        holdout,
        fee_rate,
        *selected["settings"],
    )

    (
        fast,
        slow,
        rsi_min,
        volume_mult,
        atr_stop,
        atr_target,
        trail_start,
        trail_distance,
    ) = selected["settings"]

    train = selected["train"]
    valid = selected["validation"]
    final = holdout_result

    print()
    print(
        "SELECTED BEFORE HOLDOUT"
    )

    print("-" * 76)

    print(
        f"EMA fast/slow: "
        f"{fast}/{slow}"
    )

    print(
        f"RSI minimum: "
        f"{rsi_min}"
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

    print(
        f"Trail starts: "
        f"{trail_start} ATR"
    )

    print(
        f"Trail distance: "
        f"{trail_distance} ATR"
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
    print("=" * 76)

    print(
        "UNTOUCHED HOLDOUT"
    )

    print("=" * 76)

    print_result(
        "HOLDOUT",
        final,
    )

    print(
        f"Holdout money -> "
        f"Gross ${final['gross']:+.2f} | "
        f"Fees ${final['fees']:.2f} | "
        f"Net ${final['net']:+.2f}"
    )

    print(
        f"Holdout exits -> "
        f"Targets {final['targets']} | "
        f"Stops {final['stops']} | "
        f"Trails {final['trails']}"
    )

    print(
        f"Average winner: "
        f"${final['average_winner']:+.2f}"
    )

    print(
        f"Average loser: "
        f"${final['average_loser']:+.2f}"
    )

    print(
        f"Expectancy/trade: "
        f"${final['expectancy']:+.2f}"
    )

    if math.isinf(
        final["profit_factor"]
    ):
        pf_text = "INF"
    else:
        pf_text = (
            f"{final['profit_factor']:.2f}"
        )

    print(
        f"Profit factor: {pf_text}"
    )

    print("=" * 76)


def print_result(
    label,
    result,
):
    if math.isinf(
        result["profit_factor"]
    ):
        pf_text = "INF"
    else:
        pf_text = (
            f"{result['profit_factor']:.2f}"
        )

    print(
        f"{label} -> "
        f"{result['return_pct']:+.2f}% | "
        f"{result['trades']} trades | "
        f"{result['win_rate']:.1f}% win | "
        f"DD {result['drawdown']:.2f}% | "
        f"PF {pf_text}"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "BTC/ETH STRATEGY V4"
    )

    print(
        "1-HOUR SWING BACKTEST"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Historical period: "
        f"{DAYS_TO_TEST} days"
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

    print(
        "No early EMA trend exits."
    )

    print(
        "ATR stop + target + trailing stop."
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
