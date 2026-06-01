import yfinance as yf
import ta
import requests
import time
import gc
from datetime import datetime
import pytz

# =========================
# TELEGRAM
# =========================
TOKEN = "8897393036:AAEucfnbK2HdESXv-D6Sgd5RDITT9LTBA4A"
CHAT_ID = "1016589957"

# =========================
# WATCHLIST
# =========================
WATCHLIST = [
    "TSLA", "NVDA", "AMD",
    "AMZN", "AAPL", "MSFT"
]

# =========================
# SETTINGS
# =========================
CHECK_SECONDS = 15

# تنبيه لنفس السهم كل 5 دقائق طبيعي
ALERT_COOLDOWN = 300

# استمرار الموجة: إذا السهم كمل +0.60% بعد آخر تنبيه نسمح بتنبيه متابعة
CONTINUATION_MOVE = 0.006
CONTINUATION_COOLDOWN = 120

EARLY_SCORE = 75
STRONG_SCORE = 95

TARGET_MIN = 0.006
TARGET_MID = 0.008
TARGET_MAX = 0.010
STOP_LOSS = 0.003

MAX_HISTORY_BARS = 220
OPENING_BLOCK_MINUTES = 5

# فلتر الجودة الحقيقي
MIN_REAL_SCORE_TO_SEND = 5
MIN_GOLDEN_REAL_SCORE = 6

# كاش السوق عشان ما نحمل SPY/QQQ مع كل سهم
MARKET_CACHE_SECONDS = 60

last_alert_time = {}
last_alert_snapshot = {}
last_heartbeat = time.time()

market_cache = {
    "time": 0,
    "move": 0,
    "reason": "السوق غير واضح"
}


# =========================
# SEND TELEGRAM
# =========================
def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)
send("✅ V32.4 CLEAN MOMENTUM BOT STARTED")


# =========================
# MARKET TIME
# =========================
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


