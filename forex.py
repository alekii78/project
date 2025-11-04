# forex_trading_bot_ensemble.py
# Combined strategy: EMA crossover + RSI + Bollinger + MACD (ensemble voting)
# Autonomous demo trading (Deriv synthetic indices, 1-min contracts)
#
# Requirements: pandas, websockets, asyncio
# Put your DERIV_TOKEN and DERIV_APP_ID in config.py:
# DERIV_TOKEN = "your_token"
# DERIV_APP_ID = "109903" (or whatever you use)

import asyncio
import json
import math
import pandas as pd
import websockets
from config import DERIV_TOKEN, DERIV_APP_ID

# ===================================================================
# Settings
# ===================================================================
TRADE_INTERVAL = 1            # minutes between checks
STAKE = 1.0                   # USD stake per contract
DERIV_SYMBOLS = ["R_100", "R_25"]  # synthetic demo symbols that support 1-min
CANDLE_COUNT = 50             # candles to fetch (enough for indicators)
EMA_FAST = 5
EMA_SLOW = 20
RSI_PERIOD = 14
BB_PERIOD = 20
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOTE_THRESHOLD = 2            # need at least 2 indicator votes to trade
MIN_CONTRACT_DURATION = 1     # minutes (demo synthetic indices typically allow 1m)

# ===================================================================
# Utils: indicators
# ===================================================================
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.rolling(period, min_periods=period).mean()
    ma_down = down.rolling(period, min_periods=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def bollinger_bands(series, period=20, stds=2):
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + stds * std
    lower = ma - stds * std
    return ma, upper, lower

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ===================================================================
# Deriv WebSocket helpers
# ===================================================================
DERIV_WS = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

async def authorize(ws):
    await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    resp = await ws.recv()
    return json.loads(resp)

async def get_valid_symbol():
    """Return first available symbol from DERIV_SYMBOLS that Deriv reports as active."""
    try:
        async with websockets.connect(DERIV_WS) as ws:
            await authorize(ws)
            # ask for brief active symbols for synthetic indices
            await ws.send(json.dumps({"active_symbols": "brief", "product_type": "synthetic_index"}))
            resp = await ws.recv()
            data = json.loads(resp)
            active = [s.get("symbol") for s in data.get("active_symbols", [])]
            for s in DERIV_SYMBOLS:
                if s in active:
                    return s
    except Exception as e:
        print("⚠️ get_valid_symbol error:", e)
    # fallback to first configured symbol
    return DERIV_SYMBOLS[0]

async def get_available_durations(symbol):
    """Return available contract durations (minutes) for the symbol."""
    try:
        async with websockets.connect(DERIV_WS) as ws:
            await authorize(ws)
            await ws.send(json.dumps({"contracts_for": symbol}))
            resp = await ws.recv()
            data = json.loads(resp)
            available = data.get("contracts_for", {}).get("available", [])
            durations = []
            for c in available:
                # Deriv contract entries may have min/max durations
                minc = int(c.get("min_contract_duration", 1))
                maxc = int(c.get("max_contract_duration", minc))
                durations.extend(range(minc, maxc + 1))
            durations = sorted(set(durations))
            # keep only durations >= MIN_CONTRACT_DURATION
            durations = [d for d in durations if d >= MIN_CONTRACT_DURATION]
            return durations
    except Exception as e:
        print("⚠️ get_available_durations error:", e)
        return []

async def fetch_candles(symbol, count=CANDLE_COUNT, granularity_mins=1):
    """Fetch recent candles (in minutes) as DataFrame."""
    try:
        async with websockets.connect(DERIV_WS) as ws:
            await authorize(ws)
            req = {
                "ticks_history": symbol,
                "end": "latest",
                "count": count,
                "granularity": granularity_mins * 60,
                "style": "candles"
            }
            await ws.send(json.dumps(req))
            resp = await ws.recv()
            data = json.loads(resp)
            candles = data.get("candles", [])
            if not candles:
                return pd.DataFrame()
            df = pd.DataFrame(candles)
            # ensure numeric
            df = df[['open','high','low','close','epoch']].rename(
                columns={'open':'Open','high':'High','low':'Low','close':'Close','epoch':'Epoch'}
            )
            for col in ['Open','High','Low','Close']:
                df[col] = df[col].astype(float)
            df['Time'] = pd.to_datetime(df['Epoch'], unit='s')
            df.set_index('Time', inplace=True)
            return df
    except Exception as e:
        print("⚠️ fetch_candles error:", e)
        return pd.DataFrame()

# ===================================================================
# Decision logic: ensemble of indicators
# ===================================================================
def ensemble_signal(df):
    """
    Returns:
      2 -> BUY (CALL)
      1 -> SELL (PUT)
      0 -> NO ACTION
    Ensemble scoring: each indicator votes; we trade if votes >= VOTE_THRESHOLD
    """
    votes_buy = 0
    votes_sell = 0

    close = df['Close']

    # EMA crossover vote
    ema_f = ema(close, EMA_FAST)
    ema_s = ema(close, EMA_SLOW)
    if ema_f.iloc[-1] > ema_s.iloc[-1] and ema_f.iloc[-2] <= ema_s.iloc[-2]:
        votes_buy += 1
    elif ema_f.iloc[-1] < ema_s.iloc[-1] and ema_f.iloc[-2] >= ema_s.iloc[-2]:
        votes_sell += 1

    # RSI vote
    r = rsi(close, RSI_PERIOD)
    r_now = r.iloc[-1]
    if r_now > 55 and r_now < 90:
        votes_buy += 1
    elif r_now < 45 and r_now > 10:
        votes_sell += 1

    # Bollinger / mean reversion vote
    ma, ub, lb = bollinger_bands(close, BB_PERIOD, 2)
    if close.iloc[-1] > ub.iloc[-1]:
        # price above upper band -> short mean reversion
        votes_sell += 1
    elif close.iloc[-1] < lb.iloc[-1]:
        votes_buy += 1

    # MACD momentum vote
    macd_line, signal_line, hist = macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        votes_buy += 1
    elif hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
        votes_sell += 1

    # final decision
    if votes_buy >= VOTE_THRESHOLD and votes_buy > votes_sell:
        return 2, (votes_buy, votes_sell)
    if votes_sell >= VOTE_THRESHOLD and votes_sell > votes_buy:
        return 1, (votes_buy, votes_sell)
    return 0, (votes_buy, votes_sell)

# ===================================================================
# Execution: propose + buy
# ===================================================================
async def place_trade(symbol, stake, duration_minutes, side_label):
    """
    Send proposal and buy request. side_label: "CALL" or "PUT"
    Returns buy response (dict) or None on failure.
    """
    try:
        async with websockets.connect(DERIV_WS) as ws:
            await authorize(ws)

            proposal = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": side_label,
                "currency": "USD",
                "duration": duration_minutes,
                "duration_unit": "m",
                "symbol": symbol
            }
            await ws.send(json.dumps(proposal))
            prop_resp = await ws.recv()
            prop_data = json.loads(prop_resp)

            if "error" in prop_data:
                # return error message
                return {"error": prop_data["error"]}

            # proposal id expected
            prop_id = prop_data.get("proposal", {}).get("id")
            if not prop_id:
                return {"error": "no_proposal_id"}

            buy_req = {"buy": prop_id, "price": stake}
            await ws.send(json.dumps(buy_req))
            buy_resp = await ws.recv()
            return json.loads(buy_resp)
    except Exception as e:
        return {"error": str(e)}

