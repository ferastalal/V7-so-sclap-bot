import os
import yfinance as yf
import ta
import requests
import time
import gc
from datetime import datetime
import pytz


# ==========================================================
# V36 MOMENTUM BEAST - QUALITY + ANTI DUPLICATE VERSION
# ==========================================================
# مهم:
# الأفضل تحط التوكن في Render Environment Variables باسم TELEGRAM_TOKEN
# وإذا تبي تحطه داخل الكود، بدّل PUT_YOUR_TELEGRAM_TOKEN_HERE بتوكنك.
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "8897393036:AAEucfnbK2HdESXv-D6Sgd5RDITT9LTBA4A")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1016589957")

WATCHLIST = [
    "TSLA", "NVDA", "AMD", "AVGO", "BE",
    "META", "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 30
ALERT_COOLDOWN = 900          # 15 دقيقة بين نفس السهم
MIN_UPGRADE_SECONDS = 180     # لا يسمح بإعادة تنبيه ترقية قبل 3 دقائق
HEARTBEAT_SECONDS = 3600

EARLY_SCORE = 80              # رفعناه عشان يقل الضعيف
STRONG_SCORE = 110
GOLDEN_SCORE = 135

MIN_REAL_SCORE_TO_SEND = 5
MIN_REAL_SCORE_GOLDEN = 7

TARGET_MIN = 0.006
TARGET_MID = 0.008
TARGET_MAX = 0.010
STOP_LOSS = 0.003

MAX_HISTORY_BARS = 220
OPENING_BLOCK_MINUTES = 5

# منع تكرار نفس الفرصة
SAME_SETUP_PRICE_DIFF = 0.0025     # 0.25%
SAME_SETUP_SCORE_DIFF = 12
SAME_SETUP_RVOL_DIFF = 0.45

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
# TELEGRAM
# =========================
def send(msg):
    if not TOKEN or TOKEN == "PUT_YOUR_TELEGRAM_TOKEN_HERE":
        print("TELEGRAM TOKEN NOT SET - MESSAGE NOT SENT", flush=True)
        print(msg, flush=True)
        return

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)
send("✅ V36 MOMENTUM BEAST STOCK BOT STARTED")


# =========================
# TIME FILTERS
# =========================
def market_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if now.weekday() >= 5:
        return False

    current = now.strftime("%H:%M")
    return "09:30" <= current <= "15:55"


def first_minutes_after_open():
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
def download(stock, period, interval, retries=2):
    for attempt in range(retries + 1):
        try:
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

        except Exception as e:
            print(f"DOWNLOAD ERROR {stock} {interval} attempt {attempt + 1}:", e, flush=True)

        time.sleep(1)

    return None


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def vwap(df):
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    if safe_float(volume.sum()) <= 0:
        return float(close.iloc[-1])

    typical = (high + low + close) / 3
    return float((typical * volume).sum() / volume.sum())


# =========================
# QUALITY FILTER
# =========================
def real_quality_filter(move_1m, move_3m, move_5m, relative_strength, relative_volume, adx, atr_pct, vwap_distance, rsi1, rsi5):
    real_score = 0
    warnings = []

    if move_1m > 0.0005:
        real_score += 1
    else:
        warnings.append("Move 1m ضعيف أو سلبي")

    if move_3m > 0.0018:
        real_score += 1
    else:
        warnings.append("Move 3m ضعيف")

    if move_5m > 0.003:
        real_score += 1
    else:
        warnings.append("Move 5m ضعيف")

    if relative_strength > 0.0015:
        real_score += 1
    else:
        warnings.append("القوة النسبية ضعيفة")

    if relative_volume >= 1.05:
        real_score += 1
    else:
        warnings.append("الفوليوم غير كافي")

    if adx >= 22:
        real_score += 1
    else:
        warnings.append("ADX ضعيف")

    if 0.0012 <= atr_pct <= 0.010:
        real_score += 1
    else:
        warnings.append("ATR غير مثالي")

    if 0.000 <= vwap_distance <= 0.010:
        real_score += 1
    else:
        warnings.append("البعد عن VWAP غير مثالي")

    if 50 <= rsi5 <= 74:
        real_score += 1
    else:
        warnings.append("RSI 5m غير مثالي")

    aggressive_momentum = (
        adx >= 32
        and move_5m > 0.0035
        and relative_strength > 0.002
        and relative_volume >= 1.25
        and 50 <= rsi5 <= 76
    )

    golden_setup = real_score >= MIN_REAL_SCORE_GOLDEN or aggressive_momentum
    good_setup = real_score >= MIN_REAL_SCORE_TO_SEND or aggressive_momentum

    if golden_setup:
        quality_label = "🔥 GOLDEN SETUP"
    elif good_setup:
        quality_label = "✅ GOOD SETUP"
    else:
        quality_label = "⚠️ WEAK / WATCH ONLY"

    return real_score, golden_setup, good_setup, aggressive_momentum, quality_label, warnings


