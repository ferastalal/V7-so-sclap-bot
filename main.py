import os
import yfinance as yf
import ta
import requests
import time
import gc
from datetime import datetime
import pytz

# =========================================================
# V32.3 CLEAN MOMENTUM SCALP BOT
# الهدف:
# 1) يمنع رسائل WEAK / WATCH ONLY والفرص الزبالة
# 2) لا يخنق الفرص القوية إذا كملت موجتها
# 3) يصلح مشكلة Relative Volume = 0.00x بسبب شمعة ياهو غير المكتملة
# 4) يقلل تكرار التنبيهات غير المفيد، ويسمح بتنبيه استمرار الموجة
# =========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "8897393036:AAEucfnbK2HdESXv-D6Sgd5RDITT9LTBA4A")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1016589957")

WATCHLIST = [
    "TSLA", "NVDA", "AMD",
    "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 15

# لا نبي سبام، لكن إذا السهم كمل موجته نبيه ينبه
ALERT_COOLDOWN = 300              # التنبيه العادي كل 5 دقائق
CONTINUATION_COOLDOWN = 90        # استمرار الموجة أسرع
CONTINUATION_STEP = 0.006         # إذا تحرك من آخر تنبيه +0.60% وما زالت الجودة قوية

# إرسال التنبيه
MIN_SCORE_TO_SEND = 105
MIN_REAL_SCORE_TO_SEND = 6        # يمنع Real Quality 1/9 و 3/9
GOLDEN_REAL_SCORE = 6

TARGET_GOOD = 0.006               # 0.60%
TARGET_STRONG = 0.008             # 0.80%
TARGET_GOLDEN = 0.010             # 1.00%
STOP_LOSS = 0.003                 # 0.30%

MAX_HISTORY_BARS = 260
OPENING_HARD_BLOCK_MINUTES = 2    # أول دقيقتين فقط تجاهل كامل
OPENING_CAUTION_MINUTES = 5       # من 2 إلى 5 دقائق لا يرسل إلا القوي جداً

MARKET_CACHE_SECONDS = 45

last_alert_time = {}
last_alert_snapshot = {}
last_heartbeat = time.time()

market_cache = {
    "time": 0,
    "move": 0,
    "reason": "السوق غير واضح"
}


# =========================================================
# TELEGRAM
# =========================================================
def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


# =========================================================
# TIME
# =========================================================
def market_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if now.weekday() >= 5:
        return False

    current = now.strftime("%H:%M")
    return "09:30" <= current <= "15:55"


def minutes_after_open():
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if now.weekday() >= 5:
        return -1

    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    diff = (now - open_time).total_seconds() / 60
    return diff


# =========================================================
# DATA HELPERS
# =========================================================
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


def safe_series(df, name):
    return df[name].squeeze()


def vwap(df):
    high = safe_series(df, "High")
    low = safe_series(df, "Low")
    close = safe_series(df, "Close")
    volume = safe_series(df, "Volume")

    if float(volume.sum()) <= 0:
        return float(close.iloc[-1])

    typical = (high + low + close) / 3
    return float((typical * volume).sum() / volume.sum())


def get_market_move():
    # كاش عشان ما يحمل SPY/QQQ لكل سهم ويبطئ البوت
    now = time.time()
    if now - market_cache["time"] < MARKET_CACHE_SECONDS:
        return market_cache["move"], market_cache["reason"]

    try:
        spy = download("SPY", "1d", "1m")
        qqq = download("QQQ", "1d", "1m")

        if spy.empty or qqq.empty or len(spy) < 30 or len(qqq) < 30:
            return 0, "السوق غير واضح"

        # نستخدم آخر شمعة مكتملة -2 لتقليل مشاكل ياهو
        spy_close = safe_series(spy, "Close")
        qqq_close = safe_series(qqq, "Close")

        spy_move = (float(spy_close.iloc[-2]) - float(spy_close.iloc[-7])) / float(spy_close.iloc[-7])
        qqq_move = (float(qqq_close.iloc[-2]) - float(qqq_close.iloc[-7])) / float(qqq_close.iloc[-7])

        avg_move = (spy_move + qqq_move) / 2

        if avg_move >= 0.001:
            reason = "السوق داعم"
        elif avg_move <= -0.0015:
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


# =========================================================
# QUALITY FILTER
# =========================================================
def real_quality_filter(
    move_1m, move_3m, move_5m, relative_strength,
    relative_volume, adx, atr_pct, vwap_distance, rsi1, rsi5,
    price, ema9_1, ema21_1, near_breakout
):
    real_score = 0
    warnings = []

    if move_1m >= 0.0005:
        real_score += 1
    else:
        warnings.append("زخم الدقيقة ضعيف")

    if move_3m >= 0.0015:
        real_score += 1
    else:
        warnings.append("زخم 3 دقائق غير كافي")

    if move_5m >= 0.0025:
        real_score += 1
    else:
        warnings.append("زخم 5 دقائق ضعيف")

    if relative_strength >= 0.0012:
        real_score += 1
    else:
        warnings.append("السهم مو أقوى من السوق كفاية")

    # إصلاح مهم: لا نقبل RVOL صفر أو ضعيف حتى لو السكور عالي
    if relative_volume >= 1.05:
        real_score += 1
    else:
        warnings.append("الفوليوم أقل من المطلوب")

    if adx >= 22:
        real_score += 1
    else:
        warnings.append("ADX ضعيف")

    if 0.0012 <= atr_pct <= 0.010:
        real_score += 1
    else:
        warnings.append("ATR غير مناسب للسكالب")

    # لا نخنق الفرص القريبة من VWAP، لكن نمنع المطاردة البعيدة
    if -0.0015 <= vwap_distance <= 0.010:
        real_score += 1
    else:
        warnings.append("السعر بعيد/سيء عن VWAP")

    if 50 <= rsi5 <= 74 and rsi1 <= 82:
        real_score += 1
    else:
        warnings.append("RSI غير مثالي")

    trend_ok = price > ema9_1 > ema21_1

    aggressive_momentum = (
        relative_volume >= 1.35
        and adx >= 26
        and move_3m >= 0.0022
        and move_5m >= 0.0030
        and relative_strength >= 0.0015
        and vwap_distance <= 0.012
        and rsi1 <= 82
        and (trend_ok or near_breakout)
    )

    golden_setup = (
        real_score >= GOLDEN_REAL_SCORE
        and relative_volume >= 1.10
        and vwap_distance <= 0.010
        and rsi1 <= 80
    ) or aggressive_momentum

    good_setup = (
        real_score >= MIN_REAL_SCORE_TO_SEND
        and relative_volume >= 1.05
        and -0.0015 <= vwap_distance <= 0.012
        and rsi1 <= 82
    ) or aggressive_momentum

    if golden_setup:
        quality_label = "🔥 GOLDEN SETUP"
    elif good_setup:
        quality_label = "✅ GOOD SETUP"
    else:
        quality_label = "🚫 FILTERED - NO ALERT"

    return real_score, golden_setup, good_setup, aggressive_momentum, quality_label, warnings


def detect_liquidity_grab(
    near_breakout, breakout_level,
    last_close, prev_close, last_high, last_low,
    move_1m, move_3m, relative_volume
):
    candle_range = max(last_high - last_low, 0.0001)
    upper_wick = last_high - max(last_close, prev_close)
    wick_ratio = upper_wick / candle_range

    breakout_failed = last_high > breakout_level * 1.001 and last_close < breakout_level
    fast_rejection = move_1m < -0.002
    weak_breakout_volume = near_breakout and relative_volume < 0.90 and move_3m < 0.0015
    fake_breakout_wick = near_breakout and wick_ratio > 0.60 and move_3m < 0.0015

    return breakout_failed or fast_rejection or weak_breakout_volume or fake_breakout_wick


def assistant_scalp_view(price, high1, low1, vwap_value, vwap_distance, move_5m, rsi1):
    resistance = float(high1.tail(12).max())
    support = float(low1.tail(12).min())
    recent_low = float(low1.tail(8).min())

    entry = resistance * 1.0003
    stop = max(entry * (1 - STOP_LOSS), recent_low * 0.999)

    target1 = entry * 1.006
    target2 = entry * 1.010

    if move_5m > 0.018 or vwap_distance > 0.014 or rsi1 > 82:
        status = "قوي لكن لا تطارد"
        advice = "انتظر تهدئة أو كسر جديد واضح، لا تدخل بعد تمدد قوي."
    elif price >= entry:
        status = "اختراق فعلي"
        advice = "دخول سكالب محتمل بعد إغلاق شمعة 1m فوق الكسر."
    elif price > vwap_value and price >= support:
        status = "راقب الاختراق"
        advice = f"الدخول الأفضل فوق {entry:.2f} بعد إغلاق 1m."
    else:
        status = "انتظار"
        advice = "لا تدخل قبل رجوع السعر فوق VWAP أو كسر مقاومة واضح."

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


# =========================================================
# ANALYZE
# =========================================================
def analyze(stock):
    try:
        df1 = download(stock, "2d", "1m")
        df5 = download(stock, "5d", "5m")
        df15 = download(stock, "10d", "15m")

        if df1.empty or df5.empty or df15.empty or len(df1) < 90 or len(df5) < 40 or len(df15) < 30:
            return None

        close1 = safe_series(df1, "Close")
        high1 = safe_series(df1, "High")
        low1 = safe_series(df1, "Low")
        volume1 = safe_series(df1, "Volume")

        close5 = safe_series(df5, "Close")
        close15 = safe_series(df15, "Close")

        # live price للعرض، signal bar للمؤشرات والفوليوم
        live_price = float(close1.iloc[-1])
        signal_price = float(close1.iloc[-2])

        ema9_1_series = ta.trend.EMAIndicator(close1, window=9).ema_indicator()
        ema21_1_series = ta.trend.EMAIndicator(close1, window=21).ema_indicator()
        ema50_1_series = ta.trend.EMAIndicator(close1, window=50).ema_indicator()

        ema9_1 = float(ema9_1_series.iloc[-2])
        ema21_1 = float(ema21_1_series.iloc[-2])
        ema50_1 = float(ema50_1_series.iloc[-2])

        ema9_5 = float(ta.trend.EMAIndicator(close5, window=9).ema_indicator().iloc[-2])
        ema21_5 = float(ta.trend.EMAIndicator(close5, window=21).ema_indicator().iloc[-2])

        ema9_15 = float(ta.trend.EMAIndicator(close15, window=9).ema_indicator().iloc[-2])
        ema21_15 = float(ta.trend.EMAIndicator(close15, window=21).ema_indicator().iloc[-2])

        rsi1 = float(ta.momentum.RSIIndicator(close1, window=14).rsi().iloc[-2])
        rsi5 = float(ta.momentum.RSIIndicator(close5, window=14).rsi().iloc[-2])

        macd1 = ta.trend.MACD(close1)
        macd1_now = float(macd1.macd().iloc[-2])
        macd1_signal = float(macd1.macd_signal().iloc[-2])

        adx = float(ta.trend.ADXIndicator(high=high1, low=low1, close=close1, window=14).adx().iloc[-2])

        atr = float(ta.volatility.AverageTrueRange(
            high=high1, low=low1, close=close1, window=14
        ).average_true_range().iloc[-2])

        atr_pct = atr / signal_price

        # إصلاح RVOL: آخر شمعة مكتملة / متوسط 30 شمعة قبلها، وليس الشمعة الحالية الناقصة
        vol_signal = float(volume1.iloc[-2])
        vol_avg = float(volume1.iloc[-32:-2].mean())

        if vol_avg <= 0 or vol_signal <= 0:
            return None

        relative_volume = vol_signal / vol_avg
        dollar_volume = signal_price * vol_signal

        move_1m = (signal_price - float(close1.iloc[-3])) / float(close1.iloc[-3])
        move_3m = (signal_price - float(close1.iloc[-5])) / float(close1.iloc[-5])
        move_5m = (signal_price - float(close1.iloc[-7])) / float(close1.iloc[-7])
        move_15m = (signal_price - float(close1.iloc[-17])) / float(close1.iloc[-17])

        # قمة آخر 20 شمعة قبل شمعة الإشارة حتى لا نقارن السعر بنفسه
        high_20 = float(high1.iloc[-22:-2].max())
        near_breakout = signal_price >= high_20 * 0.997
        breakout_level = high_20

        last_close = float(close1.iloc[-2])
        prev_close = float(close1.iloc[-3])
        last_high = float(high1.iloc[-2])
        last_low = float(low1.iloc[-2])

        if detect_liquidity_grab(
            near_breakout, breakout_level,
            last_close, prev_close, last_high, last_low,
            move_1m, move_3m, relative_volume
        ):
            print(f"{stock}: FILTERED LIQUIDITY GRAB", flush=True)
            return None

        vwap_value = vwap(df1.tail(90).iloc[:-1])
        vwap_distance = (signal_price - vwap_value) / vwap_value

        spy_move, market_reason = get_market_move()
        relative_strength = move_5m - spy_move

        # فلاتر مطاردة: تخفف الرسائل الزبالة بدون خنق الفرص الطبيعية
        if move_1m > 0.010:
            return None
        if move_5m > 0.022:
            return None
        if move_15m > 0.045:
            return None
        if vwap_distance > 0.015:
            return None
        if rsi1 > 84:
            return None
        if relative_volume < 1.00:
            return None

        assistant_view = assistant_scalp_view(
            signal_price, high1, low1, vwap_value, vwap_distance, move_5m, rsi1
        )

        real_score, golden_setup, good_setup, aggressive_momentum, quality_label, quality_warnings = real_quality_filter(
            move_1m, move_3m, move_5m, relative_strength,
            relative_volume, adx, atr_pct, vwap_distance,
            rsi1, rsi5, signal_price, ema9_1, ema21_1, near_breakout
        )

        # أهم تعديل: ممنوع ترسل WATCH ONLY
        if not good_setup or real_score < MIN_REAL_SCORE_TO_SEND:
            return None

        score = 0
        reasons = []

        if near_breakout:
            score += 20
            reasons.append("قريب من كسر قمة آخر 20 شمعة")

        if 48 <= rsi1 <= 78:
            score += 12
            reasons.append(f"RSI 1m مناسب {rsi1:.1f}")

        if 50 <= rsi5 <= 74:
            score += 12
            reasons.append(f"RSI 5m داعم {rsi5:.1f}")

        if macd1_now > macd1_signal:
            score += 12
            reasons.append("MACD إيجابي")

        if signal_price > vwap_value and -0.001 <= vwap_distance <= 0.010:
            score += 14
            reasons.append("فوق VWAP وبمسافة مقبولة")

        if relative_volume >= 1.05:
            score += 14
            reasons.append(f"فوليوم مقبول {relative_volume:.2f}x")

        if relative_volume >= 1.50:
            score += 10
            reasons.append("فوليوم قوي")

        if signal_price > ema9_1 > ema21_1:
            score += 14
            reasons.append("ترند 1m صاعد")

        if ema9_5 > ema21_5:
            score += 8
            reasons.append("ترند 5m داعم")

        if ema9_15 > ema21_15:
            score += 6
            reasons.append("ترند 15m داعم")

        if signal_price > ema50_1:
            score += 8
            reasons.append("فوق EMA50")

        if relative_strength >= 0.0012:
            score += 14
            reasons.append("أقوى من السوق SPY/QQQ")

        if 0.0015 <= move_3m <= 0.010:
            score += 12
            reasons.append("زخم 3 دقائق صحي")

        if 0.0025 <= move_5m <= 0.015:
            score += 10
            reasons.append("زخم 5 دقائق صحي")

        if adx >= 22:
            score += 10
            reasons.append(f"ADX داعم {adx:.1f}")

        if dollar_volume > 500000:
            score += 8
            reasons.append("Dollar Volume جيد")

        if 0.0012 <= atr_pct <= 0.010:
            score += 8
            reasons.append("ATR مناسب للسكالب")

        momentum_beast = (
            aggressive_momentum
            or (
                real_score >= 7
                and relative_volume >= 1.25
                and move_3m >= 0.002
                and relative_strength >= 0.0012
            )
            or (
                near_breakout
                and relative_volume >= 1.35
                and move_1m >= 0.0008
                and move_3m >= 0.002
                and signal_price > vwap_value
                and relative_strength >= 0.0012
            )
        )

        if momentum_beast:
            score += 18
            reasons.append("🐺 Momentum Beast حقيقي: زخم + فوليوم + قوة نسبية")

        if score < MIN_SCORE_TO_SEND and not momentum_beast and not golden_setup:
            return None

        # أول 5 دقائق: لا نخنقها بالكامل، لكن نسمح فقط بالقوي جداً بعد أول دقيقتين
        mins = minutes_after_open()
        if 0 <= mins < OPENING_HARD_BLOCK_MINUTES:
            return None
        if OPENING_HARD_BLOCK_MINUTES <= mins < OPENING_CAUTION_MINUTES:
            if not (golden_setup and momentum_beast and relative_volume >= 1.5):
                return None

        if golden_setup or momentum_beast or score >= 135:
            target_pct = TARGET_GOLDEN
            alert_rank = "GOLDEN"
        elif score >= 120:
            target_pct = TARGET_STRONG
            alert_rank = "STRONG"
        else:
            target_pct = TARGET_GOOD
            alert_rank = "GOOD"

        return {
            "stock": stock,
            "price": live_price,
            "signal_price": signal_price,
            "score": score,
            "alert_rank": alert_rank,
            "target": live_price * (1 + target_pct),
            "stop": live_price * (1 - STOP_LOSS),
            "target_pct": target_pct,
            "reasons": reasons[:8],
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
            "quality_warnings": quality_warnings[:3]
        }

    except Exception as e:
        print(f"ANALYZE ERROR {stock}:", e, flush=True)
        return None

    finally:
        gc.collect()


# =========================================================
# ALERT CONTROL
# =========================================================
def should_send_alert(stock, result, now_time):
    last_time = last_alert_time.get(stock, 0)
    snapshot = last_alert_snapshot.get(stock, {})

    last_score = snapshot.get("score", 0)
    last_price = snapshot.get("price", 0)
    last_rank = snapshot.get("rank", "")
    last_beast = snapshot.get("beast", False)

    normal_ok = now_time - last_time >= ALERT_COOLDOWN

    quality_upgrade = (
        result["momentum_beast"]
        and (
            not last_beast
            or result["score"] >= last_score + 15
            or (last_rank != "GOLDEN" and result["alert_rank"] == "GOLDEN")
            or result["relative_volume"] >= 2.0
        )
    )

    continuation_base = (
        last_price > 0
        and now_time - last_time >= CONTINUATION_COOLDOWN
        and result["price"] >= last_price * (1 + CONTINUATION_STEP)
        and result["relative_volume"] >= 1.05
    )

    continuation = continuation_base and (
        result["momentum_beast"]
        or (
            result["alert_rank"] == "GOLDEN"
            and result["real_score"] >= GOLDEN_REAL_SCORE
        )
    )

    if not snapshot:
        return True, "FIRST_ALERT"

    if normal_ok:
        return True, "COOLDOWN_OK"

    if quality_upgrade:
        return True, "QUALITY_UPGRADE"

    if continuation:
        return True, "CONTINUATION"

    return False, "SKIP"


def build_message(stock, result, now_ksa, send_reason):
    av = result["assistant_view"]

    if result["alert_rank"] == "GOLDEN":
        title = "🔥🚀 V32.3 GOLDEN STOCK ALERT 🚀🔥"
        note = "فرصة ذهبية عالية الجودة"
    elif result["alert_rank"] == "STRONG":
        title = "✅🚀 V32.3 STRONG STOCK ALERT 🚀✅"
        note = "فرصة قوية بجودة مقبولة"
    else:
        title = "👀⚡ V32.3 GOOD STOCK ALERT ⚡👀"
        note = "فرصة جيدة وليست دخول أعمى"

    if send_reason == "CONTINUATION":
        reason_text = "استمرار الموجة بعد آخر تنبيه"
    elif send_reason == "QUALITY_UPGRADE":
        reason_text = "ترقية جودة/زخم"
    elif send_reason == "COOLDOWN_OK":
        reason_text = "انتهى الكولداون وما زالت الفرصة صالحة"
    else:
        reason_text = "أول تنبيه للفرصة"

    warnings_text = (
        "\n".join(["- " + w for w in result["quality_warnings"]])
        if result["quality_warnings"]
        else "- لا توجد ملاحظات خطيرة"
    )

    reasons_text = "\n".join(["- " + r for r in result["reasons"]])

    msg = f"""
{title}

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}
🔁 سبب الإرسال: {reason_text}

💰 السعر الحالي:
{result['price']:.2f}

🎯 الهدف المتوقع:
{result['target']:.2f}
({result['target_pct']*100:.2f}%)

🛑 وقف المتابعة:
{result['stop']:.2f}
({STOP_LOSS*100:.2f}%)

🔥 السكور:
{result['score']}/100

📌 النوع:
{note}

🐺 Momentum Beast:
{'YES 🔥🔥' if result['momentum_beast'] else 'NO'}

🏆 جودة الفرصة:
{result['quality_label']}

📊 Real Quality Score:
{result['real_score']}/9

📊 التحليل المختصر:
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
{warnings_text}

✅ أهم أسباب التنبيه:
{reasons_text}

🧠 رأي السكالب:
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
    return msg


# =========================================================
# START
# =========================================================
print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)

send(
    "✅ V32.3 CLEAN MOMENTUM BOT STARTED\n"
    "فلترة الرسائل الضعيفة مفعلة + استمرار الموجة مفعّل + RVOL محسوب من شمعة مكتملة"
)

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(
                f"👀 V32.3 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب الأسهم"
            )
            last_heartbeat = time.time()

        if not market_open():
            print("MARKET CLOSED - BOT ALIVE", now_ksa, flush=True)
            time.sleep(15)
            gc.collect()
            continue

        for stock in WATCHLIST:
            result = analyze(stock)

            if not result:
                gc.collect()
                continue

            now_time = time.time()
            send_ok, send_reason = should_send_alert(stock, result, now_time)

            if send_ok:
                msg = build_message(stock, result, now_ksa, send_reason)
                send(msg)
                print(msg, flush=True)

                last_alert_time[stock] = now_time
                last_alert_snapshot[stock] = {
                    "score": result["score"],
                    "price": result["price"],
                    "rank": result["alert_rank"],
                    "beast": result["momentum_beast"],
                    "real_score": result["real_score"],
                }

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
