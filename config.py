# Paper-trading configuration
PRODUCTS = ["BTC-USD", "ETH-USD"]
GRANULARITY = "FIVE_MINUTE"
CANDLE_SECONDS = 300
LOOKBACK_CANDLES = 120

STARTING_CASH = 10000.0

# Risk controls (paper trading)
RISK_PER_TRADE = 0.01       # 1% of current equity
MAX_POSITION_PCT = 0.25     # max 25% of equity in one position
MAX_DAILY_LOSS_PCT = 0.03   # stop new entries after 3% daily loss
STOP_LOSS_PCT = 0.012       # 1.2%
TAKE_PROFIT_PCT = 0.024     # 2.4% (2:1 reward/risk)
FEE_RATE = 0.006             # conservative placeholder; set to your actual tier

# Signal parameters
EMA_FAST = 9
EMA_SLOW = 21
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.20
RSI_PERIOD = 14
RSI_MIN = 52
RSI_MAX = 72

POLL_SECONDS = 30
STATE_FILE = "paper_state.json"
TRADE_LOG = "trades.csv"
