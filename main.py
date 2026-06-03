"""
=============================================================
SCALP BOT PRO -
-
-
-
-
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
# # - -
import daily_summary as ds
# # - Logging -
logging.basicConfig(
level=logging.INFO,
format="%(asctime)s | %(levelname)s | %(message)s",
handlers=[logging.StreamHandler()]
)
log = logging.getLogger("ScalpBot")
# # - -
# # Chat ID
# # export TELEGRAM_TOKEN=""
# # export TELEGRAM_CHAT_ID="chat id "
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8948332078:AAEYXuDitmQfB9iYB-kF8lkW-5QfimabOQI")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1016589957")
# # - -
WATCHLIST = ["TSLA", "NVDA", "AMD", "SMCI", "PLTR"]
# # - -
CHECK_SECONDS = 20 # 20
ALERT_COOLDOWN = 600 # 10 cooldown
# # - -
MIN_SCORE = 100 #
MIN_REAL_SCORE = 5 # 9
# # - -
TARGET_NORMAL = 0.008 # 0.8%
TARGET_GOLDEN = 0.012 # 1.2%
STOP_LOSS = 0.003 # 0.3%
# # - -
# # +
POWER_SESSIONS = [
("09:35", "11:00"), # 09:35 5
("15:00", "15:55"), #
]
NORMAL_SESSION = ("11:00", "15:00") #
OPENING_BLOCK = 5 # 5
MAX_HISTORY_BARS = 200
# # - -
last_alert_time = {}
last_alert_snapshot = {}
last_heartbeat = time.time()
# # - Cache -
market_cache = {"time": 0, "value": (0.0, " ")}
# # -
# # TELEGRAM
# # -
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
# # -
# #
# # -
def get_ny_time():
return datetime.now(pytz.timezone("America/New_York"))
def get_ksa_time():
return datetime.now(pytz.timezone("Asia/Riyadh")).strftime("%H:%M:%S")
def market_open() -> bool:
now = get_ny_time()
if now.weekday() >= 5:
return False
t = now.strftime("%H:%M")
return "09:30" <= t <= "15:55"
def is_opening_block() -> bool:
now = get_ny_time()
if now.weekday() >= 5:
return False
t = now.strftime("%H:%M")
return "09:30" <= t < f"09:{30 + OPENING_BLOCK}"
def is_power_session() -> bool:
""" """
now = get_ny_time()
t = now.strftime("%H:%M")
for start, end in POWER_SESSIONS:
if start <= t <= end:
return True
return False
def session_label() -> str:
now = get_ny_time()
t = now.strftime("%H:%M")
if "09:35" <= t <= "11:00":
return " "
elif "15:00" <= t <= "15:55":
return " "
else:
return " "
def is_after_close() -> bool:
""" """
now = get_ny_time()
if now.weekday() >= 5:
return False
t = now.strftime("%H:%M")
return "16:00" <= t <= "16:10"
# # -
# #
# # -
def clean_columns(df, stock):
try:
if isinstance(df.columns, pd.MultiIndex):
if stock in df.columns.get_level_values(-1):
df = df.xs(stock, axis=1, level=-1)
else:
df.columns = df.columns.get_level_values(0)
except Exception:
pass
return df
def download(stock: str, period: str, interval: str) -> pd.DataFrame:
try:
df = yf.download(
stock, period=period, interval=interval,
progress=False, auto_adjust=True, threads=False
)
df = clean_columns(df, stock)
if df is not None and not df.empty:
df = df.tail(MAX_HISTORY_BARS).copy()
return df if df is not None else pd.DataFrame()
except Exception as e:
log.error(f"Download error {stock} {interval}: {e}")
return pd.DataFrame()
# # -
# # VWAP
# # -
def calc_vwap(df: pd.DataFrame) -> float:
"""VWAP """
try:
ny = pytz.timezone("America/New_York")
today_open = datetime.now(ny).replace(hour=9, minute=30, second=0, microsecond=0)
# #
if df.index.tz is None:
df_today = df[df.index >= today_open.replace(tzinfo=None)]
else:
df_today = df[df.index >= today_open]
if len(df_today) < 2:
df_today = df.tail(30) # fallback
high = df_today["High"].squeeze()
low = df_today["Low"].squeeze()
close = df_today["Close"].squeeze()
volume = df_today["Volume"].squeeze()
if volume.sum() <= 0:
return float(close.iloc[-1])
typical = (high + low + close) / 3
return float((typical * volume).sum() / volume.sum())
except Exception:
close = df["Close"].squeeze()
return float(close.iloc[-1])
# # -
# # SPY + QQQ
# # -
def market_move():
try:
now_time = time.time()
if now_time - market_cache["time"] < 60:
return market_cache["value"]
spy = download("SPY", "1d", "1m")
qqq = download("QQQ", "1d", "1m")
if spy.empty or qqq.empty or len(spy) < 10:
market_cache.update({"value": (0.0, " "), "time": now_time})
return market_cache["value"]
spy_c = spy["Close"].squeeze()
qqq_c = qqq["Close"].squeeze()
spy_move = (float(spy_c.iloc[-1]) - float(spy_c.iloc[-6])) / float(spy_c.iloc[-6])
qqq_move = (float(qqq_c.iloc[-1]) - float(qqq_c.iloc[-6])) / float(qqq_c.iloc[-6])
avg = (spy_move + qqq_move) / 2
label = "Market up" if avg > 0.0003 else ("Market weak" if avg < -0.0003 else "Market neutral")
value = (avg, label)
market_cache.update({"value": value, "time": now_time})
del spy, qqq
gc.collect()
return value
except Exception as e:
log.error(f"Market move error: {e}")
return 0.0, " "
# # -
# # real_quality_filter
# # -
def real_quality_filter(move_1m, move_3m, move_5m, rel_strength,
rel_volume, adx, atr_pct, vwap_dist, rsi1, rsi5,
power_session: bool):
score = 0
warnings = []
# #
move_1m_min = 0.0005 if power_session else 0.0008
move_3m_min = 0.0015 if power_session else 0.002
move_5m_min = 0.002 if power_session else 0.003
rvol_min = 0.7 if power_session else 0.85
if move_1m > move_1m_min:
score += 1
else:
warnings.append("Move 1m ")
if move_3m > move_3m_min:
score += 1
else:
warnings.append("Move 3m ")
if move_5m > move_5m_min:
score += 1
else:
warnings.append("Move 5m ")
if rel_strength > 0.001:
score += 1
else:
warnings.append(" ")
if rel_volume >= rvol_min:
score += 1
else:
warnings.append(" ")
if adx >= 18:
score += 1
else:
warnings.append("ADX - ")
if 0.001 <= atr_pct <= 0.025:
score += 1
else:
warnings.append("ATR ")
if -0.001 <= vwap_dist <= 0.012:
score += 1
else:
warnings.append(" VWAP ")
if 45 <= rsi5 <= 75:
score += 1
else:
warnings.append("RSI 5m ")
# #
aggressive = (
score >= 5 and adx >= 28
and move_5m > 0.003 and rel_strength > 0.0015
and vwap_dist > 0
)
golden = score >= 7 or aggressive
good = score >= 5
if golden:
elif good:
label = " GOLDEN SETUP"
label = " GOOD SETUP"
else:
label = " WEAK"
return score, golden, good, aggressive, label, warnings
# # -
# #
# # -
def detect_entry_pattern(close1, high1, low1, price, vwap_value, ema9, ema21):
""" : breakout / vwap bounce / momentum continuation"""
high_20 = float(high1.tail(20).max())
near_break = price >= high_20 * 0.997
above_vwap = price > vwap_value
ema_bull = price > ema9 > ema21
# # Consolidation breakout:
recent_range = float(high1.tail(8).max()) - float(low1.tail(8).min())
price_range = float(high1.tail(20).max()) - float(low1.tail(20).min())
tight_range = recent_range < price_range * 0.35
if near_break and tight_range and above_vwap:
return " Consolidation Breakout", 25
elif near_break and above_vwap and ema_bull:
return " Breakout + Trend", 20
elif above_vwap and ema_bull and not near_break:
return " Momentum Continuation", 15
elif above_vwap and near_break:
return " VWAP Breakout", 15
else:
return " Watch", 5
# # -
# #
# # -
def scalp_levels(price, high1, low1, vwap_value, golden: bool):
resistance = float(high1.tail(15).max())
support = float(low1.tail(15).min())
recent_low = float(low1.tail(6).min())
entry = max(resistance * 1.0003, price)
stop = max(min(support, recent_low) * 0.9985, price * (1 - STOP_LOSS))
target = price * (1 + (TARGET_GOLDEN if golden else TARGET_NORMAL))
# #
risk = entry - stop
reward = target - entry
rr = round(reward / risk, 2) if risk > 0 else 0
return {
"entry": round(entry, 2),
"stop": round(stop, 2),
"target": round(target, 2),
"support": round(support, 2),
"resistance": round(resistance, 2),
"rr": rr,
}
# # -
# #
# # -
def analyze(stock: str):
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
# # - EMAs -
ema9_1 = float(ta.trend.EMAIndicator(close1, window=9 ).ema_indicator().iloc[-1])
ema21_1 = float(ta.trend.EMAIndicator(close1, window=21).ema_indicator().iloc[-1])
ema50_1 = float(ta.trend.EMAIndicator(close1, window=50).ema_indicator().iloc[-1])
ema9_5 = float(ta.trend.EMAIndicator(close5, window=9 ).ema_indicator().iloc[-1])
ema21_5 = float(ta.trend.EMAIndicator(close5, window=21).ema_indicator().iloc[-1])
ema9_15 = float(ta.trend.EMAIndicator(close15, window=9 ).ema_indicator().iloc[-1])
ema21_15= float(ta.trend.EMAIndicator(close15, window=21).ema_indicator().iloc[-1])
# # - RSI -
rsi1 = float(ta.momentum.RSIIndicator(close1, window=14).rsi().iloc[-1])
rsi5 = float(ta.momentum.RSIIndicator(close5, window=14).rsi().iloc[-1])
# # - MACD -
macd = ta.trend.MACD(close1)
macd_now = float(macd.macd().iloc[-1])
macd_sig = float(macd.macd_signal().iloc[-1])
macd_hist = macd_now - macd_sig
macd_prev = float(macd.macd().iloc[-2])
macd_cross = macd_now > macd_sig and macd_prev <= float(macd.macd_signal().iloc[-2])
# # - ADX -
adx = float(ta.trend.ADXIndicator(high=high1, low=low1, close=close1, window=14).adx(
# # - ATR -
atr = float(ta.volatility.AverageTrueRange(high=high1, low=low1, close=close1, wi
atr_pct = atr / price
# # - Relative Volume -
if len(volume1) < 32:
return None
vol_last = float(volume1.iloc[-2])
vol_avg = float(volume1.iloc[-32:-2].mean())
rel_vol = (vol_last / vol_avg) if vol_avg > 0 else 0.0
dollar_vol = price * vol_last
# # - -
move_1m = (price - float(close1.iloc[-2])) / float(close1.iloc[-2])
move_3m = (price - float(close1.iloc[-4])) / float(close1.iloc[-4])
move_5m = (price - float(close1.iloc[-6])) / float(close1.iloc[-6])
move_15m = (price - float(close1.iloc[-16])) / float(close1.iloc[-16])
# # - VWAP -
vwap_value = calc_vwap(df1)
vwap_dist = (price - vwap_value) / vwap_value
# # - -
spy_move, market_label = market_move()
rel_strength = move_5m - spy_move
# # - Momentum Acceleration -
momentum_acc = move_1m > (price - float(close1.iloc[-3])) / float(close1.iloc[-3])
# # - -
power = is_power_session()
# #
max_rsi1 = 85
max_move_1m = 0.025 if power else 0.018
max_move_5m = 0.040 if power else 0.030
max_vwap = 0.025 if power else 0.018
if rsi1 > max_rsi1:
ds.log_filtered()
log.info(f"{stock}: FILTERED RSI1={rsi1:.1f}")
return None
if move_1m > max_move_1m:
ds.log_filtered()
log.info(f"{stock}: FILTERED move_1m={move_1m*100:.2f}%")
return None
if move_5m > max_move_5m:
ds.log_filtered()
log.info(f"{stock}: FILTERED move_5m={move_5m*100:.2f}%")
return None
if vwap_dist > max_vwap:
ds.log_filtered()
log.info(f"{stock}: FILTERED vwap_dist={vwap_dist*100:.2f}%")
return None
if vwap_dist < -0.004:
ds.log_filtered()
log.info(f"{stock}: FILTERED below VWAP")
return None
if rsi5 < 30:
ds.log_filtered()
return None
if dollar_vol < 200000:
ds.log_filtered()
log.info(f"{stock}: FILTERED dollar_vol low")
return None
# # - Liquidity Grab -
high_20 = float(high1.tail(20).max())
near_break = price >= high_20 * 0.997
last_high = float(high1.iloc[-1])
last_close = float(close1.iloc[-1])
prev_close = float(close1.iloc[-2])
candle_range = max(last_high - float(low1.iloc[-1]), 0.0001)
upper_wick = last_high - max(last_close, prev_close)
wick_ratio = upper_wick / candle_range
breakout_failed = last_high > high_20 * 1.001 and last_close < high_20
fast_rejection = move_1m < -0.0025
weak_break_vol = near_break and rel_vol < 0.8 and move_3m < 0.001
fake_wick_break = near_break and wick_ratio > 0.6 and move_3m < 0.001
if breakout_failed or fast_rejection or weak_break_vol or fake_wick_break:
ds.log_filtered()
log.info(f"{stock}: LIQUIDITY GRAB filtered")
return None
# # - -
real_score, golden, good, aggressive, quality_label, warnings = real_quality_filter(
move_1m, move_3m, move_5m, rel_strength,
rel_vol, adx, atr_pct, vwap_dist, rsi1, rsi5,
power_session=power
)
# # - -
pattern_label, pattern_score = detect_entry_pattern(
close1, high1, low1, price, vwap_value, ema9_1, ema21_1
)
# # - -
score = 0
reasons = []
# #
score += pattern_score
reasons.append(f"{pattern_label}")
# # EMA trend
if price > ema9_1 > ema21_1:
score += 15
reasons.append(" 1m (EMA9 > EMA21)")
if ema9_5 > ema21_5:
score += 10
reasons.append(" 5m ")
if ema9_15 > ema21_15:
score += 8
reasons.append(" 15m ")
if price > ema50_1:
score += 8
reasons.append(" EMA50")
# # RSI
if 48 <= rsi1 <= 70:
score += 12
reasons.append(f"RSI 1m ({rsi1:.1f})")
elif 70 < rsi1 <= 78:
score += 6
reasons.append(f"RSI 1m ({rsi1:.1f})")
# # MACD
if macd_cross:
score += 18
reasons.append(" MACD Cross ")
elif macd_now > macd_sig and macd_hist > 0:
score += 10
reasons.append("MACD ")
# # VWAP
if price > vwap_value and 0.0003 <= vwap_dist <= 0.008:
score += 15
reasons.append(" VWAP ")
# # Volume
if rel_vol >= 2.0:
score += 20
reasons.append(f" {rel_vol:.2f}x")
elif rel_vol >= 1.3:
score += 15
reasons.append(f" {rel_vol:.2f}x")
elif rel_vol >= 1.0:
score += 8
reasons.append(f" {rel_vol:.2f}x")
# # Relative Strength
if rel_strength > 0.002:
score += 15
reasons.append(" ")
elif rel_strength > 0.001:
score += 8
reasons.append(" SPY/QQQ")
# # ADX
if adx >= 30:
score += 12
reasons.append(f"ADX ({adx:.1f})")
elif adx >= 20:
score += 7
reasons.append(f"ADX ({adx:.1f})")
# # Momentum Acceleration
if momentum_acc and move_1m > 0.001:
score += 10
reasons.append(" ")
# # Power Session Bonus
if power:
score += 10
reasons.append(f" {session_label()}")
# # Golden bonus
if golden:
score += 15
reasons.append(" Golden Setup confirmed")
# # - -
levels = scalp_levels(price, high1, low1, vwap_value, golden)
del df1, df5, df15
gc.collect()
return {
"stock": stock,
"price": price,
"score": score,
"real_score": real_score,
"golden": golden,
"good": good,
"aggressive": aggressive,
"quality_label": quality_label,
"warnings": warnings,
"pattern": pattern_label,
"levels": levels,
"market_label": market_label,
"session": session_label(),
"power_session": power,
"rsi1": rsi1,
"rsi5": rsi5,
"adx": adx,
"rel_vol": rel_vol,
"atr_pct": atr_pct,
"vwap_dist": vwap_dist,
"move_1m": move_1m,
"move_3m": move_3m,
"move_5m": move_5m,
"rel_strength": rel_strength,
"macd_cross": macd_cross,
"momentum_acc": momentum_acc,
"reasons": reasons,
"dollar_vol": dollar_vol,
}
except Exception as e:
log.error(f"Analyze error {stock}: {e}")
return None
finally:
gc.collect()
# # -
# #
# # -
def build_alert_message(r: dict) -> str:
lv = r["levels"]
w = "\n".join([f" {x}" for x in r["warnings"]]) if r["warnings"] else " "
rs = "\n".join([f" • {x}" for x in r["reasons"]])
title = " GOLDEN SCALP ALERT" if r["golden"] else " GOOD SCALP ALERT"
rr_emoji = " " if lv["rr"] >= 2 else (" " if lv["rr"] >= 1.5 else " ")
msg = f"""
<b>{title}</b>
<b>{r['stock']}</b> | {r['session']}
{get_ksa_time()} KSA
: <b>{r['price']:.2f}</b>
{r['market_label']}
-
<b> :</b>
: <b>{lv['entry']:.2f}</b>
: <b>{lv['target']:.2f}</b>
: <b>{lv['stop']:.2f}</b>
: {lv['support']:.2f}
: {lv['resistance']:.2f}
{rr_emoji} R:R = <b>{lv['rr']:.1f}x</b>
-
{(' {(' {(' -
<b> :</b> {r['quality_label']}
Real Score: {r['real_score']}/9
Score: {r['score']}
Aggressive Momentum') if r['aggressive'] else ''}
MACD Cross !') if r['macd_cross'] else ''}
Momentum ') if r['momentum_acc'] else ''}
<b>:</b>
RSI 1m: {r['rsi1']:.1f} | RSI 5m: {r['rsi5']:.1f}
ADX: {r['adx']:.1f} | RVol: {r['rel_vol']:.2f}x
ATR: {r['atr_pct']*100:.2f}% | VWAP Dist: {r['vwap_dist']*100:.2f}%
Move 1m: {r['move_1m']*100:.2f}% | 3m: {r['move_3m']*100:.2f}% | 5m: {r['move_5m']*100:.2f}
Rel.Strength vs SPY: {r['rel_strength']*100:.2f}%
-
<b> :</b>
{rs}
<b> :</b>
{w}
: {lv['entry']:.2f} |
.
"""
return msg.strip()
# # -
# #
# # -
log.info("BOT STARTED")
send(" <b>SCALP BOT PRO - </b>\n \n VWAP \n \n MACD Cross + Momentum Acc
while True:
try:
now_ksa = get_ksa_time()
# # - Heartbeat -
if time.time() - last_heartbeat >= 3600:
send(f" BOT ALIVE | {now_ksa} KSA | {session_label()}")
last_heartbeat = time.time()
# # - daily_summary.py -
if ds.should_send_summary():
ds.send_summary(send)
log.info("Daily summary sent via daily_summary.py")
# # - -
if not market_open():
log.info(f"MARKET CLOSED | {now_ksa}")
time.sleep(30)
gc.collect()
continue
# # - 5 -
if is_opening_block():
log.info(f"Opening block - waiting | {now_ksa}")
time.sleep(20)
continue
# # - -
for stock in WATCHLIST:
result = analyze(stock)
if not result:
gc.collect()
continue
score = result["score"]
real_score= result["real_score"]
now_time = time.time()
last_time = last_alert_time.get(stock, 0)
# #
if not result["good"]:
log.info(f"{stock}: SKIP not good setup")
del result; gc.collect(); continue
if real_score < MIN_REAL_SCORE:
log.info(f"{stock}: SKIP real_score={real_score}/9")
del result; gc.collect(); continue
if score < MIN_SCORE:
log.info(f"{stock}: SKIP score={score}")
del result; gc.collect(); continue
# # Cooldown Logic
normal_ok = now_time - last_time >= ALERT_COOLDOWN
snap = last_alert_snapshot.get(stock, {})
last_golden = snap.get("golden", False)
last_real = snap.get("real_score", 0)
last_price = snap.get("price", 0)
# #
elite_upgrade = (
result["golden"] and not last_golden
and real_score >= last_real + 2
)
continuation = (
result["golden"] and last_price > 0
and result["price"] >= last_price * 1.006
)
macd_new_cross = result["macd_cross"] and not snap.get("macd_cross", False)
if not (normal_ok or elite_upgrade or continuation or macd_new_cross):
log.info(f"{stock}: SKIP cooldown")
del result; gc.collect(); continue
# # - -
msg = build_alert_message(result)
send(msg)
log.info(f"ALERT SENT: {stock} | score={score} | real={real_score}/9 | {result['q
# # daily_summary.py
ds.log_alert(
stock = stock,
score = score,
real_score = real_score,
golden = result["golden"],
quality_label = result["quality_label"],
price = result["price"],
session = result["session"],
)
last_alert_time[stock] = now_time
last_alert_snapshot[stock] = {
"score": score,
"price": result["price"],
"real_score": real_score,
"golden": result["golden"],
"macd_cross": result["macd_cross"],
}
del result
gc.collect()
time.sleep(CHECK_SECONDS)
except Exception as e:
log.error(f"MAIN LOOP ERROR: {e}")
gc.collect()
time.sleep(30)
