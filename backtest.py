import time
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import config


# ============================================================
# V5.1 — FIXED STRATEGY COST STRESS TEST
# PAPER / BACKTEST ONLY
# ============================================================

DAYS_TO_TEST = 365
GRANULARITY_SECONDS = 3600
CANDLES_PER_REQUEST = 250

STARTING_CASH = float(config.STARTING_CASH)

RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20


# ============================================================
# FREEZE THE V5 PARAMETERS
# DO NOT OPTIMIZE THESE IN THIS TEST
# ============================================================

SETUPS = {
    "BTC-USD": {
        "breakout_lookback": 48,
        "trend_period": 50,
        "volume_mult": 1.2,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 3.0,
        "max_hold_bars": 24,
    },

    "ETH-USD": {
        "breakout_lookback": 12,
        "trend_period": 50,
        "volume_mult": 1.2,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 3.0,
        "max_hold_bars": 24,
    },
}


# ============================================================
# COST SCENARIOS
#
# Fee = per side
# Slippage = adverse movement per side
#
# These are stress-test assumptions, not claims about what
# your actual account will necessarily pay.
# ============================================================

COST_SCENARIOS = {
    "ZERO COST": {
        "fee": 0.0,
        "slippage": 0.0,
    },

    "MAKER 0.40%": {
        "fee": 0.004,
        "slippage": 0.0,
    },

    "MAKER 0.40% + 0.05% SLIPPAGE": {
        "fee": 0.004,
        "slippage": 0.0005,
    },

    "TAKER 0.60%": {
        "fee": 0.006,
        "slippage": 0.0,
    },

    "TAKER 0.60% + 0.05% SLIPPAGE": {
        "fee": 0.006,
        "slippage": 0.0005,
    },
}


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


def atr(candles, period=14):
    output = [0.0] * len(candles)
    true_ranges = [0.0] * len(candles)

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
                    "coinbase-v51-backtest/1.0",
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
# FIXED STRATEGY BACKTEST
# ============================================================

