from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    print("TELEGRAM STATUS:", r.status_code)
    print(r.text)

@app.route("/")
def home():
    return "TradingView Webhook Bot Running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print("WEBHOOK DATA:", data)

    ticker = data.get("ticker", "UNKNOWN")
    price = data.get("price", "UNKNOWN")
    signal = data.get("signal", "TRADINGVIEW")

    msg = f"""🚨 TradingView Signal

السهم: {ticker}
السعر: {price}
الإشارة: {signal}

✅ وصل التنبيه من TradingView
"""
    send_telegram(msg)

    return jsonify({"status": "ok"}), 200
