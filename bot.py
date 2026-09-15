"""
Coinbase BTC/ETH momentum PAPER trader.

This version DOES NOT place live Coinbase orders and does not require API keys.
It uses Coinbase Advanced Trade public candle data via Coinbase's official
coinbase-advanced-py SDK and simulates trades locally.

Run:
    pip install -r requirements.txt
    python bot.py
"""

import csv
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

from coinbase.rest import RESTClient

import config


def utc_now():
    return datetime.now(timezone.utc)


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = alpha * x + (1 - alpha) * value
    return value


def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(values[-period-1:-1], values[-period:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def load_state():
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "cash": config.STARTING_CASH,
        "positions": {},
        "day": utc_now().date().isoformat(),
        "day_start_equity": config.STARTING_CASH,
    }


def save_state(state):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_trade(row):
    exists = os.path.exists(config.TRADE_LOG)
    fields = ["time", "product", "action", "price", "quantity", "fee", "pnl", "reason"]
    with open(config.TRADE_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def get_candles(client, product):
    end = utc_now()
    start = end - timedelta(seconds=config.CANDLE_SECONDS * config.LOOKBACK_CANDLES)
    response = client.get_public_candles(
        product_id=product,
        start=str(int(start.timestamp())),
        end=str(int(end.timestamp())),
        granularity=config.GRANULARITY,
        limit=config.LOOKBACK_CANDLES,
    )
    raw = response.to_dict().get("candles", [])
    candles = []
    for c in raw:
        candles.append({
            "start": int(c["start"]),
            "low": float(c["low"]),
            "high": float(c["high"]),
            "open": float(c["open"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]),
        })
    candles.sort(key=lambda x: x["start"])
    return candles


def signal(candles):
    # Ignore the newest bucket because it may still be forming.
    if len(candles) < 30:
        return False, {}
    closed = candles[:-1]
    closes = [c["close"] for c in closed]
    volumes = [c["volume"] for c in closed]

    fast = ema(closes, config.EMA_FAST)
    slow = ema(closes, config.EMA_SLOW)
    momentum = rsi(closes, config.RSI_PERIOD)
    recent_vol = volumes[-1]
    avg_vol = sum(volumes[-config.VOLUME_LOOKBACK-1:-1]) / config.VOLUME_LOOKBACK
    price = closes[-1]

    # Long-only momentum setup: fast trend above slow trend, positive RSI,
    # and current closed-candle volume above its recent average.
    enter = (
        fast is not None and slow is not None and momentum is not None
        and fast > slow
        and config.RSI_MIN <= momentum <= config.RSI_MAX
        and recent_vol >= avg_vol * config.VOLUME_MULTIPLIER
        and closes[-1] > closes[-2]
    )
    return enter, {
        "price": price, "ema_fast": fast, "ema_slow": slow,
        "rsi": momentum, "volume": recent_vol, "avg_volume": avg_vol,
    }


def equity(state, prices):
    total = state["cash"]
    for product, pos in state["positions"].items():
        total += pos["quantity"] * prices.get(product, pos["entry"])
    return total


def maybe_reset_day(state, eq):
    today = utc_now().date().isoformat()
    if state["day"] != today:
        state["day"] = today
        state["day_start_equity"] = eq


def open_position(state, product, price, eq):
    risk_dollars = eq * config.RISK_PER_TRADE
    stop_distance = price * config.STOP_LOSS_PCT
    qty_by_risk = risk_dollars / stop_distance
    qty_by_cap = (eq * config.MAX_POSITION_PCT) / price
    qty_by_cash = state["cash"] / (price * (1 + config.FEE_RATE))
    qty = max(0.0, min(qty_by_risk, qty_by_cap, qty_by_cash))
    if qty <= 0:
        return

    cost = qty * price
    fee = cost * config.FEE_RATE
    state["cash"] -= cost + fee
    state["positions"][product] = {
        "entry": price,
        "quantity": qty,
        "entry_fee": fee,
        "stop": price * (1 - config.STOP_LOSS_PCT),
        "target": price * (1 + config.TAKE_PROFIT_PCT),
        "opened": utc_now().isoformat(),
    }
    log_trade({
        "time": utc_now().isoformat(), "product": product, "action": "BUY",
        "price": round(price, 8), "quantity": round(qty, 10),
        "fee": round(fee, 4), "pnl": "", "reason": "momentum_signal"
    })
    print(f"PAPER BUY {product}: {qty:.8f} @ ${price:,.2f}")


def close_position(state, product, price, reason):
    pos = state["positions"].pop(product)
    gross = pos["quantity"] * price
    exit_fee = gross * config.FEE_RATE
    state["cash"] += gross - exit_fee
    pnl = ((price - pos["entry"]) * pos["quantity"]) - pos["entry_fee"] - exit_fee
    log_trade({
        "time": utc_now().isoformat(), "product": product, "action": "SELL",
        "price": round(price, 8), "quantity": round(pos["quantity"], 10),
        "fee": round(exit_fee, 4), "pnl": round(pnl, 4), "reason": reason
    })
    print(f"PAPER SELL {product}: ${price:,.2f} | P/L ${pnl:,.2f} | {reason}")


def run_once(client, state):
    market = {}
    analyses = {}

    for product in config.PRODUCTS:
        candles = get_candles(client, product)
        if len(candles) < 2:
            continue
        # Use last completed candle for decision/marking.
        price = candles[-2]["close"]
        market[product] = price
        analyses[product] = signal(candles)

    eq = equity(state, market)
    maybe_reset_day(state, eq)
    daily_loss = max(0.0, state["day_start_equity"] - eq)
    daily_limit = state["day_start_equity"] * config.MAX_DAILY_LOSS_PCT
    entries_allowed = daily_loss < daily_limit

    # Exits first.
    for product in list(state["positions"].keys()):
        if product not in market:
            continue
        pos = state["positions"][product]
        price = market[product]
        if price <= pos["stop"]:
            close_position(state, product, price, "stop_loss")
        elif price >= pos["target"]:
            close_position(state, product, price, "take_profit")

    # Then entries.
    eq = equity(state, market)
    if entries_allowed:
        for product, (enter, data) in analyses.items():
            if enter and product not in state["positions"]:
                open_position(state, product, data["price"], eq)

    eq = equity(state, market)
    print(
        f"{utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')} | "
        f"equity=${eq:,.2f} cash=${state['cash']:,.2f} "
        f"positions={list(state['positions'])} "
        f"daily_loss=${max(0, state['day_start_equity']-eq):,.2f}"
    )
    save_state(state)


def main():
    print("=== COINBASE BTC/ETH PAPER BOT ===")
    print("LIVE ORDER PLACEMENT: DISABLED")
    print(f"Starting simulated balance: ${config.STARTING_CASH:,.2f}")
    client = RESTClient()
    state = load_state()

    while True:
        try:
            run_once(client, state)
        except KeyboardInterrupt:
            save_state(state)
            print("\nStopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()
