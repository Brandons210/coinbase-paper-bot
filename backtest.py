import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ============================================================
# NASDAQ 5-MINUTE SCALPER V2
# SELECTIVE EMA + VWAP MOMENTUM
# BACKTEST / PAPER RESEARCH ONLY
#
# LIVE ORDER PLACEMENT: DISABLED
#
# ^IXIC is the Nasdaq Composite INDEX.
# It is being used for signal research.
# ============================================================


# ============================================================
# ACCOUNT
# ============================================================

STARTING_CASH = 500.0

RISK_PER_TRADE = 0.01

MAX_POSITION_PCT = 1.00

SYMBOL = "^IXIC"

INTERVAL = "5m"

RANGE = "60d"


# ============================================================
# TIMEZONE
# ============================================================

try:

    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo(
        "America/New_York"
    )

except Exception:

    EASTERN = None


# ============================================================
# INDICATORS
# ============================================================

FAST_EMA = 9

SLOW_EMA = 21

ATR_PERIOD = 14


# ============================================================
# V2 ENTRY QUALITY FILTERS
# ============================================================

# EMA9 must be separated from EMA21 by at least
# this percentage of price.
MIN_EMA_SEPARATION_PCT = 0.00025

# ATR / price must be at least 0.08%.
MIN_ATR_PCT = 0.0008

# Breakout candle body must represent at least
# 55% of its total high-low range.
MIN_BODY_RATIO = 0.55

# Close should occur in upper portion of candle.
MIN_CLOSE_LOCATION = 0.70

# Require breakout beyond previous high by a tiny amount.
MIN_BREAKOUT_PCT = 0.00005


# ============================================================
# TRADING WINDOWS
#
# Avoid:
# - first 15 minutes
# - much of lunchtime
# - very late entries
#
# WINDOW 1:
# 09:45 -> 11:30
#
# WINDOW 2:
# 13:30 -> 15:15
# ============================================================

MORNING_START = (
    9 * 60 + 45
)

MORNING_END = (
    11 * 60 + 30
)

AFTERNOON_START = (
    13 * 60 + 30
)

AFTERNOON_END = (
    15 * 60 + 15
)


# ============================================================
# EXIT SETTINGS
# ============================================================

STOP_ATR = 0.90

TARGET_ATR = 1.80

MAX_HOLD_BARS = 10

FORCE_EXIT_MINUTE = (
    15 * 60 + 55
)


# ============================================================
# TRADE FREQUENCY CONTROL
# ============================================================

MAX_TRADES_PER_DAY = 2

# Wait 3 complete 5-minute bars after exiting.
COOLDOWN_BARS = 3


# ============================================================
# EXECUTION FRICTION
#
# Slippage applies to BOTH entry and exit.
# ============================================================

COST_SCENARIOS = {

    "ZERO COST": {
        "fee": 0.0,
        "slippage": 0.0,
    },

    "LOW FRICTION": {
        "fee": 0.0,
        "slippage": 0.0001,
    },

    "NORMAL FRICTION": {
        "fee": 0.0,
        "slippage": 0.00025,
    },

    "STRESS FRICTION": {
        "fee": 0.0,
        "slippage": 0.0005,
    },
}


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if not values:

        return []

    k = (
        2.0
        / (
            period + 1.0
        )
    )

    current = (
        values[0]
    )

    result = [
        current
    ]

    for value in values[1:]:

        current = (

            value * k

            + current
            * (
                1.0 - k
            )
        )

        result.append(
            current
        )

    return result


# ============================================================
# ATR
# ============================================================

def atr(candles, period):

    result = (
        [0.0]
        * len(candles)
    )

    true_ranges = (
        [0.0]
        * len(candles)
    )

    for i in range(
        1,
        len(candles)
    ):

        high = (
            candles[i]["high"]
        )

        low = (
            candles[i]["low"]
        )

        previous_close = (
            candles[
                i - 1
            ]["close"]
        )

        true_ranges[i] = max(

            high - low,

            abs(
                high
                - previous_close
            ),

            abs(
                low
                - previous_close
            ),
        )

    if (
        len(candles)
        <= period
    ):

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

    result[
        period
    ] = current

    for i in range(
        period + 1,
        len(candles)
    ):

        current = (

            current
            * (
                period - 1
            )

            + true_ranges[i]

        ) / period

        result[i] = current

    return result


# ============================================================
# TIME HELPERS
# ============================================================

