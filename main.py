import yfinance as yf
import ta
import requests
import time
import gc
import pandas as pd
from datetime import datetime
import pytz

TOKEN = "PUT_YOUR_TELEGRAM_TOKEN_HERE"
CHAT_ID = "1016589957"

WATCHLIST = [
    "TSLA", "NVDA", "AMD",
    "AMZN", "AAPL", "MSFT"
]

CHECK_SECONDS = 30
ALERT_COOLDOWN = 1800

MIN_SEND_SCORE = 110
MIN_REAL_SCORE_TO_SEND = 5

TARGET_MID = 0.008
TARGET_MAX = 0.010
STOP_LOSS = 0.003

MAX_HISTORY_BARS = 180
OPENING_BLOCK_MINUTES = 5

last_alert_time = {}
last_alert_snapshot = {}
last_heartbeat = time.time()

market_cache = {
    "time": 0,
    "value": (0, "السوق غير واضح")
}


def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("TELEGRAM STATUS:", r.status_code, flush=True)
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


print("BOT FILE STARTED", flush=True)
print("SERVICE READY", flush=True)
send("✅ V35 STOCK BOT STARTED - ROCKET NOW + ENTRY NOW + RVOL FIX")


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


def clean_yf_columns(df, stock):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if stock in df.columns.get_level_values(-1):
                df = df.xs(stock, axis=1, level=-1)
            else:
                df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return df


def download(stock, period, interval):
    try:
        df = yf.download(
            stock,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False
        )

        df = clean_yf_columns(df, stock)

        if df is not None and not df.empty:
            df = df.tail(MAX_HISTORY_BARS).copy()

        return df

    except Exception as e:
        print(f"YAHOO DOWNLOAD ERROR {stock} {interval}:", e, flush=True)
        return pd.DataFrame()


def vwap(df):
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    valid_volume = volume.fillna(0)
    if valid_volume.sum() <= 0:
        return float(close.iloc[-1])

    typical = (high + low + close) / 3
    return float((typical * valid_volume).sum() / valid_volume.sum())


def calc_relative_volume(volume1):
    """
    إصلاح RVOL:
    - يتجاهل الشمعة الحالية لأنها أحياناً ناقصة أو صفر.
    - يستخدم آخر شمعة مكتملة غير صفرية.
    - يحسب المتوسط من آخر 30 شمعة غير صفرية.
    - إذا بيانات Yahoo سيئة يرجع 1.0 لكن يعلّمنا أن الفوليوم غير مؤكد.
    """
    try:
        vol = volume1.copy().fillna(0).astype(float)

        if len(vol) < 35:
            return 1.0, 0, 0, False

        completed = vol.iloc[:-1]
        nonzero = completed[completed > 0]

        if len(nonzero) < 10:
            return 1.0, 0, 0, False

        vol_now = float(nonzero.iloc[-1])
        recent_nonzero = nonzero.tail(30)
        vol_avg = float(recent_nonzero.mean())

        if pd.isna(vol_now) or pd.isna(vol_avg) or vol_avg <= 0:
            return 1.0, vol_now, vol_avg, False

        rvol = vol_now / vol_avg

        if rvol <= 0 or pd.isna(rvol):
            return 1.0, vol_now, vol_avg, False

        return rvol, vol_now, vol_avg, True

    except Exception:
        return 1.0, 0, 0, False


def real_quality_filter(move_1m, move_3m, move_5m, relative_strength, relative_volume, volume_confirmed, adx, atr_pct, vwap_distance, rsi1, rsi5):
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

    if move_5m > 0.003:
        real_score += 1
    else:
        warnings.append("Move 5m ضعيف")

    if relative_strength > 0.0015:
        real_score += 1
    else:
        warnings.append("القوة النسبية ضعيفة")

    if volume_confirmed and relative_volume >= 0.8:
        real_score += 1
    elif not volume_confirmed:
        warnings.append("الفوليوم غير مؤكد من Yahoo")
    else:
        warnings.append("الفوليوم ضعيف")

    if adx >= 18:
        real_score += 1
    else:
        warnings.append("ADX ضعيف")

    if 0.0015 <= atr_pct <= 0.0065:
        real_score += 1
    else:
        warnings.append("ATR غير مثالي")

    if 0.0005 <= vwap_distance <= 0.012:
        real_score += 1
    else:
        warnings.append("البعد عن VWAP غير مثالي")

    if 50 <= rsi5 <= 72:
        real_score += 1
    else:
        warnings.append("RSI 5m غير مثالي")

    aggressive_momentum = (
        real_score >= 5
        and adx >= 30
        and move_5m > 0.0035
        and relative_strength > 0.002
        and vwap_distance > 0
    )

    golden_setup = real_score >= 7 or aggressive_momentum
    good_setup = real_score >= 5

    if golden_setup:
        quality_label = "🔥 GOLDEN SETUP"
    elif good_setup:
        quality_label = "✅ GOOD SETUP"
    else:
        quality_label = "⚠️ WEAK / WATCH ONLY"

    return real_score, golden_setup, good_setup, aggressive_momentum, quality_label, warnings


