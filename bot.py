#!/usr/bin/env python3
import asyncio
import json
import pandas as pd
import numpy as np
import websockets
from config import DERIV_TOKEN, DERIV_APP_ID
import logging
from datetime import datetime

# ================= CONFIGURATION =================
TRADE_INTERVAL = 1          # minutes between *successful* trades
STAKE = 1.0                 # USD per trade
DERIV_SYMBOLS = ["R_25", "R_10"]   # lower-volatility first
MIN_ATR = 0.30              # realistic volatility filter for R_25
DAILY_LOSS_LIMIT = -10.0    # stop if daily loss >= $10

# Global P&L
daily_pnl = 0.0
today_date = datetime.now().date()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ================= HELPERS =================
async def get_valid_symbol():
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            auth = json.loads(await ws.recv())
            if auth.get("error"):
                logging.error(f"Auth error: {auth['error']['message']}")
                return DERIV_SYMBOLS[0]

            await ws.send(json.dumps({"active_symbols": "brief", "product_type": "synthetic_index"}))
            data = json.loads(await ws.recv())
            available = {s["symbol"] for s in data.get("active_symbols", [])}
            for sym in DERIV_SYMBOLS:
                if sym in available:
                    return sym
    except Exception as e:
        logging.error(f"Symbol lookup failed: {e}")
    return DERIV_SYMBOLS[0]


async def get_deriv_candles(symbol, n=60, granularity=1):
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()

            req = {
                "ticks_history": symbol,
                "end": "latest",
                "count": n,
                "granularity": granularity * 60,
                "style": "candles"
            }
            await ws.send(json.dumps(req))
            resp = json.loads(await ws.recv())
            candles = resp.get("candles")
            if not candles:
                return pd.DataFrame()

            df = pd.DataFrame(candles)
            df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
            for c in ['Open', 'High', 'Low', 'Close']:
                df[c] = df[c].astype(float)
            return df.iloc[-n:]
    except Exception as e:
        logging.error(f"Candle fetch error: {e}")
        return pd.DataFrame()


# def hybrid_signal_generator(df):
#     """
#     RELAXED Hybrid: EMA trend + RSI momentum + ATR filter
#     Returns (signal, info_dict)
#     """
#     if len(df) < 35:
#         return 0, {}

#     close = df['Close'].values
#     high = df['High'].values
#     low = df['Low'].values

#     # ----- EMA -----
#     ema9  = pd.Series(close).ewm(span=9,  adjust=False).mean().iloc[-1]
#     ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().iloc[-1]

#     # ----- RSI -----
#     delta = np.diff(close)
#     up   = np.where(delta > 0, delta, 0)
#     down = np.where(delta < 0, -delta, 0)

#     roll_up   = pd.Series(up).rolling(14).mean()
#     roll_down = pd.Series(down).rolling(14).mean()

#     rs  = roll_up.iloc[-1] / roll_down.iloc[-1] if roll_down.iloc[-1] != 0 else np.inf
#     rsi = 100 - (100 / (1 + rs)) if rs != np.inf else 100

#     # RSI direction (previous bar)
#     rsi_prev = 100 - (100 / (1 + (roll_up.iloc[-2] / roll_down.iloc[-2]))) if len(roll_up) > 1 else rsi
#     rsi_rising = rsi > rsi_prev

#     # ----- ATR -----
#     tr = np.maximum(high[1:] - low[1:],
#                     np.maximum(np.abs(high[1:] - close[:-1]),
#                                np.abs(low[1:] - close[:-1])))
#     atr = pd.Series(tr).rolling(14).mean().iloc[-1] if len(tr) >= 14 else 0

#     cur = close[-1]
#     prev = close[-2]

#     # ----- Conditions -----
#     uptrend   = ema9 > ema21
#     downtrend = ema9 < ema21
#     rsi_buy   = rsi < 40 and rsi_rising
#     rsi_sell  = rsi > 60 and not rsi_rising
#     volatile  = atr > MIN_ATR
#     bull_c    = cur > prev
#     bear_c    = cur < prev

#     info = {
#         "EMA9": round(ema9, 6),
#         "EMA21": round(ema21, 6),
#         "RSI": round(rsi, 2),
#         "RSI_Dir": "Up" if rsi_rising else "Down",
#         "ATR": round(atr, 6),
#         "Close": round(cur, 6)
#     }

#     if uptrend and rsi_buy and cur > ema9 and volatile and bull_c:
#         return 2, info                      # BUY
#     if downtrend and rsi_sell and cur < ema9 and volatile and bear_c:
#         return 1, info                      # SELL

