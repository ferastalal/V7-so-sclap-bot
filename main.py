import yfinance as yf
import ta
import requests
import time
from datetime import datetime
import pytz

TOKEN = "8897393036:AAEzgF12EEGc0fW2_wkR6SeaYE1RGyzfmSg"
CHAT_ID = "1016589957"

WATCHLIST = [
    # AI / TECH
    "TSLA",
    "NVDA",
    "AMD",
    "AVGO",
    "META",
    # ENERGY / OIL
    "OXY",
    "SLB",
    # CLEAN ENERGY
    "BE",
]

last_alert = {}


def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.get(url, params={"chat_id": CHAT_ID, "text": msg})


send("🚀 V6 BUY ONLY BOT ONLINE 🚀")

while True:
    for stock in WATCHLIST:
        try:
            df = yf.download(stock, period="2d", interval="1m", progress=False)

            if df.empty or len(df) < 50:
                continue

            close = df["Close"].squeeze()
            volume = df["Volume"].squeeze()

            price = float(close.iloc[-1])
            ema9 = float(
                ta.trend.EMAIndicator(close, window=9).ema_indicator().iloc[-1]
            )
            ema21 = float(
                ta.trend.EMAIndicator(close, window=21).ema_indicator().iloc[-1]
            )
            rsi = float(ta.momentum.RSIIndicator(close).rsi().iloc[-1])

            macd = ta.trend.MACD(close)
            macd_now = float(macd.macd().iloc[-1])
            macd_signal = float(macd.macd_signal().iloc[-1])

            vol_now = float(volume.iloc[-1])
            vol_avg = float(volume.tail(20).mean())

            buy = (
                price > ema9 > ema21
                and 40 <= rsi <= 70
                and macd_now > macd_signal
                and vol_now > vol_avg * 0.8
            )

            saudi = pytz.timezone("Asia/Riyadh")
            now = datetime.now(saudi).strftime("%H:%M:%S")

            if buy and last_alert.get(stock) != "BUY":
                entry = round(price, 2)
                target = round(price * 1.01, 2)
                stop = round(price * 0.995, 2)

                msg = f"""
🟢🚀 V6 BUY ALERT 🚀🟢

📈 STOCK: {stock}
⏰ TIME KSA: {now}

💰 ENTRY: {entry}
🎯 TARGET: {target}
🛑 STOP LOSS: {stop}

📊 RSI: {round(rsi, 2)}
🔥 VOLUME: ACTIVE
📈 TREND: BULLISH

⚡ MODE: SCALP
⚠️ Alerts only, no auto trading.
"""

                send(msg)
                print(msg)
                last_alert[stock] = "BUY"

        except Exception as e:
            print(stock, e)

    time.sleep(60)
print("BOT STARTED")
