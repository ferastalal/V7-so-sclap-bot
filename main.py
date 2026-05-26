import yfinance as yf
import ta
import requests
import time
import gc
from datetime import datetime
import pytz

TOKEN = "8897393036:AAEucfnbK2HdESXv-D6Sgd5RDITT9LTBA4A"
CHAT_ID = "1016589957"

WATCHLIST = [
    "TSLA", "NVDA", "AMD", "AVGO", "BE",
    "META", "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 15
ALERT_COOLDOWN = 900

EARLY_SCORE = 55
STRONG_SCORE = 80

TARGET_MIN = 0.004
TARGET_MAX = 0.010
STOP_LOSS = 0.003

MAX_HISTORY_BARS = 300
OPENING_BLOCK_MINUTES = 5

last_alert_time = {}
last_heartbeat = time.time()


def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)
send("✅ V31 STOCK EARLY ALERT BOT STARTED")


def market_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)
    if now.weekday() >= 5:
        return False
    current = now.strftime("%H:%M")
    return "09:30" <= current <= "15:55"


def first_5_minutes_after_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if now.weekday() >= 5:
        return False

    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    block_end = now.replace(hour=9, minute=30 + OPENING_BLOCK_MINUTES, second=0, microsecond=0)

    return open_time <= now < block_end


def download(stock, period, interval):
    df = yf.download(
        stock,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True
    )

    if df is not None and not df.empty:
        df = df.tail(MAX_HISTORY_BARS).copy()

    return df


def vwap(df):
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    if volume.sum() <= 0:
        return float(close.iloc[-1])

    typical = (high + low + close) / 3
    return float((typical * volume).sum() / volume.sum())


def assistant_scalp_view(price, high1, low1, close1, vwap_value, vwap_distance, move_1m, move_3m, move_5m, rsi1):
    resistance = float(high1.tail(12).max())
    support = float(low1.tail(12).min())
    recent_low = float(low1.tail(8).min())

    entry = resistance * 1.0005
    stop = min(support, recent_low) * 0.998

    target1 = entry * 1.006
    target2 = entry * 1.010

    if move_5m > 0.020 or vwap_distance > 0.018 or rsi1 > 82:
        status = "قوي لكن لا تطارد"
        advice = "انتظر Pullback أو اختراق جديد بعد تهدئة."
    elif price >= entry:
        status = "اختراق فعلي"
        advice = "دخول سكالب محتمل، لكن التزم بالوقف."
    elif price > vwap_value and price >= support:
        status = "راقب الاختراق"
        advice = f"الدخول الأفضل فوق {entry:.2f} فقط."
    else:
        status = "انتظار"
        advice = "الزخم غير مؤكد، لا تدخل الآن."

    return {
        "status": status,
        "entry": entry,
        "target1": target1,
        "target2": target2,
        "stop": stop,
        "support": support,
        "resistance": resistance,
        "advice": advice
    }


def market_move():
    try:
        spy = download("SPY", "1d", "1m")
        qqq = download("QQQ", "1d", "1m")

        if spy.empty or qqq.empty or len(spy) < 30 or len(qqq) < 30:
            return 0, "السوق غير واضح"

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()

        spy_move = (float(spy_close.iloc[-1]) - float(spy_close.iloc[-6])) / float(spy_close.iloc[-6])
        qqq_move = (float(qqq_close.iloc[-1]) - float(qqq_close.iloc[-6])) / float(qqq_close.iloc[-6])

        avg_move = (spy_move + qqq_move) / 2

        del spy, qqq, spy_close, qqq_close
        gc.collect()

        if avg_move > 0:
            return avg_move, "السوق داعم"
        else:
            return avg_move, "السوق ضعيف شوي"

    except Exception as e:
        print("MARKET MOVE ERROR:", e, flush=True)
        return 0, "تعذر فحص السوق"


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

        if vol_avg <= 0:
            return None

        relative_volume = vol_now / vol_avg
        dollar_volume = price * vol_now

        move_1m = (price - float(close1.iloc[-2])) / float(close1.iloc[-2])
        move_3m = (price - float(close1.iloc[-4])) / float(close1.iloc[-4])
        move_5m = (price - float(close1.iloc[-6])) / float(close1.iloc[-6])
        move_15m = (price - float(close1.iloc[-16])) / float(close1.iloc[-16])

        high_20 = float(high1.tail(20).max())
        near_breakout = price >= high_20 * 0.997

        vwap_value = vwap(df1.tail(60))
        vwap_distance = (price - vwap_value) / vwap_value

        spy_move, market_reason = market_move()
        relative_strength = move_5m - spy_move

        assistant_view = assistant_scalp_view(
            price, high1, low1, close1,
            vwap_value, vwap_distance,
            move_1m, move_3m, move_5m, rsi1
        )

        # فلتر منع المطاردة الخفيف
        if move_1m > 0.012:
            return None

        if move_5m > 0.020:
            return None

        if move_15m > 0.040:
            return None

        if vwap_distance > 0.018:
            return None

        if rsi1 > 82:
            return None

        score = 0
        reasons = []

        if near_breakout:
            score += 20
            reasons.append("قريب من كسر قمة آخر 20 شمعة")

        if 45 <= rsi1 <= 68:
            score += 15
            reasons.append(f"RSI يجهز {rsi1:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD بدأ يعطي إيجابية")

        if price > vwap_value and -0.002 <= vwap_distance <= 0.006:
            score += 15
            reasons.append("فوق VWAP وقريب منه")

        if relative_volume >= 1.1:
            score += 15
            reasons.append(f"فوليوم بدأ يزيد {relative_volume:.2f}x")

        if relative_volume >= 1.5:
            score += 15
            reasons.append("فوليوم سبايك قوي")

        if price > ema9_1 > ema21_1:
            score += 15
            reasons.append("ترند 1m صاعد")

        if ema9_5 > ema21_5:
            score += 10
            reasons.append("ترند 5m داعم")

        if ema9_15 > ema21_15:
            score += 10
            reasons.append("ترند 15m داعم")

        if price > ema50_1:
            score += 10
            reasons.append("فوق EMA50")

        if relative_strength > 0.001:
            score += 15
            reasons.append("أقوى من السوق SPY/QQQ")

        if 0.0005 <= move_3m <= 0.006:
            score += 15
            reasons.append("زخم مبكر آخر 3 دقائق")

        if 0 <= move_5m <= 0.010:
            score += 10
            reasons.append("حركة صحية بدون مطاردة")

        if adx >= 16:
            score += 10
            reasons.append(f"ADX جيد {adx:.1f}")

        if dollar_volume > 500000:
            score += 10
            reasons.append("Dollar Volume مقبول")

        if 0.0015 <= atr_pct <= 0.018:
            score += 10
            reasons.append("تذبذب مناسب للسكالب")

        alert_type = "EARLY" if score < STRONG_SCORE else "STRONG"
        target_pct = TARGET_MAX if score >= STRONG_SCORE else TARGET_MIN

        return {
            "stock": stock,
            "price": price,
            "score": score,
            "alert_type": alert_type,
            "target": price * (1 + target_pct),
            "stop": price * (1 - STOP_LOSS),
            "target_pct": target_pct,
            "reasons": reasons,
            "market_reason": market_reason,
            "rsi1": rsi1,
            "rsi5": rsi5,
            "adx": adx,
            "relative_volume": relative_volume,
            "atr_pct": atr_pct,
            "vwap_distance": vwap_distance,
            "move_1m": move_1m,
            "move_3m": move_3m,
            "move_5m": move_5m,
            "relative_strength": relative_strength,
            "assistant_view": assistant_view
        }

    except Exception as e:
        print(f"ANALYZE ERROR {stock}:", e, flush=True)
        return None

    finally:
        gc.collect()


