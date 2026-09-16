import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta


# ============================================================
# NASDAQ COMPOSITE STRATEGY V1
# DAILY TREND / BREAKOUT BACKTEST
# PAPER / BACKTEST ONLY
#
# MARKET:
# Nasdaq Composite Index (^IXIC)
#
# IMPORTANT:
# ^IXIC is an index, not a directly tradable security.
# This tests the SIGNAL / MARKET BEHAVIOR.
#
# LIVE ORDER PLACEMENT: DISABLED
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

YEARS_TO_TEST = 10

STARTING_CASH = 500.0

# 1% = $5 initial risk on a $500 account.
RISK_PER_TRADE = 0.01

# Allow up to 100% of current cash to be allocated.
MAX_POSITION_PCT = 1.00

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

SYMBOL = "^IXIC"


# ============================================================
# EXECUTION COST / SLIPPAGE SCENARIOS
#
# ^IXIC itself cannot be directly bought or sold.
# These scenarios test whether the SIGNAL can survive
# execution friction when later mapped to a tradable product.
# ============================================================

COST_SCENARIOS = {

    "ZERO COST": {
        "fee": 0.0,
        "slippage": 0.0,
    },

    "LOW FRICTION": {
        "fee": 0.0,
        "slippage": 0.0001,       # 0.01%
    },

    "NORMAL FRICTION": {
        "fee": 0.0,
        "slippage": 0.0005,       # 0.05%
    },

    "STRESS FRICTION": {
        "fee": 0.0,
        "slippage": 0.0010,       # 0.10%
    },
}


# ============================================================
# FIXED STRATEGY SET
#
# No giant optimizer.
#
# All periods are measured in TRADING DAYS.
# ============================================================

STRATEGIES = [

    {
        "name": "BREAKOUT-20D",

        "breakout": 20,

        "trend_ema": 50,

        "volume_mult": 1.0,

        "stop_atr": 2.0,

        "target_atr": 5.0,

        "max_hold": 40,
    },

    {
        "name": "BREAKOUT-50D",

        "breakout": 50,

        "trend_ema": 100,

        "volume_mult": 1.0,

        "stop_atr": 2.5,

        "target_atr": 7.0,

        "max_hold": 80,
    },

    {
        "name": "BREAKOUT-100D",

        "breakout": 100,

        "trend_ema": 200,

        "volume_mult": 0.9,

        "stop_atr": 3.0,

        "target_atr": 10.0,

        "max_hold": 160,
    },
]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):

    if not values:
        return []

    k = 2.0 / (period + 1.0)

    current = values[0]

    result = [current]

    for value in values[1:]:

        current = (
            value * k
            + current * (1.0 - k)
        )

        result.append(current)

    return result


def atr(candles, period):

    result = [0.0] * len(candles)

    true_ranges = [0.0] * len(candles)

    for i in range(1, len(candles)):

        high = candles[i]["high"]

        low = candles[i]["low"]

        previous_close = (
            candles[i - 1]["close"]
        )

        true_ranges[i] = max(

            high - low,

            abs(
                high - previous_close
            ),

            abs(
                low - previous_close
            ),
        )

    if len(candles) <= period:
        return result

    current = (
        sum(
            true_ranges[
                1:
                period + 1
            ]
        )
        / period
    )

    result[period] = current

    for i in range(
        period + 1,
        len(candles)
    ):

        current = (

            current * (period - 1)

            + true_ranges[i]

        ) / period

        result[i] = current

    return result


# ============================================================
# DOWNLOAD NASDAQ COMPOSITE DAILY DATA
#
# Yahoo Finance chart endpoint.
# ============================================================