def assistant_scalp_view(price, high1, low1, close1, vwap_value, vwap_distance, move_1m, move_3m, move_5m, rsi1):
    resistance = float(high1.tail(12).max())
    support = float(low1.tail(12).min())
    recent_low = float(low1.tail(8).min())

    entry = resistance * 1.0005
    stop = max(entry * 0.997, recent_low * 0.999)

    target1 = entry * 1.006
    target2 = entry * 1.010

    late_momentum = (
        rsi1 > 82
        or vwap_distance > 0.014
        or move_5m > 0.014
    )

    if late_momentum:
        status = "قوي لكن لا تطارد"
        advice = "السهم تحرك كثير. لا تدخل إلا إذا الزخم ما زال قوي أو أعطى تماسك."
    elif price >= entry:
        status = "اختراق فعلي"
        advice = "تم تجاوز نقطة الدخول. راقب استمرار الزخم والتزم بالوقف."
    elif price > vwap_value and price >= support:
        status = "قبل الانفجار"
        advice = f"الفرصة مبكرة. الدخول الهجومي مسموح فقط إذا ظهر ROCKET NOW، أو انتظر فوق {entry:.2f}."
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
        "advice": advice,
        "late_momentum": late_momentum
    }


def classify_signal(result):
    av = result["assistant_view"]

    price = result["price"]
    entry = av["entry"]

    rocket_now = (
        result["momentum_beast"]
        and result["real_score"] >= 6
        and result["relative_strength"] > 0.0025
        and result["move_1m"] >= 0.0008
        and result["move_3m"] >= 0.002
        and result["move_5m"] >= 0.003
        and 0.0003 <= result["vwap_distance"] <= 0.012
        and 50 <= result["rsi1"] <= 82
        and result["market_reason"] != "السوق ضعيف شوي" or (
            result["momentum_beast"]
            and result["aggressive_momentum"]
            and result["relative_strength"] > 0.004
            and result["move_3m"] >= 0.004
            and result["vwap_distance"] <= 0.014
            and result["rsi1"] <= 84
        )
    )

    entry_now = (
        price >= entry
        and result["real_score"] >= 6
        and result["momentum_beast"]
        and result["relative_strength"] > 0.002
        and result["move_3m"] > 0.002
        and result["vwap_distance"] > 0
        and result["rsi1"] <= 84
    )

    if entry_now:
        return "🔥 ENTRY NOW", "تم تحقق الكسر أو تجاوز نقطة الدخول. دخول تأكيدي."

    if rocket_now:
        return "🚀 ROCKET NOW", "زخم مبكر قوي قبل أو أثناء الانفجار. دخول هجومي بحجم أقل."

    return "👀 WATCH ONLY", "ليست دخول الآن. فقط مراقبة."


