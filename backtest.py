import time
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta


# ============================================================
# V6 — 4H LOW-TURNOVER BREAKOUT TEST
# PAPER / BACKTEST ONLY
# ============================================================

DAYS_TO_TEST = 730
GRANULARITY_SECONDS = 14400  # 4 hours
CANDLES_PER_REQUEST = 250

STARTING_CASH = 10000.0

RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.30

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

PRODUCTS = ["BTC-USD", "ETH-USD"]


# ============================================================
# COST SCENARIOS
#
# These remain TEST assumptions, not confirmed account fees.
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

    "MAKER 0.40% + 0.05% SLIP": {
        "fee": 0.004,
        "slippage": 0.0005,
    },

    "TAKER 0.60%": {
        "fee": 0.006,
        "slippage": 0.0,
    },

    "TAKER 0.60% + 0.05% SLIP": {
        "fee": 0.006,
        "slippage": 0.0005,
    },
}


# ============================================================
# SMALL FIXED STRATEGY SET
#
# No giant optimization grid.
# ============================================================

STRATEGIES = [
    {
        "name": "BREAKOUT-3D",
        "breakout": 18,       # 18 x 4H = 72 hours
        "trend_ema": 50,
        "volume_mult": 1.0,
        "stop_atr": 2.5,
        "target_atr": 6.0,
        "max_hold": 42,       # 7 days
    },

    {
        "name": "BREAKOUT-5D",
        "breakout": 30,       # 5 days
        "trend_ema": 75,
        "volume_mult": 1.0,
        "stop_atr": 3.0,
        "target_atr": 8.0,
        "max_hold": 60,       # 10 days
    },

    {
        "name": "BREAKOUT-7D",
        "breakout": 42,       # 7 days
        "trend_ema": 100,
        "volume_mult": 0.9,
        "stop_atr": 3.0,
        "target_atr": 10.0,
        "max_hold": 84,       # 14 days
    },
]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if not values:
        return []

    k = 2.0 / (period + 1)

    result = [values[0]]
    current = values[0]

    for value in values[1:]:
        current = (
            value * k
            + current * (1.0 - k)
        )

        result.append(current)

    return result


def atr(candles, period):
    result = [0.0] * len(candles)
    trs = [0.0] * len(candles)

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        trs[i] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

    if len(candles) <= period:
        return result

    current = (
        sum(trs[1:period + 1])
        / period
    )

    result[period] = current

    for i in range(period + 1, len(candles)):
        current = (
            current * (period - 1)
            + trs[i]
        ) / period

        result[i] = current

    return result


# ============================================================
# DATA DOWNLOAD
# ============================================================

def iso_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def download_history(product):
    now = datetime.now(timezone.utc)

    end_timestamp = (
        int(now.timestamp())
        // GRANULARITY_SECONDS
        * GRANULARITY_SECONDS
    )

    end = datetime.fromtimestamp(
        end_timestamp - GRANULARITY_SECONDS,
        timezone.utc,
    )

    start = end - timedelta(days=DAYS_TO_TEST)

    candles_by_time = {}

    cursor = start
    batches = 0

    chunk_seconds = (
        CANDLES_PER_REQUEST
        * GRANULARITY_SECONDS
    )

    print()
    print(
        f"Downloading {DAYS_TO_TEST} days "
        f"of {product} 4H candles..."
    )

    while cursor < end:
        chunk_end = min(
            cursor + timedelta(seconds=chunk_seconds),
            end,
        )

        params = urllib.parse.urlencode({
            "start": iso_time(cursor),
            "end": iso_time(chunk_end),
            "granularity": GRANULARITY_SECONDS,
        })

        url = (
            "https://api.exchange.coinbase.com/"
            f"products/{product}/candles?"
            f"{params}"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "coinbase-v6-backtest/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected Coinbase response: {data}"
            )

        for c in data:
            if len(c) < 6:
                continue

            candles_by_time[int(c[0])] = {
                "time": int(c[0]),
                "low": float(c[1]),
                "high": float(c[2]),
                "open": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }

        batches += 1

        if batches % 5 == 0:
            print(f"Downloaded {batches} batches...")

        cursor = chunk_end

        time.sleep(0.20)

    candles = list(candles_by_time.values())
    candles.sort(key=lambda x: x["time"])

    print(f"Total candles: {len(candles)}")

    if candles:
        first = datetime.fromtimestamp(
            candles[0]["time"],
            timezone.utc,
        )

        last = datetime.fromtimestamp(
            candles[-1]["time"],
            timezone.utc,
        )

        print(f"Range: {first} -> {last}")

    return candles


