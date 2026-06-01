print("🔥 THIS IS MY REAL FILE V32 🔥", flush=True)
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
    "TSLA", "NVDA", "AMD", "AVGO",
    "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 15
ALERT_COOLDOWN = 300  # 5 دقائق
PROGRESS_ALERT_STEP = 0.005  # إعادة تنبيه كل +0.5% من أول تنبيه لنفس الموجة
MIN_REAL_SCORE_TO_SEND = 5  # منع فرص WEAK / WATCH ONLY
MIN_SECONDS_BETWEEN_PROGRESS_ALERTS = 45  # يمنع تكرار سريع جداً داخل نفس الموجة

EARLY_SCORE = 55
STRONG_SCORE = 80

TARGET_MIN = 0.006
TARGET_MID = 0.008
TARGET_MAX = 0.010
STOP_LOSS = 0.003

MAX_HISTORY_BARS = 300
OPENING_BLOCK_MINUTES = 2  # فقط أول دقيقتين، وبعدها Momentum Beast مسموح

last_alert_time = {}
last_alert_snapshot = {}
last_heartbeat = time.time()


# =========================
# FLEXIBLE QUALITY FILTER
# =========================
def real_quality_filter(move_1m, move_3m, move_5m, relative_strength, relative_volume, adx, atr_pct, vwap_distance, rsi1, rsi5):
    real_score = 0
    warnings = []

    if move_1m > 0.0008:
        real_score += 1
    else:
        warnings.append("Move 1m ضعيف أو سلبي")

    if move_3m > 0.002:
        real_score += 1
    else:
        warnings.append("Move 3m ضعيف")

    if move_5m > 0.0035:
        real_score += 1
    else:
        warnings.append("Move 5m ضعيف")

    if relative_strength > 0.0015:
        real_score += 1
    else:
        warnings.append("القوة النسبية ضعيفة")

    if relative_volume >= 0.8:
        real_score += 1
    else:
        warnings.append("الفوليوم ضعيف")

    if adx >= 25:
        real_score += 1
    else:
        warnings.append("ADX ضعيف")

    if 0.0015 <= atr_pct <= 0.006:
        real_score += 1
    else:
        warnings.append("ATR غير مثالي")

    if 0.001 <= vwap_distance <= 0.012:
        real_score += 1
    else:
        warnings.append("البعد عن VWAP غير مثالي")

    if 55 <= rsi5 <= 72:
        real_score += 1
    else:
        warnings.append("RSI 5m غير مثالي")

    aggressive_momentum = (
        adx >= 32
        and move_5m > 0.0035
        and relative_strength > 0.002
    )

    golden_setup = real_score >= 6 or aggressive_momentum
    good_setup = real_score >= 5 or aggressive_momentum

    if golden_setup:
        quality_label = "🔥 GOLDEN SETUP"
    elif good_setup:
        quality_label = "✅ GOOD SETUP"
    else:
        quality_label = "⚠️ WEAK / WATCH ONLY"

    return real_score, golden_setup, good_setup, aggressive_momentum, quality_label, warnings


def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)
send("✅ V32.2 MOMENTUM BEAST STOCK BOT STARTED")


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


