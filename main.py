"""
=============================================================
SCALP BOT PRO v3 - upgraded
=============================================================
"""
import os
import yfinance as yf
import ta
import requests
import time
import gc
import pandas as pd
from datetime import datetime, date
import pytz
import logging

try:
    import daily_summary as ds
    DS_AVAILABLE = True
except Exception:
    DS_AVAILABLE = False
    class _DS:
        def log_filtered(self): pass
        def log_alert(self, **kw): pass
        def should_send_summary(self): return False
        def send_summary(self, fn): pass
    ds = _DS()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("ScalpBot")

TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "1016589957")
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY")
log.info(f"Claude Key: {bool(CLAUDE_KEY)}")
WATCHLIST        = ["TSLA", "NVDA", "AMD", "SMCI", "PLTR"]
CHECK_SECONDS    = 20
ALERT_COOLDOWN   = 600
MIN_SCORE        = 80
MIN_REAL_SCORE   = 5        # رفعناه لـ 5
TARGET_NORMAL    = 0.008
TARGET_GOLDEN    = 0.012
STOP_LOSS        = 0.003
OPENING_BLOCK    = 5
MAX_HISTORY_BARS = 200

POWER_SESSIONS = [
    ("09:35", "11:00"),
    ("15:00", "15:55"),
]

EXPLOSION_LOOKBACK = 5
EXPLOSION_VOL_MULT = 2.0
EXPLOSION_MOVE_MIN = 0.004

last_alert_time     = {}
last_alert_snapshot = {}
last_heartbeat      = time.time()
market_cache        = {"time": 0, "value": (0.0, "")}
followup_tracking   = {}


