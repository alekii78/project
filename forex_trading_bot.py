import asyncio
import json
import pandas as pd
import websockets
from config import DERIV_TOKEN, DERIV_APP_ID

DERIV_SYMBOL = "frxEURUSD"
TRADE_INTERVAL = 15  # in minutes
STAKE = 1  # USD


async def get_valid_duration(symbol):
    """Fetch the minimum valid contract duration for the symbol."""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        await ws.send(json.dumps({"contracts_for": symbol}))
        resp = await ws.recv()
        data = json.loads(resp)

        contracts = data.get("contracts_for", {}).get("available", [])
        for c in contracts:
            if "forex" in c.get("underlying", ""):
                # Use the first valid duration
                return int(c.get("min_contract_duration", 1))
        return 1  # fallback


async def get_deriv_candles(n=5, granularity=15):
    """Fetch latest n candles from Deriv."""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        request = {
            "ticks_history": DERIV_SYMBOL,
            "end": "latest",
            "count": n,
            "granularity": granularity * 60,
            "style": "candles"
        }
        await ws.send(json.dumps(request))
        resp = await ws.recv()
        data = json.loads(resp)

        print("DEBUG Raw response from Deriv:", data)

        candles = data.get("candles")
        if not candles:
            print("⚠️ No candles received from Deriv.")
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'}, inplace=True)
        for col in ['Open','High','Low','Close']:
            df[col] = df[col].astype(float)
        return df


def signal_generator(df):
    """Generate BUY/SELL signals based on last two candles."""
    if len(df) < 2:
        return 0
    open_ = df.Open.iloc[-1]
    close_ = df.Close.iloc[-1]
    prev_open = df.Open.iloc[-2]
    prev_close = df.Close.iloc[-2]

    if open_ > close_ and prev_open < prev_close and close_ < prev_open and open_ >= prev_close:
        return 1  # SELL
    elif open_ < close_ and prev_open > prev_close and close_ > prev_open and open_ <= prev_close:
        return 2  # BUY
    return 0


async def trade_on_deriv(signal, stake=STAKE, duration=None):
    """Execute a CALL or PUT contract on Deriv."""
    if duration is None:
        duration = await get_valid_duration(DERIV_SYMBOL)

    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        contract_type = "CALL" if signal == 2 else "PUT"
        proposal = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": "m",
            "symbol": DERIV_SYMBOL
        }

        await ws.send(json.dumps(proposal))
        proposal_resp = await ws.recv()
        proposal_data = json.loads(proposal_resp)

        if "error" in proposal_data:
            print("❌ Proposal error:", proposal_data["error"]["message"])
            return

        contract_id = proposal_data["proposal"]["id"]
        await ws.send(json.dumps({"buy": contract_id, "price": stake}))
        buy_resp = await ws.recv()
        print("💰 Trade executed:", buy_resp)


async def trading_loop():
    """Run trading job continuously every TRADE_INTERVAL minutes."""
    while True:
        try:
            print("🚀 Running trading job...")
            df = await get_deriv_candles()
            print("Latest candles:\n", df.tail())

            signal = signal_generator(df)
            print("Signal:", "SELL" if signal == 1 else "BUY" if signal == 2 else "NONE")

            if signal in (1, 2):
                await trade_on_deriv(signal)
            else:
                print("⚪ No trade this round.")

        except Exception as e:
            print("⚠️ Error during trading job:", e)

        print(f"⏳ Waiting {TRADE_INTERVAL} minutes before next trade...\n")
        await asyncio.sleep(TRADE_INTERVAL * 60)


if __name__ == "__main__":
    asyncio.run(trading_loop())
