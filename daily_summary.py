"""
=============================================================
DAILY SUMMARY SYSTEM - v2
=============================================================
"""
from datetime import date, datetime
from collections import Counter
import pytz
import requests
import os

CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

today_alerts: list = []
today_filtered: int = 0
summary_sent: bool = False
summary_date: date = None


def log_alert(stock: str, score: int, real_score: int,
              golden: bool, quality_label: str,
              price: float, session: str,
              entry: float = 0, target: float = 0, stop: float = 0,
              reasons: list = None, claude_verdict: str = "",
              rr: float = 0, adx: float = 0, rsi: float = 0,
              vol: float = 0, vwap_dist: float = 0):
    """Save alert to today's log."""
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
        "entry": entry,
        "target": target,
        "stop": stop,
        "reasons": reasons or [],
        "claude_verdict": claude_verdict,
        "rr": rr,
        "adx": adx,
        "rsi": rsi,
        "vol": vol,
        "vwap_dist": vwap_dist,
        "final_price": 0,
        "result": "unknown",
    })


def log_filtered():
    """Count filtered opportunities."""
    global today_filtered
    today_filtered += 1


def fetch_final_prices():
    """يجيب السعر النهائي لكل سهم"""
    try:
        import yfinance as yf
        stocks = list({a["stock"] for a in today_alerts})
        for stock in stocks:
            try:
                df = yf.download(stock, period="1d", interval="1m",
                                 progress=False, auto_adjust=True, threads=False)
                if df is not None and not df.empty:
                    final = float(df["Close"].squeeze().iloc[-1])
                    for a in today_alerts:
                        if a["stock"] == stock and a["final_price"] == 0:
                            a["final_price"] = final
                            entry = a.get("entry", 0)
                            target = a.get("target", 0)
                            stop = a.get("stop", 0)
                            if entry > 0 and final > 0:
                                pnl = (final - entry) / entry * 100
                                if final >= target:
                                    a["result"] = f"✅ هدف محقق +{pnl:.2f}%"
                                elif final <= stop:
                                    a["result"] = f"❌ وقف خسارة {pnl:.2f}%"
                                elif final > entry:
                                    a["result"] = f"📈 ربح جزئي +{pnl:.2f}%"
                                else:
                                    a["result"] = f"📉 خسارة {pnl:.2f}%"
            except:
                pass
    except:
        pass


def claude_analyze_summary(alerts_text: str) -> str:
    """يطلب من Claude تحليل ملخص اليوم"""
    try:
        if not CLAUDE_KEY:
            return ""
        prompt = f"""أنت محلل تداول خبير. حلل نتائج هذا اليوم في 3-4 أسطر:

{alerts_text}

ركز على:
1. ما هي الأنماط الناجحة؟
2. ما هي أسباب الإخفاقات؟
3. توصية واحدة لتحسين الأداء غداً."""

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        if response.status_code == 200:
            return response.json()["content"][0]["text"].strip()
        return ""
    except:
        return ""


def build_summary() -> str:
    fetch_final_prices()

    total = len(today_alerts)
    golden_n = sum(1 for a in today_alerts if a["golden"])
    good_n = total - golden_n
    stocks = list({a["stock"] for a in today_alerts})
    stock_count = Counter(a["stock"] for a in today_alerts)
    most_active = stock_count.most_common(1)[0] if stock_count else None

    # إحصاء النتائج
    success = sum(1 for a in today_alerts if "✅" in a.get("result", ""))
    failed  = sum(1 for a in today_alerts if "❌" in a.get("result", ""))
    partial = sum(1 for a in today_alerts if "📈" in a.get("result", ""))

    # بناء تفاصيل كل صفقة
    alert_lines = ""
    alerts_for_claude = ""
    for i, a in enumerate(today_alerts, 1):
        icon = "🥇" if a["golden"] else "✅"
        reasons_text = " | ".join(a.get("reasons", [])[:3]) if a.get("reasons") else "—"
        claude_text = a.get("claude_verdict", "")
        result_text = a.get("result", "لم يتابع")
        final = a.get("final_price", 0)

        alert_lines += f"""
━━━━━━━━━━━━━━━
{i}. {icon} <b>{a['stock']}</b> | {a['time']} | {a['session']}
💵 دخول: {a.get('entry', a['price']):.2f} | 🎯 {a.get('target', 0):.2f} | 🛑 {a.get('stop', 0):.2f}
📊 {a['quality_label']} | Real:{a['real_score']}/9 | R:R:{a.get('rr', 0):.1f}x
📈 ADX:{a.get('adx', 0):.0f} RSI:{a.get('rsi', 0):.0f} Vol:{a.get('vol', 0):.1f}x
🔍 أسباب: {reasons_text}
🤖 Claude: {claude_text if claude_text else '—'}
📌 النتيجة: {result_text}{f' | سعر الإغلاق: {final:.2f}' if final > 0 else ''}"""

        alerts_for_claude += f"{a['stock']} | {a['quality_label']} | نتيجة: {result_text}\n"

    # تحليل Claude للملخص
    claude_summary = claude_analyze_summary(alerts_for_claude) if today_alerts else ""
    claude_section = f"\n━━━━━━━━━━━━━━━\n🤖 <b>تحليل Claude لليوم:</b>\n{claude_summary}" if claude_summary else ""

    stocks_line = ', '.join(stocks) if stocks else 'لا يوجد'
    most_active_line = f"الأكثر نشاطاً: {most_active[0]} ({most_active[1]} مرة)" if most_active else ""

    summary = f"""<b>ملخص اليوم - {date.today().strftime('%Y/%m/%d')}</b>
━━━━━━━━━━━━━━━━━
<b>إجمالي التنبيهات: {total}</b>
🥇 Golden: {golden_n} | ✅ Good: {good_n}
✅ نجح: {success} | ❌ وقف: {failed} | 📈 جزئي: {partial}
🚫 مفلتر: {today_filtered}
━━━━━━━━━━━━━━━━━
الأسهم: {stocks_line}
{most_active_line}
أعلى سكور: {max((a['score'] for a in today_alerts), default=0)}
━━━━━━━━━━━━━━━━━
<b>تفاصيل الصفقات:</b>
{alert_lines if alert_lines else 'لا تنبيهات اليوم'}{claude_section}
━━━━━━━━━━━━━━━━━
انتهت الجلسة. أراك غداً!""".strip()

    return summary


def send_summary(send_func) -> bool:
    global summary_sent, summary_date, today_alerts, today_filtered
    try:
        msg = build_summary()
        send_func(msg)
        today_alerts = []
        today_filtered = 0
        summary_sent = True
        summary_date = date.today()
        return True
    except Exception as e:
        print(f"SUMMARY ERROR: {e}", flush=True)
        return False


def should_send_summary() -> bool:
    global summary_sent, summary_date
    today = date.today()
    if summary_date != today:
        summary_sent = False
    if summary_sent:
        return False
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return "16:00" <= t <= "16:10"