# =========================
# DATA
# =========================
def download(stock, period, interval):
    df = yf.download(
        stock,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True,
        threads=False
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


# =========================
# REAL QUALITY FILTER
# =========================
def real_quality_filter(move_1m, move_3m, move_5m, relative_strength, relative_volume, adx, atr_pct, vwap_distance, rsi1, rsi5):
    real_score = 0
    warnings = []

    if move_1m > 0.0006:
        real_score += 1
    else:
        warnings.append("Move 1m ضعيف")

    if move_3m > 0.0016:
        real_score += 1
    else:
        warnings.append("Move 3m ضعيف")

    if move_5m > 0.0025:
        real_score += 1
    else:
        warnings.append("Move 5m ضعيف")

    if relative_strength > 0.0012:
        real_score += 1
    else:
        warnings.append("القوة النسبية ضعيفة")

    if relative_volume >= 1.00:
        real_score += 1
    else:
        warnings.append("الفوليوم أقل من المطلوب")

    if adx >= 20:
        real_score += 1
    else:
        warnings.append("ADX ضعيف")

    if 0.0010 <= atr_pct <= 0.010:
        real_score += 1
    else:
        warnings.append("ATR غير مناسب")

    if -0.001 <= vwap_distance <= 0.012:
        real_score += 1
    else:
        warnings.append("البعد عن VWAP غير مناسب")

    if 50 <= rsi5 <= 74:
        real_score += 1
    else:
        warnings.append("RSI 5m غير داعم")

    aggressive_momentum = (
        real_score >= 5
        and adx >= 28
        and move_3m >= 0.002
        and move_5m >= 0.003
        and relative_strength >= 0.0015
        and relative_volume >= 1.10
        and price_safe_rsi(rsi1, rsi5)
    )

    golden_setup = real_score >= MIN_GOLDEN_REAL_SCORE or aggressive_momentum
    good_setup = real_score >= MIN_REAL_SCORE_TO_SEND or aggressive_momentum

    if golden_setup:
        quality_label = "🔥 GOLDEN SETUP"
    elif good_setup:
        quality_label = "✅ GOOD SETUP"
    else:
        quality_label = "⚠️ WEAK / FILTERED"

    return real_score, golden_setup, good_setup, aggressive_momentum, quality_label, warnings


def price_safe_rsi(rsi1, rsi5):
    return rsi1 <= 80 and rsi5 <= 76


# =========================
# ASSISTANT VIEW
# =========================
def assistant_scalp_view(price, high1, low1, close1, vwap_value, vwap_distance, move_1m, move_3m, move_5m, rsi1):
    resistance = float(high1.tail(12).max())
    support = float(low1.tail(12).min())
    recent_low = float(low1.tail(8).min())

    entry = resistance * 1.0005

    # وقف عملي قريب: لا نخلي الوقف بعيد تحت الدعم بشكل مبالغ
    stop = max(entry * (1 - STOP_LOSS), recent_low * 0.999)

    target1 = entry * 1.006
    target2 = entry * 1.010

    if move_5m > 0.018 or vwap_distance > 0.016 or rsi1 > 80:
        status = "قوي لكن لا تطارد"
        advice = "انتظر اختراق جديد أو تهدئة بسيطة."
    elif price >= entry:
        status = "اختراق فعلي"
        advice = "دخول محتمل بشرط شمعة 1m تغلق فوق الدخول."
    elif price > vwap_value and price >= support:
        status = "راقب الاختراق"
        advice = f"الدخول الأفضل فوق {entry:.2f}."
    else:
        status = "انتظار"
        advice = "لا تدخل قبل تأكيد الاختراق."

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


# =========================
# MARKET MOVE WITH CACHE
# =========================
def market_move():
    now = time.time()

    if now - market_cache["time"] <= MARKET_CACHE_SECONDS:
        return market_cache["move"], market_cache["reason"]

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

        if avg_move > 0.0002:
            reason = "السوق داعم"
        elif avg_move < -0.0015:
            reason = "السوق ضعيف"
        else:
            reason = "السوق محايد"

        market_cache["time"] = now
        market_cache["move"] = avg_move
        market_cache["reason"] = reason

        del spy, qqq, spy_close, qqq_close
        gc.collect()

        return avg_move, reason

    except Exception as e:
        print("MARKET MOVE ERROR:", e, flush=True)
        return 0, "تعذر فحص السوق"


# =========================
# ANALYZE STOCK
# =========================
def analyze(stock):
    try:
        df1 = download(stock, "2d", "1m")
        df5 = download(stock, "5d", "5m")
        df15 = download(stock, "10d", "15m")

        if df1.empty or df5.empty or df15.empty or len(df1) < 80 or len(df5) < 30 or len(df15) < 30:
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

        # الفوليوم: نستخدم آخر شمعة مكتملة غالباً عشان ما يطلع 0.00x أو يخرب القرار
        vol_now = float(volume1.iloc[-2])
        vol_avg = float(volume1.iloc[-32:-2].mean())

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
        breakout_level = high_20

        last_close = float(close1.iloc[-1])
        prev_close = float(close1.iloc[-2])
        last_high = float(high1.iloc[-1])
        last_low = float(low1.iloc[-1])

        candle_range = max(last_high - last_low, 0.0001)
        upper_wick = last_high - max(last_close, prev_close)
        wick_ratio = upper_wick / candle_range

        breakout_failed = (
            last_high > breakout_level * 1.001
            and last_close < breakout_level
        )

        fast_rejection = move_1m < -0.002

        weak_breakout_volume = (
            near_breakout
            and relative_volume < 0.75
            and move_3m < 0.0012
        )

        fake_breakout_wick = (
            near_breakout
            and wick_ratio > 0.60
            and move_3m < 0.0015
        )

        liquidity_grab_detected = (
            breakout_failed
            or fast_rejection
            or weak_breakout_volume
            or fake_breakout_wick
        )

        if liquidity_grab_detected:
            print(f"{stock}: LIQUIDITY GRAB FILTERED", flush=True)
            return None

        vwap_value = vwap(df1.tail(60))
        vwap_distance = (price - vwap_value) / vwap_value

        spy_move, market_reason = market_move()
        relative_strength = move_5m - spy_move

        # لا نطارد الممتد جداً
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

        if price < vwap_value * 0.998:
            return None

        assistant_view = assistant_scalp_view(
            price, high1, low1, close1,
            vwap_value, vwap_distance,
            move_1m, move_3m, move_5m, rsi1
        )

        real_score, golden_setup, good_setup, aggressive_momentum, quality_label, quality_warnings = real_quality_filter(
            move_1m, move_3m, move_5m,
            relative_strength, relative_volume,
            adx, atr_pct, vwap_distance,
            rsi1, rsi5
        )

        # أهم تعديل: ممنوع إرسال فرص WEAK حتى لو السكور الحسابي عالي
        if not good_setup:
            return None

        score = 0
        reasons = []

        if near_breakout:
            score += 20
            reasons.append("قريب من كسر قمة آخر 20 شمعة")

        score += 10
        reasons.append("لا يوجد Liquidity Grab واضح")

        if 45 <= rsi1 <= 76:
            score += 15
            reasons.append(f"RSI 1m مناسب {rsi1:.1f}")

        if 50 <= rsi5 <= 74:
            score += 10
            reasons.append(f"RSI 5m داعم {rsi5:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD إيجابي")

        if price > vwap_value and -0.001 <= vwap_distance <= 0.012:
            score += 15
            reasons.append("فوق VWAP وبمسافة مقبولة")

        if relative_volume >= 1.00:
            score += 15
            reasons.append(f"الفوليوم مقبول {relative_volume:.2f}x")

        if relative_volume >= 1.50:
            score += 15
            reasons.append("فوليوم قوي")

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
            reasons.append("أقوى من السوق")

        if 0.0005 <= move_3m <= 0.008:
            score += 15
            reasons.append("زخم 3 دقائق صحي")

        if 0 <= move_5m <= 0.012:
            score += 10
            reasons.append("حركة 5 دقائق بدون مطاردة")

        if adx >= 20:
            score += 10
            reasons.append(f"ADX مقبول {adx:.1f}")

        if dollar_volume > 500000:
            score += 10
            reasons.append("Dollar Volume مقبول")

        if 0.0010 <= atr_pct <= 0.010:
            score += 10
            reasons.append("ATR مناسب للسكالب")

        momentum_beast = (
            aggressive_momentum
            or real_score >= 7
            or (
                relative_volume >= 1.50
                and move_3m >= 0.0022
                and relative_strength >= 0.0015
                and adx >= 22
            )
            or (
                near_breakout
                and move_1m >= 0.0008
                and move_3m >= 0.0018
                and price > vwap_value
                and real_score >= 5
            )
        )

        if momentum_beast:
            score += 20
            reasons.append("🐺 Momentum Beast فعلي")

        if score < EARLY_SCORE:
            return None

        if golden_setup or score >= STRONG_SCORE:
            alert_type = "STRONG"
        else:
            alert_type = "EARLY"

        if golden_setup or momentum_beast:
            target_pct = TARGET_MAX
        elif score >= STRONG_SCORE:
            target_pct = TARGET_MID
        else:
            target_pct = TARGET_MIN

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
            "assistant_view": assistant_view,
            "real_score": real_score,
            "golden_setup": golden_setup,
            "good_setup": good_setup,
            "aggressive_momentum": aggressive_momentum,
            "momentum_beast": momentum_beast,
            "quality_label": quality_label,
            "quality_warnings": quality_warnings
        }

    except Exception as e:
        print(f"ANALYZE ERROR {stock}:", e, flush=True)
        return None

    finally:
        gc.collect()


