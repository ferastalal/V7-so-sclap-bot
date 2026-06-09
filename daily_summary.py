"""
=============================================================
DAILY SUMMARY SYSTEM v2 - Professional
يحلل بعد كل جلسة: عدد الصفقات + أسباب الدخول + أسباب الربح/الخسارة
=============================================================
"""
from datetime import date, datetime
from collections import Counter, defaultdict
import pytz
import os
import json

# ─── ذاكرة اليوم ─────────────────────────────────────────────
# كل صفقة = dict فيها بيانات الدخول + النتيجة (تُملأ لاحقاً)
today_trades: list = []
today_filtered: int = 0
summary_sent: bool = False
summary_date: date = None

# فهرس سريع: stock -> آخر صفقة مفتوحة (للربط مع النتيجة)
_open_index: dict = {}

# ─── التخزين الدائم ──────────────────────────────────────────
# يحفظ على قرص Render الدائم (/var/data) حتى لا تُمسح الصفقات عند إعادة التشغيل.
# لو القرص غير موجود (تشغيل محلي) يستخدم المجلد الحالي.
_DATA_DIR = "/var/data" if os.path.isdir("/var/data") else "."
_STORE = os.path.join(_DATA_DIR, "trades_store.json")


def _save_state():
    """يحفظ كل حالة اليوم في ملف على القرص الدائم."""
    try:
        data = {
            "summary_date": summary_date.isoformat() if summary_date else None,
            "summary_sent": summary_sent,
            "today_filtered": today_filtered,
            "today_trades": today_trades,
        }
        tmp = _STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _STORE)   # كتابة ذرية (آمنة)
    except Exception:
        pass   # الحفظ لا يجب أن يكسر البوت أبداً