def eastern_datetime(
    timestamp
):

    utc_dt = (
        datetime
        .fromtimestamp(
            timestamp,
            timezone.utc,
        )
    )

    if EASTERN is None:

        return utc_dt

    return (
        utc_dt
        .astimezone(
            EASTERN
        )
    )


def trading_date(
    candle
):

    return (

        eastern_datetime(
            candle["time"]
        )

        .date()
    )


def market_minute(
    candle
):

    dt = (
        eastern_datetime(
            candle["time"]
        )
    )

    return (
        dt.hour * 60
        + dt.minute
    )


def valid_entry_time(
    candle
):

    minute = (
        market_minute(
            candle
        )
    )

    morning = (

        minute >= MORNING_START

        and

        minute < MORNING_END
    )

    afternoon = (

        minute >= AFTERNOON_START

        and

        minute < AFTERNOON_END
    )

    return (
        morning
        or
        afternoon
    )


# ============================================================
# DOWNLOAD 5-MINUTE NASDAQ DATA
# ============================================================

def download_history(
    symbol
):

    encoded_symbol = (
        urllib.parse.quote(
            symbol,
            safe=""
        )
    )

    params = (
        urllib.parse.urlencode({

            "interval":
                INTERVAL,

            "range":
                RANGE,

            "includePrePost":
                "false",

            "events":
                "history",
        })
    )

    url = (

        "https://query1.finance.yahoo.com/"
        "v8/finance/chart/"
        f"{encoded_symbol}?"
        f"{params}"
    )

    print()

    print(

        f"Downloading {RANGE} "
        f"of {symbol} "
        f"{INTERVAL} candles..."
    )

    request = (
        urllib.request.Request(

            url,

            headers={

                "User-Agent":
                    "Mozilla/5.0",

                "Accept":
                    "application/json",
            },
        )
    )

    try:

        with urllib.request.urlopen(

            request,

            timeout=30,

        ) as response:

            data = json.loads(

                response
                .read()
                .decode(
                    "utf-8"
                )
            )

    except Exception as exc:

        raise RuntimeError(

            f"Yahoo download "
            f"failed: {exc}"
        )

    chart = (
        data.get(
            "chart",
            {}
        )
    )

    error = (
        chart.get(
            "error"
        )
    )

    if error:

        raise RuntimeError(

            f"Yahoo error: "
            f"{error}"
        )

    results = (
        chart.get(
            "result"
        )
    )

    if not results:

        raise RuntimeError(
            "No Yahoo chart data."
        )

    result = (
        results[0]
    )

    timestamps = (
        result.get(
            "timestamp",
            []
        )
    )

    quotes = (

        result
        .get(
            "indicators",
            {}
        )
        .get(
            "quote",
            []
        )
    )

    if not quotes:

        raise RuntimeError(
            "No quote data."
        )

    quote = (
        quotes[0]
    )

    opens = (
        quote.get(
            "open",
            []
        )
    )

    highs = (
        quote.get(
            "high",
            []
        )
    )

    lows = (
        quote.get(
            "low",
            []
        )
    )

    closes = (
        quote.get(
            "close",
            []
        )
    )

    volumes = (
        quote.get(
            "volume",
            []
        )
    )

    candles = []

    for i in range(
        len(timestamps)
    ):

        try:

            timestamp = (
                timestamps[i]
            )

            open_price = (
                opens[i]
            )

            high_price = (
                highs[i]
            )

            low_price = (
                lows[i]
            )

            close_price = (
                closes[i]
            )

            volume = (
                volumes[i]
            )

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

        key=lambda x:
            x["time"]
    )

    print(

        f"Downloaded candles: "
        f"{len(candles)}"
    )

    if candles:

        first = (
            eastern_datetime(
                candles[0]["time"]
            )
        )

        last = (
            eastern_datetime(
                candles[-1]["time"]
            )
        )

        print(

            f"Range: "
            f"{first} -> {last}"
        )

    if len(candles) < 500:

        raise RuntimeError(

            "Not enough "
            "5-minute data."
        )

    return candles


# ============================================================
# SESSION VWAP
# ============================================================

