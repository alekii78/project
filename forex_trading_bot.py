# ==========================================
# Forex Signal Trading Script (Converted from Jupyter Notebook)
# ==========================================

# 1 - Import test data
import yfinance as yf
import pandas as pd

# Download EUR/USD data (15-minute interval)
dataF = yf.download("EURUSD=X", start="2022-10-7", end="2022-12-5", interval='15m')
print(dataF)

# ==========================================
# 2 - Define your signal function
# ==========================================
def signal_generator(df):
    open_ = df.Open.iloc[-1]
    close_ = df.Close.iloc[-1]
    previous_open = df.Open.iloc[-2]
    previous_close = df.Close.iloc[-2]

    # Bearish Pattern
    if (open_ > close_ and
        previous_open < previous_close and
        close_ < previous_open and
        open_ >= previous_close):
        return 1

    # Bullish Pattern
    elif (open_ < close_ and
          previous_open > previous_close and
          close_ > previous_open and
          open_ <= previous_close):
        return 2

    # No clear pattern
    else:
        return 0

# Generate signals
signal = [0]
for i in range(1, len(dataF)):
    df = dataF[i-1:i+1]
    signal.append(signal_generator(df))

dataF["signal"] = signal
print(dataF.signal.value_counts())

# ==========================================
# 3 - Connect to the market and execute trades
# ==========================================
from apscheduler.schedulers.blocking import BlockingScheduler
from oandapyV20 import API
import oandapyV20.endpoints.orders as orders
from oandapyV20.contrib.requests import MarketOrderRequest
from oanda_candles import Pair, Gran, CandleClient
from oandapyV20.contrib.requests import TakeProfitDetails, StopLossDetails

# ==========================================
# 4 - OANDA connection setup
# ==========================================
from config import access_token, accountID  # Your OANDA credentials file

def get_candles(n):
    # Create OANDA candle client (set real=True for live)
    client = CandleClient(access_token, real=False)
    collector = client.get_collector(Pair.EUR_USD, Gran.M15)
    candles = collector.grab(n)
    return candles

# Test connection
candles = get_candles(3)
for candle in candles:
    print(float(str(candle.bid.o)) > 1)

# ==========================================
# 5 - Define trading job
# ==========================================
def trading_job():
    candles = get_candles(3)
    dfstream = pd.DataFrame(columns=['Open', 'Close', 'High', 'Low'])

    for i, candle in enumerate(candles):
        dfstream.loc[i, ['Open']] = float(str(candle.bid.o))
        dfstream.loc[i, ['Close']] = float(str(candle.bid.c))
        dfstream.loc[i, ['High']] = float(str(candle.bid.h))
        dfstream.loc[i, ['Low']] = float(str(candle.bid.l))

    dfstream = dfstream.astype(float)

    signal = signal_generator(dfstream.iloc[:-1, :])

    # Connect to OANDA API
    client = API(access_token)

    SLTPRatio = 2.0
    previous_candleR = abs(dfstream['High'].iloc[-2] - dfstream['Low'].iloc[-2])

    SLBuy = float(str(candle.bid.o)) - previous_candleR
    SLSell = float(str(candle.bid.o)) + previous_candleR

    TPBuy = float(str(candle.bid.o)) + previous_candleR * SLTPRatio
    TPSell = float(str(candle.bid.o)) - previous_candleR * SLTPRatio

    print(dfstream.iloc[:-1, :])
    print(TPBuy, SLBuy, TPSell, SLSell)

    # Example: force a Buy for testing
    signal = 2

    if signal == 1:  # Sell
        mo = MarketOrderRequest(
            instrument="EUR_USD",
            units=-1000,
            takeProfitOnFill=TakeProfitDetails(price=TPSell).data,
            stopLossOnFill=StopLossDetails(price=SLSell).data
        )
        r = orders.OrderCreate(accountID, data=mo.data)
        rv = client.request(r)
        print(rv)

    elif signal == 2:  # Buy
        mo = MarketOrderRequest(
            instrument="EUR_USD",
            units=1000,
            takeProfitOnFill=TakeProfitDetails(price=TPBuy).data,
            stopLossOnFill=StopLossDetails(price=SLBuy).data
        )
        r = orders.OrderCreate(accountID, data=mo.data)
        rv = client.request(r)
        print(rv)

# ==========================================
# 6 - Run or schedule
# ==========================================
if __name__ == "__main__":
    trading_job()
    # Uncomment below for scheduling automatic runs
    scheduler = BlockingScheduler()
    scheduler.add_job(trading_job, 'cron', day_of_week='mon-fri', hour='00-23', minute='1,16,31,46', timezone='America/Chicago' )
    scheduler.start()
