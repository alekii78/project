import websocket
import json
from config import DERIV_APP_ID, DERIV_TOKEN

def on_open(ws):
    print("✅ Connected to Deriv API")

    # Authorize using your token
    auth_request = {
        "authorize": DERIV_TOKEN
    }
    ws.send(json.dumps(auth_request))

def on_message(ws, message):
    data = json.loads(message)
    print("📩 Message:", data)

    if "error" in data:
        print("❌ Error:", data["error"]["message"])
        ws.close()
    elif "authorize" in data:
        print("✅ Authorized successfully as:", data["authorize"]["loginid"])
        ws.close()

def on_error(ws, error):
    print("⚠️ Error:", error)

def on_close(ws, close_status_code, close_msg):
    print("🔒 Connection closed")

if __name__ == "__main__":
    ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.run_forever()


import websocket
import json
from config import DERIV_APP_ID, DERIV_TOKEN, DERIV_SYMBOL

DERIV_WS_URL = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

def on_open(ws):
    print("✅ WebSocket opened. Subscribing to ticks...")
    # Authorize
    ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    # Subscribe to ticks
    ws.send(json.dumps({"ticks": DERIV_SYMBOL, "subscribe": 1}))

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        tick = data["tick"]
        print(f"Tick: bid={tick['bid']}, ask={tick['ask']}, time={tick['epoch']}")
    else:
        print(data)

def on_error(ws, error):
    print("❌ Error:", error)

def on_close(ws):
    print("🔒 Connection closed")

ws = websocket.WebSocketApp(
    DERIV_WS_URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close
)

ws.run_forever()

