import time
from datetime import datetime, timezone, timedelta

from coinbase.rest import RESTClient
import config


# ============================================================
# SETTINGS
# ============================================================

DAYS_TO_TEST = 14
CANDLES_PER_REQUEST = 300
GRANULARITY_SECONDS = 300  # 5 minutes


# ============================================================
# INDICATORS
# ============================================================

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

    if avg_loss == 0:
        output[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        output[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            output[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            output[i] = 100 - (100 / (1 + rs))

    return output


# ============================================================
# DOWNLOAD HISTORICAL COINBASE CANDLES IN CHUNKS
# ============================================================

def get_history(client, product, days=DAYS_TO_TEST):
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    all_candles = {}
    cursor = start_time
    batch_number = 0

    # Coinbase allows a maximum window of 300 five-minute candles.
    batch_seconds = 300 * GRANULARITY_SECONDS

    print(f"Downloading about {days} days of {product} candles...")

    while cursor < end_time:
        chunk_end = min(
            cursor + timedelta(seconds=batch_seconds),
            end_time
        )

        # Use positional start/end values because some SDK versions
        # mishandle these values when sent as keyword arguments.
        response = client.get_public_candles(
            product,
            str(int(cursor.timestamp())),
            str(int(chunk_end.timestamp())),
            "FIVE_MINUTE",
            300,
        )

        batch_number += 1

        if response.candles:
            first_time = min(int(c.start) for c in response.candles)
            last_time = max(int(c.start) for c in response.candles)

            print(
                f"Batch {batch_number}: {len(response.candles)} candles "
                f"{datetime.fromtimestamp(first_time, timezone.utc)} -> "
                f"{datetime.fromtimestamp(last_time, timezone.utc)}"
            )

        for c in response.candles:
            candle = {
                "time": int(c.start),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }

            all_candles[candle["time"]] = candle

        cursor = chunk_end
        time.sleep(0.15)

    candles = list(all_candles.values())
    candles.sort(key=lambda x: x["time"])

    print(f"Total unique candles downloaded: {len(candles)}")

    return candles


# ============================================================
# BACKTEST
# ============================================================

def backtest(product, candles):
    if len(candles) < 50:
        print(f"Not enough candles for {product}.")
        return

    closes = [c["close"] for c in candles]

    fast = ema(closes, config.EMA_FAST)
    slow = ema(closes, config.EMA_SLOW)
    rsis = rsi(closes, config.RSI_PERIOD)

    cash = config.STARTING_CASH
    peak = cash
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

        # ----------------------------------------------------
        # EXIT
        # ----------------------------------------------------

        if position is not None:
            exit_price = None
            reason = None

            # Conservative assumption:
            # if stop and target are both touched in one candle,
            # count the stop first.
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
                    "entry": position["entry"],
                    "exit": exit_price,
                })

                position = None

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        if position is None:
            volume_window = [
                candles[j]["volume"]
                for j in range(
                    i - config.VOLUME_LOOKBACK,
                    i
                )
            ]

            avg_volume = sum(volume_window) / len(volume_window)

            signal = (
                fast[i] > slow[i]
                and config.RSI_MIN <= rsis[i] <= config.RSI_MAX
                and candle["volume"]
                >= avg_volume * config.VOLUME_MULTIPLIER
                and candle["close"] > candles[i - 1]["close"]
            )

            if signal:
                stop = price * (1 - config.STOP_LOSS_PCT)
                target = price * (1 + config.TAKE_PROFIT_PCT)

                stop_distance = price - stop

                risk_dollars = cash * config.RISK_PER_TRADE

                if stop_distance <= 0:
                    continue

                qty_by_risk = risk_dollars / stop_distance

                max_position_value = cash * config.MAX_POSITION_PCT
                qty_by_cap = max_position_value / price

                qty = min(qty_by_risk, qty_by_cap)

                position_value = qty * price
                entry_fee = position_value * config.FEE_RATE

                total_cost = position_value + entry_fee

                if total_cost > cash:
                    qty = cash / (price * (1 + config.FEE_RATE))
                    position_value = qty * price
                    entry_fee = position_value * config.FEE_RATE
                    total_cost = position_value + entry_fee

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

        # ----------------------------------------------------
        # EQUITY / DRAWDOWN
        # ----------------------------------------------------

        equity = cash

        if position is not None:
            equity += position["qty"] * price

        peak = max(peak, equity)

        if peak > 0:
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)

    # Close remaining position at final candle for reporting.
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
            "entry": position["entry"],
            "exit": final_price,
        })

    wins = sum(1 for trade in trades if trade["pnl"] > 0)
    losses = sum(1 for trade in trades if trade["pnl"] <= 0)

    net = cash - config.STARTING_CASH

    print()
    print("=" * 50)
    print(f"{product} BACKTEST")
    print("=" * 50)

    print(f"Candles tested: {len(candles):,}")
    print(f"Trades:         {len(trades)}")
    print(f"Wins:           {wins}")
    print(f"Losses:         {losses}")

    if trades:
        print(f"Win rate:       {wins / len(trades) * 100:.1f}%")
    else:
        print("Win rate:       N/A")

    print(f"Starting cash:  ${config.STARTING_CASH:,.2f}")
    print(f"Ending cash:    ${cash:,.2f}")
    print(f"Net P/L:        ${net:,.2f}")
    print(
        f"Return:         "
        f"{net / config.STARTING_CASH * 100:.2f}%"
    )
    print(f"Max drawdown:   {max_drawdown * 100:.2f}%")

    return cash


# ============================================================
# MAIN
# ============================================================

def main():
    print("BTC/ETH PAPER STRATEGY BACKTEST")
    print("LIVE ORDER PLACEMENT: DISABLED")
    print(f"Testing approximately {DAYS_TO_TEST} days.\n")

    client = RESTClient()

    for product in config.PRODUCTS:
        try:
            candles = get_history(
                client,
                product,
                DAYS_TO_TEST,
            )

            backtest(product, candles)

        except Exception as e:
            print(f"{product} backtest error: {e}")

        time.sleep(1)


if __name__ == "__main__":
    main()
