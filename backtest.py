import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import config


# ============================================================
# DIAGNOSTIC SETTINGS
# ============================================================

DAYS_TO_TEST = 30
GRANULARITY_SECONDS = 300
CANDLES_PER_REQUEST = 250

ATR_PERIOD = 14

# Keep risk modest while diagnosing.
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

# Uses whatever fee assumption is currently in config.py
FEE_RATE = config.FEE_RATE


# ============================================================
# FIXED TEST SETTINGS
# ============================================================

SETUPS = {
    "BTC-USD": {
        "fast": 12,
        "slow": 30,
        "trend": 50,
        "rsi_min": 45,
        "rsi_max": 65,
        "volume_mult": 1.1,
        "atr_stop": 1.5,
        "atr_target": 2.0,
        "cooldown": 3,
    },

    "ETH-USD": {
        "fast": 12,
        "slow": 24,
        "trend": 50,
        "rsi_min": 45,
        "rsi_max": 65,
        "volume_mult": 1.1,
        "atr_stop": 1.5,
        "atr_target": 4.0,
        "cooldown": 6,
    },
}


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
        timezone.utc
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
                    "coinbase-paper-diagnostic/1.0",
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

            all_candles[
                candle["time"]
            ] = candle

        batches += 1

        if batches % 10 == 0:
            print(
                f"Downloaded {batches} batches..."
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
# DIAGNOSTIC BACKTEST
# ============================================================

def run_diagnostic(product_name, candles):
    settings = SETUPS[product_name]

    closes = [
        candle["close"]
        for candle in candles
    ]

    fast = ema(
        closes,
        settings["fast"]
    )

    slow = ema(
        closes,
        settings["slow"]
    )

    trend = ema(
        closes,
        settings["trend"]
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

    cooldown_until = 0

    start_index = max(
        settings["trend"] + 2,
        settings["slow"] + 2,
        config.VOLUME_LOOKBACK + 2,
        config.RSI_PERIOD + 2,
        ATR_PERIOD + 2,
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

            if (
                candle["low"]
                <= position["stop"]
            ):
                exit_price = (
                    position["stop"]
                )

                reason = "STOP"

            elif (
                candle["high"]
                >= position["target"]
            ):
                exit_price = (
                    position["target"]
                )

                reason = "TARGET"

            elif price < slow[i]:
                exit_price = price
                reason = "TREND_EXIT"

            if exit_price is not None:
                exit_value = (
                    position["qty"]
                    * exit_price
                )

                exit_fee = (
                    exit_value
                    * FEE_RATE
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

                trade = {
                    "entry_time":
                        position["entry_time"],

                    "exit_time":
                        candle["time"],

                    "entry":
                        position["entry"],

                    "exit":
                        exit_price,

                    "qty":
                        position["qty"],

                    "gross_pnl":
                        gross_pnl,

                    "entry_fee":
                        position["entry_fee"],

                    "exit_fee":
                        exit_fee,

                    "total_fees":
                        total_fees,

                    "net_pnl":
                        net_pnl,

                    "reason":
                        reason,
                }

                trades.append(trade)

                position = None

                cooldown_until = (
                    i
                    + settings["cooldown"]
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

            crossover = (
                fast[i - 1]
                <= slow[i - 1]
                and
                fast[i]
                > slow[i]
            )

            trend_ok = (
                price > trend[i]
                and
                slow[i] > trend[i]
            )

            rsi_ok = (
                settings["rsi_min"]
                <= rsi_values[i]
                <= settings["rsi_max"]
            )

            volume_ok = (
                candle["volume"]
                >= (
                    avg_volume
                    * settings["volume_mult"]
                )
            )

            atr_percent = (
                current_atr / price
            )

            volatility_ok = (
                atr_percent >= 0.001
            )

            signal = (
                crossover
                and trend_ok
                and rsi_ok
                and volume_ok
                and volatility_ok
            )

            if not signal:
                continue

            stop_distance = (
                current_atr
                * settings["atr_stop"]
            )

            target_distance = (
                current_atr
                * settings["atr_target"]
            )

            if stop_distance <= 0:
                continue

            stop_price = (
                price - stop_distance
            )

            target_price = (
                price + target_distance
            )

            risk_dollars = (
                cash * RISK_PER_TRADE
            )

            qty_by_risk = (
                risk_dollars
                / stop_distance
            )

            max_position_value = (
                cash
                * MAX_POSITION_PCT
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

            entry_value = (
                qty * price
            )

            entry_fee = (
                entry_value
                * FEE_RATE
            )

            total_entry_cost = (
                entry_value
                + entry_fee
            )

            if total_entry_cost > cash:
                qty = (
                    cash
                    / (
                        price
                        * (1 + FEE_RATE)
                    )
                )

                entry_value = (
                    qty * price
                )

                entry_fee = (
                    entry_value
                    * FEE_RATE
                )

                total_entry_cost = (
                    entry_value
                    + entry_fee
                )

            cash -= total_entry_cost

            position = {
                "entry_time":
                    candle["time"],

                "entry":
                    price,

                "qty":
                    qty,

                "entry_fee":
                    entry_fee,

                "stop":
                    stop_price,

                "target":
                    target_price,
            }

    # ========================================================
    # FINAL OPEN POSITION
    # ========================================================

    if position is not None:
        candle = candles[-1]

        exit_price = (
            candle["close"]
        )

        exit_value = (
            position["qty"]
            * exit_price
        )

        exit_fee = (
            exit_value
            * FEE_RATE
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
            "entry_time":
                position["entry_time"],

            "exit_time":
                candle["time"],

            "entry":
                position["entry"],

            "exit":
                exit_price,

            "qty":
                position["qty"],

            "gross_pnl":
                gross_pnl,

            "entry_fee":
                position["entry_fee"],

            "exit_fee":
                exit_fee,

            "total_fees":
                total_fees,

            "net_pnl":
                net_pnl,

            "reason":
                "END",
        })

    # ========================================================
    # PRINT EVERY TRADE
    # ========================================================

    print()
    print("=" * 72)

    print(
        f"{product_name} ACCOUNTING DIAGNOSTIC"
    )

    print("=" * 72)

    print(
        f"Fee rate used PER SIDE: "
        f"{FEE_RATE * 100:.4f}%"
    )

    print(
        f"Starting cash: "
        f"${config.STARTING_CASH:,.2f}"
    )

    print()

    total_gross = 0.0
    total_fees = 0.0
    total_net = 0.0

    for number, trade in enumerate(
        trades,
        start=1
    ):
        entry_dt = datetime.fromtimestamp(
            trade["entry_time"],
            timezone.utc
        )

        exit_dt = datetime.fromtimestamp(
            trade["exit_time"],
            timezone.utc
        )

        total_gross += (
            trade["gross_pnl"]
        )

        total_fees += (
            trade["total_fees"]
        )

        total_net += (
            trade["net_pnl"]
        )

        print("-" * 72)

        print(
            f"TRADE {number} | "
            f"{trade['reason']}"
        )

        print(
            f"Entry: {entry_dt} | "
            f"${trade['entry']:,.2f}"
        )

        print(
            f"Exit:  {exit_dt} | "
            f"${trade['exit']:,.2f}"
        )

        print(
            f"Quantity: "
            f"{trade['qty']:.8f}"
        )

        print(
            f"Gross P/L: "
            f"${trade['gross_pnl']:,.2f}"
        )

        print(
            f"Entry fee: "
            f"${trade['entry_fee']:,.2f}"
        )

        print(
            f"Exit fee:  "
            f"${trade['exit_fee']:,.2f}"
        )

        print(
            f"Total fees: "
            f"${trade['total_fees']:,.2f}"
        )

        print(
            f"NET P/L: "
            f"${trade['net_pnl']:,.2f}"
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    gross_winners = sum(
        1
        for trade in trades
        if trade["gross_pnl"] > 0
    )

    net_winners = sum(
        1
        for trade in trades
        if trade["net_pnl"] > 0
    )

    net_losers = (
        len(trades)
        - net_winners
    )

    ending_cash_check = (
        config.STARTING_CASH
        + total_net
    )

    actual_change = (
        cash
        - config.STARTING_CASH
    )

    accounting_difference = (
        actual_change
        - total_net
    )

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    print(
        f"Trades: "
        f"{len(trades)}"
    )

    print(
        f"Gross winning trades: "
        f"{gross_winners}"
    )

    print(
        f"Net winning trades: "
        f"{net_winners}"
    )

    print(
        f"Net losing trades: "
        f"{net_losers}"
    )

    print(
        f"Total gross P/L: "
        f"${total_gross:,.2f}"
    )

    print(
        f"Total fees: "
        f"${total_fees:,.2f}"
    )

    print(
        f"Total net P/L: "
        f"${total_net:,.2f}"
    )

    print(
        f"Ending cash: "
        f"${cash:,.2f}"
    )

    print(
        f"Expected ending cash: "
        f"${ending_cash_check:,.2f}"
    )

    print(
        f"Accounting difference: "
        f"${accounting_difference:,.8f}"
    )

    print()

    if abs(accounting_difference) < 0.01:
        print(
            "ACCOUNTING CHECK: PASS"
        )
    else:
        print(
            "ACCOUNTING CHECK: FAIL"
        )

    if trades:
        print(
            f"Net win rate: "
            f"{net_winners / len(trades) * 100:.2f}%"
        )

    print(
        f"Return: "
        f"{actual_change / config.STARTING_CASH * 100:.2f}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "BTC/ETH BACKTEST ACCOUNTING AUDIT"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Test period: {DAYS_TO_TEST} days"
    )

    print(
        f"Configured fee per side: "
        f"{FEE_RATE * 100:.4f}%"
    )

    for product_name in config.PRODUCTS:
        try:
            candles = get_history(
                product_name
            )

            run_diagnostic(
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
