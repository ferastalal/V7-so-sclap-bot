"""
=============================================================
ﻧﻈﺎم اﻟﻨﺘﺎﺋﺞ اﻟﯿﻮﻣﯿﺔ - DAILY SUMMARY SYSTEM
ً
ﻋﻦ اﻟﺒﻮت اﻟﺮﺋﯿﺴﻲ
ﻣﻠﻒ ﻣﺴﺘﻘﻞ ﺗﻤﺎﻣﺎ
ُ
ﺴﺘﺪﻋﻰ ﻣﻦ
ﻓﻘﻂ scalp_bot_pro.py ﯾ
=============================================================
"""
from datetime import date, datetime
import pytz
══════════════════════════════════════════════════════════════ #
اﻟﺬاﻛﺮة اﻟﯿﻮﻣﯿﺔ #
══════════════════════════════════════════════════════════════ #
ً #
ﺤﻔﻆ ھﻨﺎ ﺗﻠﻘﺎﺋﯿﺎ
ُ
ﻛﻞ ﺗﻨﺒﯿﮫ ﯾ
today_alerts: list = []
ﻋﺪد اﻟﻔﺮص اﻟﻠﻲ اﺗﻔﻠﺘﺮت )ﻣﺎ وﺻﻠﺖ ﻟﻺرﺳﺎل( #
today_filtered: int = 0
ھﻞ ﺗﻢ إرﺳﺎل اﻟﻤﻠﺨﺺ اﻟﯿﻮم؟ #
summary_sent: bool = False
summary_date: date = None
══════════════════════════════════════════════════════════════ #
ﺴﺘﺪﻋﻰ ﻣﻦ اﻟﺒﻮت ﻛﻞ ﻣﺮة ﯾﺮﺳﻞ ﺗﻨﺒﯿﮫ( #
ُ
ﺗﺴﺠﯿﻞ ﺗﻨﺒﯿﮫ ﺟﺪﯾﺪ )ﯾ
══════════════════════════════════════════════════════════════ #
def log_alert(stock: str, score: int, real_score: int,
golden: bool, quality_label: str,
price: float, session: str):
"""
.اﺣﻔﻆ اﻟﺘﻨﺒﯿﮫ ﻓﻲ ﺳﺠﻞ اﻟﯿﻮم
.اﺳﺘﺪﻋﺎء ﻣﻦ اﻟﺒﻮت اﻟﺮﺋﯿﺴﻲ ﺑﻌﺪ ﻛﻞ إرﺳﺎل ﻧﺎﺟﺢ
"""
ksa_time = datetime.now(pytz.timezone("Asia/Riyadh")).strftime("%H:%M:%S")
today_alerts.append({
"stock": stock,
"score": score,
"real_score": real_score,
"golden": golden,
"quality_label": quality_label,
"price": price,
"session": session,
"time": ksa_time,
)}
def log_filtered():
ّ اﻟﻔﺮص اﻟﻤﻔﻠﺘﺮة"""
""".ﻋﺪ
global today_filtered
today_filtered += 1
══════════════════════════════════════════════════════════════ #
ﺑﻨﺎء رﺳﺎﻟﺔ اﻟﻤﻠﺨﺺ اﻟﯿﻮﻣﻲ #
══════════════════════════════════════════════════════════════ #
def build_summary() -> str:
total = len(today_alerts)
golden_n = sum(1 for a in today_alerts if a["golden"])
good_n = total - golden_n
stocks = list({a["stock"] for a in today_alerts})
أﻓﻀﻞ ﻓﺮﺻﺔ اﻟﯿﻮم #
best = max(today_alerts, key=lambda x: x["score"]) if today_alerts else None
أﻛﺜﺮ ﺳﮭﻢ ﻇﮭﺮ #
from collections import Counter
stock_count = Counter(a["stock"] for a in today_alerts)
most_active = stock_count.most_common(1)[0] if stock_count else None
ﺑﻨﺎء ﻗﺎﺋﻤﺔ اﻟﺘﻨﺒﯿﮭﺎت #
alert_lines = ""
for i, a in enumerate(today_alerts, 1):
icon = " " if a["golden"] else " "
alert_lines += (
f"\n {i}. {icon} <b>{a['stock']}</b> | "
f"Score:{a['score']} | Real:{a['real_score']}/9 | "
f"{a['price']:.2f}$ | {a['time']} | {a['session']}"
)
إﺣﺼﺎءات اﻟﺠﻠﺴﺎت #
power_alerts = sum(1 for a in today_alerts if "ﻣﻮﺟﺔ" in a.get("session", ""))
normal_alerts = total - power_alerts
summary = f"""
<b>ﻣﻠﺨﺺ اﻟﯿﻮم - {date.today().strftime('%Y/%m/%d')}</b>
━━━━━━━━━━━━━━━━━
<b>إﺟﻤﺎﻟﻲ اﻟﺘﻨﺒﯿﮭﺎت: {total}</b>
Golden Setup: {golden_n}
Good Setup: {good_n}
}today_filtered{ :ﻓﺮص ﻣﻔﻠﺘﺮة
<b>ﺗﻮزﯾﻊ اﻟﺠﻠﺴﺎت:</b>
}power_alerts{ :ﻓﻲ وﻗﺖ اﻟﺰﺧﻢ
}normal_alerts{ :ﺟﻠﺴﺔ ﻋﺎدﯾﺔ
━━━━━━━━━━━━━━━━━
{f' }'ﻻ ﺷﻲء' b> {', '.join(stocks) if stocks else/<:اﻷﺳﮭﻢ اﻟﻨﺸﻄﺔ>b<
أﻛﺜﺮ ﺳﮭﻢ ﻇﮭﺮ: {most_active[0]} ({most_active[1]} ﻣﺮة)' if most_active else ''}
<b>أﻋﻠﻰ ﺳﻜﻮر:</b> {max((a['score'] for a in today_alerts), default=0)}
━━━━━━━━━━━━━━━━━
<b>اﻟﺘﻨﺒﯿﮭﺎت ﺑﺎﻟﺘﺮﺗﯿﺐ:</b>
}'ﻻ ﺗﻨﺒﯿﮭﺎت اﻟﯿﻮم ' alert_lines if alert_lines else{
━━━━━━━━━━━━━━━━━
{f' <b>أﻓﻀﻞ ﻓﺮﺻﺔ اﻟﯿﻮم:</b> {best["stock"]} | Score:{best["score"]} | {best["quality_label"
━━━━━━━━━━━━━━━━━
ً
اﻧﺘﮭﺖ اﻟﺠﻠﺴﺔ. أراك ﻏﺪا
""".strip()
return summary
══════════════════════════════════════════════════════════════ #
ﺴﺘﺪﻋﻰ ﻣﻦ اﻟﺒﻮت اﻟﺮﺋﯿﺴﻲ( #
ُ
إرﺳﺎل اﻟﻤﻠﺨﺺ )ﯾ
══════════════════════════════════════════════════════════════ #
def send_summary(send_func) -> bool:
"""
.أرﺳﻞ اﻟﻤﻠﺨﺺ اﻟﯿﻮﻣﻲ
داﻟﺔ اﻹرﺳﺎل ﻣﻦ اﻟﺒﻮت اﻟﺮﺋﯿﺴﻲ = send_func
ُ
رﺳﻞ ﺑﻨﺠﺎح True ﯾﺮﺟﻊ
إذا أ
"""
global summary_sent, summary_date, today_alerts, today_filtered
try:
msg = build_summary()
send_func(msg)
ﺗﺼﻔﯿﺮ اﻟﺴﺠﻞ ﻟﻠﯿﻮم اﻟﺠﺪﯾﺪ #
today_alerts = []
today_filtered = 0
summary_sent = True
summary_date = date.today()
return True
except Exception as e:
print(f"SUMMARY ERROR: {e}", flush=True)
return False
══════════════════════════════════════════════════════════════ #
ﻓﺤﺺ ھﻞ ﯾﺠﺐ إرﺳﺎل اﻟﻤﻠﺨﺺ اﻵن؟ #
══════════════════════════════════════════════════════════════ #
def should_send_summary() -> bool:
"""
ھﻞ اﻟﻮﻗﺖ ﻣﻨﺎﺳﺐ ﻹرﺳﺎل اﻟﻤﻠﺨﺺ؟
ﺑﺘﻮﻗﯿﺖ ﻧﯿﻮﯾﻮرك وﻣﺮة واﺣﺪة ﻓﻘﻂ ﻓﻲ اﻟﯿﻮم PM ﺑﻌﺪ 4:00
"""
global summary_sent, summary_date
ﯾﻮﻣﻲ reset #
today = date.today()
if summary_date != today:
summary_sent = False
if summary_sent:
return False
ny = pytz.timezone("America/New_York")
now = datetime.now(ny)
أﯾﺎم اﻟﻌﻤﻞ ﻓﻘﻂ #
if now.weekday() >= 5:
return False
t = now.strftime("%H:%M")
return "16:00" <= t <= "16:10"