def progress_stage(base_price, current_price):
    """
    يحسب مرحلة استمرار الصعود من أول سعر تنبيه.
    مثال: 500 -> 502.5 = Stage 1، ثم 505 = Stage 2.
    """
    if base_price <= 0 or current_price <= base_price:
        return 0
    gain = (current_price - base_price) / base_price
    return int(gain // PROGRESS_ALERT_STEP)


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

        fast_rejection = (
            move_1m < -0.002
        )

        weak_breakout_volume = (
            near_breakout
            and relative_volume < 0.85
            and move_3m < 0.0015
        )

        fake_breakout_wick = (
            near_breakout
            and wick_ratio > 0.55
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

        # لا نرسل فرص WEAK نهائياً، إلا لو فيه Momentum Aggressive حقيقي
        if real_score < MIN_REAL_SCORE_TO_SEND and not aggressive_momentum:
            return None

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

        if not liquidity_grab_detected:
            score += 10
            reasons.append("الاختراق يبدو حقيقي وليس Liquidity Grab")

        if 45 <= rsi1 <= 72:
            score += 15
            reasons.append(f"RSI يجهز {rsi1:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD بدأ يعطي إيجابية")

        if price > vwap_value and -0.002 <= vwap_distance <= 0.0075:
            score += 15
            reasons.append("فوق VWAP وقريب منه")

        if relative_volume >= 1.05:
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

        if 0.0005 <= move_3m <= 0.008:
            score += 15
            reasons.append("زخم مبكر آخر 3 دقائق")

        if 0 <= move_5m <= 0.012:
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

        momentum_beast = (
            aggressive_momentum
            or real_score >= 7
            or score >= 115
            or (
                relative_volume >= 1.7
                and move_3m >= 0.0025
                and relative_strength >= 0.0015
            )
            or (
                near_breakout
                and move_1m >= 0.001
                and move_3m >= 0.002
                and price > vwap_value
            )
        )

        if momentum_beast:
            score += 20
            reasons.append("🔥 Momentum Beast: انفجار محتمل مبكر")

        alert_type = "EARLY" if score < STRONG_SCORE else "STRONG"

        if momentum_beast or real_score >= 7 or score >= 115:
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


send(
    "🚀 V32.2 MOMENTUM BEAST STOCK SCANNER ONLINE 🚀\n"
    "👀 Early Alert + 🔥 Strong Alert + 🐺 Momentum Beast + استمرار تنبيه كل +1% + منع WEAK + Dynamic Target 0.6%-1%"
)

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(
                f"👀 V32 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب الأسهم"
            )
            last_heartbeat = time.time()

        if not market_open():
            print("MARKET CLOSED - BOT ALIVE", now_ksa, flush=True)
            time.sleep(15)
            gc.collect()
            continue

        if first_minutes_after_open():
            print("⏳ أول دقيقتين من الافتتاح - تجاهل التنبيهات", now_ksa, flush=True)
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
            base_price = snapshot.get("base_price", result["price"])
            last_stage = snapshot.get("progress_stage", 0)
            last_progress_time = snapshot.get("last_progress_time", 0)

            beast_now = result["momentum_beast"]

            normal_cooldown_ok = now_time - last_time >= ALERT_COOLDOWN

            strong_upgrade = (
                beast_now
                and (
                    not last_beast
                    or score >= last_score + 15
                    or result["price"] >= last_price * 1.003
                    or result["relative_volume"] >= 2.0
                )
            )

            current_stage = progress_stage(base_price, result["price"])
            progress_continuation = (
                snapshot
                and current_stage > last_stage
                and now_time - last_progress_time >= MIN_SECONDS_BETWEEN_PROGRESS_ALERTS
                and (result["good_setup"] or beast_now)
                and result["price"] > last_price
            )

            should_send = (
                score >= EARLY_SCORE
                and result["good_setup"]
                and (normal_cooldown_ok or strong_upgrade or progress_continuation)
            )

            if should_send:
                if progress_continuation:
                    title = "🚀🔁 V32.2 CONTINUATION STOCK ALERT 🔁🚀"
                    stage_pct = current_stage * PROGRESS_ALERT_STEP * 100
                    note = f"استمرار موجة الصعود - مرحلة +{stage_pct:.1f}% من أول تنبيه"
                elif result["alert_type"] == "STRONG":
                    title = "🔥🚀 V32.2 STRONG STOCK ALERT 🚀🔥"
                    note = "فرصة قوية الآن"
                else:
                    title = "👀⚡ V32.2 EARLY STOCK ALERT ⚡👀"
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
- ملاحظة: {av['advice']}

⚠️ تنبيه فقط، القرار النهائي عليك.
"""

                send(msg)
                print(msg, flush=True)

                last_alert_time[stock] = now_time

                if not snapshot or normal_cooldown_ok:
                    base_price_to_store = result["price"]
                    stage_to_store = 0
                else:
                    base_price_to_store = base_price
                    stage_to_store = max(last_stage, current_stage)

                last_alert_snapshot[stock] = {
                    "score": score,
                    "price": result["price"],
                    "beast": beast_now,
                    "base_price": base_price_to_store,
                    "progress_stage": stage_to_store,
                    "last_progress_time": now_time if progress_continuation else last_progress_time
                }

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
