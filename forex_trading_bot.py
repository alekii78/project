import asyncio
import json
import pandas as pd
import random
import websockets
from config import DERIV_TOKEN, DERIV_APP_ID

TRADE_INTERVAL = 1  # trade every 1 minute
STAKE = 1  # USD stake per trade
DERIV_SYMBOLS = ["R_100", "R_25"]  # Synthetic indices trade 24/7


# -------------------- Helper Functions --------------------

async def get_valid_symbol():
    """Return the first available synthetic index symbol."""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        await ws.send(json.dumps({"active_symbols": "brief", "product_type": "synthetic_index"}))
        resp = await ws.recv()
        data = json.loads(resp)
        available = [s["symbol"] for s in data.get("active_symbols", [])]
        for sym in DERIV_SYMBOLS:
            if sym in available:
                return sym
    return DERIV_SYMBOLS[0]


async def get_deriv_candles(symbol, n=5, granularity=1):
    """Fetch latest n candles for a symbol."""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        request = {
            "ticks_history": symbol,
            "end": "latest",
            "count": n,
            "granularity": granularity * 60,
            "style": "candles"
        }
        await ws.send(json.dumps(request))
        resp = await ws.recv()
        data = json.loads(resp)

        candles = data.get("candles")
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
        for col in ['Open', 'High', 'Low', 'Close']:
            df[col] = df[col].astype(float)
        return df


def signal_generator(df):
    """Simple BUY/SELL signal generator using last two candles."""
    if len(df) < 2:
        return 0
    open_ = df.Open.iloc[-1]
    close_ = df.Close.iloc[-1]
    prev_open = df.Open.iloc[-2]
    prev_close = df.Close.iloc[-2]

    # Basic reversal or momentum detection
    if open_ > close_ and prev_open < prev_close:
        return 1  # SELL
    elif open_ < close_ and prev_open > prev_close:
        return 2  # BUY
    elif df.Close.iloc[-1] > df.Close.iloc[-2]:
        return 2  # BUY
    elif df.Close.iloc[-1] < df.Close.iloc[-2]:
        return 1  # SELL

    return random.choice([1, 2])  # random if no clear signal


async def trade_on_deriv(symbol, signal, stake=STAKE):
    """Place a single trade based on signal."""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        contract_type = "CALL" if signal == 2 else "PUT"
        print(f"📈 Placing {contract_type} trade for {symbol} ...")

        proposal = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 1,
            "duration_unit": "m",
            "symbol": symbol
        }

        await ws.send(json.dumps(proposal))
        proposal_resp = await ws.recv()
        proposal_data = json.loads(proposal_resp)

        if "error" in proposal_data:
            print(f"❌ Proposal failed: {proposal_data['error']['message']}")
            return False

        contract_id = proposal_data["proposal"]["id"]
        await ws.send(json.dumps({"buy": contract_id, "price": stake}))
        buy_resp = await ws.recv()
        print(f"💰 Trade executed for {symbol}: {buy_resp}")
        return True


# -------------------- Continuous Trading Loop --------------------

async def trading_loop():
    """Run continuous trading."""
    while True:
        try:
            symbol = await get_valid_symbol()
            print(f"\n✅ Using symbol: {symbol}")

            df = await get_deriv_candles(symbol, granularity=1)
            if df.empty:
                print("⚠️ No candle data available.")
                await asyncio.sleep(5)
                continue

            print(df.tail())
            signal = signal_generator(df)
            print("📊 Signal:", "SELL" if signal == 1 else "BUY")

            success = await trade_on_deriv(symbol, signal)
            if not success:
                print(f"⚠️ Trade failed on {symbol}, switching symbol...")
                continue

            print(f"⏳ Waiting {TRADE_INTERVAL} minute(s) before next trade...\n")
            await asyncio.sleep(TRADE_INTERVAL * 60)

        except Exception as e:
            print("⚠️ Error in trading loop:", e)
            await asyncio.sleep(10)


if __name__ == "__main__":
    print("🚀 Starting continuous Deriv bot (demo)...\n")
    asyncio.run(trading_loop())
