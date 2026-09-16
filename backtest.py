import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from itertools import product

import config


# ============================================================
# SETTINGS
# ============================================================

DAYS_TO_TEST = 30
GRANULARITY_SECONDS = 300
CANDLES_PER_REQUEST = 250

# First 70% = optimization/training
# Last 30% = validation
TRAIN_RATIO = 0.70

# Test combinations
EMA_FAST_VALUES = [5, 9, 12]
EMA_SLOW_VALUES = [18, 21, 30]

RSI_MIN_VALUES = [35, 40, 45, 50]
RSI_MAX_VALUES = [65, 70, 75, 80]

VOLUME_MULTIPLIERS = [0.7, 0.9, 1.1]

STOP_LOSS_VALUES = [0.008, 0.012, 0.016]
TAKE_PROFIT_VALUES = [0.016, 0.024, 0.032]

MIN_TRAIN_TRADES = 8


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


# ============================================================
# COINBASE HISTORICAL DATA
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

    # Ignore currently-forming candle
    end_time = datetime.fromtimestamp(
        end_timestamp - GRANULARITY_SECONDS,
        timezone.utc
    )

    start_time = end_time - timedelta(days=days)

    all_candles = {}
    cursor = start_time
    batch_number = 0

    chunk_seconds = (
        CANDLES_PER_REQUEST
        * GRANULARITY_SECONDS
    )

    print()
    print(
        f"Downloading {days} days of {product_name} "
        f"from Coinbase Exchange..."
    )

    while cursor < end_time:
        chunk_end = min(
            cursor + timedelta(seconds=chunk_seconds),
            end_time
        )

        params = urllib.parse.urlencode({
            "start": iso_time(cursor),
            "end": iso_time(chunk_end),
            "granularity": GRANULARITY_SECONDS,
        })

        url = (
            "https://api.exchange.coinbase.com/"
            f"products/{product_name}/candles?{params}"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "coinbase-paper-optimizer/1.0",
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

            all_candles[candle["time"]] = candle

        print(
            f"Batch {batch_number}: "
            f"{len(raw)} candles"
        )

        cursor = chunk_end
        time.sleep(0.20)

    candles = list(all_candles.values())

    candles.sort(
        key=lambda candle: candle["time"]
    )

    print(
        f"Total unique candles: {len(candles)}"
    )

    return candles


# ============================================================
# STRATEGY ENGINE
# ============================================================

def run_strategy(
    candles,
    ema_fast_period,
    ema_slow_period,
    rsi_min,
    rsi_max,
    volume_multiplier,
    stop_loss_pct,
    take_profit_pct,
):

    if len(candles) < 100:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    fast = ema(
        closes,
        ema_fast_period
    )

    slow = ema(
        closes,
        ema_slow_period
    )

    rsi_values = rsi(
        closes,
        config.RSI_PERIOD
    )

    cash = float(
        config.STARTING_CASH
    )

    peak_equity = cash
    max_drawdown = 0.0

    position = None
    trades = []

    start_index = max(
        ema_slow_period + 2,
        config.VOLUME_LOOKBACK + 2,
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

            if stop_hit:
                exit_price = position["stop"]
                reason = "STOP"

            elif target_hit:
                exit_price = position["target"]
                reason = "TARGET"

            if exit_price is not None:
                gross = (
                    position["qty"]
                    * exit_price
                )

                exit_fee = (
                    gross
                    * config.FEE_RATE
                )

                cash += gross - exit_fee

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

        # ====================================================
        # ENTRY
        # ====================================================

        if position is None:
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

            signal = (
                fast[i] > slow[i]
                and
                rsi_min
                <= rsi_values[i]
                <= rsi_max
                and
                candle["volume"]
                >= avg_volume * volume_multiplier
                and
                candle["close"]
                > candles[i - 1]["close"]
            )

            if signal:
                stop = (
                    price
                    * (1 - stop_loss_pct)
                )

                target = (
                    price
                    * (1 + take_profit_pct)
                )

                stop_distance = (
                    price - stop
                )

                if stop_distance <= 0:
                    continue

                risk_dollars = (
                    cash
                    * config.RISK_PER_TRADE
                )

                qty_by_risk = (
                    risk_dollars
                    / stop_distance
                )

                max_position_value = (
                    cash
                    * config.MAX_POSITION_PCT
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

                cost = qty * price

                entry_fee = (
                    cost
                    * config.FEE_RATE
                )

                total_cost = (
                    cost + entry_fee
                )

                if total_cost > cash:
                    qty = (
                        cash
                        / (
                            price
                            * (1 + config.FEE_RATE)
                        )
                    )

                    cost = qty * price

                    entry_fee = (
                        cost
                        * config.FEE_RATE
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
                    "stop": stop,
                    "target": target,
                }

        # ====================================================
        # DRAWDOWN
        # ====================================================

        equity = cash

        if position is not None:
            equity += (
                position["qty"]
                * price
            )

        if equity > peak_equity:
            peak_equity = equity

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
        final_price = candles[-1]["close"]

        gross = (
            position["qty"]
            * final_price
        )

        exit_fee = (
            gross
            * config.FEE_RATE
        )

        cash += gross - exit_fee

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
    else:
        win_rate = 0.0

    net_profit = (
        cash
        - config.STARTING_CASH
    )

    return_pct = (
        net_profit
        / config.STARTING_CASH
        * 100
    )

    return {
        "ending_cash": cash,
        "net_profit": net_profit,
        "return_pct": return_pct,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown * 100,
    }


# ============================================================
# OPTIMIZER
# ============================================================

def optimize(product_name, candles):

    split_index = int(
        len(candles) * TRAIN_RATIO
    )

    training = candles[:split_index]
    validation = candles[split_index:]

    print()
    print("=" * 65)
    print(f"{product_name} OPTIMIZATION")
    print("=" * 65)

    print(
        f"Training candles:   {len(training)}"
    )

    print(
        f"Validation candles: {len(validation)}"
    )

    combinations = list(
        product(
            EMA_FAST_VALUES,
            EMA_SLOW_VALUES,
            RSI_MIN_VALUES,
            RSI_MAX_VALUES,
            VOLUME_MULTIPLIERS,
            STOP_LOSS_VALUES,
            TAKE_PROFIT_VALUES,
        )
    )

    # Remove impossible / poor structural combinations
    combinations = [
        combo
        for combo in combinations
        if combo[0] < combo[1]
        and combo[2] < combo[3]
    ]

    print(
        f"Strategy combinations: "
        f"{len(combinations)}"
    )

    training_results = []

    for number, combo in enumerate(
        combinations,
        start=1
    ):
        (
            fast_period,
            slow_period,
            rsi_min,
            rsi_max,
            volume_mult,
            stop_pct,
            target_pct,
        ) = combo

        result = run_strategy(
            training,
            fast_period,
            slow_period,
            rsi_min,
            rsi_max,
            volume_mult,
            stop_pct,
            target_pct,
        )

        if (
            result is not None
            and
            result["trades"] >= MIN_TRAIN_TRADES
        ):
            training_results.append({
                "settings": combo,
                "result": result,
            })

        if number % 500 == 0:
            print(
                f"Tested {number}/"
                f"{len(combinations)} combinations..."
            )

    if not training_results:
        print(
            "No strategies met the minimum "
            "trade requirement."
        )
        return

    # Rank training candidates by return.
    training_results.sort(
        key=lambda x: x["result"]["return_pct"],
        reverse=True
    )

    # Only validate the strongest training candidates.
    finalists = training_results[:20]

    validation_results = []

    for candidate in finalists:
        combo = candidate["settings"]

        (
            fast_period,
            slow_period,
            rsi_min,
            rsi_max,
            volume_mult,
            stop_pct,
            target_pct,
        ) = combo

        result = run_strategy(
            validation,
            fast_period,
            slow_period,
            rsi_min,
            rsi_max,
            volume_mult,
            stop_pct,
            target_pct,
        )

        if result is not None:
            validation_results.append({
                "settings": combo,
                "train": candidate["result"],
                "validation": result,
            })

    # Validation performance is what matters here.
    validation_results.sort(
        key=lambda x: (
            x["validation"]["return_pct"],
            -x["validation"]["max_drawdown"],
        ),
        reverse=True
    )

    print()
    print(
        "TOP VALIDATION RESULTS"
    )

    print(
        "These settings were selected using "
        "training data, then tested separately."
    )

    print()

    for rank, candidate in enumerate(
        validation_results[:5],
        start=1
    ):
        (
            fast_period,
            slow_period,
            rsi_min,
            rsi_max,
            volume_mult,
            stop_pct,
            target_pct,
        ) = candidate["settings"]

        train = candidate["train"]
        test = candidate["validation"]

        print("-" * 65)
        print(f"CANDIDATE {rank}")

        print(
            f"EMA: {fast_period}/{slow_period}"
        )

        print(
            f"RSI: {rsi_min}-{rsi_max}"
        )

        print(
            f"Volume multiplier: {volume_mult}"
        )

        print(
            f"Stop loss: {stop_pct * 100:.2f}%"
        )

        print(
            f"Take profit: {target_pct * 100:.2f}%"
        )

        print(
            f"TRAIN -> "
            f"Return {train['return_pct']:.2f}% | "
            f"Trades {train['trades']} | "
            f"Win {train['win_rate']:.1f}% | "
            f"DD {train['max_drawdown']:.2f}%"
        )

        print(
            f"VALID -> "
            f"Return {test['return_pct']:.2f}% | "
            f"Trades {test['trades']} | "
            f"Win {test['win_rate']:.1f}% | "
            f"DD {test['max_drawdown']:.2f}%"
        )

    print()
    print(
        "IMPORTANT: A positive backtest does not "
        "guarantee future profit."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC/ETH STRATEGY OPTIMIZER"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "Historical data: Coinbase Exchange"
    )

    print(
        f"Testing {DAYS_TO_TEST} days"
    )

    print(
        "70% training / 30% validation"
    )

    for product_name in config.PRODUCTS:

        try:
            candles = get_history(
                product_name
            )

            optimize(
                product_name,
                candles
            )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    main()
