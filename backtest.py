import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import config


DAYS_TO_TEST = 14
INTERVAL = "5m"
LIMIT = 1000

SYMBOLS = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
}


def ema(values, period):
    if not values:
        return []

    k = 2 / (period + 1)
    result = values[0]
    output = [result]

    for value in values[1:]:
        result = value * k + result * (1 - k)
        output.append(result)

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

    for i in range(period, len(values)):
        if i > period:
            change = values[i] - values[i - 1]
            gain = max(change, 0)
            loss = max(-change, 0)

            avg_gain = (
                (avg_gain * (period - 1)) + gain
            ) / period

            avg_loss = (
                (avg_loss * (period - 1)) + loss
            ) / period

        if avg_loss == 0:
            output[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            output[i] = 100 - (100 / (1 + rs))

    return output


def get_history(product, days=DAYS_TO_TEST):
    symbol = SYMBOLS[product]

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    all_candles = {}
    batch = 0

    print()
    print(
        f"Downloading {days} days of {product} "
        f"using {symbol} historical candles..."
    )

    while start_ms < end_ms:
        params = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": LIMIT,
        })

        url = (
            "https://api.binance.com/api/v3/klines?"
            + params
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "paper-strategy-backtest/1.0"
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        if not data:
            break

        batch += 1

        for row in data:
            candle = {
                "time": int(row[0] / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }

            all_candles[candle["time"]] = candle

        first_date = datetime.fromtimestamp(
            int(data[0][0] / 1000),
            timezone.utc,
        )

        last_date = datetime.fromtimestamp(
            int(data[-1][0] / 1000),
            timezone.utc,
        )

        print(
            f"Batch {batch}: {len(data)} candles | "
            f"{first_date} -> {last_date}"
        )

        next_start = int(data[-1][0]) + 300000

        if next_start <= start_ms:
            break

        start_ms = next_start

        time.sleep(0.15)

    candles = list(all_candles.values())
    candles.sort(key=lambda x: x["time"])

    print(
        f"Total unique candles downloaded: "
        f"{len(candles)}"
    )

    return candles


def backtest(product, candles):
    if len(candles) < 50:
        print(f"{product}: not enough historical data.")
        return

    closes = [c["close"] for c in candles]

    fast = ema(closes, config.EMA_FAST)
    slow = ema(closes, config.EMA_SLOW)
    rsis = rsi(closes, config.RSI_PERIOD)

    cash = float(config.STARTING_CASH)

    peak_equity = cash
    max_drawdown = 0.0

    position = None
    trades = []

    start_index = max(
        config.EMA_SLOW + 2,
        config.VOLUME_LOOKBACK + 2,
        config.RSI_PERIOD + 2,
    )

    for i in range(start_index, len(candles)):
        candle = candles[i]
        price = candle["close"]

        if position is not None:
            exit_price = None
            reason = None

            if candle["low"] <= position["stop"]:
                exit_price = position["stop"]
                reason = "STOP"

            elif candle["high"] >= position["target"]:
                exit_price = position["target"]
                reason = "TARGET"

            if exit_price is not None:
                gross = position["qty"] * exit_price
                exit_fee = gross * config.FEE_RATE

                cash += gross - exit_fee

                pnl = (
                    (exit_price - position["entry"])
                    * position["qty"]
                    - position["entry_fee"]
                    - exit_fee
                )

                trades.append({
                    "pnl": pnl,
                    "reason": reason,
                })

                position = None

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
                and config.RSI_MIN <= rsis[i] <= config.RSI_MAX
                and candle["volume"]
                >= avg_volume * config.VOLUME_MULTIPLIER
                and candle["close"]
                > candles[i - 1]["close"]
            )

            if signal:
                stop = price * (
                    1 - config.STOP_LOSS_PCT
                )

                target = price * (
                    1 + config.TAKE_PROFIT_PCT
                )

                stop_distance = price - stop

                if stop_distance > 0:
                    risk_dollars = (
                        cash * config.RISK_PER_TRADE
                    )

                    qty_by_risk = (
                        risk_dollars / stop_distance
                    )

                    qty_by_cap = (
                        cash
                        * config.MAX_POSITION_PCT
                        / price
                    )

                    qty = min(
                        qty_by_risk,
                        qty_by_cap,
                    )

                    cost = qty * price

                    entry_fee = (
                        cost * config.FEE_RATE
                    )

                    if cost + entry_fee <= cash:
                        cash -= cost + entry_fee

                        position = {
                            "entry": price,
                            "qty": qty,
                            "entry_fee": entry_fee,
                            "stop": stop,
                            "target": target,
                        }

        equity = cash

        if position is not None:
            equity += position["qty"] * price

        peak_equity = max(
            peak_equity,
            equity,
        )

        if peak_equity > 0:
            drawdown = (
                peak_equity - equity
            ) / peak_equity

            max_drawdown = max(
                max_drawdown,
                drawdown,
            )

    if position is not None:
        final_price = candles[-1]["close"]

        gross = position["qty"] * final_price
        exit_fee = gross * config.FEE_RATE

        cash += gross - exit_fee

        pnl = (
            (final_price - position["entry"])
            * position["qty"]
            - position["entry_fee"]
            - exit_fee
        )

        trades.append({
            "pnl": pnl,
            "reason": "END",
        })

    wins = sum(
        1 for t in trades
        if t["pnl"] > 0
    )

    losses = len(trades) - wins

    targets = sum(
        1 for t in trades
        if t["reason"] == "TARGET"
    )

    stops = sum(
        1 for t in trades
        if t["reason"] == "STOP"
    )

    net = cash - config.STARTING_CASH

    print()
    print("=" * 48)
    print(f"{product} BACKTEST")
    print("=" * 48)

    print(f"Candles tested: {len(candles):,}")
    print(f"Trades:         {len(trades)}")
    print(f"Wins:           {wins}")
    print(f"Losses:         {losses}")

    if trades:
        print(
            f"Win rate:       "
            f"{wins / len(trades) * 100:.1f}%"
        )
    else:
        print("Win rate:       0.0%")

    print(
        f"Starting cash:  "
        f"${config.STARTING_CASH:,.2f}"
    )

    print(f"Ending cash:    ${cash:,.2f}")
    print(f"Net P/L:        ${net:,.2f}")

    print(
        f"Return:         "
        f"{net / config.STARTING_CASH * 100:.2f}%"
    )

    print(
        f"Max drawdown:   "
        f"{max_drawdown * 100:.2f}%"
    )

    print(f"Targets hit:    {targets}")
    print(f"Stops hit:      {stops}")


def main():
    print("BTC/ETH STRATEGY BACKTEST")
    print("LIVE ORDER PLACEMENT: DISABLED")
    print(
        "Historical data source: Binance "
        "(backtesting only)"
    )
    print(f"Test period: {DAYS_TO_TEST} days")

    for product in config.PRODUCTS:
        try:
            candles = get_history(product)
            backtest(product, candles)

        except Exception as exc:
            print(
                f"{product} ERROR: "
                f"{type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    main()