#     return 0, info
def hybrid_signal_generator(df):
    """
    EMA Crossover + Price Action + ATR Filter
    Returns: (2=BUY, 1=SELL, 0=NO)
    """
    if len(df) < 35:
        return 0, {}

    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values

    # EMA
    ema9_series = pd.Series(close).ewm(span=9, adjust=False).mean()
    ema21_series = pd.Series(close).ewm(span=21, adjust=False).mean()

    ema9 = ema9_series.iloc[-1]
    ema21 = ema21_series.iloc[-1]
    prev_ema9 = ema9_series.iloc[-2]
    prev_ema21 = ema21_series.iloc[-2]

    # Crossover detection
    bullish_cross = (prev_ema9 <= prev_ema21) and (ema9 > ema21)
    bearish_cross = (prev_ema9 >= prev_ema21) and (ema9 < ema21)

    # Price action
    cur = close[-1]
    prev = close[-2]
    bull_candle = cur > prev
    bear_candle = cur < prev

    # ATR
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr = pd.Series(tr).rolling(14).mean().iloc[-1] if len(tr) >= 14 else 0

    info = {
        "EMA9": round(ema9, 6),
        "EMA21": round(ema21, 6),
        "Cross": "Bull" if bullish_cross else ("Bear" if bearish_cross else "None"),
        "ATR": round(atr, 6),
        "Close": round(cur, 6)
    }

    # BUY: Bullish EMA crossover + price above EMA9 + bullish candle + volatility
    if bullish_cross and cur > ema9 and bull_candle and atr > MIN_ATR:
        return 2, info

    # SELL: Bearish EMA crossover + price below EMA9 + bearish candle + volatility
    if bearish_cross and cur < ema9 and bear_candle and atr > MIN_ATR:
        return 1, info

    return 0, info

async def trade_on_deriv(symbol, signal, stake=STAKE):
    global daily_pnl
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()

            ctype = "CALL" if signal == 2 else "PUT"
            logging.info(f"Placing {ctype} on {symbol} | Stake ${stake}")

            proposal = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": ctype,
                "currency": "USD",
                "duration": 1,
                "duration_unit": "m",
                "symbol": symbol
            }
            await ws.send(json.dumps(proposal))
            resp = json.loads(await ws.recv())
            if resp.get("error"):
                logging.error(f"Proposal error: {resp['error']['message']}")
                return False, 0.0

            cid = resp["proposal"]["id"]
            await ws.send(json.dumps({"buy": cid, "price": stake}))
            buy_resp = json.loads(await ws.recv())

            if "buy" not in buy_resp:
                logging.error(f"Buy failed: {buy_resp}")
                return False, 0.0

            profit = buy_resp["buy"].get("profit", 0.0)
            daily_pnl += profit
            logging.info(f"Trade done | P&L ${profit:+.2f} | Daily ${daily_pnl:+.2f}")
            return True, profit
    except Exception as e:
        logging.error(f"Trade execution error: {e}")
        return False, 0.0


# ================= MAIN LOOP =================
async def trading_loop():
    global daily_pnl, today_date

    logging.info("Deriv Hybrid Bot STARTED")

    while True:
        # ---- Daily reset ----
        if datetime.now().date() != today_date:
            daily_pnl = 0.0
            today_date = datetime.now().date()
            logging.info("New day – P&L reset")

        if daily_pnl <= DAILY_LOSS_LIMIT:
            logging.warning(f"Daily loss limit reached (${daily_pnl:.2f}). Stopping.")
            break

        symbol = await get_valid_symbol()
        logging.info(f"Using symbol: {symbol}")

        df = await get_deriv_candles(symbol, n=60, granularity=1)
        if df.empty or len(df) < 35:
            logging.warning("Not enough data – retrying...")
            await asyncio.sleep(10)
            continue

        logging.info(f"\n{df[['Open','Close']].tail(3).to_string()}")

        signal, info = hybrid_signal_generator(df)
        logging.info(
            f"Indicators → EMA9:{info.get('EMA9','?')} EMA21:{info.get('EMA21','?')} "
            f"RSI:{info.get('RSI','?')} {info.get('RSI_Dir','?')} ATR:{info.get('ATR','?')}"
        )

        if signal == 0:
            logging.info("No strong signal – waiting 30s")
            await asyncio.sleep(30)
            continue

        direction = "BUY" if signal == 2 else "SELL"
        logging.info(f"STRONG {direction} SIGNAL → EXECUTING")

        ok, _ = await trade_on_deriv(symbol, signal)
        if not ok:
            await asyncio.sleep(10)
            continue

        logging.info(f"Waiting {TRADE_INTERVAL} minute(s) before next analysis")
        await asyncio.sleep(TRADE_INTERVAL * 60)

    logging.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(trading_loop())
    except KeyboardInterrupt:
        logging.info("Stopped by user")