# ============================================================
# BACKTEST
# ============================================================

def backtest(
    candles,
    strategy,
    fee_rate,
    slippage_rate,
):

    closes = [x["close"] for x in candles]

    trend = ema(
        closes,
        strategy["trend_ema"],
    )

    atr_values = atr(
        candles,
        ATR_PERIOD,
    )

    cash = STARTING_CASH
    position = None
    pending = None

    trades = []

    peak_equity = STARTING_CASH
    max_drawdown = 0.0

    start_index = max(
        strategy["breakout"] + 5,
        strategy["trend_ema"] + 5,
        VOLUME_LOOKBACK + 5,
        ATR_PERIOD + 5,
    )

    for i in range(start_index, len(candles)):
        candle = candles[i]

        # ====================================================
        # ENTER NEXT CANDLE OPEN
        # ====================================================

        if pending is not None and position is None:
            raw_entry = candle["open"]

            entry = raw_entry * (
                1.0 + slippage_rate
            )

            atr_signal = pending["atr"]

            stop_distance = (
                atr_signal
                * strategy["stop_atr"]
            )

            target_distance = (
                atr_signal
                * strategy["target_atr"]
            )

            risk_cash = (
                cash * RISK_PER_TRADE
            )

            qty_risk = (
                risk_cash / stop_distance
                if stop_distance > 0
                else 0.0
            )

            qty_cap = (
                cash
                * MAX_POSITION_PCT
                / entry
            )

            qty = min(
                qty_risk,
                qty_cap,
            )

            entry_value = qty * entry
            entry_fee = entry_value * fee_rate

            if (
                qty > 0
                and entry_value + entry_fee <= cash
            ):
                cash -= (
                    entry_value
                    + entry_fee
                )

                position = {
                    "entry": entry,
                    "qty": qty,
                    "entry_fee": entry_fee,
                    "stop":
                        entry - stop_distance,
                    "target":
                        entry + target_distance,
                    "bars": 0,
                }

            pending = None

        # ====================================================
        # MANAGE POSITION
        # ====================================================

        if position is not None:
            position["bars"] += 1

            stop_hit = (
                candle["low"]
                <= position["stop"]
            )

            target_hit = (
                candle["high"]
                >= position["target"]
            )

            raw_exit = None
            reason = None

            # Conservative ordering.
            if stop_hit:
                raw_exit = position["stop"]
                reason = "STOP"

            elif target_hit:
                raw_exit = position["target"]
                reason = "TARGET"

            elif (
                position["bars"]
                >= strategy["max_hold"]
            ):
                raw_exit = candle["close"]
                reason = "TIME"

            if raw_exit is not None:
                exit_price = (
                    raw_exit
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

                price_pnl = (
                    (
                        exit_price
                        - position["entry"]
                    )
                    * position["qty"]
                )

                fees = (
                    position["entry_fee"]
                    + exit_fee
                )

                net = price_pnl - fees

                cash += (
                    exit_value
                    - exit_fee
                )

                trades.append({
                    "gross": price_pnl,
                    "fees": fees,
                    "net": net,
                    "reason": reason,
                })

                position = None

        # ====================================================
        # SIGNAL
        # ====================================================

        if (
            position is None
            and pending is None
            and i < len(candles) - 1
        ):
            price = candle["close"]
            current_atr = atr_values[i]

            if current_atr <= 0:
                continue

            prior_high = max(
                candles[j]["high"]
                for j in range(
                    i - strategy["breakout"],
                    i,
                )
            )

            avg_volume = (
                sum(
                    candles[j]["volume"]
                    for j in range(
                        i - VOLUME_LOOKBACK,
                        i,
                    )
                )
                / VOLUME_LOOKBACK
            )

            # Bullish regime.
            trend_ok = (
                price > trend[i]
                and
                trend[i] > trend[i - 3]
            )

            # Actual breakout above previous range.
            breakout_ok = (
                price > prior_high
            )

            volume_ok = (
                candle["volume"]
                >= avg_volume
                * strategy["volume_mult"]
            )

            # Require meaningful volatility.
            atr_pct = current_atr / price

            volatility_ok = (
                atr_pct >= 0.005
            )

            if (
                trend_ok
                and breakout_ok
                and volume_ok
                and volatility_ok
            ):
                pending = {
                    "atr": current_atr
                }

        # ====================================================
        # EQUITY
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
            dd = (
                peak_equity - equity
            ) / peak_equity

            max_drawdown = max(
                max_drawdown,
                dd,
            )

    # ========================================================
    # CLOSE AT END
    # ========================================================

    if position is not None:
        raw_exit = candles[-1]["close"]

        exit_price = (
            raw_exit
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

        price_pnl = (
            (
                exit_price
                - position["entry"]
            )
            * position["qty"]
        )

        fees = (
            position["entry_fee"]
            + exit_fee
        )

        net = price_pnl - fees

        cash += (
            exit_value
            - exit_fee
        )

        trades.append({
            "gross": price_pnl,
            "fees": fees,
            "net": net,
            "reason": "END",
        })

    return stats(
        trades,
        max_drawdown,
    )


# ============================================================
# STATS
# ============================================================

def stats(trades, max_drawdown):
    count = len(trades)

    gross = sum(
        x["gross"] for x in trades
    )

    fees = sum(
        x["fees"] for x in trades
    )

    net = sum(
        x["net"] for x in trades
    )

    winners = [
        x["net"]
        for x in trades
        if x["net"] > 0
    ]

    losers = [
        x["net"]
        for x in trades
        if x["net"] <= 0
    ]

    profit = sum(winners)
    loss = abs(sum(losers))

    if loss > 0:
        pf = profit / loss
    elif profit > 0:
        pf = math.inf
    else:
        pf = 0.0

    win_rate = (
        len(winners) / count * 100
        if count
        else 0.0
    )

    expectancy = (
        net / count
        if count
        else 0.0
    )

    avg_gross = (
        gross / count
        if count
        else 0.0
    )

    avg_fee = (
        fees / count
        if count
        else 0.0
    )

    return {
        "trades": count,
        "gross": gross,
        "fees": fees,
        "net": net,
        "return": (
            net / STARTING_CASH * 100
        ),
        "win_rate": win_rate,
        "pf": pf,
        "expectancy": expectancy,
        "avg_gross": avg_gross,
        "avg_fee": avg_fee,
        "dd": max_drawdown * 100,
        "targets": sum(
            x["reason"] == "TARGET"
            for x in trades
        ),
        "stops": sum(
            x["reason"] == "STOP"
            for x in trades
        ),
        "times": sum(
            x["reason"] == "TIME"
            for x in trades
        ),
    }


def pf_text(value):
    if math.isinf(value):
        return "INF"

    return f"{value:.2f}"


# ============================================================
# CHRONOLOGICAL WINDOWS
#
# Four consecutive ~6-month windows.
# This helps expose regime dependence.
# ============================================================

def make_windows(candles):
    n = len(candles)

    quarter = n // 4

    return [
        (
            "WINDOW 1",
            candles[0:quarter],
        ),
        (
            "WINDOW 2",
            candles[quarter:quarter * 2],
        ),
        (
            "WINDOW 3",
            candles[quarter * 2:quarter * 3],
        ),
        (
            "WINDOW 4",
            candles[quarter * 3:],
        ),
    ]


# ============================================================
# TEST ONE PRODUCT
# ============================================================

def test_product(product, candles):

    windows = make_windows(candles)

    print()
    print("=" * 80)
    print(f"{product} | V6")
    print("=" * 80)

    for strategy in STRATEGIES:

        print()
        print("#" * 80)
        print(strategy["name"])
        print("#" * 80)

        print(
            f"Breakout: "
            f"{strategy['breakout'] * 4} hours"
        )

        print(
            f"EMA: {strategy['trend_ema']}"
        )

        print(
            f"Stop/Target: "
            f"{strategy['stop_atr']} / "
            f"{strategy['target_atr']} ATR"
        )

        print(
            f"Maximum hold: "
            f"{strategy['max_hold'] * 4} hours"
        )

        # ====================================================
        # ZERO-COST REGIME TEST
        # ====================================================

        positive_windows = 0
        total_window_trades = 0

        print()
        print("ZERO-COST WINDOW TEST")

        for window_name, window in windows:

            result = backtest(
                window,
                strategy,
                0.0,
                0.0,
            )

            total_window_trades += (
                result["trades"]
            )

            if (
                result["net"] > 0
                and result["pf"] > 1.0
            ):
                positive_windows += 1

            print(
                f"{window_name}: "
                f"{result['return']:+.2f}% | "
                f"{result['trades']} trades | "
                f"PF {pf_text(result['pf'])} | "
                f"Expect "
                f"${result['expectancy']:+.2f}"
            )

        print(
            f"Positive windows: "
            f"{positive_windows}/4"
        )

        # ====================================================
        # FULL TWO-YEAR COST TEST
        # ====================================================

        print()
        print("FULL-PERIOD COST TEST")

        full_results = {}

        for scenario_name, costs in (
            COST_SCENARIOS.items()
        ):

            result = backtest(
                candles,
                strategy,
                costs["fee"],
                costs["slippage"],
            )

            full_results[
                scenario_name
            ] = result

            status = (
                "PASS"
                if (
                    result["net"] > 0
                    and result["pf"] > 1.0
                    and result["expectancy"] > 0
                )
                else "FAIL"
            )

            print(
                f"{scenario_name}: "
                f"{status} | "
                f"{result['return']:+.2f}% | "
                f"{result['trades']} trades | "
                f"Net ${result['net']:+.2f} | "
                f"PF {pf_text(result['pf'])} | "
                f"Expect "
                f"${result['expectancy']:+.2f}"
            )

        zero = full_results["ZERO COST"]

        print()
        print(
            f"ZERO-COST avg gross/trade: "
            f"${zero['avg_gross']:+.2f}"
        )

        maker = full_results["MAKER 0.40%"]

        print(
            f"MAKER avg fee/trade: "
            f"${maker['avg_fee']:.2f}"
        )

        print(
            f"Exits: "
            f"{zero['targets']} targets | "
            f"{zero['stops']} stops | "
            f"{zero['times']} time"
        )

        print(
            f"Max zero-cost DD: "
            f"{zero['dd']:.2f}%"
        )

        # ====================================================
        # SURVIVAL LABEL
        # ====================================================

        maker_slip = full_results[
            "MAKER 0.40% + 0.05% SLIP"
        ]

        print()

        if (
            positive_windows >= 3
            and
            total_window_trades >= 20
            and
            maker_slip["net"] > 0
            and
            maker_slip["pf"] > 1.0
        ):
            print(
                "V6 RESULT: COST-RESISTANT "
                "CANDIDATE"
            )

        elif (
            zero["net"] > 0
            and
            positive_windows >= 2
        ):
            print(
                "V6 RESULT: GROSS EDGE ONLY"
            )

        else:
            print(
                "V6 RESULT: REJECT"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC/ETH STRATEGY V6"
    )

    print(
        "4-HOUR LOW-TURNOVER BREAKOUT"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "Historical test: 730 days"
    )

    print(
        "Risk per trade: 1.0%"
    )

    print(
        "Goal: fewer trades + "
        "larger average moves"
    )

    for product in PRODUCTS:

        try:
            candles = download_history(
                product
            )

            test_product(
                product,
                candles,
            )

        except Exception as exc:
            print(
                f"{product} ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


if __name__ == "__main__":
    main()