def market_move():
    try:
        now_time = time.time()

        if now_time - market_cache["time"] < 60:
            return market_cache["value"]

        spy = download("SPY", "1d", "1m")
        qqq = download("QQQ", "1d", "1m")

        if spy.empty or qqq.empty or len(spy) < 30 or len(qqq) < 30:
            market_cache["value"] = (0, "السوق غير واضح")
            market_cache["time"] = now_time
            return market_cache["value"]

        spy_close = spy["Close"].squeeze()
        qqq_close = qqq["Close"].squeeze()

        spy_move = (float(spy_close.iloc[-1]) - float(spy_close.iloc[-6])) / float(spy_close.iloc[-6])
        qqq_move = (float(qqq_close.iloc[-1]) - float(qqq_close.iloc[-6])) / float(qqq_close.iloc[-6])

        avg_move = (spy_move + qqq_move) / 2

        if avg_move > 0:
            value = (avg_move, "السوق داعم")
        else:
            value = (avg_move, "السوق ضعيف شوي")

        market_cache["value"] = value
        market_cache["time"] = now_time

        del spy, qqq, spy_close, qqq_close
        gc.collect()

        return value

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

        adx = ta.trend.ADXIndicator(high=high1, low=low1, close=close1, window=14).adx().iloc[-1]

        atr = ta.volatility.AverageTrueRange(high=high1, low=low1, close=close1, window=14).average_true_range().iloc[-1]
        atr_pct = float(atr) / price

        relative_volume, vol_now, vol_avg, volume_confirmed = calc_relative_volume(volume1)
        dollar_volume = price * max(vol_now, vol_avg)

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

        breakout_failed = last_high > breakout_level * 1.001 and last_close < breakout_level
        fast_rejection = move_1m < -0.002

        weak_breakout_volume = near_breakout and volume_confirmed and relative_volume < 0.70 and move_3m < 0.0015
        fake_breakout_wick = near_breakout and wick_ratio > 0.60 and move_3m < 0.0015

        liquidity_grab_detected = breakout_failed or fast_rejection or weak_breakout_volume or fake_breakout_wick

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

        late_momentum = assistant_view["late_momentum"]
        trade_timing = "⚠️ قوي لكن لا تطارد" if late_momentum else "✅ توقيت مناسب"

        real_score, golden_setup, good_setup, aggressive_momentum, quality_label, quality_warnings = real_quality_filter(
            move_1m, move_3m, move_5m,
            relative_strength, relative_volume, volume_confirmed,
            adx, atr_pct, vwap_distance,
            rsi1, rsi5
        )

        if move_1m > 0.014:
            return None
        if move_5m > 0.025:
            return None
        if move_15m > 0.050:
            return None
        if vwap_distance > 0.020:
            return None
        if rsi1 > 86:
            return None
        if rsi5 < 35:
            return None
        if vwap_distance < -0.003:
            return None

        score = 0
        reasons = []

        if near_breakout:
            score += 20
            reasons.append("قريب من كسر قمة آخر 20 شمعة")

        score += 10
        reasons.append("الاختراق يبدو حقيقي وليس Liquidity Grab")

        if 45 <= rsi1 <= 75:
            score += 15
            reasons.append(f"RSI يجهز {rsi1:.1f}")

        if macd1_now > macd1_signal:
            score += 15
            reasons.append("MACD بدأ يعطي إيجابية")

        if price > vwap_value and -0.002 <= vwap_distance <= 0.012:
            score += 15
            reasons.append("فوق VWAP وقريب منه")

        if volume_confirmed and relative_volume >= 1.05:
            score += 15
            reasons.append(f"فوليوم بدأ يزيد {relative_volume:.2f}x")
        elif not volume_confirmed:
            reasons.append("⚠️ بيانات الفوليوم غير مؤكدة من Yahoo")

        if volume_confirmed and relative_volume >= 1.5:
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

        if 0.0005 <= move_3m <= 0.010:
            score += 15
            reasons.append("زخم مبكر آخر 3 دقائق")

        if 0 <= move_5m <= 0.015:
            score += 10
            reasons.append("حركة صحية بدون مطاردة")

        if adx >= 18:
            score += 10
            reasons.append(f"ADX جيد {adx:.1f}")

        if dollar_volume > 500000:
            score += 10
            reasons.append("Dollar Volume مقبول")

        if 0.0015 <= atr_pct <= 0.018:
            score += 10
            reasons.append("تذبذب مناسب للسكالب")

        momentum_beast = (
            real_score >= 6
            and price > vwap_value
            and move_3m > 0.0015
            and relative_strength > 0.001
            and 45 <= rsi1 <= 82
        ) or aggressive_momentum

        if momentum_beast:
            score += 20
            reasons.append("🔥 Momentum Beast: زخم قوي بجودة مقبولة")

        if late_momentum:
            reasons.append("⚠️ Late Momentum: السهم تحرك كثير، لا تطارد إلا إذا التصنيف ROCKET/ENTRY واضح")

        target_pct = TARGET_MAX if golden_setup else TARGET_MID

        result = {
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
            "volume_confirmed": volume_confirmed,
            "vol_now": vol_now,
            "vol_avg": vol_avg,
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
            "quality_warnings": quality_warnings,
            "late_momentum": late_momentum,
            "trade_timing": trade_timing
        }

        signal_type, signal_reason = classify_signal(result)
        result["signal_type"] = signal_type
        result["signal_reason"] = signal_reason

        print(
            f"{stock} RVOL DEBUG | confirmed={volume_confirmed} | vol_now={vol_now:.0f} | vol_avg={vol_avg:.0f} | rvol={relative_volume:.2f}x | signal={signal_type}",
            flush=True
        )

        del df1, df5, df15
        gc.collect()
        return result

    except Exception as e:
        print(f"ANALYZE ERROR {stock}:", e, flush=True)
        return None

    finally:
        gc.collect()