def _load_state():
    """يقرأ حالة اليوم من القرص عند بدء البوت."""
    global today_trades, today_filtered, summary_sent, summary_date, _open_index
    try:
        if not os.path.isfile(_STORE):
            return
        with open(_STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
        sd = data.get("summary_date")
        summary_date = date.fromisoformat(sd) if sd else None
        summary_sent = bool(data.get("summary_sent", False))
        today_filtered = int(data.get("today_filtered", 0))
        today_trades = data.get("today_trades", []) or []
        # إعادة بناء فهرس الصفقات المفتوحة
        _open_index = {}
        for t in today_trades:
            if t.get("outcome") is None:
                _open_index[t["stock"]] = t
    except Exception:
        pass   # أي خطأ في القراءة = نبدأ فاضي بأمان


def _ksa_now() -> str:
    return datetime.now(pytz.timezone("Asia/Riyadh")).strftime("%H:%M:%S")


# ─── تسجيل الدخول ────────────────────────────────────────────
def log_alert(stock, score, real_score, golden, quality_label,
              price, session, entry=None, target=None, stop=None,
              reasons=None, rr=None, adx=None, rsi=None, vol=None,
              vwap_dist=None, **kwargs):
    """يسجّل صفقة جديدة لحظة التنبيه مع كل أسباب الدخول.
    أي باراميتر إضافي يُتجاهل بأمان عبر **kwargs."""
    trade = {
        "stock": stock,
        "score": score,
        "real_score": real_score,
        "golden": bool(golden),
        "quality_label": quality_label,
        "price": float(price) if price is not None else 0.0,
        "session": session or "عادي",
        "entry": float(entry) if entry is not None else float(price or 0),
        "target": float(target) if target is not None else 0.0,
        "stop": float(stop) if stop is not None else 0.0,
        "reasons": list(reasons) if reasons else [],
        "rr": float(rr) if rr is not None else 0.0,
        "adx": float(adx) if adx is not None else 0.0,
        "rsi": float(rsi) if rsi is not None else 0.0,
        "vol": float(vol) if vol is not None else 0.0,
        "vwap_dist": float(vwap_dist) if vwap_dist is not None else 0.0,
        "time": _ksa_now(),
        # النتيجة - تُملأ عند الإغلاق
        "outcome": None,        # "win" | "loss" | "open_close"
        "exit_price": None,
        "pnl_pct": None,
        "exit_time": None,
    }
    today_trades.append(trade)
    _open_index[stock] = trade   # آخر صفقة مفتوحة لهذا السهم
    _save_state()


def log_result(stock, outcome, exit_price, exit_time=None):
    """يربط نتيجة الصفقة (هدف/وقف/إغلاق) بآخر صفقة مفتوحة لنفس السهم.
    outcome: 'win' | 'loss' | 'open_close'"""
    trade = _open_index.get(stock)
    if not trade or trade.get("outcome") is not None:
        return  # ما فيه صفقة مفتوحة أو أنها مقفلة أصلاً
    entry = trade["entry"] if trade["entry"] > 0 else trade["price"]
    pnl = ((float(exit_price) - entry) / entry * 100) if entry > 0 else 0.0
    trade["outcome"] = outcome
    trade["exit_price"] = float(exit_price)
    trade["pnl_pct"] = round(pnl, 2)
    trade["exit_time"] = exit_time or _ksa_now()
    _open_index.pop(stock, None)
    _save_state()
    # يرجّع تحليل فوري للصفقة (يرسله البوت في تلقرام)
    return build_trade_analysis(trade)


def build_trade_analysis(t) -> str:
    """تحليل فوري لصفقة مقفلة: سبب التنبيه + بيانات الدخول + قراءة السبب."""
    won = t["outcome"] == "win"
    icon = "🎯 ربح" if won else ("🛑 خسارة" if t["outcome"] == "loss" else "🔔 إغلاق")
    pnl = t.get("pnl_pct") or 0.0
    rsi = t.get("rsi", 0); adx = t.get("adx", 0); vol = t.get("vol", 0)
    rr = t.get("rr", 0); vwap = t.get("vwap_dist", 0)

    triggers = list(t.get("reasons", []))
    trigger_line = "، ".join(triggers[:4]) if triggers else "—"

    insights = []
    if won:
        if vol >= 2.0: insights.append("✅ حجم قوي دعم الحركة")
        if adx >= 28: insights.append("✅ ترند قوي (ADX عالي)")
        if 48 <= rsi <= 62: insights.append("✅ RSI صحي وقت الدخول")
        if rr >= 2.0: insights.append("✅ R:R ممتاز")
        if not insights: insights.append("✅ الصفقة سارت لصالحك")
    else:
        if rsi >= 65: insights.append("⚠️ RSI عالي = دخول متأخر محتمل")
        if 0 < vol < 1.3: insights.append("⚠️ حجم ضعيف = زخم غير مؤكد")
        if adx < 20: insights.append("⚠️ ADX ضعيف = لا يوجد ترند واضح")
        if rr < 1.5: insights.append("⚠️ R:R ضعيف = مخاطرة غير مبررة")
        if vwap > 0.010: insights.append("⚠️ بعيد عن VWAP = دخول متأخر")
        if not insights: insights.append("⚠️ تحرك السعر عكسك رغم الإشارات الجيدة")
    insight_text = "\n".join(f"   {x}" for x in insights)

    return f"""📊 <b>تحليل الصفقة - {t['stock']}</b> | {icon} {pnl:+.2f}%
━━━━━━━━━━━━━━━
🔔 <b>سبب التنبيه:</b> {trigger_line}
📥 <b>بيانات الدخول:</b>
   دخول {t['entry']:.2f} | هدف {t['target']:.2f} | وقف {t['stop']:.2f}
   RSI:{rsi:.0f} | ADX:{adx:.0f} | Vol:{vol:.1f}x | R:R:{rr:.1f}x | VWAP:{vwap*100:+.1f}%
🧠 <b>القراءة:</b>
{insight_text}""".strip()


def log_filtered():
    global today_filtered
    today_filtered += 1
    _save_state()


# ─── محرك التحليل ────────────────────────────────────────────
def _factor_breakdown(wins, losses):
    """يقارن متوسط كل عامل بين الرابحة والخاسرة + يطلع رؤى."""
    lines = []

    def avg(trades, key):
        vals = [t[key] for t in trades if t.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    if not wins and not losses:
        return "  لا توجد صفقات مكتملة للتحليل"

    factors = [
        ("RSI", "rsi", "{:.0f}"),
        ("ADX", "adx", "{:.0f}"),
        ("الحجم النسبي", "vol", "{:.2f}x"),
        ("بُعد VWAP", "vwap_dist", "{:+.2%}"),
        ("Score", "score", "{:.0f}"),
        ("R:R", "rr", "{:.1f}x"),
    ]
    for label, key, fmt in factors:
        wv = avg(wins, key)
        lv = avg(losses, key)
        wstr = fmt.format(wv) if wins else "—"
        lstr = fmt.format(lv) if losses else "—"
        lines.append(f"  • {label}: رابحة {wstr} | خاسرة {lstr}")

    return "\n".join(lines)


def _pattern_insights(wins, losses):
    """يطلع أهم أنماط الربح والخسارة من أسباب الدخول والجلسة."""
    insights = []

    # 1) الجلسة
    def sess_rate(trades_subset, all_trades, name):
        n = sum(1 for t in all_trades if name in (t["session"] or ""))
        w = sum(1 for t in trades_subset if name in (t["session"] or ""))
        return w, n

    # 2) Golden vs عادي
    golden_done = [t for t in (wins + losses) if t["golden"]]
    golden_wins = [t for t in wins if t["golden"]]
    if golden_done:
        gr = len(golden_wins) / len(golden_done) * 100
        insights.append(f"🥇 الصفقات الذهبية: {len(golden_wins)}/{len(golden_done)} رابحة ({gr:.0f}%)")

    # 3) أكثر أسباب الدخول في الصفقات الرابحة
    win_reasons = Counter()
    for t in wins:
        for r in t["reasons"]:
            win_reasons[r] += 1
    if win_reasons:
        top_win = win_reasons.most_common(3)
        insights.append("✅ أكثر أسباب ظهرت في الرابحة: " +
                        ", ".join(f"{r} ({c})" for r, c in top_win))

    # 4) أكثر أسباب الدخول في الخاسرة
    loss_reasons = Counter()
    for t in losses:
        for r in t["reasons"]:
            loss_reasons[r] += 1
    if loss_reasons:
        top_loss = loss_reasons.most_common(3)
        insights.append("❌ أكثر أسباب ظهرت في الخاسرة: " +
                        ", ".join(f"{r} ({c})" for r, c in top_loss))

    # 5) RSI عالي مرتبط بالخسارة؟
    if losses:
        high_rsi_losses = sum(1 for t in losses if t["rsi"] >= 65)
        if high_rsi_losses >= max(2, len(losses) // 2):
            insights.append(f"⚠️ {high_rsi_losses} من الخاسرة دخلت بـ RSI ≥ 65 (دخول متأخر محتمل)")

    # 6) حجم ضعيف مرتبط بالخسارة؟
    if losses:
        low_vol_losses = sum(1 for t in losses if 0 < t["vol"] < 1.3)
        if low_vol_losses >= max(2, len(losses) // 2):
            insights.append(f"⚠️ {low_vol_losses} من الخاسرة دخلت بحجم نسبي ضعيف (<1.3x)")

    return insights


def build_summary() -> str:
    total = len(today_trades)

    completed = [t for t in today_trades if t["outcome"] in ("win", "loss", "open_close")]
    wins = [t for t in today_trades if t["outcome"] == "win"]
    losses = [t for t in today_trades if t["outcome"] == "loss"]
    open_close = [t for t in today_trades if t["outcome"] == "open_close"]
    still_open = [t for t in today_trades if t["outcome"] is None]

    # نضم صفقات الإغلاق للرابحة/الخاسرة حسب نتيجتها الفعلية
    for t in open_close:
        if (t["pnl_pct"] or 0) >= 0:
            wins.append(t)
        else:
            losses.append(t)

    n_win = len(wins)
    n_loss = len(losses)
    decided = n_win + n_loss
    win_rate = (n_win / decided * 100) if decided else 0.0

    total_pnl = sum(t["pnl_pct"] for t in (wins + losses) if t["pnl_pct"] is not None)
    avg_win = (sum(t["pnl_pct"] for t in wins if t["pnl_pct"] is not None) / n_win) if n_win else 0.0
    avg_loss = (sum(t["pnl_pct"] for t in losses if t["pnl_pct"] is not None) / n_loss) if n_loss else 0.0

    golden_n = sum(1 for t in today_trades if t["golden"])
    stocks = list({t["stock"] for t in today_trades})
    stock_count = Counter(t["stock"] for t in today_trades)
    most_active = stock_count.most_common(1)[0] if stock_count else None

    # قائمة الصفقات
    trade_lines = ""
    for i, t in enumerate(today_trades, 1):
        if t["outcome"] == "win":
            icon, res = "🎯", f"+{t['pnl_pct']:.2f}%"
        elif t["outcome"] == "loss":
            icon, res = "🛑", f"{t['pnl_pct']:.2f}%"
        elif t["outcome"] == "open_close":
            sign = "+" if (t["pnl_pct"] or 0) >= 0 else ""
            icon, res = "🔔", f"{sign}{t['pnl_pct']:.2f}% (إغلاق)"
        else:
            icon, res = "⏳", "مفتوحة"
        gicon = "🥇" if t["golden"] else "✅"
        reasons_short = "، ".join(t["reasons"][:3]) if t["reasons"] else "—"
        trade_lines += (
            f"\n  {i}. {icon} {gicon} <b>{t['stock']}</b> {res}"
            f"\n     دخول {t['entry']:.2f} | {t['time']} | {t['session']}"
            f"\n     أسباب: {reasons_short}"
        )

    factor_text = _factor_breakdown(
        [t for t in wins], [t for t in losses]
    )
    insights = _pattern_insights(
        [t for t in wins], [t for t in losses]
    )
    insights_text = "\n".join(f"  {x}" for x in insights) if insights else "  لا توجد أنماط كافية"

    most_active_line = (f"الأنشط: {most_active[0]} ({most_active[1]} مرات)"
                        if most_active else "")

    summary = f"""<b>📊 ملخص الجلسة - {date.today().strftime('%Y/%m/%d')}</b>
━━━━━━━━━━━━━━━━━
<b>عدد الصفقات: {total}</b>
🎯 رابحة: {n_win}  |  🛑 خاسرة: {n_loss}
⏳ بقيت مفتوحة: {len(still_open)}
📈 نسبة النجاح: <b>{win_rate:.0f}%</b>
💰 محصلة الأداء: <b>{total_pnl:+.2f}%</b>
متوسط الرابحة: +{avg_win:.2f}% | متوسط الخاسرة: {avg_loss:.2f}%
🥇 ذهبية: {golden_n}  |  🔍 مفلترة: {today_filtered}
━━━━━━━━━━━━━━━━━
<b>🔬 تحليل العوامل (رابحة vs خاسرة):</b>
{factor_text}
━━━━━━━━━━━━━━━━━
<b>🧠 أنماط ورؤى:</b>
{insights_text}
━━━━━━━━━━━━━━━━━
<b>الأسهم النشطة:</b> {', '.join(stocks) if stocks else 'لا يوجد'}
{most_active_line}
━━━━━━━━━━━━━━━━━
<b>📋 الصفقات:</b>{trade_lines if trade_lines else ' لا توجد صفقات اليوم'}
━━━━━━━━━━━━━━━━━
انتهت الجلسة. نراك غداً! 👋""".strip()

    return summary


def send_summary(send_func) -> bool:
    global summary_sent, summary_date, today_trades, today_filtered, _open_index
    try:
        msg = build_summary()
        send_func(msg)
        today_trades = []
        today_filtered = 0
        _open_index = {}
        summary_sent = True
        summary_date = date.today()
        _save_state()
        return True
    except Exception as e:
        print(f"SUMMARY ERROR: {e}", flush=True)
        return False


def should_send_summary() -> bool:
    """بعد 4 عصراً بتوقيت نيويورك، مرة واحدة في اليوم."""
    global summary_sent, summary_date
    today = date.today()
    if summary_date != today:
        summary_sent = False
    if summary_sent:
        return False
    now = datetime.now(pytz.timezone("America/New_York"))
    if now.weekday() >= 5:
        return False
    return now.strftime("%H:%M") >= "16:00"


def close_all_open(get_price_func):
    """يُستدعى عند نهاية الجلسة: يقفل الصفقات المفتوحة على آخر سعر.
    get_price_func(stock) لازم يرجّع آخر سعر أو None."""
    for stock, trade in list(_open_index.items()):
        try:
            px = get_price_func(stock)
            if px:
                log_result(stock, "open_close", px)
        except Exception as e:
            print(f"close_all_open {stock}: {e}", flush=True)


def _reset_if_new_day():
    """لو الملف المحفوظ من يوم سابق (وأُرسل ملخصه)، نبدأ يوم جديد نظيف."""
    global today_trades, today_filtered, summary_sent, summary_date, _open_index
    today = date.today()
    # إذا التاريخ المحفوظ قديم والملخص اترسل، نظّف لليوم الجديد
    if summary_date is not None and summary_date != today and summary_sent:
        today_trades = []
        today_filtered = 0
        _open_index = {}
        summary_sent = False
        summary_date = None
        _save_state()


# ─── تحميل الحالة المحفوظة عند بدء البوت ─────────────────────
_load_state()
_reset_if_new_day()
