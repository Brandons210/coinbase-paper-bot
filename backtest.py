import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# V3 — FEE-AWARE 15-MINUTE STRATEGY
# ============================================================

DAYS_TO_TEST = 60

# 15-minute candles
GRANULARITY_SECONDS = 900

# Coinbase endpoint batch size
CANDLES_PER_REQUEST = 250

TRAIN_RATIO = 0.70

STARTING_CASH = float(config.STARTING_CASH)

# Keep risk controlled during testing.
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

# Test both execution assumptions.
FEE_MODELS = {
    "TAKER": 0.006,
    "MAKER": 0.004,
}

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

MIN_TRAIN_TRADES = 5
FINALISTS_TO_VALIDATE = 20


# ============================================================
# SMALL V3 PARAMETER GRID
# ============================================================

FAST_EMAS = [8, 12]
SLOW_EMAS = [24, 30]
TREND_EMAS = [50, 75]

RSI_MINS = [48, 52]
RSI_MAXS = [68, 72]

VOLUME_MULTS = [0.9, 1.1]

ATR_STOP_MULTS = [1.5, 2.0]
ATR_TARGET_MULTS = [3.0, 4.0, 5.0]

# Target must exceed round-trip fees by this multiple.
FEE_EDGE_MULTS = [2.0, 3.0]


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
        output[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            avg_gain * (period - 1) + gain
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + loss
        ) / period

        if avg_loss == 0:
            output[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            output[i] = 100 - (100 / (1 + rs))

    return output


def atr(candles, period=14):
    true_ranges = [0.0] * len(candles)
    output = [0.0] * len(candles)

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
            (
                current * (period - 1)
                + true_ranges[i]
            )
            / period
        )

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

    end_time = datetime.fromtimestamp(
        end_timestamp - GRANULARITY_SECONDS,
        timezone.utc
    )

    start_time = (
        end_time - timedelta(days=DAYS_TO_TEST)
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
        f"of {product_name} 15m candles..."
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
                "User-Agent":
                    "coinbase-paper-v3-backtester/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30
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

        time.sleep(0.20)

    candles = list(all_candles.values())

    candles.sort(
        key=lambda x: x["time"]
    )

    print(
        f"Total unique candles: {len(candles)}"
    )

    return candles


# ============================================================
# V3 BACKTEST
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
    fee_edge_mult,
):

    if len(candles) < 200:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    trend = ema(closes, trend_period)

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
        20,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        # ====================================================
        # EXECUTE SIGNAL AT NEXT CANDLE OPEN
        # ====================================================

        if (
            pending_entry is not None
            and position is None
        ):
            entry_price = candle["open"]

            stop_distance = (
                pending_entry["atr"]
                * atr_stop_mult
            )

            target_distance = (
                pending_entry["atr"]
                * atr_target_mult
            )

            if stop_distance > 0:
                risk_dollars = (
                    cash * RISK_PER_TRADE
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
                    qty_by_position
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
                            "entry": entry_price,
                            "qty": qty,
                            "entry_fee": entry_fee,
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

            # Conservative if both happen in same candle.
            if stop_hit:
                exit_price = position["stop"]
                reason = "STOP"

            elif target_hit:
                exit_price = position["target"]
                reason = "TARGET"

            # Only use slow EMA as emergency trend failure.
            elif candle["close"] < trend[i]:
                exit_price = candle["close"]
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

                net_pnl = (
                    gross_pnl
                    - position["entry_fee"]
                    - exit_fee
                )

                cash += (
                    exit_value - exit_fee
                )

                trades.append({
                    "gross": gross_pnl,
                    "fees":
                        position["entry_fee"]
                        + exit_fee,
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
            current_atr = atr_values[i]

            if current_atr <= 0:
                continue

            price = candle["close"]

            volume_window = [
                candles[j]["volume"]
                for j in range(
                    i - VOLUME_LOOKBACK,
                    i
                )
            ]

            avg_volume = (
                sum(volume_window)
                / len(volume_window)
            )

            # Trend alignment.
            trend_ok = (
                fast[i] > slow[i]
                and
                slow[i] > trend[i]
                and
                price > trend[i]
            )

            # Slow EMA must actually be rising.
            trend_slope_ok = (
                slow[i]
                > slow[i - 3]
            )

            # Instead of demanding only one exact crossover,
            # look for price reclaiming fast EMA.
            pullback_reclaim = (
                closes[i - 1]
                <= fast[i - 1]
                and
                closes[i]
                > fast[i]
            )

            momentum_ok = (
                rsi_min
                <= rsi_values[i]
                <= rsi_max
            )

            volume_ok = (
                candle["volume"]
                >= avg_volume * volume_mult
            )

            # =================================================
            # FEE-AWARE FILTER
            # =================================================

            projected_target_pct = (
                current_atr
                * atr_target_mult
                / price
            )

            estimated_round_trip_fee = (
                fee_rate * 2
            )

            minimum_required_move = (
                estimated_round_trip_fee
                * fee_edge_mult
            )

            fee_edge_ok = (
                projected_target_pct
                >= minimum_required_move
            )

            signal = (
                trend_ok
                and trend_slope_ok
                and pullback_reclaim
                and momentum_ok
                and volume_ok
                and fee_edge_ok
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
            equity
        )

        if peak_equity > 0:
            drawdown = (
                peak_equity - equity
            ) / peak_equity

            max_drawdown = max(
                max_drawdown,
                drawdown
            )

    # ========================================================
    # CLOSE FINAL POSITION
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
            exit_value * fee_rate
        )

        gross_pnl = (
            (
                final_price
                - position["entry"]
            )
            * position["qty"]
        )

        net_pnl = (
            gross_pnl
            - position["entry_fee"]
            - exit_fee
        )

        cash += (
            exit_value - exit_fee
        )

        trades.append({
            "gross": gross_pnl,
            "fees":
                position["entry_fee"]
                + exit_fee,
            "net": net_pnl,
            "reason": "END",
        })

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

    trade_count = len(trades)

    win_rate = (
        wins / trade_count * 100
        if trade_count
        else 0.0
    )

    return_pct = (
        total_net
        / STARTING_CASH
        * 100
    )

    return {
        "trades": trade_count,
        "wins": wins,
        "win_rate": win_rate,
        "gross": total_gross,
        "fees": total_fees,
        "net": total_net,
        "return_pct": return_pct,
        "drawdown": max_drawdown * 100,
        "targets": targets,
        "stops": stops,
        "trend_exits": trend_exits,
    }


# ============================================================
# OPTIMIZATION
# ============================================================

def optimize(
    product_name,
    candles,
    fee_name,
    fee_rate,
):

    split = int(
        len(candles) * TRAIN_RATIO
    )

    training = candles[:split]
    validation = candles[split:]

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
            FEE_EDGE_MULTS,
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
    print("=" * 72)

    print(
        f"{product_name} | "
        f"{fee_name} FEE MODEL"
    )

    print("=" * 72)

    print(
        f"Fee per side: "
        f"{fee_rate * 100:.2f}%"
    )

    print(
        f"Training candles: {len(training)}"
    )

    print(
        f"Validation candles: {len(validation)}"
    )

    print(
        f"Combinations: {len(combinations)}"
    )

    results = []

    for combo in combinations:
        result = run_strategy(
            training,
            fee_rate,
            *combo
        )

        if (
            result is not None
            and result["trades"]
            >= MIN_TRAIN_TRADES
        ):
            results.append({
                "settings": combo,
                "train": result,
            })

    if not results:
        print(
            "No strategies produced enough trades."
        )
        return

    results.sort(
        key=lambda x: (
            x["train"]["return_pct"]
            - x["train"]["drawdown"] * 0.5
        ),
        reverse=True
    )

    finalists = (
        results[:FINALISTS_TO_VALIDATE]
    )

    validated = []

    for candidate in finalists:
        valid = run_strategy(
            validation,
            fee_rate,
            *candidate["settings"]
        )

        if valid is None:
            continue

        validated.append({
            "settings":
                candidate["settings"],
            "train":
                candidate["train"],
            "valid":
                valid,
        })

    validated.sort(
        key=lambda x: (
            x["valid"]["return_pct"]
            - x["valid"]["drawdown"] * 0.5
        ),
        reverse=True
    )

    print()
    print("TOP VALIDATION RESULTS")
    print()

    for rank, candidate in enumerate(
        validated[:5],
        start=1
    ):
        (
            fast,
            slow,
            trend,
            rsi_min,
            rsi_max,
            volume_mult,
            atr_stop,
            atr_target,
            fee_edge,
        ) = candidate["settings"]

        train = candidate["train"]
        valid = candidate["valid"]

        print("-" * 72)
        print(f"CANDIDATE {rank}")

        print(
            f"EMA: {fast}/{slow}/{trend}"
        )

        print(
            f"RSI: {rsi_min}-{rsi_max}"
        )

        print(
            f"Volume: {volume_mult}x"
        )

        print(
            f"ATR stop/target: "
            f"{atr_stop}x / {atr_target}x"
        )

        print(
            f"Fee-edge requirement: "
            f"{fee_edge}x round-trip fees"
        )

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

        print(
            f"VALID money -> "
            f"Gross ${valid['gross']:+.2f} | "
            f"Fees ${valid['fees']:.2f} | "
            f"Net ${valid['net']:+.2f}"
        )

        print(
            f"VALID exits -> "
            f"Targets {valid['targets']} | "
            f"Stops {valid['stops']} | "
            f"Trend {valid['trend_exits']}"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "BTC/ETH STRATEGY V3"
    )

    print(
        "15-MINUTE FEE-AWARE BACKTEST"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Historical period: {DAYS_TO_TEST} days"
    )

    print(
        "Testing maker and taker fee assumptions separately."
    )

    for product_name in config.PRODUCTS:
        try:
            candles = get_history(
                product_name
            )

            for fee_name, fee_rate in FEE_MODELS.items():
                optimize(
                    product_name,
                    candles,
                    fee_name,
                    fee_rate,
                )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    main()
