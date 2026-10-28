"""
Candlestick + indicator signal bot (Pydroid 3)
Install first (Pydroid > Pip):  requests  websocket-client
Data: Deriv websocket (real candles/ticks), Twelve Data as fallback.
This gives a SCORE, not a guaranteed win rate. Test on demo only.
"""
import json, time, requests, websocket

TG_TOKEN = "9188400911:AAF2vvdr9DqBlukNwwMjovuVX82L_Qy6bcY"
TD_KEY = "e9370f151032475485690506f705a1d7"
DERIV_WS = "wss://ws.derivws.com/websockets/v3?app_id=61234"
TG = f"https://api.telegram.org/bot{TG_TOKEN}/"

FOREX = {"EUR/USD": ("frxEURUSD", "EUR/USD"), "GBP/USD": ("frxGBPUSD", "GBP/USD"),
         "USD/JPY": ("frxUSDJPY", "USD/JPY"), "AUD/USD": ("frxAUDUSD", "AUD/USD"),
         "USD/CAD": ("frxUSDCAD", "USD/CAD"), "EUR/JPY": ("frxEURJPY", "EUR/JPY")}
# Pocket Option OTC is NOT available via API; Deriv synthetics used as substitute
OTC = {"Volatility 10": ("R_10", None), "Volatility 25": ("R_25", None),
       "Volatility 50": ("R_50", None), "Volatility 100": ("R_100", None)}
EXPIRIES = {"5s": 5, "15s": 15, "1m": 60, "3m": 180, "5m": 300, "15m": 900}
# analysis timeframe chosen per expiry (seconds per candle)
TF_FOR = {5: 5, 15: 5, 60: 60, 180: 60, 300: 300, 900: 300}

# ---------------- DATA ----------------
def aggregate_ticks(times, prices, sec):
    out, cur, bucket = [], None, None
    for t, p in zip(times, prices):
        b = t - t % sec
        if b != bucket:
            if cur: out.append(cur)
            cur, bucket = {"t": b, "o": p, "h": p, "l": p, "c": p}, b
        else:
            cur["h"] = max(cur["h"], p); cur["l"] = min(cur["l"], p); cur["c"] = p
    if cur: out.append(cur)
    return out

def deriv_candles(symbol, sec):
    ws = websocket.create_connection(DERIV_WS, timeout=15)
    try:
        if sec >= 60:
            req = {"ticks_history": symbol, "end": "latest", "count": 250,
                   "style": "candles", "granularity": sec}
        else:
            req = {"ticks_history": symbol, "end": "latest", "count": 5000, "style": "ticks"}
        ws.send(json.dumps(req))
        r = json.loads(ws.recv())
    finally:
        ws.close()
    if "error" in r:
        raise RuntimeError(r["error"]["message"])
    if sec >= 60:
        return [{"t": x["epoch"], "o": float(x["open"]), "h": float(x["high"]),
                 "l": float(x["low"]), "c": float(x["close"])} for x in r["candles"]]
    h = r["history"]
    return aggregate_ticks(h["times"], [float(p) for p in h["prices"]], sec)

def twelve_candles(sym, sec):
    iv = {60: "1min", 300: "5min", 900: "15min"}.get(sec)
    if not iv or not sym: return None
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": sym, "interval": iv, "outputsize": 250,
                             "apikey": TD_KEY}, timeout=15).json()
    if "values" not in r: return None
    v = r["values"][::-1]
    return [{"t": i, "o": float(x["open"]), "h": float(x["high"]),
             "l": float(x["low"]), "c": float(x["close"])} for i, x in enumerate(v)]

def get_candles(dsym, tdsym, sec):
    try:
        c = deriv_candles(dsym, sec)
        if len(c) >= 60: return c, "Deriv"
    except Exception as e:
        print("deriv err", e)
    c = twelve_candles(tdsym, sec)
    if c and len(c) >= 60: return c, "TwelveData"
    return None, None

# ---------------- INDICATORS ----------------
def ema(vals, n):
    k = 2 / (n + 1); e = vals[0]; out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k); out.append(e)
    return out