send(
    "🚀 V35 STOCK SCANNER ONLINE 🚀\n"
    "✅ ROCKET NOW added\n"
    "✅ ENTRY NOW added\n"
    "✅ RVOL fixed using non-zero completed candles\n"
    "✅ Yahoo volume confirmation added\n"
    "✅ Rocket/Entry cooldown upgrade enabled"
)

while True:
    try:
        saudi = pytz.timezone("Asia/Riyadh")
        now_ksa = datetime.now(saudi).strftime("%H:%M:%S")

        if time.time() - last_heartbeat >= 3600:
            send(
                f"👀 V35 STOCK BOT STILL RUNNING\n"
                f"⏰ KSA: {now_ksa}\n"
                f"📡 البوت حي ويراقب الأسهم"
            )
            last_heartbeat = time.time()

        if not market_open():
            print("MARKET CLOSED - BOT ALIVE", now_ksa, flush=True)
            time.sleep(30)
            gc.collect()
            continue

        if first_5_minutes_after_open():
            print("⏳ أول 5 دقائق من الافتتاح - تجاهل التنبيهات", now_ksa, flush=True)
            time.sleep(30)
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

            if not result["good_setup"]:
                print(f"{stock}: SKIPPED - NOT GOOD SETUP", flush=True)
                del result
                gc.collect()
                continue

            if result["real_score"] < MIN_REAL_SCORE_TO_SEND:
                print(f"{stock}: SKIPPED - REAL SCORE LOW {result['real_score']}/9", flush=True)
                del result
                gc.collect()
                continue

            if score < MIN_SEND_SCORE:
                print(f"{stock}: SKIPPED - SCORE LOW {score}", flush=True)
                del result
                gc.collect()
                continue

            if result["signal_type"] == "👀 WATCH ONLY":
                print(f"{stock}: SKIPPED - WATCH ONLY", flush=True)
                del result
                gc.collect()
                continue

            normal_cooldown_ok = now_time - last_time >= ALERT_COOLDOWN

            snapshot = last_alert_snapshot.get(stock, {})
            last_price = snapshot.get("price", 0)
            last_real_score = snapshot.get("real_score", 0)
            last_golden = snapshot.get("golden", False)
            last_signal = snapshot.get("signal_type", "")

            signal_upgrade = (
                last_signal == "🚀 ROCKET NOW"
                and result["signal_type"] == "🔥 ENTRY NOW"
            )

            elite_upgrade = (
                result["golden_setup"]
                and not last_golden
                and result["real_score"] >= last_real_score + 1
            )

            big_price_continuation = (
                result["golden_setup"]
                and last_price > 0
                and result["price"] >= last_price * 1.008
            )

            if normal_cooldown_ok or signal_upgrade or elite_upgrade or big_price_continuation:
                if result["signal_type"] == "🚀 ROCKET NOW":
                    title = "🚀🐺 V35 ROCKET NOW ALERT 🐺🚀"
                    note = "انفجار مبكر - دخول هجومي بحجم أقل"
                else:
                    title = "🔥✅ V35 ENTRY NOW ALERT ✅🔥"
                    note = "دخول تأكيدي بعد تحقق الشرط"

                quality_warnings_text = (
                    chr(10).join(["- " + w for w in result["quality_warnings"]])
                    if result["quality_warnings"]
                    else "- القيم ممتازة"
                )

                volume_text = "مؤكد ✅" if result["volume_confirmed"] else "غير مؤكد من Yahoo ⚠️"

                msg = f"""
{title}

📈 السهم: {stock}
⏰ الوقت KSA: {now_ksa}

🚦 القرار النهائي:
{result['signal_type']}

📌 سبب القرار:
{result['signal_reason']}

📌 النوع:
{note}

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

⏱️ توقيت الصفقة:
{result['trade_timing']}

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
- Volume Data: {volume_text}
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

🧠 خطة السكالب:
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
                last_alert_snapshot[stock] = {
                    "score": score,
                    "price": result["price"],
                    "real_score": result["real_score"],
                    "golden": result["golden_setup"],
                    "signal_type": result["signal_type"]
                }

            del result
            gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        print("ERROR:", e, flush=True)
        gc.collect()
        time.sleep(30)
