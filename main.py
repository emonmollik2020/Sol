# =====================================================================
# SECTION 1: প্রয়োজনীয় লাইব্রেরি ইম্পোর্ট ও গ্লোবাল সেটিংস
# =====================================================================
import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ট্রেডিং পেয়ার এবং ফাইলের সেটিংস
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0

# টেক প্রফিট (০.৭%) এবং স্টপ লস (১.০%) এর পার্সেন্টেজ
DEF_TP = 0.007
DEF_SL = 0.010

# থ্রেড ক্ল্যাশ বা ফাইল লক সমস্যা এড়াতে গ্লোবাল থ্রেড লক
STATE_LOCK = threading.Lock()

# এক্সচেঞ্জ কানেকশন (Bitget)
exchange = ccxt.bitget({'enableRateLimit': True})

# ডিফল্ট স্টেট ডাটা স্ট্রাকচার
DEFAULT_STATE = {
    "price": 0.0,
    "balance": INITIAL_FUND,
    "total_pnl": 0.0,
    "last_update": "...",
    "trades": 0,
    "win_rate": 0,
    "best": 0.0,
    "worst": 0.0,
    "last_action": "---",
    "in_position": False,
    "live_pnl_pct": 0.0,
    "live_pnl_val": 0.0,
    "entry_price": 0.0,
    "sl_level": 0.0,
    "tp_level": 0.0,
    "analysis_1m": {"rsi": 0, "ema": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "wait_reason": "লোড হচ্ছে...",
    "log": [],
    "history": []
}


# =====================================================================
# SECTION 2: থ্রেড-সেফ ফাইল ম্যানেজমেন্ট (State Management)
# =====================================================================
def save_state(d):
    """থ্রেড লক ব্যবহার করে নিরাপদভাবে ফাইল সেভ করে"""
    with STATE_LOCK:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(d, f, indent=4)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error saving state: {e}")


def load_state():
    """থ্রেড লক ব্যবহার করে নিরাপদভাবে ফাইল লোড করে"""
    with STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            return DEFAULT_STATE.copy()
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error loading state: {e}")
            return DEFAULT_STATE.copy()


# Flask ওয়েব অ্যাপ্লিকেশন ইনিশিয়েট করা
app = Flask(__name__)


# =====================================================================
# SECTION 3: ক্যান্ডেলস্টিক প্যাটার্ন ডিটেক্টর
# =====================================================================
def get_advanced_pats(df):
    """১৭টি শক্তিশালী ক্যান্ডেলস্টিক প্যাটার্ন সনাক্ত করার জন্য গাণিতিক লজিক"""
    p = []
    if len(df) < 5:
        return p
        
    c1, c2, c3 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    
    def info(c):
        body = abs(c['c'] - c['o'])
        total = max(0.001, c['h'] - c['l'])
        u_wick = c['h'] - max(c['c'], c['o'])
        l_wick = min(c['c'], c['o']) - c['l']
        is_green = c['c'] > c['o']
        return body, total, u_wick, l_wick, is_green
        
    b1, t1, u1, l1, g1 = info(c1)
    b2, t2, u2, l2, g2 = info(c2)
    b3, t3, u3, l3, g3 = info(c3)

    # ==========================================
    # ১. বুলিশ প্যাটার্নস (BUY সিগন্যাল)
    # ==========================================
    # 1. হ্যামার 🔨
    if b1 > 0 and l1 >= 1.8 * b1 and u1 <= 0.2 * b1:
        p.append({"n": "হ্যামার 🔨", "t": "bull"})
        
    # 2. ইনভার্টেড হ্যামার 🔨
    if b1 > 0 and u1 >= 1.8 * b1 and l1 <= 0.2 * b1 and g1:
        p.append({"n": "ইনভার্টেড হ্যামার 🔨", "t": "bull"})
        
    # 3. বুলিশ এনগালফিং 📈
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c']:
        p.append({"n": "বুলিশ এনগালফিং 📈", "t": "bull"})
        
    # 4. মর্নিং স্টার 🌅
    if not g3 and b2 < (b3 * 0.3) and g1 and c1['c'] > (c3['o'] + c3['c']) / 2:
        p.append({"n": "মর্নিং স্টার 🌅", "t": "bull"})
        
    # 5. বুলিশ মারুবোজু 💪
    if b1 / t1 > 0.85 and g1:
        p.append({"n": "বুলিশ মারুবোজু 💪", "t": "bull"})
        
    # 6. পিয়ার্সিং লাইন ⚡
    if not g2 and g1 and c1['o'] < c2['c'] and c1['c'] > (c2['o'] + c2['c']) / 2 and c1['c'] < c2['o']:
        p.append({"n": "পিয়ার্সিং লাইন ⚡", "t": "bull"})
        
    # 7. বুলিশ হারামি 🤰
    if not g2 and g1 and c1['c'] < c2['o'] and c1['o'] > c2['c'] and b1 < b2:
        p.append({"n": "বুলিশ হারামি 🤰", "t": "bull"})
        
    # 8. থ্রি হোয়াইট সোলজার্স 💂‍♂️
    if g1 and g2 and g3 and c1['c'] > c2['c'] and c2['c'] > c3['c'] and b1 > 0.3 * t1 and b2 > 0.3 * t2:
        p.append({"n": "থ্রি হোয়াইট সোলজার্স 💂‍♂️", "t": "bull"})
        
    # 9. টুইজার বটম 🧲
    if abs(c1['l'] - c2['l']) / max(0.001, c1['l']) < 0.001 and not g2 and g1:
        p.append({"n": "টুইজার বটম 🧲", "t": "bull"})


    # ==========================================
    # ২. বেয়ারিশ প্যাটার্নস (SELL সিগন্যাল)
    # ==========================================
    # 10. শুটিং স্টার ☄️
    if b1 > 0 and u1 >= 1.8 * b1 and l1 <= 0.2 * b1 and not g1:
        p.append({"n": "শুটিং স্টার ☄️", "t": "bear"})
        
    # 11. হ্যাঙ্গিং ম্যান 🕴️
    if b1 > 0 and l1 >= 1.8 * b1 and u1 <= 0.2 * b1 and not g1:
        p.append({"n": "হ্যাঙ্গিং ম্যান 🕴️", "t": "bear"})
        
    # 12. বেয়ারিশ এনগালফিং 📉
    if g2 and not g1 and c1['c'] <= c2['o'] and c1['o'] >= c2['c']:
        p.append({"n": "বেয়ারিশ এনগালফিং 📉", "t": "bear"})
        
    # 13. ইভনিং স্টার 🌃
    if g3 and b2 < (b3 * 0.3) and not g1 and c1['c'] < (c3['o'] + c3['c']) / 2:
        p.append({"n": "ইভনিং স্টার 🌃", "t": "bear"})
        
    # 14. বেয়ারিশ মারুবোজু 🔴
    if b1 / t1 > 0.85 and not g1:
        p.append({"n": "বেয়ারিশ মারুবোজু 🔴", "t": "bear"})
        
    # 15. ডার্ক ক্লাউড কভার ⛈️
    if g2 and not g1 and c1['o'] > c2['c'] and c1['c'] < (c2['o'] + c2['c']) / 2 and c1['c'] > c2['o']:
        p.append({"n": "ডার্ক ক্লাউড কভার ⛈️", "t": "bear"})
        
    # 16. বেয়ারিশ হারামি 🤰
    if g2 and not g1 and c1['c'] > c2['o'] and c1['o'] < c2['c'] and b1 < b2:
        p.append({"n": "বেয়ারিশ হারামি 🤰", "t": "bear"})
        
    # 17. থ্রি ব্ল্যাক ক্রোস 🐦
    if not g1 and not g2 and not g3 and c1['c'] < c2['c'] and c2['c'] < c3['c'] and b1 > 0.3 * t1 and b2 > 0.3 * t2:
        p.append({"n": "থ্রি ব্ল্যাক ক্রোস 🐦", "t": "bear"})

    return p


# =====================================================================
# SECTION 4: মূল ট্রেডিং বট ইঞ্জিন লজিক (সহায়ক ব্যাকগ্রাউন্ড থ্রেড)
# =====================================================================
def bot_engine():
    wins, total, net_pnl, pnl_hist = 0, 0, 0.0, [0]
    in_pos, entry_p, peak_p = False, 0.0, 0.0
    
    last_trade_time = 0         # শেষ সফল ট্রেড ক্লোজের টাইমস্ট্যাম্প
    COOLDOWN_SECONDS = 300      # নতুন ট্রেড শুরুর আগে ৫ মিনিট (৩০০ সেকেন্ড) বিরতি

    while True:
        try:
            # ১ মিনিট এবং ৩ মিনিটের মোমবাতি (OHLCV) ডেটা সংগ্রহ
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=200)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=200)
            
            df1 = pd.DataFrame(bars1, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df3 = pd.DataFrame(bars3, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            
            p = df1['c'].iloc[-1]
            
            # টেকনিক্যাল ইন্ডিকেটর গণনা (RSI, EMA, MACD)
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], 20).fillna(0).iloc[-1]
            r3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            
            m_obj = ta.trend.MACD(df3['c'])
            mv = m_obj.macd().iloc[-1]
            ms = m_obj.macd_signal().iloc[-1]
            
            pats1 = get_advanced_pats(df1)
            pats3 = get_advanced_pats(df3)
            
            cur = load_state()
            l_pnl = ((p / entry_p) - 1) * 100 if in_pos else 0.0
            l_val = (100.0 / entry_p * p) - 100.0 if in_pos else 0.0

            # কুলডাউন শেষ হয়েছে কিনা যাচাই
            time_since_last_trade = time.time() - last_trade_time
            cooldown_over = time_since_last_trade >= COOLDOWN_SECONDS

            # ১. ক্রয়ের লজিক (BUY Condition)
            bull_signal = any(pt['t'] == 'bull' for pt in pats1) or any(pt['t'] == 'bull' for pt in pats3)
            can_buy = p > e20 and (40 < r1 < 65) and mv > ms and bull_signal and cooldown_over

            # ২. বিক্রয়ের লজিক (SELL / Exit Condition)
            bear_signal = any(pt['t'] == 'bear' for pt in pats3)
            smart_sell = r1 > 75 or bear_signal

            if in_pos:
                # ট্রেইলিং স্টপ লস আপডেট করা
                if p > peak_p:
                    peak_p = p
                    cur.update({"sl_level": round(p * (1 - DEF_SL), 2)})

                # টার্গেট লাভ বা লস বা স্মার্ট সংকেত মিললে পজিশন থেকে প্রস্থান
                if p >= cur["tp_level"] or p <= cur["sl_level"] or smart_sell:
                    in_pos = False
                    net_pnl += l_val
                    pnl_hist.append(net_pnl)
                    
                    if p > entry_p:
                        wins += 1
                        
                    cur.update({
                        "balance": round(100.0 + net_pnl, 2),
                        "total_pnl": round(net_pnl, 2),
                        "win_rate": round((wins / total) * 100, 1),
                        "best": round(max(pnl_hist), 2),
                        "last_action": "SELL"
                    })
                    cur["history"].insert(0, {
                        "t": datetime.now().strftime("%H:%M"),
                        "a": "SELL",
                        "p": round(p, 2),
                        "r": f"{round(l_pnl, 2)}%"
                    })
                    cur["log"].insert(0, {
                        "t": datetime.now().strftime("%H:%M"),
                        "m": f"🔴 SELL @ ${p:.2f} ({'Smart Exit' if smart_sell else 'Target'})"
                    })
                    
                    # বিক্রি সম্পন্ন হওয়ার পর কুলডাউন কাউন্টডাউন শুরু করা
                    last_trade_time = time.time()
            else:
                # ক্রয়ের সিদ্ধান্ত কার্যকর
                if can_buy:
                    entry_p = p
                    peak_p = p
                    in_pos = True
                    total += 1
                    
                    cur.update({
                        "trades": total,
                        "balance": 0.0,
                        "sl_level": round(p * (1 - DEF_SL), 2),
                        "tp_level": round(p * (1 + DEF_TP), 2),
                        "last_action": "BUY"
                    })
                    cur["history"].insert(0, {
                        "t": datetime.now().strftime("%H:%M"),
                        "a": "BUY",
                        "p": round(p, 2),
                        "r": "---"
                    })
                    cur["log"].insert(0, {
                        "t": datetime.now().strftime("%H:%M"),
                        "m": f"🟢 BUY @ ${p:.2f} (Prediction Confirmed)"
                    })
            
            # সিগন্যালসমূহ বাংলা ও ইমোজি দিয়ে ফরম্যাট করে স্টেট ফাইলে আপডেট করা
            cur.update({
                "price": round(p, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos,
                "live_pnl_pct": round(l_pnl, 2),
                "live_pnl_val": round(l_val, 2),
                "entry_price": round(entry_p, 2),
                "analysis_1m": {
                    "rsi": round(r1, 1),
                    "ema": round(e20, 2),
                    "sig": "বুলিশ ✅" if p > e20 else "বেয়ারিশ ❌",
                    "pats": pats1
                },
                "analysis_3m": {
                    "rsi": round(r3, 1),
                    "macd": round(mv, 3),
                    "sig": "বুলিশ ✅" if mv > ms else "নিরপেক্ষ ⚖️",
                    "pats": pats3
                }
            })
            
            # ড্যাশবোর্ডের জন্য ওয়েটিং মেসেজ সাজানো
            if in_pos:
                cur["wait_reason"] = "পজিশন সক্রিয়"
            elif not cooldown_over:
                remaining_seconds = int(COOLDOWN_SECONDS - time_since_last_trade)
                cur["wait_reason"] = f"কুলডাউন ({remaining_seconds} সেকেন্ড বাকি)"
            else:
                cur["wait_reason"] = "প্যাটার্ন খুঁজছে..." if p > e20 else "ট্রেন্ড ডাউন"
                
            save_state(cur)
        except Exception as e:
            # এক্সচেঞ্জ নেটওয়ার্ক বা যেকোনো সমস্যার লগ কনসোলে প্রিন্ট করা (ডিব্যাগ করার জন্য)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot Engine Warning: {e}")
            
        time.sleep(3)


# ব্যাকগ্রাউন্ড ট্রেডিং থ্রেড রান করা
threading.Thread(target=bot_engine, daemon=True).start()


# =====================================================================
# SECTION 5: Flask ওয়েব সার্ভার এবং এপিআই রাউটস
# =====================================================================
@app.route('/api/data')
def api():
    """বটের বর্তমান তথ্য JSON ফরম্যাটে প্রদান করে (থ্রেড সেফ)"""
    return jsonify(load_state())


@app.route('/')
def index():
    """ওয়েব ড্যাশবোর্ড লোড করে"""
    return render_template_string(UI)


# =====================================================================
# SECTION 6: ড্যাশবোর্ড UI টেমপ্লেট (HTML, CSS ও JS স্ক্রিপ্ট)
# =====================================================================
UI = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Master SOL Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 600000);</script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { border: 1px solid #dcfce7; color: #166534; padding: 2px 10px; border-radius: 99px; font-size: 10px; font-weight: 800; display: inline-block; margin: 2px; }
        .tag-bull { background: #f0fdf4; } 
        .tag-bear { background: #fef2f2; color: #991b1b; border-color: #fee2e2; }
    </style>
</head>
<body class="p-3 text-slate-800">
<div class="max-w-md mx-auto">
    <!-- বট স্ট্যাটাস -->
    <div class="text-center mb-6">
        <span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span>
    </div>
    
    <!-- পরিসংখ্যান -->
    <div class="grid grid-cols-3 gap-2 mb-2 text-center text-[10px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>মোট ট্রেড</p><p id="t" class="text-lg font-black text-slate-800">0</p></div>
        <div class="card p-3"><p>জয়ের হার</p><p id="w" class="text-lg font-black text-slate-800">0%</p></div>
        <div class="card p-3"><p>মোট P&L</p><p id="pnl" class="text-lg font-black text-green-600">+$0.00</p></div>
    </div>

    <div class="grid grid-cols-3 gap-2 mb-4 text-center text-[9px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>সেরা</p><p id="bt" class="text-xs font-bold text-green-400">--</p></div>
        <div class="card p-3"><p>খারাপ</p><p id="wt" class="text-xs font-bold text-red-400">--</p></div>
        <div class="card p-3"><p>শেষ</p><p id="la" class="text-xs font-bold text-slate-500">---</p></div>
    </div>

    <!-- রিয়েল-টাইম মূল্য ও ব্যালেন্স কার্ড -->
    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4">
            <span id="pr" class="text-4xl font-black tracking-tighter">$0.00</span>
            <div class="text-right text-[10px] text-slate-400 font-bold">ব্যালেন্স: <b id="bl">$100.00</b></div>
        </div>
        
        <!-- লাইভ পজিশন কালার বক্স -->
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg">
            <p class="text-[10px] font-bold text-slate-400 uppercase mb-1">লাইভ পজিশন প্রফিট</p>
            <p id="lp" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t pt-2">
                <div class="text-red-500">🛑 SL: <span id="sl">0</span></div>
                <div class="text-green-600">✅ TP: <span id="tp">0</span></div>
            </div>
        </div>
        
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 text-center uppercase tracking-wide italic">&#8987; লোড হচ্ছে...</div>
    </div>

    <!-- ১ মিনিট বিশ্লেষণ প্যানেল -->
    <div class="card p-4 mb-4 text-[11px]">
        <div class="flex justify-between mb-3 items-center">
            <h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3>
            <span id="s1" class="font-bold px-2 py-0.5 rounded text-[10px]">WAIT</span>
        </div>
        <div class="grid grid-cols-2 text-slate-500 font-medium">
            <span>RSI: <b id="r1">0</b></span>
            <span>EMA 20: <b id="e1">0</b></span>
        </div>
        <div id="pats1" class="mt-3 flex flex-wrap gap-1"></div>
    </div>
    
    <!-- ৩ মিনিট বিশ্লেষণ প্যানেল -->
    <div class="card p-4 mb-4 text-[11px]">
        <div class="flex justify-between mb-3 items-center">
            <h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3>
            <span id="s3" class="font-bold px-2 py-0.5 rounded text-[10px]">WAIT</span>
        </div>
        <div class="grid grid-cols-2 text-slate-500 font-medium">
            <span>RSI: <b id="r3">0</b></span>
            <span>MACD: <b id="m3">0</b></span>
        </div>
        <div id="pats3" class="mt-3 flex flex-wrap gap-1"></div>
    </div>

    <!-- চার্ট উইজেট -->
    <div class="card overflow-hidden h-60 mb-4 border border-slate-100 shadow-inner">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    
    <!-- ট্রেড হিস্ট্রি টেবিল -->
    <div class="card p-4 mb-4 overflow-hidden">
        <h3 class="font-black text-slate-700 text-[10px] mb-3 uppercase tracking-wider">&#128203; ট্রেড হিস্ট্রি</h3>
        <div class="overflow-x-auto">
            <table class="w-full text-[10px] text-left">
                <thead class="text-slate-400 border-b">
                    <tr>
                        <th class="pb-2">সময়</th>
                        <th class="pb-2 text-center">ধরন</th>
                        <th class="pb-2 text-right">মূল্য</th>
                        <th class="pb-2 text-right">P&L</th>
                    </tr>
                </thead>
                <tbody id="hb" class="divide-y divide-slate-50"></tbody>
            </table>
        </div>
    </div>
    
    <!-- লাইভ লগ প্যানেল -->
    <div class="card p-4 mb-6">
        <h3 class="font-bold text-slate-700 text-xs mb-2 uppercase tracking-widest">&#128214; লাইভ লগ</h3>
        <div id="lg" class="space-y-1 text-[10px]"></div>
    </div>
</div>

<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); 
            const d = await r.json();
            
            if (d.price > 0) {
                // মূল ব্যালেন্স ও রিয়েল-টাইম প্রাইস আপডেট
                document.getElementById('pr').innerText = '$' + d.price; 
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                
                // পরিসংখ্যান আপডেট
                document.getElementById('t').innerText = d.trades; 
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = (d.total_pnl >= 0 ? '+$' : '$') + d.total_pnl.toFixed(2);
                document.getElementById('bt').innerText = '$' + d.best.toFixed(2); 
                document.getElementById('wt').innerText = '$' + d.worst.toFixed(2);
                document.getElementById('la').innerText = d.last_action; 
                document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                
                // লাইভ ট্রেড ওপেন থাকলে প্রফিট-বক্স শো করা
                if (d.in_position) {
                    const disp = document.getElementById('pnl_display'); 
                    disp.classList.remove('hidden');
                    
                    document.getElementById('lp').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl').innerText = d.sl_level; 
                    document.getElementById('tp').innerText = d.tp_level;
                    
                    const col = d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
                    document.getElementById('lp').className = 'text-4xl font-black ' + col;
                    disp.className = 'mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg ' + (d.live_pnl_pct >= 0 ? 'border-green-100' : 'border-red-100');
                } else { 
                    document.getElementById('pnl_display').classList.add('hidden'); 
                }
                
                // ১ মিনিট সিগন্যাল ডাইনামিক স্টাইল
                document.getElementById('r1').innerText = d.analysis_1m.rsi; 
                document.getElementById('e1').innerText = '$' + d.analysis_1m.ema;
                
                const s1 = document.getElementById('s1'); 
                s1.innerText = d.analysis_1m.sig;
                if (d.analysis_1m.sig.includes('বুলিশ')) {
                    s1.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-green-50 text-green-700 border border-green-200';
                } else if (d.analysis_1m.sig.includes('বেয়ারিশ')) {
                    s1.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-red-50 text-red-700 border border-red-200';
                } else {
                    s1.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200';
                }
                
                // ৩ মিনিট সিগন্যাল ডাইনামিক স্টাইল
                document.getElementById('r3').innerText = d.analysis_3m.rsi; 
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                
                const s3 = document.getElementById('s3'); 
                s3.innerText = d.analysis_3m.sig;
                if (d.analysis_3m.sig.includes('বুলিশ')) {
                    s3.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-green-50 text-green-700 border border-green-200';
                } else if (d.analysis_3m.sig.includes('বেয়ারিশ')) {
                    s3.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-red-50 text-red-700 border border-red-200';
                } else {
                    s3.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200';
                }

                // প্যাটার্ন ডিসপ্লে রেন্ডার
                const tag = (p) => `<span class="tag ${p.t==='bull'?'tag-bull':'tag-bear'}">${p.n}</span>`;
                const no_pat = '<p class="text-gray-400 italic text-[10px]">কোনো ক্যান্ডেলস্টিক প্যাটার্ন নেই</p>';
                
                document.getElementById('pats1').innerHTML = d.analysis_1m.pats.length > 0 ? d.analysis_1m.pats.map(tag).join('') : no_pat;
                document.getElementById('pats3').innerHTML = d.analysis_3m.pats.length > 0 ? d.analysis_3m.pats.map(tag).join('') : no_pat;

                // ট্রেড হিস্ট্রি ডাটা টেবিল আপডেট
                document.getElementById('hb').innerHTML = d.history.slice(0,5).map(h => `
                    <tr class="border-b border-slate-50">
                        <td class="py-2 text-slate-400 font-bold">${h.t}</td>
                        <td class="font-black text-center ${h.a=='BUY'?'text-blue-500':'text-orange-500'}">${h.a}</td>
                        <td class="text-right font-black">$${h.p}</td>
                        <td class="text-right font-black ${h.r.includes('-')?'text-red-400':'text-green-500'}">${h.r}</td>
                    </tr>
                `).join('');
                
                // লাইভ লগ মেসেজ আপডেট
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `
                    <div class="flex justify-between text-slate-500 pb-1">
                        <span>${l.t}</span>
                        <span>${l.m}</span>
                    </div>
                `).join('');
            }
        } catch (e) {}
    }
    // প্রতি ৩ সেকেন্ড পর পর ড্যাশবোর্ড আপডেট হবে
    setInterval(update, 3000); 
    update();
</script>
</body>
</html>
"""


# =====================================================================
# SECTION 7: অ্যাপ্লিকেশন এক্সিকিউশন ব্লক (Run App)
# =====================================================================
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
