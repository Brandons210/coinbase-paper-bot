import time
from datetime import datetime, timezone, timedelta

from coinbase.rest import RESTClient
import config


def ema(values, period):
    k = 2 / (period + 1)
    result = values[0]
    out = [result]
    for value in values[1:]:
        result = value * k + result * (1 - k)
        out.append(result)
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return [50.0] * len(values)

    output = [50.0] * len(values)
    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

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


def get_history(client, product, hours=72):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    response = client.get_public_candles(
        product_id=product,
        start=str(int(start.timestamp())),
        end=str(int(end.timestamp())),
        granularity="FIVE_MINUTE",
        limit=350,
    )

    candles = []
    for c in response.candles:
        candles.append({
            "time": int(c.start),
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
        })

    candles.sort(key=lambda x: x["time"])
    return candles


def backtest(product, candles):
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

        if position:
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
                    (exit_price - position["entry"]) * position["qty"]
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
                for j in range(i - config.VOLUME_LOOKBACK, i)
            ]
            avg_volume = sum(volume_window) / len(volume_window)

            signal = (
                fast[i] > slow[i]
                and config.RSI_MIN <= rsis[i] <= config.RSI_MAX
                and candle["volume"] >= avg_volume * config.VOLUME_MULTIPLIER
                and candle["close"] > candles[i - 1]["close"]
            )

            if signal:
                stop = price * (1 - config.STOP_LOSS_PCT)
                target = price * (1 + config.TAKE_PROFIT_PCT)

                risk_dollars = cash * config.RISK_PER_TRADE
                risk_per_coin = price - stop

                qty_by_risk = risk_dollars / risk_per_coin
                qty_by_cap = (cash * config.MAX_POSITION_PCT) / price
                qty = min(qty_by_risk, qty_by_cap)

                cost = qty * price
                entry_fee = cost * config.FEE_RATE

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

        if position:
            equity += position["qty"] * price

        peak = max(peak, equity)

        if peak > 0:
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)

    if position:
        final_price = candles[-1]["close"]
        gross = position["qty"] * final_price
        exit_fee = gross * config.FEE_RATE
        cash += gross - exit_fee

        pnl = (
            (final_price - position["entry"]) * position["qty"]
            - position["entry_fee"]
            - exit_fee
        )
        trades.append({"pnl": pnl, "reason": "END"})

    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    net = cash - config.STARTING_CASH

    print("\n" + "=" * 42)
    print(f"{product} BACKTEST")
    print("=" * 42)
    print(f"Candles tested: {len(candles)}")
    print(f"Trades:         {len(trades)}")
    print(f"Wins:           {wins}")
    print(f"Losses:         {losses}")

    if trades:
        print(f"Win rate:       {wins / len(trades) * 100:.1f}%")

    print(f"Starting cash:  ${config.STARTING_CASH:,.2f}")
    print(f"Ending cash:    ${cash:,.2f}")
    print(f"Net P/L:        ${net:,.2f}")
    print(f"Return:         {net / config.STARTING_CASH * 100:.2f}%")
    print(f"Max drawdown:   {max_drawdown * 100:.2f}%")

    return cash


def main():
    print("BTC/ETH PAPER STRATEGY BACKTEST")
    print("No live orders can be placed.\n")

    client = RESTClient()

    for product in config.PRODUCTS:
        print(f"Downloading {product} history...")
        candles = get_history(client, product)
        backtest(product, candles)
        time.sleep(1)


if __name__ == "__main__":
    main()
