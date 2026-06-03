"""
=============================================================
DAILY SUMMARY SYSTEM
=============================================================
"""
from datetime import date, datetime
from collections import Counter
import pytz

# Daily memory
today_alerts: list = []
today_filtered: int = 0
summary_sent: bool = False
summary_date: date = None


def log_alert(stock: str, score: int, real_score: int,
              golden: bool, quality_label: str,
              price: float, session: str):
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
    })


def log_filtered():
    """Count filtered opportunities."""
    global today_filtered
    today_filtered += 1


def build_summary() -> str:
    total = len(today_alerts)
    golden_n = sum(1 for a in today_alerts if a["golden"])
    good_n = total - golden_n
    stocks = list({a["stock"] for a in today_alerts})

    best = max(today_alerts, key=lambda x: x["score"]) if today_alerts else None

    stock_count = Counter(a["stock"] for a in today_alerts)
    most_active = stock_count.most_common(1)[0] if stock_count else None

    alert_lines = ""
    for i, a in enumerate(today_alerts, 1):
        icon = "🥇" if a["golden"] else "✅"
        alert_lines += (
            f"\n  {i}. {icon} <b>{a['stock']}</b> | "
            f"Score:{a['score']} | Real:{a['real_score']}/9 | "
            f"{a['price']:.2f}$ | {a['time']} | {a['session']}"
        )

    power_alerts = sum(1 for a in today_alerts if "Power" in a.get("session", ""))
    normal_alerts = total - power_alerts

    most_active_line = f"Most active: {most_active[0]} ({most_active[1]} times)" if most_active else ""
    best_line = f"<b>Best trade:</b> {best['stock']} | Score:{best['score']} | {best['quality_label']}" if best else ""
    stocks_line = ', '.join(stocks) if stocks else 'None'

    summary = f"""<b>Daily Summary - {date.today().strftime('%Y/%m/%d')}</b>
━━━━━━━━━━━━━━━━━
<b>Total Alerts: {total}</b>
Golden Setup: {golden_n}
Good Setup: {good_n}
Filtered: {today_filtered}
<b>Sessions:</b>
Power session: {power_alerts}
Normal session: {normal_alerts}
━━━━━━━━━━━━━━━━━
<b>Active stocks:</b> {stocks_line}
{most_active_line}
<b>Top score:</b> {max((a['score'] for a in today_alerts), default=0)}
━━━━━━━━━━━━━━━━━
<b>Alerts:</b>
{alert_lines if alert_lines else 'No alerts today'}
━━━━━━━━━━━━━━━━━
{best_line}
━━━━━━━━━━━━━━━━━
Session ended. See you tomorrow!""".strip()

    return summary


def send_summary(send_func) -> bool:
    """Send daily summary. Returns True if sent successfully."""
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
    """Check if it's time to send the summary (after 4PM NY, once per day)."""
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