# ===================================================================
# Main loop: continuous autonomous trading
# ===================================================================
async def trading_loop():
    print("🚀 Starting continuous Deriv ensemble bot (demo)...\n")
    while True:
        try:
            symbol = await get_valid_symbol()
            print(f"✅ Using symbol: {symbol}")

            df = await fetch_candles(symbol, count=CANDLE_COUNT, granularity_mins=1)
            if df.empty or len(df) < max(EMA_SLOW, BB_PERIOD, RSI_PERIOD, MACD_SLOW) + 2:
                print("⚠️ Not enough candle data yet, waiting...")
                await asyncio.sleep(TRADE_INTERVAL * 60)
                continue

            # compute ensemble signal
            sig, votes = ensemble_signal(df)
            print(df[['Close']].tail(5).to_string())
            print(f"📊 Votes (buy,sell): {votes} -> Signal: { 'BUY' if sig==2 else 'SELL' if sig==1 else 'NONE' }")

            if sig == 0:
                print("⚪ No trade this round.")
            else:
                # find durations to try (prefer 1-minute for demo synthetic indices)
                durations = await get_available_durations(symbol)
                if not durations:
                    print(f"⚠️ No tradeable durations found for {symbol}")
                else:
                    # prefer 1 if available else smallest available
                    dur = 1 if 1 in durations else durations[0]
                    side = "CALL" if sig == 2 else "PUT"
                    print(f"📈 Placing {side} trade for {symbol} for {dur} minute(s) stake ${STAKE} ...")
                    buy_resp = await place_trade(symbol, STAKE, dur, side)
                    if buy_resp is None:
                        print("⚠️ Unknown error placing trade.")
                    elif "error" in buy_resp:
                        print("❌ Trade failed:", buy_resp["error"])
                    else:
                        print("💰 Trade executed:", buy_resp)

            # wait until next iteration
            print(f"⏳ Waiting {TRADE_INTERVAL} minute(s) before next trade...\n")
        except Exception as e:
            print("⚠️ Error in trading loop:", e)
        await asyncio.sleep(TRADE_INTERVAL * 60)

# ===================================================================
# Entrypoint
# ===================================================================
if __name__ == "__main__":
    try:
        asyncio.run(trading_loop())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user.")