# =========================
# MAIN LOOP
# =========================
send(
    "🚀 V32.4 ONLINE\n"
    "✅ يرسل فقط GOOD / GOLDEN\n"
    "🚫 يمنع WEAK حتى لو السكور عالي\n"
    "🐺 Momentum Beast مضبوط\n"
    "🔁 يتابع السهم إذا كمل موجة جديدة"
)

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(f"👀 V32.4 STILL RUNNING | KSA {now_ksa}")
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

            snapshot = last_alert_snapshot.get(stock, {})
            last_score = snapshot.get("score", 0)
            last_price = snapshot.get("price", 0)
            last_beast = snapshot.get("beast", False)
            last_real_score = snapshot.get("real_score", 0)

            beast_now = result["momentum_beast"]

            normal_cooldown_ok = now_time - last_time >= ALERT_COOLDOWN

            # ترقية جودة: من عادي إلى Beast أو تحسن واضح في الجودة
            quality_upgrade = (
                beast_now
                and (
                    not last_beast
                    or score >= last_score + 15
                    or result["real_score"] > last_real_score
                    or result["relative_volume"] >= 2.0
                )
            )

            # استمرار الموجة: مثل 500 -> 505 ثم 505 -> 510
            continuation_alert = (
                last_price > 0
                and result["price"] >= last_price * (1 + CONTINUATION_MOVE)
                and now_time - last_time >= CONTINUATION_COOLDOWN
                and result["good_setup"]
                and result["move_3m"] > 0
                and result["price"] > av["entry"] * 0.998
            )

            should_send = normal_cooldown_ok or quality_upgrade or continuation_alert

            if should_send:
                if continuation_alert:
                    reason_send = "استمرار الموجة بعد آخر تنبيه"
                elif quality_upgrade:
                    reason_send = "ترقية جودة / Momentum Beast"
                else:
                    reason_send = "أول تنبيه للفرصة"

                if result["golden_setup"] or result["momentum_beast"]:
                    title = "🔥🚀 V32.4 GOLDEN STOCK ALERT 🚀🔥"
                    note = "فرصة ذهبية عالية الجودة"
                else:
                    title = "✅🚀 V32.4 GOOD STOCK ALERT 🚀✅"
                    note = "فرصة جيدة بجودة مقبولة"

                warnings_text = (
                    " | ".join(result["quality_warnings"][:3])
                    if result["quality_warnings"]
                    else "القيم ممتازة"
                )

                top_reasons = "\n".join(["- " + r for r in result["reasons"][:6]])

                msg = f"""
{title}

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}
🔁 سبب الإرسال: {reason_send}

💰 السعر الحالي: {result['price']:.2f}

🎯 الهدف المتوقع: {result['target']:.2f}
({result['target_pct']*100:.2f}%)

🛑 وقف المتابعة: {result['stop']:.2f}
({STOP_LOSS*100:.2f}%)

🔥 السكور: {score}/100
📌 النوع: {note}

🐺 Momentum Beast: {'YES 🔥🔥' if result['momentum_beast'] else 'NO'}
🏆 جودة الفرصة: {result['quality_label']}
📊 Real Quality Score: {result['real_score']}/9

📊 التحليل المختصر:
- {result['market_reason']}
- RSI 1m: {result['rsi1']:.1f}
- RSI 5m: {result['rsi5']:.1f}
- ADX: {result['adx']:.1f}
- RVOL: {result['relative_volume']:.2f}x
- VWAP Distance: {result['vwap_distance']*100:.2f}%
- Move 3m: {result['move_3m']*100:.2f}%
- Move 5m: {result['move_5m']*100:.2f}%
- Relative Strength: {result['relative_strength']*100:.2f}%

✅ أهم الأسباب:
{top_reasons}

🧠 خطة السكالب:
- الحالة: {av['status']}
- الدخول الأفضل: فوق {av['entry']:.2f}
- الهدف الأول: {av['target1']:.2f}
- الهدف الثاني: {av['target2']:.2f}
- وقف السكالب: {av['stop']:.2f}
- ملاحظة: {av['advice']}

⚠️ ملاحظات: {warnings_text}
⚠️ تنبيه فقط، القرار النهائي عليك.
"""

                send(msg)
                print(msg, flush=True)

                last_alert_time[stock] = now_time
                last_alert_snapshot[stock] = {
                    "score": score,
                    "price": result["price"],
                    "beast": beast_now,
                    "real_score": result["real_score"]
                }

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
