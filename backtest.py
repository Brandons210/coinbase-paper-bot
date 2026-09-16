import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import config


# ============================================================
# SETTINGS
# ============================================================

DAYS_TO_TEST = 14
GRANULARITY_SECONDS = 300       # 5-minute candles
CANDLES_PER_REQUEST = 250       # safely below 300 max


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
# COINBASE EXCHANGE HISTORICAL DATA
# ============================================================

def iso_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_history(product, days=DAYS_TO_TEST):
    end_time = datetime.now(timezone.utc)

    # Ignore the currently forming 5-minute candle.
    end_timestamp = (
        int(end_time.timestamp())
        // GRANULARITY_SECONDS
        * GRANULARITY_SECONDS
    )

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
        f"Downloading {days} days of {product} "
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
            f"https://api.exchange.coinbase.com/"
            f"products/{product}/candles?{params}"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "coinbase-paper-backtester/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30
            ) as response:

                raw_candles = json.loads(
                    response.read().decode("utf-8")
                )

        except Exception as exc:
            print(
                f"Batch {batch_number + 1} "
                f"download failed: {exc}"
            )
            raise

        batch_number += 1

        if not isinstance(raw_candles, list):
            raise RuntimeError(
                f"Unexpected Coinbase response: "
                f"{raw_candles}"
            )

        for c in raw_candles:
            # Coinbase Exchange format:
            # [time, low, high, open, close, volume]

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

        if raw_candles:
            times = [
                int(c[0])
                for c in raw_candles
                if len(c) >= 6
            ]

            if times:
                first = datetime.fromtimestamp(
                    min(times),
                    timezone.utc
                )

                last = datetime.fromtimestamp(
                    max(times),
                    timezone.utc
                )

                print(
                    f"Batch {batch_number}: "
                    f"{len(raw_candles)} candles | "
                    f"{first} -> {last}"
                )

        else:
            print(
                f"Batch {batch_number}: 0 candles"
            )

        cursor = chunk_end

        time.sleep(0.25)

    candles = list(all_candles.values())

    candles.sort(
        key=lambda candle: candle["time"]
    )

    print(
        f"Total unique candles downloaded: "
        f"{len(candles)}"
    )

    if candles:
        first = datetime.fromtimestamp(
            candles[0]["time"],
            timezone.utc
        )

        last = datetime.fromtimestamp(
            candles[-1]["time"],
            timezone.utc
        )

        print(
            f"Actual data range: {first} -> {last}"
        )

    return candles


# ============================================================
# BACKTEST
# ============================================================

def backtest(product, candles):
    if len(candles) < 50:
        print(
            f"{product}: not enough candles."
        )
        return

    closes = [
        candle["close"]
        for candle in candles
    ]

    fast_ema = ema(
        closes,
        config.EMA_FAST
    )

    slow_ema = ema(
        closes,
        config.EMA_SLOW
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
        config.EMA_SLOW + 2,
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
        # EXIT EXISTING POSITION
        # ====================================================

        if position is not None:
            exit_price = None
            exit_reason = None

            stop_hit = (
                candle["low"]
                <= position["stop"]
            )

            target_hit = (
                candle["high"]
                >= position["target"]
            )

            # Conservative assumption:
            # if both are hit in one candle,
            # assume the stop happened first.

            if stop_hit:
                exit_price = position["stop"]
                exit_reason = "STOP"

            elif target_hit:
                exit_price = position["target"]
                exit_reason = "TARGET"

            if exit_price is not None:
                gross_value = (
                    position["qty"]
                    * exit_price
                )

                exit_fee = (
                    gross_value
                    * config.FEE_RATE
                )

                cash += (
                    gross_value
                    - exit_fee
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
                    "reason": exit_reason,
                    "entry": position["entry"],
                    "exit": exit_price,
                })

                position = None

        # ====================================================
        # LOOK FOR NEW ENTRY
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
                fast_ema[i] > slow_ema[i]
                and
                config.RSI_MIN
                <= rsi_values[i]
                <= config.RSI_MAX
                and
                candle["volume"]
                >= (
                    avg_volume
                    * config.VOLUME_MULTIPLIER
                )
                and
                candle["close"]
                > candles[i - 1]["close"]
            )

            if signal:
                stop_price = (
                    price
                    * (
                        1
                        - config.STOP_LOSS_PCT
                    )
                )

                target_price = (
                    price
                    * (
                        1
                        + config.TAKE_PROFIT_PCT
                    )
                )

                stop_distance = (
                    price - stop_price
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
                            * (
                                1
                                + config.FEE_RATE
                            )
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
                    "stop": stop_price,
                    "target": target_price,
                }

        # ====================================================
        # EQUITY + DRAWDOWN
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
    # CLOSE ANY OPEN POSITION AT END
    # ========================================================

    if position is not None:
        final_price = (
            candles[-1]["close"]
        )

        gross_value = (
            position["qty"]
            * final_price
        )

        exit_fee = (
            gross_value
            * config.FEE_RATE
        )

        cash += (
            gross_value
            - exit_fee
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
            "entry": position["entry"],
            "exit": final_price,
        })

    # ========================================================
    # RESULTS
    # ========================================================

    wins = sum(
        1
        for trade in trades
        if trade["pnl"] > 0
    )

    losses = sum(
        1
        for trade in trades
        if trade["pnl"] <= 0
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

    net_profit = (
        cash
        - config.STARTING_CASH
    )

    return_pct = (
        net_profit
        / config.STARTING_CASH
        * 100
    )

    if trades:
        win_rate = (
            wins
            / len(trades)
            * 100
        )
    else:
        win_rate = 0.0

    print()
    print("=" * 55)
    print(f"{product} BACKTEST RESULTS")
    print("=" * 55)

    print(
        f"Candles tested:    {len(candles)}"
    )

    print(
        f"Trades:            {len(trades)}"
    )

    print(
        f"Wins:              {wins}"
    )

    print(
        f"Losses:            {losses}"
    )

    print(
        f"Win rate:          {win_rate:.2f}%"
    )

    print(
        f"Targets hit:       {targets}"
    )

    print(
        f"Stops hit:         {stops}"
    )

    print(
        f"Starting cash:     "
        f"${config.STARTING_CASH:,.2f}"
    )

    print(
        f"Ending cash:       "
        f"${cash:,.2f}"
    )

    print(
        f"Net P/L:           "
        f"${net_profit:,.2f}"
    )

    print(
        f"Return:            "
        f"{return_pct:.2f}%"
    )

    print(
        f"Max drawdown:      "
        f"{max_drawdown * 100:.2f}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "BTC/ETH STRATEGY BACKTEST"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "Historical data source: "
        "Coinbase Exchange"
    )

    print(
        f"Test period: {DAYS_TO_TEST} days"
    )

    for product in config.PRODUCTS:
        try:
            candles = get_history(
                product
            )

            backtest(
                product,
                candles
            )

        except Exception as exc:
            print(
                f"{product} ERROR: "
                f"{type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    main()