def build_session_vwap(
    candles
):

    result = (
        [0.0]
        * len(candles)
    )

    current_day = None

    cumulative_pv = 0.0

    cumulative_volume = 0.0

    for i, candle in enumerate(
        candles
    ):

        day = (
            trading_date(
                candle
            )
        )

        if (
            day
            != current_day
        ):

            current_day = day

            cumulative_pv = 0.0

            cumulative_volume = 0.0

        typical = (

            candle["high"]

            + candle["low"]

            + candle["close"]

        ) / 3.0

        volume = max(

            candle["volume"],

            0.0,
        )

        cumulative_pv += (

            typical
            * volume
        )

        cumulative_volume += (
            volume
        )

        if cumulative_volume > 0:

            result[i] = (

                cumulative_pv

                / cumulative_volume
            )

        else:

            result[i] = (
                candle["close"]
            )

    return result


# ============================================================
# V2 SIGNAL
# ============================================================

def valid_signal(
    i,
    candles,
    fast,
    slow,
    atr_values,
    vwap,
):

    if i < 5:

        return False

    candle = (
        candles[i]
    )

    previous = (
        candles[i - 1]
    )

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

        return False

    # ========================================================
    # TIME FILTER
    # ========================================================

    if not valid_entry_time(
        candle
    ):

        return False

    # ========================================================
    # ATR FILTER
    # ========================================================

    atr_pct = (

        current_atr

        / price
    )

    if (
        atr_pct
        < MIN_ATR_PCT
    ):

        return False

    # ========================================================
    # TREND FILTER
    # ========================================================

    trend_ok = (

        fast[i]
        > slow[i]

        and

        price
        > fast[i]

        and

        price
        > slow[i]
    )

    if not trend_ok:

        return False

    # ========================================================
    # EMA SLOPE
    # ========================================================

    slope_ok = (

        fast[i]
        > fast[i - 2]

        and

        slow[i]
        > slow[i - 2]
    )

    if not slope_ok:

        return False

    # ========================================================
    # EMA SEPARATION
    #
    # Avoid nearly-flat / tangled EMA conditions.
    # ========================================================

    separation = (

        fast[i]
        - slow[i]

    ) / price

    if (
        separation
        < MIN_EMA_SEPARATION_PCT
    ):

        return False

    # ========================================================
    # VWAP CONFIRMATION
    # ========================================================

    if (
        price
        <= vwap[i]
    ):

        return False

    # ========================================================
    # CANDLE BODY QUALITY
    # ========================================================

    candle_range = (

        candle["high"]

        - candle["low"]
    )

    if candle_range <= 0:

        return False

    body = abs(

        candle["close"]

        - candle["open"]
    )

    body_ratio = (

        body

        / candle_range
    )

    if (
        body_ratio
        < MIN_BODY_RATIO
    ):

        return False

    # Must be bullish candle.

    if (
        candle["close"]
        <= candle["open"]
    ):

        return False

    # ========================================================
    # CLOSE LOCATION
    #
    # Require close near high of candle.
    # ========================================================

    close_location = (

        candle["close"]
        - candle["low"]

    ) / candle_range

    if (
        close_location
        < MIN_CLOSE_LOCATION
    ):

        return False

    # ========================================================
    # MOMENTUM BREAKOUT
    # ========================================================

    breakout_level = (

        previous["high"]

        * (
            1.0
            + MIN_BREAKOUT_PCT
        )
    )

    if (
        price
        <= breakout_level
    ):

        return False

    return True


# ============================================================
# BACKTEST
# ============================================================