# ─── TELEGRAM ───────────────────────────────────────────────
def send(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code != 200:
            log.warning(f"Telegram error: {r.status_code} - {r.text[:100]}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


# ─── CLAUDE AI ──────────────────────────────────────────────
def claude_analyze(r: dict) -> str:
    try:
        if not CLAUDE_KEY:
            return "⚠️ No Key"    
        prompt = f"""أنت محلل سكالبينج خبير. حلل هذه الفرصة في سطرين فقط باللغة العربية:

السهم: {r['stock']} | السعر: {r['price']:.2f}
النمط: {r['pattern']} | الجودة: {r['quality_label']}
RSI: {r['rsi1']:.1f} | ADX: {r['adx']:.1f} | RVol: {r['rel_vol']:.2f}x
VWAP: {r['vwap_dist']*100:.2f}% | Move5m: {r['move_5m']*100:.2f}%
R:R={r['levels']['rr']}x | MACD Cross: {r['macd_cross']}
تحذيرات: {', '.join(r['warnings']) if r['warnings'] else 'لا يوجد'}

قرارك في سطر واحد فقط:
✅ ادخل - سبب
⚠️ انتظر - سبب
❌ تجاهل - سبب"""

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 250,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        if response.status_code == 200:
            return response.json()["content"][0]["text"].strip()
        log.error(f"Claude API error: {response.status_code} - {response.text[:200]}")
        return f"API error {response.status_code}"
    except Exception as e:
        log.error(f"Claude error: {e}")
        return f"خطأ: {e}"


# ─── صياد الانفجارات ────────────────────────────────────────
def detect_explosion(close1, volume1, high1, low1) -> dict:
    try:
        vol_recent = float(volume1.iloc[-1])
        vol_avg    = float(volume1.iloc[-10:-1].mean())
        vol_surge  = vol_recent / vol_avg if vol_avg > 0 else 0

        recent_high  = float(high1.tail(EXPLOSION_LOOKBACK).max())
        recent_low   = float(low1.tail(EXPLOSION_LOOKBACK).min())
        price_now    = float(close1.iloc[-1])
        compression  = (recent_high - recent_low) / price_now

        move_1 = (float(close1.iloc[-1]) - float(close1.iloc[-2])) / float(close1.iloc[-2])
        move_2 = (float(close1.iloc[-1]) - float(close1.iloc[-3])) / float(close1.iloc[-3])
        acc    = move_1 > move_2 * 1.5

        # نقارن بأعلى الشموع السابقة (مو الحالية) - تحسين #3
        high_15  = float(high1.iloc[-16:-1].max())
        breakout = price_now >= high_15 * 0.998

        explosion_score = 0
        signals = []

        if vol_surge >= EXPLOSION_VOL_MULT:
            explosion_score += 3
            signals.append(f"💥 حجم {vol_surge:.1f}x")
        elif vol_surge >= 1.5:
            explosion_score += 1
            signals.append(f"📊 حجم {vol_surge:.1f}x")

        if compression < 0.008:
            explosion_score += 2
            signals.append("🔒 ضغط سعري")

        if acc:
            explosion_score += 2
            signals.append("⚡ تسارع")

        if breakout:
            explosion_score += 3
            signals.append("🚀 كسر مقاومة")

        if move_2 > EXPLOSION_MOVE_MIN:
            explosion_score += 2
            signals.append(f"📈 {move_2*100:.2f}%")

        return {
            "score": explosion_score,
            "signals": signals,
            "vol_surge": vol_surge,
            "is_explosion": explosion_score >= 5
        }
    except Exception as e:
        log.error(f"Explosion error: {e}")
        return {"score": 0, "signals": [], "vol_surge": 0, "is_explosion": False}


# ─── متابعة ذكية ────────────────────────────────────────────
def check_followup(stock: str, cur: float, track: dict, close1, volume1):
    """يرسل فقط عند +0.4% أو +0.7% أو هدف أو وقف"""
    try:
        entry  = track["entry"]
        target = track["target"]
        stop   = track["stop"]
        gain   = (cur - entry) / entry * 100

        # وقف أو هدف
        if cur <= stop:
            loss = (cur - entry) / entry * 100
            del followup_tracking[stock]
            return f"🛑 <b>وقف خسارة - {stock}</b>\n💵 {cur:.2f} | {loss:.2f}%"
        if cur >= target:
            profit = (cur - entry) / entry * 100
            del followup_tracking[stock]
            return f"🎯 <b>هدف محقق - {stock}</b>\n💵 {cur:.2f} | +{profit:.2f}%"

        # مستويات المتابعة الذكية
        milestones = track.get("milestones_sent", set())
        msg = None

        if gain >= 0.7 and "0.7" not in milestones:
            milestones.add("0.7")
            progress = (cur - entry) / (target - entry) * 100 if target > entry else 0
            bar = "🟩" * int(progress/20) + "⬜" * (5 - int(progress/20))
            msg = f"📈 <b>متابعة +0.7% - {stock}</b>\n💵 {cur:.2f} | {bar} {progress:.0f}% من الهدف\n🎯 {target:.2f} | 🛑 {stop:.2f}"
        elif gain >= 0.4 and "0.4" not in milestones:
            milestones.add("0.4")
            msg = f"📊 <b>متابعة +0.4% - {stock}</b>\n💵 {cur:.2f} | 🎯 {target:.2f} | 🛑 {stop:.2f}"

        if milestones != track.get("milestones_sent", set()):
            followup_tracking[stock]["milestones_sent"] = milestones

        return msg
    except:
        return None


# ─── TIME ────────────────────────────────────────────────────
def get_ny_time():
    return datetime.now(pytz.timezone("America/New_York"))

def get_ksa_time():
    return datetime.now(pytz.timezone("Asia/Riyadh")).strftime("%H:%M:%S")

def market_open() -> bool:
    now = get_ny_time()
    if now.weekday() >= 5: return False
    t = now.strftime("%H:%M")
    return "09:30" <= t <= "15:55"

def is_opening_block() -> bool:
    now = get_ny_time()
    if now.weekday() >= 5: return False
    t = now.strftime("%H:%M")
    return "09:30" <= t < f"09:{30 + OPENING_BLOCK}"

def is_power_session() -> bool:
    t = get_ny_time().strftime("%H:%M")
    for s, e in POWER_SESSIONS:
        if s <= t <= e: return True
    return False

def session_label() -> str:
    t = get_ny_time().strftime("%H:%M")
    if "09:35" <= t <= "11:00": return "⚡ فتح"
    if "15:00" <= t <= "15:55": return "⚡ إغلاق"
    return "عادي"


# ─── DATA ────────────────────────────────────────────────────
def clean_columns(df, stock):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if stock in df.columns.get_level_values(-1):
                df = df.xs(stock, axis=1, level=-1)
            else:
                df.columns = df.columns.get_level_values(0)
    except: pass
    return df

def download(stock: str, period: str, interval: str) -> pd.DataFrame:
    try:
        df = yf.download(stock, period=period, interval=interval,
                         progress=False, auto_adjust=True, threads=False)
        df = clean_columns(df, stock)
        if df is not None and not df.empty:
            df = df.tail(MAX_HISTORY_BARS).copy()
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        log.error(f"Download error {stock}: {e}")
        return pd.DataFrame()

def calc_vwap(df):
    try:
        ny = pytz.timezone("America/New_York")
        today_open = datetime.now(ny).replace(hour=9, minute=30, second=0, microsecond=0)
        df_today = df[df.index >= (today_open.replace(tzinfo=None) if df.index.tz is None else today_open)]
        if len(df_today) < 2: df_today = df.tail(30)
        h, l, c, v = df_today["High"].squeeze(), df_today["Low"].squeeze(), df_today["Close"].squeeze(), df_today["Volume"].squeeze()
        if v.sum() <= 0: return float(c.iloc[-1])
        return float(((h+l+c)/3 * v).sum() / v.sum())
    except:
        return float(df["Close"].squeeze().iloc[-1])

def market_move():
    try:
        now_time = time.time()
        if now_time - market_cache["time"] < 60:
            return market_cache["value"]
        spy = download("SPY", "1d", "1m")
        qqq = download("QQQ", "1d", "1m")
        if spy.empty or qqq.empty or len(spy) < 10:
            market_cache.update({"value": (0.0, "السوق غير معروف"), "time": now_time})
            return market_cache["value"]
        sc, qc = spy["Close"].squeeze(), qqq["Close"].squeeze()
        sm = (float(sc.iloc[-1]) - float(sc.iloc[-6])) / float(sc.iloc[-6])
        qm = (float(qc.iloc[-1]) - float(qc.iloc[-6])) / float(qc.iloc[-6])
        avg = (sm + qm) / 2
        label = "🟢 السوق صاعد" if avg > 0.0003 else ("🔴 السوق ضعيف" if avg < -0.0003 else "⚪ السوق محايد")
        value = (avg, label)
        market_cache.update({"value": value, "time": now_time})
        del spy, qqq; gc.collect()
        return value
    except Exception as e:
        log.error(f"Market move error: {e}")
        return 0.0, "السوق غير معروف"


# ─── QUALITY FILTER ─────────────────────────────────────────
def real_quality_filter(move_1m, move_3m, move_5m, rel_strength,
                        rel_volume, adx, atr_pct, vwap_dist, rsi1, rsi5, power):
    score, warnings = 0, []
    m1min  = 0.0005 if power else 0.0008
    m3min  = 0.0015 if power else 0.002
    m5min  = 0.002  if power else 0.003
    rvmin  = 0.7    if power else 0.85

    if move_1m > m1min: score += 1
    else: warnings.append("زخم 1د ضعيف")
    if move_3m > m3min: score += 1
    else: warnings.append("زخم 3د ضعيف")
    if move_5m > m5min: score += 1
    else: warnings.append("زخم 5د ضعيف")
    if rel_strength > 0.001: score += 1
    else: warnings.append("قوة نسبية ضعيفة")
    if rel_volume >= rvmin: score += 1
    else: warnings.append("حجم ضعيف")
    if adx >= 18: score += 1
    else: warnings.append("ADX ضعيف")
    if 0.001 <= atr_pct <= 0.025: score += 1
    else: warnings.append("ATR خارج النطاق")
    if -0.001 <= vwap_dist <= 0.012: score += 1
    else: warnings.append("VWAP بعيد")
    if 45 <= rsi5 <= 75: score += 1
    else: warnings.append("RSI5 خارج النطاق")

    aggressive = (score >= 5 and adx >= 28 and move_5m > 0.003
                  and rel_strength > 0.0015 and vwap_dist > 0)
    golden = score >= 7 or aggressive  # Golden الحقيقي من 7/9
    good   = score >= 5

    label = "🥇 GOLDEN" if golden else ("✅ GOOD" if good else "⚠️ WEAK")
    return score, golden, good, aggressive, label, warnings


def detect_entry_pattern(close1, high1, low1, price, vwap_value, ema9, ema21):
    # نقارن بأعلى الشموع السابقة مو الحالية - تحسين #3
    high_20    = float(high1.iloc[-21:-1].max())
    near_break = price >= high_20 * 0.997
    above_vwap = price > vwap_value
    ema_bull   = price > ema9 > ema21
    rr         = float(high1.tail(8).max()) - float(low1.tail(8).min())
    pr         = float(high1.tail(20).max()) - float(low1.tail(20).min())
    tight      = rr < pr * 0.35

    if near_break and tight and above_vwap:   return "🔥 كسر تراكم", 25
    elif near_break and above_vwap and ema_bull: return "🚀 كسر + ترند", 20
    elif above_vwap and ema_bull:             return "📈 زخم مستمر", 15
    elif above_vwap and near_break:           return "💧 كسر VWAP", 15
    else:                                     return "👀 مراقبة", 5


def scalp_levels(price, high1, low1, vwap_value, golden):
    resistance = float(high1.tail(15).max())
    support    = float(low1.tail(15).min())
    recent_low = float(low1.tail(6).min())
    entry  = max(resistance * 1.0003, price)
    stop   = max(min(support, recent_low) * 0.9985, price * (1 - STOP_LOSS))
    target = price * (1 + (TARGET_GOLDEN if golden else TARGET_NORMAL))
    risk   = entry - stop
    reward = target - entry
    rr     = round(reward / risk, 2) if risk > 0 else 0
    return {"entry": round(entry,2), "stop": round(stop,2), "target": round(target,2),
            "support": round(support,2), "resistance": round(resistance,2), "rr": rr}


# ─── ANALYZE ────────────────────────────────────────────────
def analyze(stock: str):
    try:
        df1  = download(stock, "2d",  "1m")
        df5  = download(stock, "5d",  "5m")
        df15 = download(stock, "10d", "15m")
        if df1.empty or df5.empty or df15.empty or len(df1) < 80:
            return None

        close1  = df1["Close"].squeeze()
        high1   = df1["High"].squeeze()
        low1    = df1["Low"].squeeze()
        volume1 = df1["Volume"].squeeze()
        close5  = df5["Close"].squeeze()
        close15 = df15["Close"].squeeze()
        price   = float(close1.iloc[-1])

        ema9_1   = float(ta.trend.EMAIndicator(close1,  window=9 ).ema_indicator().iloc[-1])
        ema21_1  = float(ta.trend.EMAIndicator(close1,  window=21).ema_indicator().iloc[-1])
        ema50_1  = float(ta.trend.EMAIndicator(close1,  window=50).ema_indicator().iloc[-1])
        ema9_5   = float(ta.trend.EMAIndicator(close5,  window=9 ).ema_indicator().iloc[-1])
        ema21_5  = float(ta.trend.EMAIndicator(close5,  window=21).ema_indicator().iloc[-1])
        ema9_15  = float(ta.trend.EMAIndicator(close15, window=9 ).ema_indicator().iloc[-1])
        ema21_15 = float(ta.trend.EMAIndicator(close15, window=21).ema_indicator().iloc[-1])

        rsi1 = float(ta.momentum.RSIIndicator(close1, window=14).rsi().iloc[-1])
        rsi5 = float(ta.momentum.RSIIndicator(close5, window=14).rsi().iloc[-1])

        macd      = ta.trend.MACD(close1)
        macd_now  = float(macd.macd().iloc[-1])
        macd_sig  = float(macd.macd_signal().iloc[-1])
        macd_hist = macd_now - macd_sig
        macd_prev = float(macd.macd().iloc[-2])
        macd_cross = macd_now > macd_sig and macd_prev <= float(macd.macd_signal().iloc[-2])

        adx     = float(ta.trend.ADXIndicator(high=high1, low=low1, close=close1, window=14).adx().iloc[-1])
        atr     = float(ta.volatility.AverageTrueRange(high=high1, low=low1, close=close1, window=14).average_true_range().iloc[-1])
        atr_pct = atr / price

        if len(volume1) < 32: return None
        vol_last   = float(volume1.iloc[-2])
        vol_avg    = float(volume1.iloc[-32:-2].mean())
        rel_vol    = (vol_last / vol_avg) if vol_avg > 0 else 0.0
        dollar_vol = price * vol_last

        move_1m  = (price - float(close1.iloc[-2]))  / float(close1.iloc[-2])
        move_3m  = (price - float(close1.iloc[-4]))  / float(close1.iloc[-4])
        move_5m  = (price - float(close1.iloc[-6]))  / float(close1.iloc[-6])

        vwap_value = calc_vwap(df1)
        vwap_dist  = (price - vwap_value) / vwap_value

        spy_move, market_label = market_move()
        rel_strength = move_5m - spy_move
        momentum_acc = move_1m > (price - float(close1.iloc[-3])) / float(close1.iloc[-3])

        explosion = detect_explosion(close1, volume1, high1, low1)

        power = is_power_session()

        # ─── فلاتر محسّنة ───────────────────────────────────
        max_rsi1    = 82                               # #7 RSI أقوى
        max_move_1m = 0.030 if power else 0.022
        max_move_5m = 0.050 if power else 0.038
        max_vwap    = 0.016 if power else 0.012        # #6 منع الفرص المتأخرة

        if rsi1 > max_rsi1:       ds.log_filtered(); log.info(f"{stock}: FILTERED RSI={rsi1:.1f}"); return None
        if move_1m > max_move_1m: ds.log_filtered(); return None
        if move_5m > max_move_5m: ds.log_filtered(); return None
        if vwap_dist > max_vwap:  ds.log_filtered(); log.info(f"{stock}: FILTERED VWAP متأخر {vwap_dist*100:.2f}%"); return None
        if vwap_dist < -0.004:    ds.log_filtered(); return None
        if rsi5 < 30:             ds.log_filtered(); return None
        if dollar_vol < 100000:   ds.log_filtered(); return None

        # ─── فلتر الفشل المحسّن #7 ──────────────────────────
        high_20      = float(high1.iloc[-21:-1].max())   # الشموع السابقة فقط
        near_break   = price >= high_20 * 0.997
        last_high    = float(high1.iloc[-1])
        last_close   = float(close1.iloc[-1])
        prev_close   = float(close1.iloc[-2])
        candle_range = max(last_high - float(low1.iloc[-1]), 0.0001)
        upper_wick   = last_high - max(last_close, prev_close)
        wick_ratio   = upper_wick / candle_range

        breakout_failed = last_high > high_20 * 1.001 and last_close < high_20
        fast_rejection  = move_1m < -0.0025
        weak_break_vol  = near_break and rel_vol < 0.8 and move_3m < 0.001
        fake_wick_break = near_break and wick_ratio > 0.55 and move_3m < 0.001  # أشد
        high_wick       = wick_ratio > 0.65                                      # wick عالي

        if breakout_failed or fast_rejection or weak_break_vol or fake_wick_break or high_wick:
            ds.log_filtered(); log.info(f"{stock}: FILTERED فشل/wick"); return None

        real_score, golden, good, aggressive, quality_label, warnings = real_quality_filter(
            move_1m, move_3m, move_5m, rel_strength,
            rel_vol, adx, atr_pct, vwap_dist, rsi1, rsi5, power
        )
        pattern_label, pattern_score = detect_entry_pattern(
            close1, high1, low1, price, vwap_value, ema9_1, ema21_1
        )

        score, reasons = 0, []
        score += pattern_score; reasons.append(pattern_label)

        if price > ema9_1 > ema21_1:  score += 15; reasons.append("ترند 1د ✓")
        if ema9_5 > ema21_5:          score += 10; reasons.append("ترند 5د ✓")
        if ema9_15 > ema21_15:        score += 8;  reasons.append("ترند 15د ✓")
        if price > ema50_1:           score += 8;  reasons.append("فوق EMA50")
        if 48 <= rsi1 <= 70:          score += 12; reasons.append(f"RSI {rsi1:.0f} ✓")
        elif 70 < rsi1 <= 78:         score += 6;  reasons.append(f"RSI {rsi1:.0f} مرتفع")
        if macd_cross:                score += 18; reasons.append("MACD Cross 🔔")
        elif macd_now > macd_sig and macd_hist > 0: score += 10; reasons.append("MACD صاعد")
        if price > vwap_value and 0.0003 <= vwap_dist <= 0.008: score += 15; reasons.append("فوق VWAP ✓")
        if rel_vol >= 2.0:            score += 20; reasons.append(f"حجم {rel_vol:.1f}x 🔥")
        elif rel_vol >= 1.3:          score += 15; reasons.append(f"حجم {rel_vol:.1f}x")
        elif rel_vol >= 1.0:          score += 8;  reasons.append(f"حجم {rel_vol:.1f}x")
        if rel_strength > 0.002:      score += 15; reasons.append("قوي vs SPY")
        elif rel_strength > 0.001:    score += 8;  reasons.append("OK vs SPY")
        if adx >= 30:                 score += 12; reasons.append(f"ADX {adx:.0f} 💪")
        elif adx >= 20:               score += 7;  reasons.append(f"ADX {adx:.0f}")
        if momentum_acc and move_1m > 0.001: score += 10; reasons.append("زخم متسارع ⚡")
        if power:                     score += 10; reasons.append(session_label())
        if golden:                    score += 15; reasons.append("Golden ✓")
        if explosion["is_explosion"]: score += 10; reasons.extend(explosion["signals"])

        levels = scalp_levels(price, high1, low1, vwap_value, golden)
        del df1, df5, df15; gc.collect()

        return {
            "stock": stock, "price": price, "score": score,
            "real_score": real_score, "golden": golden, "good": good,
            "aggressive": aggressive, "quality_label": quality_label,
            "warnings": warnings, "pattern": pattern_label, "levels": levels,
            "market_label": market_label, "session": session_label(),
            "power_session": power, "rsi1": rsi1, "rsi5": rsi5, "adx": adx,
            "rel_vol": rel_vol, "atr_pct": atr_pct, "vwap_dist": vwap_dist,
            "move_1m": move_1m, "move_3m": move_3m, "move_5m": move_5m,
            "rel_strength": rel_strength, "macd_cross": macd_cross,
            "momentum_acc": momentum_acc, "reasons": reasons,
            "dollar_vol": dollar_vol, "explosion": explosion,
            "close1": close1, "volume1": volume1,
        }
    except Exception as e:
        log.error(f"Analyze error {stock}: {e}")
        return None
    finally:
        gc.collect()


# ─── MESSAGE ────────────────────────────────────────────────
def build_alert_message(r: dict, claude_verdict: str) -> str:
    lv = r["levels"]
    rr_e  = "🟢" if lv["rr"] >= 2 else ("🟡" if lv["rr"] >= 1.5 else "🔴")
    title = "🥇 GOLDEN" if r["golden"] else "✅ GOOD"
    exp   = "\n💥 <b>انفجار!</b> " + " ".join(r["explosion"]["signals"]) if r["explosion"]["is_explosion"] else ""
    ex    = ("⚡" if r["aggressive"] else "") + (" 🔔" if r["macd_cross"] else "") + (" 📈" if r["momentum_acc"] else "")

    claude_line = f"\n━━━━━━━━━━━━━━━\n🤖 <b>Claude:</b> {claude_verdict}" if claude_verdict else ""

    return f"""{title} | <b>{r['stock']}</b> | {r['session']}
⏰ {get_ksa_time()} | {r['market_label']}
💵 <b>{r['price']:.2f}</b>{exp}
━━━━━━━━━━━━━━━
🎯 دخول: <b>{lv['entry']:.2f}</b>
✅ هدف:  <b>{lv['target']:.2f}</b>
🛑 وقف:  <b>{lv['stop']:.2f}</b>
{rr_e} R:R = <b>{lv['rr']:.1f}x</b>
━━━━━━━━━━━━━━━
{r['quality_label']} | {r['real_score']}/9 | Score:{r['score']}{(' ' + ex) if ex.strip() else ''}
RSI:{r['rsi1']:.0f} ADX:{r['adx']:.0f} Vol:{r['rel_vol']:.1f}x VWAP:{r['vwap_dist']*100:+.1f}%{claude_line}""".strip()


# ─── MAIN ───────────────────────────────────────────────────
log.info("BOT STARTED v3")
send("🚀 <b>SCALP BOT PRO v3</b>\n🤖 Claude AI | 💥 صياد الانفجارات | 🔄 متابعة ذكية")

while True:
    try:
        now_ksa = get_ksa_time()

        if time.time() - last_heartbeat >= 3600:
            send(f"💓 BOT ALIVE | {now_ksa} | {session_label()}")
            last_heartbeat = time.time()

        try:
            if ds.should_send_summary():
                ds.send_summary(send)
        except Exception as e:
            log.error(f"Summary error: {e}")

        if not market_open():
            log.info(f"MARKET CLOSED | {now_ksa}")
            followup_tracking.clear()
            time.sleep(30); gc.collect(); continue

        if is_opening_block():
            log.info(f"Opening block | {now_ksa}")
            time.sleep(20); continue

        # متابعة ذكية
        for stock in list(followup_tracking.keys()):
            try:
                df_f = download(stock, "1d", "1m")
                if df_f.empty: continue
                c_f = df_f["Close"].squeeze()
                v_f = df_f["Volume"].squeeze()
                msg = check_followup(stock, float(c_f.iloc[-1]), followup_tracking[stock], c_f, v_f)
                if msg: send(msg)
            except Exception as e:
                log.error(f"Followup {stock}: {e}")

        # تحليل الأسهم
        for stock in WATCHLIST:
            time.sleep(2)  # منع Rate Limit
            result = analyze(stock)
            if not result: gc.collect(); continue

            score      = result["score"]
            real_score = result["real_score"]
            now_time   = time.time()
            last_time  = last_alert_time.get(stock, 0)

            if not result["good"]:              log.info(f"{stock}: SKIP"); del result; gc.collect(); continue
            if real_score < MIN_REAL_SCORE:     log.info(f"{stock}: SKIP real={real_score}"); del result; gc.collect(); continue
            if score < MIN_SCORE:               log.info(f"{stock}: SKIP score={score}"); del result; gc.collect(); continue

            snap          = last_alert_snapshot.get(stock, {})
            normal_ok     = now_time - last_time >= ALERT_COOLDOWN
            # تحسين #4: تكرار صارم - Golden فقط + 0.6% صعود
            continuation  = (result["golden"] and snap.get("golden", False)
                             and snap.get("price", 0) > 0
                             and result["price"] >= snap.get("price", 0) * 1.006)
            elite_upgrade = (result["golden"] and not snap.get("golden", False)
                             and real_score >= snap.get("real_score", 0) + 2)
            macd_new      = result["macd_cross"] and not snap.get("macd_cross", False)
            exp_new       = result["explosion"]["is_explosion"] and not snap.get("explosion", False)

            if not (normal_ok or continuation or elite_upgrade or macd_new or exp_new):
                log.info(f"{stock}: SKIP cooldown"); del result; gc.collect(); continue

            claude_verdict = claude_analyze(result)
            close1  = result.pop("close1")
            volume1 = result.pop("volume1")
            msg = build_alert_message(result, claude_verdict)
            send(msg)
            log.info(f"ALERT: {stock} score={score} real={real_score}/9 {result['quality_label']}")

            # إضافة للمتابعة
            followup_tracking[stock] = {
                "entry": result["levels"]["entry"],
                "target": result["levels"]["target"],
                "stop": result["levels"]["stop"],
                "milestones_sent": set(),
            }

            try:
                ds.log_alert(stock=stock, score=score, real_score=real_score,
                             golden=result["golden"], quality_label=result["quality_label"],
                             price=result["price"], session=result["session"])
            except: pass

            last_alert_time[stock] = now_time
            last_alert_snapshot[stock] = {
                "score": score, "price": result["price"],
                "real_score": real_score, "golden": result["golden"],
                "macd_cross": result["macd_cross"],
                "explosion": result["explosion"]["is_explosion"],
            }
            del result; gc.collect()

        time.sleep(CHECK_SECONDS)

    except Exception as e:
        log.error(f"MAIN LOOP ERROR: {e}")
        gc.collect(); time.sleep(30)