# =========================
# SCALP VIEW
# =========================
def assistant_scalp_view(price, high1, low1, close1, vwap_value, vwap_distance, move_1m, move_3m, move_5m, rsi1):
    resistance = float(high1.tail(12).max())
    support = float(low1.tail(12).min())
    recent_low = float(low1.tail(8).min())

    entry = resistance * 1.0004

    # وقف أذكى: لا يكون بعيد جداً ولا تحت قاع مبالغ فيه
    raw_stop = min(support, recent_low) * 0.999
    max_risk_stop = entry * (1 - STOP_LOSS)
    stop = max(raw_stop, max_risk_stop)

    target1 = entry * 1.006
    target2 = entry * 1.010

    if move_5m > 0.018 or vwap_distance > 0.014 or rsi1 > 80:
        status = "قوي لكن لا تطارد"
        advice = "انتظر Pullback أو إغلاق شمعة جديدة فوق المقاومة."
    elif price >= entry:
        status = "اختراق فعلي"
        advice = "دخول سكالب محتمل بشرط إغلاق 1m فوق الدخول وعدم كسر VWAP."
    elif price > vwap_value and price >= support:
        status = "راقب الاختراق"
        advice = f"الدخول الأفضل فوق {entry:.2f} بعد إغلاق شمعة دقيقة."
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


# =========================
# MARKET
# =========================
def market_move():
    now = time.time()

    if now - market_cache["time"] <= MARKET_CACHE_SECONDS:
        return market_cache["move"], market_cache["reason"]

    try:
        spy = download("SPY", "1d", "1m")
        qqq = download("QQQ", "1d", "1m")

        if spy is None or qqq is None or spy.empty or qqq.empty or len(spy) < 30 or len(qqq) < 30:
            return 0, "السوق غير واضح"

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()

        spy_move = (float(spy_close.iloc[-1]) - float(spy_close.iloc[-6])) / float(spy_close.iloc[-6])
        qqq_move = (float(qqq_close.iloc[-1]) - float(qqq_close.iloc[-6])) / float(qqq_close.iloc[-6])

        avg_move = (spy_move + qqq_move) / 2

        if avg_move > 0.001:
            reason = "السوق داعم"
        elif avg_move < -0.001:
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
# ANTI DUPLICATE / UPGRADE
# =========================
def should_send_alert(stock, result):
    now_time = time.time()
    last_time = last_alert_time.get(stock, 0)
    snapshot = last_alert_snapshot.get(stock)

    if snapshot is None:
        return True, "FIRST_ALERT"

    seconds_since = now_time - last_time
    price = result["price"]
    score = result["score"]
    rv = result["relative_volume"]
    beast_now = result["momentum_beast"]
    real_score = result["real_score"]

    last_price = snapshot.get("price", 0)
    last_score = snapshot.get("score", 0)
    last_rv = snapshot.get("relative_volume", 0)
    last_beast = snapshot.get("beast", False)
    last_real_score = snapshot.get("real_score", 0)

    price_diff = abs(price - last_price) / last_price if last_price else 1

    same_setup = (
        price_diff < SAME_SETUP_PRICE_DIFF
        and abs(score - last_score) < SAME_SETUP_SCORE_DIFF
        and abs(rv - last_rv) < SAME_SETUP_RVOL_DIFF
        and beast_now == last_beast
        and real_score <= last_real_score + 1
    )

    if seconds_since < ALERT_COOLDOWN and same_setup:
        return False, "SAME_SETUP_BLOCKED"

    # ترقية حقيقية فقط
    true_upgrade = (
        seconds_since >= MIN_UPGRADE_SECONDS
        and (
            (beast_now and not last_beast)
            or score >= last_score + 18
            or real_score >= last_real_score + 2
            or price >= last_price * 1.004
            or rv >= max(2.0, last_rv + 0.75)
        )
    )

    if seconds_since >= ALERT_COOLDOWN:
        return True, "COOLDOWN_OK"

    if true_upgrade:
        return True, "TRUE_UPGRADE"

    return False, "COOLDOWN_BLOCKED"