def download_history(symbol):

    now = datetime.now(
        timezone.utc
    )

    # Add a little extra time so we comfortably receive
    # the requested number of calendar years.
    start = (
        now
        - timedelta(
            days=YEARS_TO_TEST * 365 + 30
        )
    )

    period1 = int(
        start.timestamp()
    )

    period2 = int(
        now.timestamp()
    )

    encoded_symbol = (
        urllib.parse.quote(
            symbol,
            safe=""
        )
    )

    params = urllib.parse.urlencode({

        "period1":
            period1,

        "period2":
            period2,

        "interval":
            "1d",

        "events":
            "history",

        "includeAdjustedClose":
            "true",
    })

    url = (

        "https://query1.finance.yahoo.com/"
        "v8/finance/chart/"
        f"{encoded_symbol}?"
        f"{params}"
    )

    print()

    print(
        f"Downloading approximately "
        f"{YEARS_TO_TEST} years "
        f"of {symbol} daily candles..."
    )

    request = urllib.request.Request(

        url,

        headers={

            "User-Agent":
                "Mozilla/5.0",

            "Accept":
                "application/json",
        },
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            data = json.loads(

                response
                .read()
                .decode("utf-8")
            )

    except Exception as exc:

        raise RuntimeError(
            f"Yahoo download failed: {exc}"
        )

    chart = data.get(
        "chart",
        {}
    )

    error = chart.get(
        "error"
    )

    if error:

        raise RuntimeError(
            f"Yahoo returned error: {error}"
        )

    results = chart.get(
        "result"
    )

    if not results:

        raise RuntimeError(
            "Yahoo returned no chart results."
        )

    result = results[0]

    timestamps = result.get(
        "timestamp",
        []
    )

    indicators = result.get(
        "indicators",
        {}
    )

    quotes = indicators.get(
        "quote",
        []
    )

    if not quotes:

        raise RuntimeError(
            "Yahoo returned no quote data."
        )

    quote = quotes[0]

    opens = quote.get(
        "open",
        []
    )

    highs = quote.get(
        "high",
        []
    )

    lows = quote.get(
        "low",
        []
    )

    closes = quote.get(
        "close",
        []
    )

    volumes = quote.get(
        "volume",
        []
    )

    candles = []

    for i in range(
        len(timestamps)
    ):

        try:

            timestamp = timestamps[i]

            open_price = opens[i]

            high_price = highs[i]

            low_price = lows[i]

            close_price = closes[i]

            volume = volumes[i]

        except IndexError:

            continue

        if (
            timestamp is None
            or
            open_price is None
            or
            high_price is None
            or
            low_price is None
            or
            close_price is None
        ):

            continue

        if volume is None:
            volume = 0.0

        candles.append({

            "time":
                int(timestamp),

            "open":
                float(open_price),

            "high":
                float(high_price),

            "low":
                float(low_price),

            "close":
                float(close_price),

            "volume":
                float(volume),
        })

    candles.sort(
        key=lambda x: x["time"]
    )

    print(
        f"Downloaded daily candles: "
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
            f"Daily range: "
            f"{first.date()} -> "
            f"{last.date()}"
        )

    if len(candles) < 1000:

        raise RuntimeError(
            "Not enough Nasdaq daily candles "
            "were downloaded."
        )

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

    closes = [

        candle["close"]

        for candle in candles
    ]

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

    peak_equity = (
        STARTING_CASH
    )

    max_drawdown = 0.0

    start_index = max(

        strategy["breakout"] + 5,

        strategy["trend_ema"] + 5,

        VOLUME_LOOKBACK + 5,

        ATR_PERIOD + 5,
    )

    for i in range(
        start_index,
        len(candles)
    ):

        candle = candles[i]

        # ====================================================
        # NEXT-DAY OPEN ENTRY
        #
        # Signal happens after prior daily close.
        # Entry happens at next session open.
        # ====================================================

        if (
            pending is not None
            and
            position is None
        ):

            raw_entry = (
                candle["open"]
            )

            entry = (

                raw_entry

                * (
                    1.0
                    + slippage_rate
                )
            )

            atr_signal = (
                pending["atr"]
            )

            stop_distance = (

                atr_signal

                * strategy["stop_atr"]
            )

            target_distance = (

                atr_signal

                * strategy["target_atr"]
            )

            if (
                entry > 0
                and
                stop_distance > 0
            ):

                risk_cash = (

                    cash

                    * RISK_PER_TRADE
                )

                qty_risk = (

                    risk_cash

                    / stop_distance
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

                entry_value = (

                    qty

                    * entry
                )

                entry_fee = (

                    entry_value

                    * fee_rate
                )

                total_entry_cost = (

                    entry_value

                    + entry_fee
                )

                if (
                    qty > 0
                    and
                    total_entry_cost <= cash
                ):

                    cash -= (
                        total_entry_cost
                    )

                    position = {

                        "entry":
                            entry,

                        "qty":
                            qty,

                        "entry_fee":
                            entry_fee,

                        "stop":
                            entry
                            - stop_distance,

                        "target":
                            entry
                            + target_distance,

                        "bars":
                            0,

                        "entry_time":
                            candle["time"],

                        "lowest":
                            entry,

                        "highest":
                            entry,
                    }

            pending = None

        # ====================================================
        # MANAGE OPEN POSITION
        # ====================================================

        if position is not None:

            position["bars"] += 1

            position["lowest"] = min(

                position["lowest"],

                candle["low"],
            )

            position["highest"] = max(

                position["highest"],

                candle["high"],
            )

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

            # =================================================
            # GAP-AWARE STOP
            #
            # If the market opens below our stop, we cannot
            # realistically assume we were filled at the
            # better stop price.
            # =================================================

            if (
                candle["open"]
                <= position["stop"]
            ):

                raw_exit = (
                    candle["open"]
                )

                reason = "GAP_STOP"

            # =================================================
            # GAP-AWARE TARGET
            #
            # If price opens above the target, use the open.
            # =================================================

            elif (
                candle["open"]
                >= position["target"]
            ):

                raw_exit = (
                    candle["open"]
                )

                reason = "GAP_TARGET"

            # Conservative same-bar assumption:
            # if both stop and target occur inside the day's
            # range, STOP is assumed first.
            elif stop_hit:

                raw_exit = (
                    position["stop"]
                )

                reason = "STOP"

            elif target_hit:

                raw_exit = (
                    position["target"]
                )

                reason = "TARGET"

            elif (
                position["bars"]
                >= strategy["max_hold"]
            ):

                raw_exit = (
                    candle["close"]
                )

                reason = "TIME"

            if raw_exit is not None:

                exit_price = (

                    raw_exit

                    * (
                        1.0
                        - slippage_rate
                    )
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

                mae_pct = (

                    (
                        position["lowest"]
                        - position["entry"]
                    )

                    / position["entry"]

                    * 100.0
                )

                mfe_pct = (

                    (
                        position["highest"]
                        - position["entry"]
                    )

                    / position["entry"]

                    * 100.0
                )

                trades.append({

                    "gross":
                        gross_pnl,

                    "fees":
                        total_fees,

                    "net":
                        net_pnl,

                    "reason":
                        reason,

                    "mae_pct":
                        mae_pct,

                    "mfe_pct":
                        mfe_pct,

                    "bars":
                        position["bars"],
                })

                position = None

        # ====================================================
        # BREAKOUT SIGNAL
        # ====================================================

        if (
            position is None
            and
            pending is None
            and
            i < len(candles) - 1
        ):

            price = (
                candle["close"]
            )

            current_atr = (
                atr_values[i]
            )

            if (
                price <= 0
                or
                current_atr <= 0
            ):

                continue

            prior_high = max(

                candles[j]["high"]

                for j in range(

                    i
                    - strategy["breakout"],

                    i,
                )
            )

            volume_slice = [

                candles[j]["volume"]

                for j in range(

                    i - VOLUME_LOOKBACK,

                    i,
                )

                if candles[j]["volume"] > 0
            ]

            if volume_slice:

                avg_volume = (

                    sum(volume_slice)

                    / len(volume_slice)
                )

            else:

                avg_volume = 0.0

            # =================================================
            # TREND FILTER
            #
            # Close must be above EMA.
            # EMA must be rising.
            # =================================================

            trend_ok = (

                price
                > trend[i]

                and

                trend[i]
                > trend[i - 5]
            )

            # =================================================
            # BREAKOUT FILTER
            # =================================================

            breakout_ok = (

                price

                > prior_high
            )

            # =================================================
            # VOLUME FILTER
            # =================================================

            if (
                avg_volume > 0
                and
                candle["volume"] > 0
            ):

                volume_ok = (

                    candle["volume"]

                    >=

                    avg_volume

                    * strategy["volume_mult"]
                )

            else:

                # Don't reject every signal if index volume
                # data is unavailable.
                volume_ok = True

            # =================================================
            # VOLATILITY FILTER
            #
            # ATR must be at least 0.30% of index value.
            # =================================================

            atr_pct = (

                current_atr

                / price
            )

            volatility_ok = (

                atr_pct

                >= 0.003
            )

            signal = (

                trend_ok

                and

                breakout_ok

                and

                volume_ok

                and

                volatility_ok
            )

            if signal:

                pending = {

                    "atr":
                        current_atr,

                    "signal_time":
                        candle["time"],
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
    # CLOSE POSITION AT END OF DATA
    # ========================================================

    if position is not None:

        raw_exit = (
            candles[-1]["close"]
        )

        exit_price = (

            raw_exit

            * (
                1.0
                - slippage_rate
            )
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

        mae_pct = (

            (
                position["lowest"]
                - position["entry"]
            )

            / position["entry"]

            * 100.0
        )

        mfe_pct = (

            (
                position["highest"]
                - position["entry"]
            )

            / position["entry"]

            * 100.0
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

            "mae_pct":
                mae_pct,

            "mfe_pct":
                mfe_pct,

            "bars":
                position["bars"],
        })

    return calculate_stats(

        trades,

        max_drawdown,
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    trades,
    max_drawdown,
):

    count = len(trades)

    gross = sum(

        trade["gross"]

        for trade in trades
    )

    fees = sum(

        trade["fees"]

        for trade in trades
    )

    net = sum(

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

    avg_mae = (

        sum(
            trade["mae_pct"]
            for trade in trades
        )

        / count

        if count

        else 0.0
    )

    avg_mfe = (

        sum(
            trade["mfe_pct"]
            for trade in trades
        )

        / count

        if count

        else 0.0
    )

    avg_bars = (

        sum(
            trade["bars"]
            for trade in trades
        )

        / count

        if count

        else 0.0
    )

    targets = sum(

        1

        for trade in trades

        if trade["reason"] in (
            "TARGET",
            "GAP_TARGET",
        )
    )

    stops = sum(

        1

        for trade in trades

        if trade["reason"] in (
            "STOP",
            "GAP_STOP",
        )
    )

    time_exits = sum(

        1

        for trade in trades

        if trade["reason"] == "TIME"
    )

    end_exits = sum(

        1

        for trade in trades

        if trade["reason"] == "END"
    )

    return {

        "trades":
            count,

        "gross":
            gross,

        "fees":
            fees,

        "net":
            net,

        "ending_cash":
            STARTING_CASH
            + net,

        "return":
            net
            / STARTING_CASH
            * 100.0,

        "win_rate":
            win_rate,

        "pf":
            profit_factor,

        "expectancy":
            expectancy,

        "avg_gross":
            avg_gross,

        "avg_fee":
            avg_fee,

        "avg_mae":
            avg_mae,

        "avg_mfe":
            avg_mfe,

        "avg_bars":
            avg_bars,

        "dd":
            max_drawdown
            * 100.0,

        "targets":
            targets,

        "stops":
            stops,

        "times":
            time_exits,

        "ends":
            end_exits,
    }


def pf_text(value):

    if math.isinf(value):
        return "INF"

    return f"{value:.2f}"


# ============================================================
# FOUR CHRONOLOGICAL WINDOWS
# ============================================================

def make_windows(candles):

    n = len(candles)

    quarter = (
        n // 4
    )

    return [

        (
            "WINDOW 1",

            candles[
                0:
                quarter
            ],
        ),

        (
            "WINDOW 2",

            candles[
                quarter:
                quarter * 2
            ],
        ),

        (
            "WINDOW 3",

            candles[
                quarter * 2:
                quarter * 3
            ],
        ),

        (
            "WINDOW 4",

            candles[
                quarter * 3:
            ],
        ),
    ]


# ============================================================
# TEST NASDAQ COMPOSITE
# ============================================================

def test_market(
    symbol,
    candles,
):

    windows = make_windows(
        candles
    )

    print()

    print("=" * 80)

    print(
        f"{symbol} | NASDAQ COMPOSITE V1"
    )

    print("=" * 80)

    for strategy in STRATEGIES:

        print()

        print("#" * 80)

        print(
            strategy["name"]
        )

        print("#" * 80)

        print(
            f"Breakout: "
            f"{strategy['breakout']} "
            f"trading days"
        )

        print(
            f"Trend EMA: "
            f"{strategy['trend_ema']}"
        )

        print(
            f"Volume filter: "
            f"{strategy['volume_mult']}x"
        )

        print(
            f"Stop/Target: "
            f"{strategy['stop_atr']} / "
            f"{strategy['target_atr']} ATR"
        )

        print(
            f"Maximum hold: "
            f"{strategy['max_hold']} "
            f"trading days"
        )

        # ====================================================
        # ZERO-COST WINDOW TEST
        # ====================================================

        positive_windows = 0

        total_window_trades = 0

        print()

        print(
            "ZERO-COST WINDOW TEST"
        )

        for (
            window_name,
            window,
        ) in windows:

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
                and
                result["pf"] > 1.0
            ):

                positive_windows += 1

            print(

                f"{window_name}: "

                f"{result['return']:+.2f}% | "

                f"{result['trades']} trades | "

                f"PF "
                f"{pf_text(result['pf'])} | "

                f"Expect "
                f"${result['expectancy']:+.2f}"
            )

        print(

            f"Positive windows: "
            f"{positive_windows}/4"
        )

        # ====================================================
        # FULL PERIOD COST TEST
        # ====================================================

        print()

        print(
            "FULL-PERIOD FRICTION TEST"
        )

        full_results = {}

        for (
            scenario_name,
            costs,
        ) in COST_SCENARIOS.items():

            result = backtest(

                candles,

                strategy,

                costs["fee"],

                costs["slippage"],
            )

            full_results[
                scenario_name
            ] = result

            if (
                result["net"] > 0
                and
                result["pf"] > 1.0
                and
                result["expectancy"] > 0
            ):

                status = "PASS"

            else:

                status = "FAIL"

            print(

                f"{scenario_name}: "

                f"{status} | "

                f"{result['return']:+.2f}% | "

                f"{result['trades']} trades | "

                f"Net "
                f"${result['net']:+.2f} | "

                f"PF "
                f"{pf_text(result['pf'])} | "

                f"Expect "
                f"${result['expectancy']:+.2f}"
            )

        zero = (
            full_results[
                "ZERO COST"
            ]
        )

        normal = (
            full_results[
                "NORMAL FRICTION"
            ]
        )

        stress = (
            full_results[
                "STRESS FRICTION"
            ]
        )

        print()

        print(
            "TRADE QUALITY"
        )

        print(

            f"Starting cash: "
            f"${STARTING_CASH:.2f}"
        )

        print(

            f"Zero-cost ending value: "
            f"${zero['ending_cash']:.2f}"
        )

        print(

            f"Zero-cost net profit: "
            f"${zero['net']:+.2f}"
        )

        print(

            f"Win rate: "
            f"{zero['win_rate']:.1f}%"
        )

        print(

            f"Avg gross/trade: "
            f"${zero['avg_gross']:+.2f}"
        )

        print(

            f"Avg MAE: "
            f"{zero['avg_mae']:+.2f}%"
        )

        print(

            f"Avg MFE: "
            f"{zero['avg_mfe']:+.2f}%"
        )

        print(

            f"Avg hold: "
            f"{zero['avg_bars']:.1f} "
            f"trading days"
        )

        print(

            f"Exits: "
            f"{zero['targets']} targets | "
            f"{zero['stops']} stops | "
            f"{zero['times']} time | "
            f"{zero['ends']} end"
        )

        print(

            f"Max zero-cost DD: "
            f"{zero['dd']:.2f}%"
        )

        print()

        # ====================================================
        # V1 CLASSIFICATION
        # ====================================================

        if (
            positive_windows >= 3
            and
            total_window_trades >= 20
            and
            normal["net"] > 0
            and
            normal["pf"] > 1.10
            and
            normal["expectancy"] > 0
            and
            stress["net"] > 0
        ):

            print(
                "V1 RESULT: "
                "ROBUST CANDIDATE"
            )

        elif (
            zero["net"] > 0
            and
            zero["pf"] > 1.0
            and
            positive_windows >= 2
        ):

            print(
                "V1 RESULT: "
                "GROSS EDGE - INVESTIGATE"
            )

        else:

            print(
                "V1 RESULT: REJECT"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "NASDAQ COMPOSITE STRATEGY V1"
    )

    print(
        "DAILY TREND / BREAKOUT"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        f"Historical test: "
        f"{YEARS_TO_TEST} years"
    )

    print(
        "Market: Nasdaq Composite (^IXIC)"
    )

    print(
        "Data: Daily OHLC + volume"
    )

    print(
        f"Starting cash: "
        f"${STARTING_CASH:.2f}"
    )

    print(
        f"Risk per trade: "
        f"{RISK_PER_TRADE * 100:.1f}%"
    )

    print(
        f"Initial risk budget: "
        f"${STARTING_CASH * RISK_PER_TRADE:.2f}"
    )

    print(
        "Next-session-open execution"
    )

    print(
        "Gap-aware stops and targets"
    )

    print(
        "MAE/MFE tracking enabled"
    )

    print(
        "Goal: determine whether Nasdaq "
        "has a durable breakout edge"
    )

    print()

    print(
        "NOTE: ^IXIC IS AN INDEX."
    )

    print(
        "This is a signal backtest, "
        "not live trading."
    )

    try:

        candles = download_history(
            SYMBOL
        )

        test_market(
            SYMBOL,
            candles,
        )

    except Exception as exc:

        print()

        print(
            f"{SYMBOL} ERROR: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )


if __name__ == "__main__":

    main()