def rsi(cl, n=14):
    g = l = 0
    for i in range(1, n + 1):
        d = cl[i] - cl[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100 if l == 0 else 100 - 100 / (1 + g / l)

def true_ranges(cs):
    return [max(cs[i]["h"] - cs[i]["l"], abs(cs[i]["h"] - cs[i - 1]["c"]),
                abs(cs[i]["l"] - cs[i - 1]["c"])) for i in range(1, len(cs))]

def atr(cs, n=14):
    tr = true_ranges(cs); a = sum(tr[:n]) / n
    for x in tr[n:]: a = (a * (n - 1) + x) / n
    return a

def adx(cs, n=14):
    tr = true_ranges(cs); pd = []; md = []
    for i in range(1, len(cs)):
        up = cs[i]["h"] - cs[i - 1]["h"]; dn = cs[i - 1]["l"] - cs[i]["l"]
        pd.append(up if up > dn and up > 0 else 0)
        md.append(dn if dn > up and dn > 0 else 0)
    t, p, m = sum(tr[:n]), sum(pd[:n]), sum(md[:n]); dx = []; pi = mi = 0
    for i in range(n, len(tr)):
        t = t - t / n + tr[i]; p = p - p / n + pd[i]; m = m - m / n + md[i]
        pi = 100 * p / t if t else 0; mi = 100 * m / t if t else 0
        dx.append(100 * abs(pi - mi) / (pi + mi) if pi + mi else 0)
    a = sum(dx[:n]) / n
    for d in dx[n:]: a = (a * (n - 1) + d) / n
    return a, pi, mi

# ---------------- PATTERNS ----------------
def body(c): return abs(c["c"] - c["o"])
def rng(c): return (c["h"] - c["l"]) or 1e-12
def up(c): return c["h"] - max(c["o"], c["c"])
def lo(c): return min(c["o"], c["c"]) - c["l"]
def bull(c): return c["c"] > c["o"]
def bear(c): return c["c"] < c["o"]
def mid(c): return (c["o"] + c["c"]) / 2

def detect(cs, trend):
    """trend: +1 up, -1 down. returns list of (name, direction, weight)"""
    a, b, c = cs[-3], cs[-2], cs[-1]
    r = []
    tol = rng(c) * 0.05
    # --- single ---
    if body(c) / rng(c) > 0.92:
        r.append(("Bullish Marubozu", 1, 2.0) if bull(c) else ("Bearish Marubozu", -1, 2.0))
    elif body(c) / rng(c) > 0.65:
        r.append(("Bullish Momentum Candle", 1, 1.0) if bull(c) else ("Bearish Momentum Candle", -1, 1.0))
    if lo(c) > 2 * body(c) and up(c) < body(c) * 0.5 + tol and body(c) > 0:
        r.append(("Hammer / Bullish Pin Bar", 1, 2.0) if trend <= 0 else ("Hanging Man", -1, 1.0))
    if up(c) > 2 * body(c) and lo(c) < body(c) * 0.5 + tol and body(c) > 0:
        r.append(("Shooting Star / Bearish Pin Bar", -1, 2.0) if trend >= 0 else ("Inverted Hammer", 1, 1.0))
    if body(c) / rng(c) < 0.1:
        if lo(c) > 0.6 * rng(c): r.append(("Dragonfly Doji", 1, 1.5))
        elif up(c) > 0.6 * rng(c): r.append(("Gravestone Doji", -1, 1.5))
    # --- two ---
    if bear(b) and bull(c) and c["o"] <= b["c"] and c["c"] >= b["o"]:
        r.append(("Bullish Engulfing", 1, 3.0))
    if bull(b) and bear(c) and c["o"] >= b["c"] and c["c"] <= b["o"]:
        r.append(("Bearish Engulfing", -1, 3.0))
    if bear(b) and bull(c) and max(c["o"], c["c"]) < b["o"] and min(c["o"], c["c"]) > b["c"] and body(c) < body(b) * 0.6:
        r.append(("Bullish Harami", 1, 1.5))
    if bull(b) and bear(c) and max(c["o"], c["c"]) < b["c"] and min(c["o"], c["c"]) > b["o"] and body(c) < body(b) * 0.6:
        r.append(("Bearish Harami", -1, 1.5))
    if bear(b) and bull(c) and c["o"] < b["c"] and mid(b) < c["c"] < b["o"]:
        r.append(("Piercing Line", 1, 2.0))
    if bull(b) and bear(c) and c["o"] > b["c"] and b["o"] < c["c"] < mid(b):
        r.append(("Dark Cloud Cover", -1, 2.0))
    if abs(b["l"] - c["l"]) < tol and bear(b) and bull(c):
        r.append(("Tweezer Bottom", 1, 2.0))
    if abs(b["h"] - c["h"]) < tol and bull(b) and bear(c):
        r.append(("Tweezer Top", -1, 2.0))
    if c["h"] < b["h"] and c["l"] > b["l"]:
        r.append(("Inside Bar (breakout pending)", trend, 0.5))
    if c["h"] > b["h"] and c["l"] < b["l"]:
        r.append(("Bullish Outside Bar", 1, 1.0) if bull(c) else ("Bearish Outside Bar", -1, 1.0))
    # --- three ---
    if bear(a) and body(b) < body(a) * 0.4 and bull(c) and c["c"] > mid(a):
        r.append(("Morning Star", 1, 3.0))
    if bull(a) and body(b) < body(a) * 0.4 and bear(c) and c["c"] < mid(a):
        r.append(("Evening Star", -1, 3.0))
    if all(bull(x) for x in (a, b, c)) and a["c"] < b["c"] < c["c"] and b["o"] > a["o"] and c["o"] > b["o"]:
        r.append(("Three White Soldiers", 1, 3.0))
    if all(bear(x) for x in (a, b, c)) and a["c"] > b["c"] > c["c"] and b["o"] < a["o"] and c["o"] < b["o"]:
        r.append(("Three Black Crows", -1, 3.0))
    if bear(a) and bull(b) and body(b) < body(a) and b["o"] > a["c"] and c["c"] > a["o"] and bull(c):
        r.append(("Three Inside Up", 1, 2.5))
    if bull(a) and bear(b) and body(b) < body(a) and b["o"] < a["c"] and c["c"] < a["o"] and bear(c):
        r.append(("Three Inside Down", -1, 2.5))
    # --- breakout of last 10 candles ---
    hi = max(x["h"] for x in cs[-11:-1]); low = min(x["l"] for x in cs[-11:-1])
    if c["c"] > hi: r.append(("Bullish Breakout Candle", 1, 2.0))
    if c["c"] < low: r.append(("Bearish Breakdown Candle", -1, 2.0))
    return r

# ---------------- ENGINE ----------------
def analyze(cs, expiry):
    cl = [x["c"] for x in cs]
    e9, e21 = ema(cl, 9)[-1], ema(cl, 21)[-1]
    rs = rsi(cl); at = atr(cs); ax, pdi, mdi = adx(cs)
    trend = 1 if e9 > e21 else -1
    pats = detect(cs, trend)
    score = sum(d * w for _, d, w in pats)
    # indicator confirmation (1-2 main: EMA + RSI; ADX filters strength)
    if e9 > e21: score += 1.0
    else: score -= 1.0
    if rs > 50: score += 0.5
    else: score -= 0.5
    if ax > 25: score += 1.0 * (1 if pdi > mdi else -1)
    # RSI extreme in a weak trend -> mean reversion nudge
    if ax < 20:
        if rs > 72: score -= 1.0
        elif rs < 28: score += 1.0
    direction = "CALL 🟢 (BUY)" if score >= 0 else "PUT 🔴 (SELL)"
    side = 1 if score >= 0 else -1
    agree = sum(1 for _, d, _ in pats if d == side)
    conf = max(50, min(85, 50 + abs(score) * 4 + agree * 2))
    strength = "strong" if abs(score) >= 6 else "moderate" if abs(score) >= 3 else "weak"
    atr_avg = sum(true_ranges(cs[-60:-14])) / 46
    vol = "expanding" if at > atr_avg * 1.3 else "quiet" if at < atr_avg * 0.7 else "normal"
    names = ", ".join(n for n, _, _ in pats) or "no named pattern"
    return (f"{direction}\nStrength: {strength}  |  Score: {score:+.1f}\n"
            f"Confidence score: {conf:.0f}% (NOT a win probability)\n"
            f"Patterns: {names}\n"
            f"EMA9 {'>' if e9 > e21 else '<'} EMA21 | RSI {rs:.1f} | ADX {ax:.1f} | ATR {vol}\n"
            f"Price: {cl[-1]}")

# ---------------- TELEGRAM ----------------
def tg(method, **p):
    try:
        return requests.post(TG + method, json=p, timeout=40).json()
    except Exception as e:
        print("tg err", e); return {}

def kb(rows): return {"inline_keyboard": rows}
def btn(t, d): return {"text": t, "callback_data": d}

def main_menu(chat):
    tg("sendMessage", chat_id=chat, text="🤖 Candlestick + Indicators Signal Bot",
       reply_markup=kb([[btn("📊 Select Market", "menu:m")],
                        [btn("💱 Select Pair", "menu:p"), btn("⏱ Select Expiry", "menu:e")],
                        [btn("🎯 Get Signal", "go")]]))

state = {}

def handle_cb(chat, data, mid_id):
    s = state.setdefault(chat, {"mk": "forex", "pair": "EUR/USD", "exp": "1m"})
    if data == "menu:m":
        tg("sendMessage", chat_id=chat, text="Select market:",
           reply_markup=kb([[btn("Forex (real)", "m:forex"), btn("OTC (Deriv synthetic)", "m:otc")]]))
    elif data.startswith("m:"):
        s["mk"] = data[2:]; s["pair"] = "EUR/USD" if s["mk"] == "forex" else "Volatility 10"
        tg("sendMessage", chat_id=chat, text=f"Market: {s['mk']} | Pair reset to {s['pair']}")
    elif data == "menu:p":
        src = FOREX if s["mk"] == "forex" else OTC
        tg("sendMessage", chat_id=chat, text="Select pair:",
           reply_markup=kb([[btn(p, "p:" + p)] for p in src]))
    elif data.startswith("p:"):
        s["pair"] = data[2:]; tg("sendMessage", chat_id=chat, text=f"Pair: {s['pair']}")
    elif data == "menu:e":
        tg("sendMessage", chat_id=chat, text="Select expiry:",
           reply_markup=kb([[btn(e, "e:" + e) for e in list(EXPIRIES)[:3]],
                            [btn(e, "e:" + e) for e in list(EXPIRIES)[3:]]]))
    elif data.startswith("e:"):
        s["exp"] = data[2:]; tg("sendMessage", chat_id=chat, text=f"Expiry: {s['exp']}")
    elif data == "go":
        src = FOREX if s["mk"] == "forex" else OTC
        if s["pair"] not in src: s["pair"] = list(src)[0]
        dsym, tdsym = src[s["pair"]]
        exp = EXPIRIES[s["exp"]]; tf = TF_FOR[exp]
        tg("sendMessage", chat_id=chat, text=f"⏳ Analysing {s['pair']} ({tf}s candles for {s['exp']} expiry)...")
        cs, source = get_candles(dsym, tdsym, tf)
        if not cs:
            tg("sendMessage", chat_id=chat, text="⚠️ Couldn't fetch data (market closed or network issue). Try again or pick an OTC/synthetic pair.")
            return
        try:
            res = analyze(cs[:-1] if len(cs) > 61 else cs, exp)  # use closed candles only
        except Exception as e:
            res = f"Analysis error: {e}"
        tg("sendMessage", chat_id=chat,
           text=f"📡 {s['pair']} | {s['exp']} | Source: {source}\n\n{res}\n\n"
                "Strategy: candlestick patterns for entry, EMA/RSI/ADX for confirmation. "
                "No indicator is reliable on its own — use demo & risk limits.",
           reply_markup=kb([[btn("🔄 New Signal", "go"), btn("🏠 Menu", "menu:home")]]))
    elif data == "menu:home":
        main_menu(chat)

def run():
    offset = 0
    print("Bot running...")
    while True:
        r = tg("getUpdates", offset=offset, timeout=30)
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    m = u["message"]
                    if m.get("text", "").startswith(("/start", "/signal")):
                        main_menu(m["chat"]["id"])
                elif "callback_query" in u:
                    q = u["callback_query"]
                    tg("answerCallbackQuery", callback_query_id=q["id"])
                    handle_cb(q["message"]["chat"]["id"], q["data"], q["message"]["message_id"])
            except Exception as e:
                print("handler err", e)
        time.sleep(0.3)

if __name__ == "__main__":
    run()