def run_strategy(
    candles,
    settings,
    fee_rate,
    slippage_rate,
):

    breakout_lookback = (
        settings["breakout_lookback"]
    )

    trend_period = (
        settings["trend_period"]
    )

    volume_mult = (
        settings["volume_mult"]
    )

    atr_stop_mult = (
        settings["atr_stop_mult"]
    )

    atr_target_mult = (
        settings["atr_target_mult"]
    )

    max_hold_bars = (
        settings["max_hold_bars"]
    )

    closes = [
        candle["close"]
        for candle in candles
    ]

    trend = ema(
        closes,
        trend_period
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
        breakout_lookback + 5,
        trend_period + 5,
        VOLUME_LOOKBACK + 5,
        ATR_PERIOD + 5,
    )

    for i in range(
        start_index,
        len(candles)
    ):
        candle = candles[i]

        # ====================================================
        # NEXT-BAR ENTRY
        #
        # Long entry receives adverse slippage upward.
        # ====================================================

        if (
            pending_entry is not None
            and position is None
        ):
            raw_entry_price = (
                candle["open"]
            )

            entry_price = (
                raw_entry_price
                * (1.0 + slippage_rate)
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

            if (
                entry_price > 0
                and stop_distance > 0
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

                            "bars_held":
                                0,
                        }

            pending_entry = None

        # ====================================================
        # POSITION MANAGEMENT
        # ====================================================

        if position is not None:
            position["bars_held"] += 1

            stop_hit = (
                candle["low"]
                <= position["stop"]
            )

            target_hit = (
                candle["high"]
                >= position["target"]
            )

            raw_exit_price = None
            reason = None

            # Conservative same-bar assumption.
            if stop_hit:
                raw_exit_price = (
                    position["stop"]
                )

                reason = "STOP"

            elif target_hit:
                raw_exit_price = (
                    position["target"]
                )

                reason = "TARGET"

            elif (
                position["bars_held"]
                >= max_hold_bars
            ):
                raw_exit_price = (
                    candle["close"]
                )

                reason = "TIME"

            if raw_exit_price is not None:
                # Long exit receives adverse slippage downward.
                exit_price = (
                    raw_exit_price
                    * (1.0 - slippage_rate)
                )

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
        # BREAKOUT SIGNAL
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

            if (
                price <= 0
                or current_atr <= 0
            ):
                continue

            prior_high = max(
                candles[j]["high"]
                for j in range(
                    i - breakout_lookback,
                    i,
                )
            )

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

            trend_ok = (
                price > trend[i]
                and
                trend[i] > trend[i - 6]
            )

            breakout_ok = (
                price > prior_high
            )

            volume_ok = (
                candle["volume"]
                >= avg_volume
                * volume_mult
            )

            atr_pct = (
                current_atr
                / price
            )

            volatility_ok = (
                atr_pct >= 0.003
            )

            signal = (
                trend_ok
                and breakout_ok
                and volume_ok
                and volatility_ok
            )

            if signal:
                pending_entry = {
                    "atr":
                        current_atr
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
    # CLOSE FINAL POSITION
    # ========================================================

    if position is not None:
        raw_exit_price = (
            candles[-1]["close"]
        )

        exit_price = (
            raw_exit_price
            * (1.0 - slippage_rate)
        )

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
                "END",
        })

    return calculate_results(
        trades,
        max_drawdown,
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_results(
    trades,
    max_drawdown,
):

    count = len(trades)

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
        trade["net"]
        for trade in trades
        if trade["net"] > 0
    ]

    losers = [
        trade["net"]
        for trade in trades
        if trade["net"] <= 0
    ]

    gross_profit = sum(
        winners
    )

    gross_loss = abs(
        sum(losers)
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )

    elif gross_profit > 0:
        profit_factor = (
            math.inf
        )

    else:
        profit_factor = 0.0

    win_rate = (
        len(winners)
        / count
        * 100.0
        if count
        else 0.0
    )

    avg_winner = (
        sum(winners)
        / len(winners)
        if winners
        else 0.0
    )

    avg_loser = (
        sum(losers)
        / len(losers)
        if losers
        else 0.0
    )

    expectancy = (
        total_net
        / count
        if count
        else 0.0
    )

    avg_fee = (
        total_fees
        / count
        if count
        else 0.0
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

    time_exits = sum(
        1
        for trade in trades
        if trade["reason"] == "TIME"
    )

    return {
        "trades":
            count,

        "win_rate":
            win_rate,

        "gross":
            total_gross,

        "fees":
            total_fees,

        "net":
            total_net,

        "return_pct":
            total_net
            / STARTING_CASH
            * 100.0,

        "drawdown":
            max_drawdown
            * 100.0,

        "profit_factor":
            profit_factor,

        "avg_winner":
            avg_winner,

        "avg_loser":
            avg_loser,

        "expectancy":
            expectancy,

        "avg_fee":
            avg_fee,

        "targets":
            targets,

        "stops":
            stops,

        "time_exits":
            time_exits,
    }


# ============================================================
# OUTPUT
# ============================================================

def pf_text(value):
    if math.isinf(value):
        return "INF"

    return f"{value:.2f}"


def print_result(
    scenario_name,
    result,
):

    print()
    print("-" * 78)

    print(
        scenario_name
    )

    print("-" * 78)

    print(
        f"Return: "
        f"{result['return_pct']:+.2f}%"
    )

    print(
        f"Trades: "
        f"{result['trades']}"
    )

    print(
        f"Win rate: "
        f"{result['win_rate']:.1f}%"
    )

    print(
        f"Gross P/L: "
        f"${result['gross']:+.2f}"
    )

    print(
        f"Fees: "
        f"${result['fees']:.2f}"
    )

    print(
        f"Net P/L: "
        f"${result['net']:+.2f}"
    )

    print(
        f"Average fee/trade: "
        f"${result['avg_fee']:.2f}"
    )

    print(
        f"Average winner: "
        f"${result['avg_winner']:+.2f}"
    )

    print(
        f"Average loser: "
        f"${result['avg_loser']:+.2f}"
    )

    print(
        f"Expectancy/trade: "
        f"${result['expectancy']:+.2f}"
    )

    print(
        f"Profit factor: "
        f"{pf_text(result['profit_factor'])}"
    )

    print(
        f"Max drawdown: "
        f"{result['drawdown']:.2f}%"
    )

    print(
        f"Exits -> "
        f"Targets {result['targets']} | "
        f"Stops {result['stops']} | "
        f"Time {result['time_exits']}"
    )


# ============================================================
# PRODUCT TEST
# ============================================================

def test_product(
    product_name,
    candles,
):

    settings = (
        SETUPS[product_name]
    )

    print()
    print("=" * 78)

    print(
        f"{product_name} | "
        f"V5.1 FIXED COST STRESS TEST"
    )

    print("=" * 78)

    print(
        "NO PARAMETER OPTIMIZATION"
    )

    print(
        f"Breakout: "
        f"{settings['breakout_lookback']}H"
    )

    print(
        f"Trend EMA: "
        f"{settings['trend_period']}"
    )

    print(
        f"Volume: "
        f"{settings['volume_mult']}x"
    )

    print(
        f"ATR stop/target: "
        f"{settings['atr_stop_mult']}x / "
        f"{settings['atr_target_mult']}x"
    )

    print(
        f"Max hold: "
        f"{settings['max_hold_bars']}H"
    )

    results = {}

    for (
        scenario_name,
        costs,
    ) in COST_SCENARIOS.items():

        result = run_strategy(
            candles,
            settings,
            costs["fee"],
            costs["slippage"],
        )

        results[
            scenario_name
        ] = result

        print_result(
            scenario_name,
            result,
        )

    print()
    print("=" * 78)

    print(
        f"{product_name} COST SURVIVAL SUMMARY"
    )

    print("=" * 78)

    for (
        scenario_name,
        result,
    ) in results.items():

        if (
            result["net"] > 0
            and
            result["profit_factor"] > 1.0
            and
            result["expectancy"] > 0
        ):
            status = "PASS"

        else:
            status = "FAIL"

        print(
            f"{scenario_name}: "
            f"{status} | "
            f"Net ${result['net']:+.2f} | "
            f"PF {pf_text(result['profit_factor'])} | "
            f"Expect ${result['expectancy']:+.2f}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC/ETH STRATEGY V5.1"
    )

    print(
        "FIXED BREAKOUT COST STRESS TEST"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Historical period: "
        f"{DAYS_TO_TEST} days"
    )

    print(
        "1-HOUR EXECUTION"
    )

    print(
        "V5 PARAMETERS ARE FROZEN"
    )

    print(
        f"Risk per trade: "
        f"{RISK_PER_TRADE * 100:.1f}%"
    )

    for product_name in [
        "BTC-USD",
        "ETH-USD",
    ]:

        try:
            candles = get_history(
                product_name
            )

            test_product(
                product_name,
                candles,
            )

        except Exception as exc:
            print(
                f"{product_name} ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


if __name__ == "__main__":
    main()
