# Coinbase BTC/ETH Momentum Paper Bot

This starter project simulates BTC-USD and ETH-USD trades using public Coinbase
Advanced Trade candle data. It **does not place live orders** and needs no API
credentials.

## Strategy
- 5-minute candles
- EMA 9 > EMA 21
- RSI between 52 and 72
- latest completed candle volume >= 1.2x recent average
- latest completed candle closes higher than the previous candle
- 1.2% simulated stop
- 2.4% simulated target
- 1% account risk calculation per trade
- 25% maximum position allocation
- stops new entries after a 3% simulated daily equity loss

These numbers are starting assumptions, not evidence of profitability.

## Install
Python 3.10+ recommended.

    pip install -r requirements.txt

## Run

    python bot.py

The bot creates:
- `paper_state.json` — simulated balance and open positions
- `trades.csv` — simulated trade history

## Reset the simulation
Stop the bot, then delete `paper_state.json` and `trades.csv`.

## Security
This paper version intentionally does not use Coinbase API keys. Do not paste
Coinbase passwords, private keys, or API secrets into chat or source code.

## Before live trading
Backtest with realistic fees/slippage, paper-trade for a meaningful period, and
review maximum drawdown and failure cases. Live trading would require a separate
explicitly enabled execution module and Coinbase API credentials stored outside
source code.