def backtest(
    candles,
    fee_rate,
    slippage_rate,
):

    closes = [

        candle["close"]

        for candle in candles
    ]

    fast = ema(

        closes,

        FAST_EMA,
    )

    slow = ema(

        closes,

        SLOW_EMA,
    )

    atr_values = atr(

        candles,

        ATR_PERIOD,
    )

    vwap = (
        build_session_vwap(
            candles
        )
    )

    cash = (
        STARTING_CASH
    )

    position = None

    pending = None

    trades = []

    current_day = None

    trades_today = 0

    cooldown = 0

    peak_equity = (
        STARTING_CASH
    )

    max_drawdown = 0.0

    daily_start_cash = (
        STARTING_CASH
    )

    daily_results = {}

    start_index = max(

        SLOW_EMA + 5,

        ATR_PERIOD + 5,
    )

    for i in range(
        start_index,
        len(candles)
    ):

        candle = (
            candles[i]
        )

        day = (
            trading_date(
                candle
            )
        )

        # ====================================================
        # NEW DAY
        # ====================================================

        if (
            day
            != current_day
        ):

            if (
                current_day
                is not None
            ):

                daily_results[
                    current_day
                ] = (

                    cash

                    - daily_start_cash
                )

            current_day = day

            trades_today = 0

            cooldown = 0

            pending = None

            daily_start_cash = (
                cash
            )

        # ====================================================
        # COOLDOWN
        # ====================================================

        if (
            cooldown > 0
            and
            position is None
        ):

            cooldown -= 1

        # ====================================================
        # NEXT-CANDLE ENTRY
        # ====================================================

        if (
            pending is not None
            and
            position is None
        ):

            if (
                pending["day"]
                != day
            ):

                pending = None

            elif (
                trades_today
                >= MAX_TRADES_PER_DAY
            ):

                pending = None

            else:

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

                    * STOP_ATR
                )

                target_distance = (

                    atr_signal

                    * TARGET_ATR
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

                    total_cost = (

                        entry_value

                        + entry_fee
                    )

                    if (
                        qty > 0
                        and
                        total_cost <= cash
                    ):

                        cash -= (
                            total_cost
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

                            "lowest":
                                entry,

                            "highest":
                                entry,

                            "day":
                                day,
                        }

                        trades_today += 1

                pending = None

        # ====================================================
        # MANAGE POSITION
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

            raw_exit = None

            reason = None

            # Gap through stop.

            if (
                candle["open"]
                <= position["stop"]
            ):

                raw_exit = (
                    candle["open"]
                )

                reason = (
                    "GAP_STOP"
                )

            # Gap through target.

            elif (
                candle["open"]
                >= position["target"]
            ):

                raw_exit = (
                    candle["open"]
                )

                reason = (
                    "GAP_TARGET"
                )

            else:

                stop_hit = (

                    candle["low"]

                    <= position["stop"]
                )

                target_hit = (

                    candle["high"]

                    >= position["target"]
                )

                # Conservative same-bar assumption.

                if stop_hit:

                    raw_exit = (
                        position["stop"]
                    )

                    reason = "STOP"

                elif target_hit:

                    raw_exit = (
                        position["target"]
                    )

                    reason = "TARGET"

            # Maximum hold.

            if (
                raw_exit is None
                and
                position["bars"]
                >= MAX_HOLD_BARS
            ):

                raw_exit = (
                    candle["close"]
                )

                reason = "TIME"

            # End-of-day exit.

            if (
                raw_exit is None
                and
                market_minute(
                    candle
                )
                >= FORCE_EXIT_MINUTE
            ):

                raw_exit = (
                    candle["close"]
                )

                reason = "EOD"

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

                    "net":
                        net_pnl,

                    "gross":
                        gross_pnl,

                    "fees":
                        total_fees,

                    "reason":
                        reason,

                    "bars":
                        position["bars"],

                    "mae_pct":
                        mae_pct,

                    "mfe_pct":
                        mfe_pct,

                    "day":
                        day,
                })

                position = None

                cooldown = (
                    COOLDOWN_BARS
                )

        # ====================================================
        # NEW SIGNAL
        # ====================================================

        if (
            position is None
            and
            pending is None
            and
            cooldown == 0
            and
            trades_today
            < MAX_TRADES_PER_DAY
            and
            i < len(candles) - 1
        ):

            signal = (
                valid_signal(

                    i,

                    candles,

                    fast,

                    slow,

                    atr_values,

                    vwap,
                )
            )

            if signal:

                pending = {

                    "atr":
                        atr_values[i],

                    "day":
                        day,
                }

        # ====================================================
        # EQUITY / DRAWDOWN
        # ====================================================

        equity = (
            cash
        )

        if (
            position
            is not None
        ):

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
    # END OF DATA SAFETY EXIT
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

        trades.append({

            "net":
                net_pnl,

            "gross":
                gross_pnl,

            "fees":
                total_fees,

            "reason":
                "END",

            "bars":
                position["bars"],

            "mae_pct":
                0.0,

            "mfe_pct":
                0.0,

            "day":
                trading_date(
                    candles[-1]
                ),
        })

    if current_day is not None:

        daily_results[
            current_day
        ] = (

            cash

            - daily_start_cash
        )

    return calculate_stats(

        trades,

        max_drawdown,

        daily_results,
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    trades,
    max_drawdown,
    daily_results,
):

    count = (
        len(trades)
    )

    net = sum(

        x["net"]

        for x in trades
    )

    gross = sum(

        x["gross"]

        for x in trades
    )

    fees = sum(

        x["fees"]

        for x in trades
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

    gross_profit = (
        sum(winners)
    )

    gross_loss = abs(
        sum(losers)
    )

    if gross_loss > 0:

        pf = (

            gross_profit

            / gross_loss
        )

    elif gross_profit > 0:

        pf = (
            math.inf
        )

    else:

        pf = 0.0

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

    avg_win = (

        sum(winners)

        / len(winners)

        if winners

        else 0.0
    )

    avg_loss = (

        sum(losers)

        / len(losers)

        if losers

        else 0.0
    )

    payoff = (

        avg_win

        / abs(avg_loss)

        if avg_loss != 0

        else 0.0
    )

    avg_bars = (

        sum(
            x["bars"]
            for x in trades
        )

        / count

        if count

        else 0.0
    )

    avg_mae = (

        sum(
            x["mae_pct"]
            for x in trades
        )

        / count

        if count

        else 0.0
    )

    avg_mfe = (

        sum(
            x["mfe_pct"]
            for x in trades
        )

        / count

        if count

        else 0.0
    )

    max_consecutive_losses = 0

    current_losses = 0

    for trade in trades:

        if trade["net"] <= 0:

            current_losses += 1

            max_consecutive_losses = max(

                max_consecutive_losses,

                current_losses,
            )

        else:

            current_losses = 0

    active_days = set(

        x["day"]

        for x in trades
    )

    trading_days = (
        len(active_days)
    )

    trades_per_day = (

        count
        / trading_days

        if trading_days

        else 0.0
    )

    winning_days = sum(

        1

        for value
        in daily_results.values()

        if value > 0
    )

    losing_days = sum(

        1

        for value
        in daily_results.values()

        if value < 0
    )

    best_day = (

        max(
            daily_results.values()
        )

        if daily_results

        else 0.0
    )

    worst_day = (

        min(
            daily_results.values()
        )

        if daily_results

        else 0.0
    )

    return {

        "trades":
            count,

        "net":
            net,

        "gross":
            gross,

        "fees":
            fees,

        "return":
            (
                net
                / STARTING_CASH
                * 100.0
            ),

        "ending_cash":
            (
                STARTING_CASH
                + net
            ),

        "pf":
            pf,

        "win_rate":
            win_rate,

        "expectancy":
            expectancy,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss,

        "payoff":
            payoff,

        "avg_bars":
            avg_bars,

        "avg_mae":
            avg_mae,

        "avg_mfe":
            avg_mfe,

        "dd":
            (
                max_drawdown
                * 100.0
            ),

        "max_losses":
            max_consecutive_losses,

        "trading_days":
            trading_days,

        "trades_per_day":
            trades_per_day,

        "winning_days":
            winning_days,

        "losing_days":
            losing_days,

        "best_day":
            best_day,

        "worst_day":
            worst_day,
    }


def pf_text(
    value
):

    if math.isinf(
        value
    ):

        return "INF"

    return (
        f"{value:.2f}"
    )


# ============================================================
# FOUR WINDOWS
# ============================================================

def make_windows(
    candles
):

    n = (
        len(candles)
    )

    quarter = (
        n // 4
    )

    return [

        (
            "WINDOW 1",

            candles[
                :quarter
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
# TEST
# ============================================================

def test_market(
    candles
):

    print()

    print(
        "=" * 80
    )

    print(
        "^IXIC | "
        "NASDAQ 5-MINUTE SCALPER V2"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "SELECTIVE EMA + VWAP MOMENTUM"
    )

    print(

        f"EMA: "
        f"{FAST_EMA}/{SLOW_EMA}"
    )

    print(

        f"Stop/Target: "
        f"{STOP_ATR} / "
        f"{TARGET_ATR} ATR"
    )

    print(

        f"Maximum hold: "
        f"{MAX_HOLD_BARS * 5} minutes"
    )

    print(

        f"Maximum trades/day: "
        f"{MAX_TRADES_PER_DAY}"
    )

    print(

        f"Cooldown: "
        f"{COOLDOWN_BARS * 5} minutes"
    )

    print()

    print(
        "ZERO-COST WINDOW TEST"
    )

    windows = (
        make_windows(
            candles
        )
    )

    positive_windows = 0

    total_window_trades = 0

    for (
        name,
        window,
    ) in windows:

        result = backtest(

            window,

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

            f"{name}: "

            f"{result['return']:+.2f}% | "

            f"{result['trades']} trades | "

            f"PF "
            f"{pf_text(result['pf'])} | "

            f"Expect "
            f"${result['expectancy']:+.3f}"
        )

    print(

        f"Positive windows: "
        f"{positive_windows}/4"
    )

    print()

    print(
        "FULL-PERIOD FRICTION TEST"
    )

    results = {}

    for (
        name,
        costs,
    ) in (
        COST_SCENARIOS.items()
    ):

        result = backtest(

            candles,

            costs["fee"],

            costs["slippage"],
        )

        results[
            name
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

            f"{name}: "

            f"{status} | "

            f"{result['return']:+.2f}% | "

            f"{result['trades']} trades | "

            f"Net "
            f"${result['net']:+.2f} | "

            f"PF "
            f"{pf_text(result['pf'])} | "

            f"Expect "
            f"${result['expectancy']:+.3f}"
        )

    zero = (
        results[
            "ZERO COST"
        ]
    )

    normal = (
        results[
            "NORMAL FRICTION"
        ]
    )

    stress = (
        results[
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

        f"Zero-cost ending cash: "
        f"${zero['ending_cash']:.2f}"
    )

    print(

        f"Win rate: "
        f"{zero['win_rate']:.1f}%"
    )

    print(

        f"Average win: "
        f"${zero['avg_win']:+.3f}"
    )

    print(

        f"Average loss: "
        f"${zero['avg_loss']:+.3f}"
    )

    print(

        f"Payoff ratio: "
        f"{zero['payoff']:.2f}"
    )

    print(

        f"Average MAE: "
        f"{zero['avg_mae']:+.3f}%"
    )

    print(

        f"Average MFE: "
        f"{zero['avg_mfe']:+.3f}%"
    )

    print(

        f"Average hold: "
        f"{zero['avg_bars'] * 5:.1f} minutes"
    )

    print(

        f"Active trading days: "
        f"{zero['trading_days']}"
    )

    print(

        f"Trades/day: "
        f"{zero['trades_per_day']:.2f}"
    )

    print(

        f"Winning days: "
        f"{zero['winning_days']}"
    )

    print(

        f"Losing days: "
        f"{zero['losing_days']}"
    )

    print(

        f"Best day: "
        f"${zero['best_day']:+.2f}"
    )

    print(

        f"Worst day: "
        f"${zero['worst_day']:+.2f}"
    )

    print(

        f"Maximum consecutive losses: "
        f"{zero['max_losses']}"
    )

    print(

        f"Maximum drawdown: "
        f"{zero['dd']:.2f}%"
    )

    print()

    # ========================================================
    # V2 CLASSIFICATION
    # ========================================================

    if (
        positive_windows >= 3
        and
        total_window_trades >= 50
        and
        normal["net"] > 0
        and
        normal["pf"] >= 1.20
        and
        normal["expectancy"] > 0
        and
        stress["net"] > 0
    ):

        print(
            "V2 RESULT: "
            "SCALPING CANDIDATE"
        )

    elif (
        zero["net"] > 0
        and
        zero["pf"] > 1.0
        and
        positive_windows >= 2
    ):

        print(
            "V2 RESULT: "
            "GROSS EDGE ONLY"
        )

    else:

        print(
            "V2 RESULT: REJECT"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "NASDAQ 5-MINUTE SCALPER V2"
    )

    print(
        "SELECTIVE EMA + VWAP MOMENTUM"
    )

    print(
        "LIVE ORDER PLACEMENT: DISABLED"
    )

    print(
        "Market signal: "
        "Nasdaq Composite (^IXIC)"
    )

    print(
        "Timeframe: 5 minutes"
    )

    print(

        f"Historical request: "
        f"{RANGE}"
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

        f"Maximum trades/day: "
        f"{MAX_TRADES_PER_DAY}"
    )

    print(
        "Morning + afternoon trading windows"
    )

    print(
        "VWAP confirmation enabled"
    )

    print(
        "EMA separation filter enabled"
    )

    print(
        "Momentum candle quality filter enabled"
    )

    print(
        "No overnight positions"
    )

    print(
        "Slippage stress testing enabled"
    )

    print()

    try:

        candles = (
            download_history(
                SYMBOL
            )
        )

        test_market(
            candles
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
