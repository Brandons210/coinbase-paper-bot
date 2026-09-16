import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# V2 SETTINGS
# ============================================================

DAYS_TO_TEST = 60
GRANULARITY_SECONDS = 300
CANDLES_PER_REQUEST = 250
TRAIN_RATIO = 0.70

# Keep risk fixed while testing strategy quality.
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

# Keep using config fee assumption.
FEE_RATE = config.FEE_RATE

# V2 optimization ranges
FAST_EMAS = [5, 8, 12]
SLOW_EMAS = [18, 24, 30]
TREND_EMAS = [50, 75]

RSI_MINS = [45, 50]
RSI_MAXS = [65, 70]

VOLUME_MULTS = [0.9, 1.1]

ATR_PERIOD = 14
ATR_STOP_MULTS = [1.5, 2.0, 2.5]
ATR_TARGET_MULTS = [2.0, 3.0, 4.0]

COOLDOWN_CANDLES = [3, 6]

MIN_TRAIN_TRADES = 10
FINALISTS_TO_VALIDATE = 30


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

            output[i] = (
                100 - (100 / (1 + rs))
            )

    return output


def atr(candles, period=14):
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

    output = [0.0] * len(candles)

    if len(candles) <= period:
        return output

    initial = sum(
        true_ranges[1:period + 1]
    ) / period

    output[period] = initial

    current = initial

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
# COINBASE EXCHANGE HISTORICAL DATA
# ============================================================