send(
    "🚀 V31 STOCK EARLY SCANNER ONLINE 🚀\n"
    "👀 تنبيه مبكر قبل الانفجار + 🔥 تنبيه قوي + VWAP + Volume Spike + Relative Strength + Assistant Scalp View"
)

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(
                f"👀 V31 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب الأسهم"
            )
            last_heartbeat = time.time()

        if not market_open():
            print("MARKET CLOSED - BOT ALIVE", now_ksa, flush=True)
            time.sleep(15)
            gc.collect()
            continue

        if first_5_minutes_after_open():
            print("⏳ أول 5 دقائق من الافتتاح - تجاهل التنبيهات", now_ksa, flush=True)
            time.sleep(15)
            gc.collect()
            continue

        for stock in WATCHLIST:
            result = analyze(stock)

            if not result:
                gc.collect()
                continue

            score = result["score"]
            now_time = time.time()
            last_time = last_alert_time.get(stock, 0)
            av = result["assistant_view"]

            if score >= EARLY_SCORE and now_time - last_time >= ALERT_COOLDOWN:
                if result["alert_type"] == "STRONG":
                    title = "🔥🚀 V31 STRONG STOCK ALERT 🚀🔥"
                    note = "فرصة قوية الآن"
                else:
                    title = "👀⚡ V31 EARLY STOCK ALERT ⚡👀"
                    note = "تنبيه مبكر قبل الانفجار المحتمل"

                msg = f"""
{title}

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}

💰 السعر الحالي:
{result['price']:.2f}

🎯 الهدف المتوقع:
{result['target']:.2f}
({result['target_pct']*100:.2f}%)

🛑 وقف المتابعة:
{result['stop']:.2f}
({STOP_LOSS*100:.2f}%)

🔥 السكور:
{score}/100

📌 النوع:
{note}

📊 التحليل:
- {result['market_reason']}
- RSI 1m: {result['rsi1']:.1f}
- RSI 5m: {result['rsi5']:.1f}
- ADX: {result['adx']:.1f}
- Relative Volume: {result['relative_volume']:.2f}x
- ATR: {result['atr_pct']*100:.2f}%
- VWAP Distance: {result['vwap_distance']*100:.2f}%
- Move 1m: {result['move_1m']*100:.2f}%
- Move 3m: {result['move_3m']*100:.2f}%
- Move 5m: {result['move_5m']*100:.2f}%
- Relative Strength: {result['relative_strength']*100:.2f}%

✅ أسباب التنبيه:
{chr(10).join(['- ' + r for r in result['reasons']])}

🧠 رأي مساعد السكالب:
- الحالة: {av['status']}
- الدعم القريب: {av['support']:.2f}
- المقاومة القريبة: {av['resistance']:.2f}
- الدخول الأفضل: فوق {av['entry']:.2f}
- الهدف الأول: {av['target1']:.2f}
- الهدف الثاني: {av['target2']:.2f}
- وقف السكالب: {av['stop']:.2f}
- الخطة: إذا وصل الهدف الأول بيع نصف الكمية وارفع الوقف لسعر الدخول.
- ملاحظة: {av['advice']}

⚠️ تنبيه فقط، القرار النهائي عليك.
"""

                send(msg)
                print(msg, flush=True)
                last_alert_time[stock] = now_time

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