# =========================
# ANALYZE
# =========================
def analyze(stock):
    try:
        df1 = download(stock, "2d", "1m")
        df5 = download(stock, "5d", "5m")
        df15 = download(stock, "10d", "15m")

        if df1 is None or df5 is None or df15 is None:
            return None

        if df1.empty or df5.empty or df15.empty or len(df1) < 80 or len(df5) < 40 or len(df15) < 25:
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

        # استخدم متوسط آخر 30 شمعة، وقلل أثر الشمعة الحالية لأنها قد تكون غير مكتملة
        vol_now = float(volume1.iloc[-1])
        vol_avg = float(volume1.iloc[-31:-1].mean())

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
        lower_wick = min(last_close, prev_close) - last_low
        wick_ratio = upper_wick / candle_range
        lower_wick_ratio = lower_wick / candle_range

        breakout_failed = (
            last_high > breakout_level * 1.001
            and last_close < breakout_level
        )

        fast_rejection = move_1m < -0.002

        weak_breakout_volume = (
            near_breakout
            and relative_volume < 0.90
            and move_3m < 0.0015
        )

        fake_breakout_wick = (
            near_breakout
            and wick_ratio > 0.58
            and move_3m < 0.0018
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

        vwap_value = vwap(df1.tail(90))
        vwap_distance = (price - vwap_value) / vwap_value

        spy_move, market_reason = market_move()
        relative_strength = move_5m - spy_move

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

        # فلاتر منع المطاردة
        if move_1m > 0.010:
            return None

        if move_5m > 0.018:
            return None

        if move_15m > 0.035:
            return None

        if vwap_distance > 0.014:
            return None

        if rsi1 > 80:
            return None

        # فلاتر منع الضعف
        if price < vwap_value:
            return None

        if rsi5 < 45:
            return None

        if relative_volume < 0.80:
            return None

        score = 0
        reasons = []

        if near_breakout:
            score += 20
            reasons.append("قريب من كسر قمة آخر 20 شمعة")

        score += 10
        reasons.append("لا يوجد Liquidity Grab واضح")

        if 45 <= rsi1 <= 74:
            score += 15
            reasons.append(f"RSI 1m مناسب {rsi1:.1f}")

        if 50 <= rsi5 <= 74:
            score += 10
            reasons.append(f"RSI 5m داعم {rsi5:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD إيجابي")

        if price > vwap_value and 0.000 <= vwap_distance <= 0.008:
            score += 15
            reasons.append("فوق VWAP وقريب منه")

        if relative_volume >= 1.05:
            score += 15
            reasons.append(f"فوليوم بدأ يزيد {relative_volume:.2f}x")

        if relative_volume >= 1.50:
            score += 15
            reasons.append("فوليوم سبايك قوي")

        if relative_volume >= 2.00:
            score += 10
            reasons.append("فوليوم عالي جداً")

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

        if 0.0005 <= move_3m <= 0.008:
            score += 15
            reasons.append("زخم مبكر آخر 3 دقائق")

        if 0 <= move_5m <= 0.012:
            score += 10
            reasons.append("حركة صحية بدون مطاردة")

        if adx >= 18:
            score += 10
            reasons.append(f"ADX مقبول {adx:.1f}")

        if adx >= 28:
            score += 10
            reasons.append("ADX قوي")

        if dollar_volume > 500000:
            score += 10
            reasons.append("Dollar Volume مقبول")

        if 0.0012 <= atr_pct <= 0.012:
            score += 10
            reasons.append("تذبذب مناسب للسكالب")

        if lower_wick_ratio > 0.25 and last_close > prev_close:
            score += 5
            reasons.append("شمعة فيها شراء من تحت")

        momentum_beast = (
            aggressive_momentum
            or real_score >= 7
            or score >= GOLDEN_SCORE
            or (
                relative_volume >= 1.7
                and move_3m >= 0.0025
                and relative_strength >= 0.0015
                and price > vwap_value
                and rsi1 <= 78
            )
            or (
                near_breakout
                and move_1m >= 0.001
                and move_3m >= 0.002
                and price > vwap_value
                and relative_volume >= 1.15
            )
        )

        if momentum_beast:
            score += 20
            reasons.append("🔥 Momentum Beast: انفجار محتمل مبكر")

        # لا نرسل فرص جودة ضعيفة حتى لو السكور انخدع
        if real_score < MIN_REAL_SCORE_TO_SEND and not momentum_beast:
            return None

        if score < EARLY_SCORE:
            return None

        if score >= GOLDEN_SCORE or momentum_beast or real_score >= 7:
            alert_type = "GOLDEN"
            target_pct = TARGET_MAX
        elif score >= STRONG_SCORE:
            alert_type = "STRONG"
            target_pct = TARGET_MID
        else:
            alert_type = "EARLY"
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
# START MESSAGE
# =========================
send(
    "🚀 V36 MOMENTUM BEAST STOCK SCANNER ONLINE 🚀\n"
    "✅ جودة أعلى\n"
    "✅ منع التكرار لنفس الفرصة\n"
    "✅ Strong/GOLDEN Upgrade فقط إذا صار تحسن حقيقي\n"
    "✅ Market Cache لتخفيف ضغط Yahoo\n"
    "✅ Dynamic Targets 0.6% / 0.8% / 1.0%"
)


# =========================
# MAIN LOOP
# =========================
while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= HEARTBEAT_SECONDS:
            send(
                f"👀 V36 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب الأسهم"
            )
            last_heartbeat = time.time()

        if not market_open():
            print("MARKET CLOSED - BOT ALIVE", now_ksa, flush=True)
            time.sleep(30)
            gc.collect()
            continue

        if first_minutes_after_open():
            print("⏳ أول 5 دقائق من الافتتاح - تجاهل التنبيهات", now_ksa, flush=True)
            time.sleep(30)
            gc.collect()
            continue

        for stock in WATCHLIST:
            result = analyze(stock)

            if not result:
                gc.collect()
                continue

            send_ok, send_reason = should_send_alert(stock, result)

            if not send_ok:
                print(f"{stock}: ALERT BLOCKED - {send_reason}", flush=True)
                del result
                gc.collect()
                continue

            av = result["assistant_view"]
            score = result["score"]

            if result["alert_type"] == "GOLDEN":
                title = "🔥🚀 V36 GOLDEN STOCK ALERT 🚀🔥"
                note = "فرصة ذهبية عالية الجودة"
            elif result["alert_type"] == "STRONG":
                title = "🔥🚀 V36 STRONG STOCK ALERT 🚀🔥"
                note = "فرصة قوية الآن"
            else:
                title = "👀⚡ V36 EARLY STOCK ALERT ⚡👀"
                note = "تنبيه مبكر قبل الانفجار المحتمل"

            quality_warnings_text = (
                chr(10).join(["- " + w for w in result["quality_warnings"]])
                if result["quality_warnings"]
                else "- القيم ممتازة"
            )

            msg = f"""
{title}

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}
🔁 سبب الإرسال: {send_reason}

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

🐺 Momentum Beast:
{'YES 🔥🔥' if result['momentum_beast'] else 'NO'}

🏆 جودة الفرصة:
{result['quality_label']}

📊 Real Quality Score:
{result['real_score']}/9

🚀 Aggressive Momentum:
{'YES 🔥' if result['aggressive_momentum'] else 'NO'}

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

⚠️ ملاحظات الجودة:
{quality_warnings_text}

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
- شرط الدخول العملي: لا تدخل إلا بعد إغلاق شمعة 1m فوق الدخول الأفضل مع فوليوم واضح.
- ملاحظة: {av['advice']}

⚠️ تنبيه فقط، القرار النهائي عليك.
"""

            send(msg)
            print(msg, flush=True)

            last_alert_time[stock] = time.time()
            last_alert_snapshot[stock] = {
                "score": score,
                "price": result["price"],
                "beast": result["momentum_beast"],
                "real_score": result["real_score"],
                "relative_volume": result["relative_volume"],
                "alert_type": result["alert_type"]
            }

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