def iso_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_history(product_name, days=DAYS_TO_TEST):
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
        end_time - timedelta(days=days)
    )

    all_candles = {}

    cursor = start_time
    batch_number = 0

    chunk_seconds = (
        CANDLES_PER_REQUEST
        * GRANULARITY_SECONDS
    )

    print()
    print(
        f"Downloading {days} days of "
        f"{product_name}..."
    )

    while cursor < end_time:
        chunk_end = min(
            cursor + timedelta(
                seconds=chunk_seconds
            ),
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
                    "coinbase-paper-v2-backtester/1.0",
                "Accept":
                    "application/json",
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
                f"Unexpected response: {raw}"
            )

        batch_number += 1

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

        if batch_number % 10 == 0:
            print(
                f"Downloaded "
                f"{batch_number} batches..."
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
# V2 STRATEGY
# ============================================================

def run_v2(
    candles,
    fast_period,
    slow_period,
    trend_period,
    rsi_min,
    rsi_max,
    volume_mult,
    atr_stop_mult,
    atr_target_mult,
    cooldown_period,
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
        config.RSI_PERIOD
    )

    atr_values = atr(
        candles,
        ATR_PERIOD
    )

    cash = float(
        config.STARTING_CASH
    )

    position = None
    trades = []

    peak_equity = cash
    max_drawdown = 0.0

    cooldown_until = 0

    start_index = max(
        trend_period + 2,
        slow_period + 2,
        config.VOLUME_LOOKBACK + 2,
        ATR_PERIOD + 2,
        config.RSI_PERIOD + 2,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        price = candle["close"]

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

            # Conservative same-candle assumption.
            if stop_hit:
                exit_price = position["stop"]
                reason = "STOP"

            elif target_hit:
                exit_price = position["target"]
                reason = "TARGET"

            # Trend failure exit
            elif price < slow[i]:
                exit_price = price
                reason = "TREND_EXIT"

            if exit_price is not None:
                gross = (
                    position["qty"]
                    * exit_price
                )

                exit_fee = (
                    gross * FEE_RATE
                )

                cash += (
                    gross - exit_fee
                )

                pnl = (
                    (
                        exit_price
                        - position["entry"]
                    )
                    * position["qty"]
                    - position["entry_fee"]
                    - exit_fee
                )

                trades.append({
                    "pnl": pnl,
                    "reason": reason,
                })

                position = None

                cooldown_until = (
                    i + cooldown_period
                )

        # ====================================================
        # ENTRY
        # ====================================================

        if (
            position is None
            and i >= cooldown_until
        ):

            volume_window = [
                candles[j]["volume"]
                for j in range(
                    i - config.VOLUME_LOOKBACK,
                    i
                )
            ]

            avg_volume = (
                sum(volume_window)
                / len(volume_window)
            )

            current_atr = (
                atr_values[i]
            )

            if current_atr <= 0:
                continue

            atr_percent = (
                current_atr / price
            )

            # Actual crossover:
            # previous fast <= previous slow
            # current fast > current slow
            crossover = (
                fast[i - 1]
                <= slow[i - 1]
                and
                fast[i]
                > slow[i]
            )

            # Longer-term bullish structure.
            trend_ok = (
                price > trend[i]
                and
                slow[i] > trend[i]
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

            # Avoid extremely dead candles.
            volatility_ok = (
                atr_percent >= 0.001
            )

            signal = (
                crossover
                and trend_ok
                and momentum_ok
                and volume_ok
                and volatility_ok
            )

            if signal:
                stop_distance = (
                    current_atr
                    * atr_stop_mult
                )

                target_distance = (
                    current_atr
                    * atr_target_mult
                )

                stop_price = (
                    price - stop_distance
                )

                target_price = (
                    price + target_distance
                )

                if stop_distance <= 0:
                    continue

                risk_dollars = (
                    cash * RISK_PER_TRADE
                )

                qty_by_risk = (
                    risk_dollars
                    / stop_distance
                )

                max_position_value = (
                    cash * MAX_POSITION_PCT
                )

                qty_by_position = (
                    max_position_value
                    / price
                )

                qty = min(
                    qty_by_risk,
                    qty_by_position
                )

                if qty <= 0:
                    continue

                cost = (
                    qty * price
                )

                entry_fee = (
                    cost * FEE_RATE
                )

                total_cost = (
                    cost + entry_fee
                )

                if total_cost > cash:
                    qty = (
                        cash
                        / (
                            price
                            * (1 + FEE_RATE)
                        )
                    )

                    cost = (
                        qty * price
                    )

                    entry_fee = (
                        cost * FEE_RATE
                    )

                    total_cost = (
                        cost + entry_fee
                    )

                if qty <= 0:
                    continue

                cash -= total_cost

                position = {
                    "entry": price,
                    "qty": qty,
                    "entry_fee": entry_fee,
                    "stop": stop_price,
                    "target": target_price,
                }

        # ====================================================
        # EQUITY
        # ====================================================

        equity = cash

        if position is not None:
            equity += (
                position["qty"]
                * price
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

        gross = (
            position["qty"]
            * final_price
        )

        exit_fee = (
            gross * FEE_RATE
        )

        cash += (
            gross - exit_fee
        )

        pnl = (
            (
                final_price
                - position["entry"]
            )
            * position["qty"]
            - position["entry_fee"]
            - exit_fee
        )

        trades.append({
            "pnl": pnl,
            "reason": "END",
        })

    wins = sum(
        1
        for trade in trades
        if trade["pnl"] > 0
    )

    losses = (
        len(trades) - wins
    )

    if trades:
        win_rate = (
            wins / len(trades) * 100
        )

        average_trade = (
            sum(
                trade["pnl"]
                for trade in trades
            )
            / len(trades)
        )

    else:
        win_rate = 0.0
        average_trade = 0.0

    net_profit = (
        cash - config.STARTING_CASH
    )

    return_pct = (
        net_profit
        / config.STARTING_CASH
        * 100
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

    trend_exits = sum(
        1
        for trade in trades
        if trade["reason"] == "TREND_EXIT"
    )

    return {
        "ending_cash": cash,
        "net_profit": net_profit,
        "return_pct": return_pct,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "average_trade": average_trade,
        "max_drawdown": max_drawdown * 100,
        "targets": targets,
        "stops": stops,
        "trend_exits": trend_exits,
    }


# ============================================================
# OPTIMIZER
# ============================================================

def optimize_v2(product_name, candles):

    split_index = int(
        len(candles) * TRAIN_RATIO
    )

    training = (
        candles[:split_index]
    )

    validation = (
        candles[split_index:]
    )

    print()
    print("=" * 70)
    print(
        f"{product_name} V2 OPTIMIZATION"
    )
    print("=" * 70)

    print(
        f"Training candles: "
        f"{len(training)}"
    )

    print(
        f"Validation candles: "
        f"{len(validation)}"
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
            COOLDOWN_CANDLES,
        )
    )

    combinations = [
        combo
        for combo in combinations
        if combo[0] < combo[1]
        and combo[1] < combo[2]
        and combo[3] < combo[4]
    ]

    print(
        f"V2 combinations: "
        f"{len(combinations)}"
    )

    training_results = []

    for number, combo in enumerate(
        combinations,
        start=1
    ):
        result = run_v2(
            training,
            *combo
        )

        if (
            result is not None
            and result["trades"]
            >= MIN_TRAIN_TRADES
        ):
            training_results.append({
                "settings": combo,
                "train": result,
            })

        if number % 500 == 0:
            print(
                f"Tested {number}/"
                f"{len(combinations)}..."
            )

    if not training_results:
        print(
            "No V2 strategies produced "
            "enough training trades."
        )
        return

    # Reward return while penalizing drawdown.
    def training_score(candidate):
        result = candidate["train"]

        return (
            result["return_pct"]
            - (
                result["max_drawdown"]
                * 0.50
            )
        )

    training_results.sort(
        key=training_score,
        reverse=True
    )

    finalists = (
        training_results[
            :FINALISTS_TO_VALIDATE
        ]
    )

    validated = []

    for candidate in finalists:
        settings = candidate["settings"]

        validation_result = run_v2(
            validation,
            *settings
        )

        if validation_result is None:
            continue

        validated.append({
            "settings": settings,
            "train": candidate["train"],
            "validation":
                validation_result,
        })

    # Sort for DISPLAY ONLY.
    # We still inspect training and validation together.
    validated.sort(
        key=lambda candidate: (
            candidate["validation"]
            ["return_pct"]
            -
            candidate["validation"]
            ["max_drawdown"] * 0.50
        ),
        reverse=True
    )

    print()
    print(
        "TOP V2 VALIDATION RESULTS"
    )

    print(
        "Positive validation is NOT "
        "a guarantee of future profit."
    )

    print()

    for rank, candidate in enumerate(
        validated[:5],
        start=1
    ):
        settings = (
            candidate["settings"]
        )

        (
            fast_period,
            slow_period,
            trend_period,
            rsi_min,
            rsi_max,
            volume_mult,
            atr_stop,
            atr_target,
            cooldown,
        ) = settings

        train = candidate["train"]

        valid = (
            candidate["validation"]
        )

        print("-" * 70)

        print(
            f"CANDIDATE {rank}"
        )

        print(
            f"EMA fast/slow/trend: "
            f"{fast_period}/"
            f"{slow_period}/"
            f"{trend_period}"
        )

        print(
            f"RSI: "
            f"{rsi_min}-{rsi_max}"
        )

        print(
            f"Volume multiplier: "
            f"{volume_mult}"
        )

        print(
            f"ATR stop: "
            f"{atr_stop}x"
        )

        print(
            f"ATR target: "
            f"{atr_target}x"
        )

        print(
            f"Cooldown: "
            f"{cooldown} candles"
        )

        print(
            f"TRAIN -> "
            f"Return "
            f"{train['return_pct']:.2f}% | "
            f"Trades "
            f"{train['trades']} | "
            f"Win "
            f"{train['win_rate']:.1f}% | "
            f"DD "
            f"{train['max_drawdown']:.2f}%"
        )

        print(
            f"VALID -> "
            f"Return "
            f"{valid['return_pct']:.2f}% | "
            f"Trades "
            f"{valid['trades']} | "
            f"Win "
            f"{valid['win_rate']:.1f}% | "
            f"DD "
            f"{valid['max_drawdown']:.2f}%"
        )

        print(
            f"VALID exits -> "
            f"Targets {valid['targets']} | "
            f"Stops {valid['stops']} | "
            f"Trend {valid['trend_exits']}"
        )

        print(
            f"VALID avg trade -> "
            f"${valid['average_trade']:.2f}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC/ETH STRATEGY V2 TESTER"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "Historical data: Coinbase Exchange"
    )

    print(
        f"Test period: "
        f"{DAYS_TO_TEST} days"
    )

    print(
        "70% training / 30% validation"
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

            optimize_v2(
                product_name,
                candles
            )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


if __name__ == "__main__":
    main()
