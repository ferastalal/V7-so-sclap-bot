import yfinance as yf
import ta
import requests
import time
from datetime import datetime
import pytz

TOKEN = "8897393036:AAGDVoXd_UuNjDKNN5KUP1DCpBxtoGWUUHM"
CHAT_ID = "1016589957"

WATCHLIST = [
    "TSLA", "NVDA", "AMD", "AVGO", "BE",
    "PLTR", "SMCI", "MARA", "COIN", "ARM",
    "META", "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 20
ALERT_COOLDOWN = 1800

MIN_SCORE = 100

TARGET_MIN = 0.005
TARGET_MAX = 0.010
STOP_LOSS = 0.002

last_alert_time = {}
last_heartbeat = time.time()


def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(
            url,
            params={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
        print("TELEGRAM STATUS:", r.status_code)
        print("TELEGRAM RESPONSE:", r.text)
    except Exception as e:
        print("TELEGRAM ERROR:", e)


def market_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if now.weekday() >= 5:
        return False

    current = now.strftime("%H:%M")
    return "09:30" <= current <= "15:55"


def download(stock, period, interval):
    return yf.download(
        stock,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True
    )


def vwap(df):
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    typical = (high + low + close) / 3
    return float((typical * volume).sum() / volume.sum())


def market_strength():
    try:
        spy = download("SPY", "2d", "5m")
        qqq = download("QQQ", "2d", "5m")

        if spy.empty or qqq.empty:
            return False, "السوق العام ضعيف"

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()

        spy_ema9 = ta.trend.EMAIndicator(spy_close, window=9).ema_indicator().iloc[-1]
        spy_ema21 = ta.trend.EMAIndicator(spy_close, window=21).ema_indicator().iloc[-1]

        qqq_ema9 = ta.trend.EMAIndicator(qqq_close, window=9).ema_indicator().iloc[-1]
        qqq_ema21 = ta.trend.EMAIndicator(qqq_close, window=21).ema_indicator().iloc[-1]

        if spy_ema9 < spy_ema21:
            return False, "SPY ضعيف"

        if qqq_ema9 < qqq_ema21:
            return False, "QQQ ضعيف"

        return True, "السوق ممتاز"

    except:
        return False, "تعذر فحص السوق"


def analyze(stock):
    try:
        df1 = download(stock, "2d", "1m")
        df5 = download(stock, "5d", "5m")
        df15 = download(stock, "10d", "15m")

        if df1.empty or df5.empty or df15.empty or len(df1) < 80:
            return None

        close1 = df1["Close"].squeeze()
        high1 = df1["High"].squeeze()
        low1 = df1["Low"].squeeze()
        volume1 = df1["Volume"].squeeze()

        close5 = df5["Close"].squeeze()
        close15 = df15["Close"].squeeze()

        price = float(close1.iloc[-1])

        ema9_1 = ta.trend.EMAIndicator(close1, window=9).ema_indicator().iloc[-1]
        ema21_1 = ta.trend.EMAIndicator(close1, window=21).ema_indicator().iloc[-1]
        ema50_1 = ta.trend.EMAIndicator(close1, window=50).ema_indicator().iloc[-1]

        ema9_5 = ta.trend.EMAIndicator(close5, window=9).ema_indicator().iloc[-1]
        ema21_5 = ta.trend.EMAIndicator(close5, window=21).ema_indicator().iloc[-1]

        ema9_15 = ta.trend.EMAIndicator(close15, window=9).ema_indicator().iloc[-1]
        ema21_15 = ta.trend.EMAIndicator(close15, window=21).ema_indicator().iloc[-1]

        rsi1 = ta.momentum.RSIIndicator(close1, window=14).rsi().iloc[-1]
        rsi5 = ta.momentum.RSIIndicator(close5, window=14).rsi().iloc[-1]

        macd1 = ta.trend.MACD(close1)
        macd1_now = macd1.macd().iloc[-1]
        macd1_signal = macd1.macd_signal().iloc[-1]

        macd5 = ta.trend.MACD(close5)
        macd5_now = macd5.macd().iloc[-1]
        macd5_signal = macd5.macd_signal().iloc[-1]

        adx = ta.trend.ADXIndicator(
            high=high1,
            low=low1,
            close=close1,
            window=14
        ).adx().iloc[-1]

        atr = ta.volatility.AverageTrueRange(
            high=high1,
            low=low1,
            close=close1,
            window=14
        ).average_true_range().iloc[-1]

        atr_pct = float(atr) / price

        vol_now = float(volume1.iloc[-1])
        vol_avg = float(volume1.tail(30).mean())
        relative_volume = vol_now / vol_avg
        dollar_volume = price * vol_now

        recent_move_5m = (price - float(close1.iloc[-6])) / float(close1.iloc[-6])
        recent_move_15m = (price - float(close1.iloc[-16])) / float(close1.iloc[-16])

        vwap_value = vwap(df1.tail(60))
        vwap_distance = (price - vwap_value) / vwap_value

        market_ok, market_reason = market_strength()

        if not market_ok:
            return None

        if recent_move_5m > 0.004:
            return None

        if recent_move_15m > 0.010:
            return None

        if rsi1 > 63:
            return None

        if vwap_distance > 0.003:
            return None

        score = 0
        reasons = []

        if price > ema9_1 > ema21_1:
            score += 20
            reasons.append("ترند 1m صاعد")

        if ema9_5 > ema21_5:
            score += 20
            reasons.append("ترند 5m صاعد")

        if ema9_15 > ema21_15:
            score += 15
            reasons.append("ترند 15m داعم")

        if price > ema50_1:
            score += 10
            reasons.append("السعر فوق EMA50")

        if 45 <= rsi1 <= 60:
            score += 15
            reasons.append(f"RSI ممتاز {rsi1:.1f}")

        if 48 <= rsi5 <= 65:
            score += 10
            reasons.append(f"RSI 5m داعم {rsi5:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD 1m إيجابي")

        if macd5_now > macd5_signal:
            score += 15
            reasons.append("MACD 5m إيجابي")

        if adx >= 20:
            score += 15
            reasons.append(f"ADX قوي {adx:.1f}")

        if relative_volume >= 1.5:
            score += 20
            reasons.append("سيولة قوية جدًا")
        elif relative_volume >= 1.2:
            score += 10
            reasons.append("سيولة جيدة")

        if dollar_volume > 1000000:
            score += 10
            reasons.append("Dollar Volume ممتاز")

        if -0.001 <= vwap_distance <= 0.003:
            score += 15
            reasons.append("قريب من VWAP")

        if 0 <= recent_move_5m <= 0.004:
            score += 15
            reasons.append("بداية زخم")

        if 0.002 <= atr_pct <= 0.012:
            score += 10
            reasons.append("تذبذب صحي")

        target_pct = TARGET_MAX if score >= 120 else TARGET_MIN

        return {
            "stock": stock,
            "price": price,
            "score": score,
            "target": price * (1 + target_pct),
            "stop": price * (1 - STOP_LOSS),
            "target_pct": target_pct,
            "reasons": reasons,
            "market_reason": market_reason,
            "rsi1": rsi1,
            "rsi5": rsi5,
            "adx": adx,
            "relative_volume": relative_volume,
            "atr_pct": atr_pct
        }

    except:
        return None


send(
    "🚀 V30 STOCK SCALP BOT ONLINE 🚀\n"
    "تحليل احترافي + منع المطاردة + سيولة + VWAP + ADX"
)

send("✅ BOT WORKING 100%")

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(
                f"👀 V30 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب النظام"
            )
            last_heartbeat = time.time()

        if not market_open():
            time.sleep(60)
            continue

        for stock in WATCHLIST:
            result = analyze(stock)

            if not result:
                continue

            score = result["score"]
            now_time = time.time()
            last_time = last_alert_time.get(stock, 0)

            if score >= MIN_SCORE and now_time - last_time >= ALERT_COOLDOWN:
                msg = f"""
🟢🚀 V30 STOCK ALERT 🚀🟢

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}

💰 الدخول: {result['price']:.2f}

🎯 الهدف:
{result['target']:.2f}
({result['target_pct']*100:.2f}%)

🛑 وقف الخسارة:
{result['stop']:.2f}
({STOP_LOSS*100:.2f}%)

🔥 قوة التوصية:
{score}/100

📊 التحليل:

- {result['market_reason']}
- RSI 1m: {result['rsi1']:.1f}
- RSI 5m: {result['rsi5']:.1f}
- ADX: {result['adx']:.1f}
- Relative Volume: {result['relative_volume']:.2f}x
- ATR: {result['atr_pct']*100:.2f}%

✅ أسباب الدخول:
{chr(10).join(['- ' + r for r in result['reasons']])}

⚠️ تنبيه فقط، القرار النهائي عليك.
"""

                send(msg)
                print(msg)
                last_alert_time[stock] = now_time

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(30)
