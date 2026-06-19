import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. সেটিংস ও ডাটা পাথ ==========
SYMBOL, STATE_FILE, INITIAL_FUND = "SOL/USDT", "bot_state.json", 100.0
DEF_TP, DEF_SL = 0.007, 0.010
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(d):
    with open(STATE_FILE, "w") as f: json.dump(d, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"price":0.0,"balance":100.0,"total_pnl":0.0,"last_update":"...","trades":0,"win_rate":0,"best":0.0,"worst":0.0,"last_action":"---","in_position":False,"live_pnl_pct":0.0,"live_pnl_val":0.0,"entry_price":0.0,"sl_level":0.0,"tp_level":0.0,"analysis_1m":{"rsi":0,"ema":0,"sig":"Loading","pats":[]},"analysis_3m":{"rsi":0,"macd":0,"sig":"Loading","pats":[]},"wait_reason":"লোড হচ্ছে...","log":[],"history":[]}
    with open(STATE_FILE, "r") as f:
        try: return json.load(f)
        except: return load_state()

app = Flask(__name__)

# --- ১৭টি অ্যাডভান্সড প্যাটার্ন ডিটেক্টর (বাংলা নাম ও ইমোজি সহ) ---
def get_advanced_pats(df):
    p = []
    if len(df) < 5: return p
    
    # শেষ ৩টি ক্যান্ডেলের ডাটা
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

    # ১. হ্যামার (Hammer)
    if b1 > 0 and l1 >= 2 * b1 and u1 <= 0.2 * b1: p.append({"n": "হ্যামার &#128296;", "t": "bull"})
    # ২. ইনভার্টেড হ্যামার (Inverted Hammer)
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: p.append({"n": "ইনভার্টেড হ্যামার &#128296;", "t": "bull"})
    # ৩. বুলিশ এনগালফিং (Bullish Engulfing)
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: p.append({"n": "বুলিশ এনগালফিং &#128200;", "t": "bull"})
    # ৪. বেয়ারিশ এনগালফিং (Bearish Engulfing)
    if g2 and not g1 and c1['c'] <= c2['o'] and c1['o'] >= c2['c']: p.append({"n": "বেয়ারিশ এনগালফিং &#128201;", "t": "bear"})
    # ৫. বুলিশ মারুবোজু (Bullish Marubozu)
    if b1 / t1 > 0.9 and g1: p.append({"n": "বুলিশ মারুবোজু &#128170;", "t": "bull"})
    # ৬. বেয়ারিশ মারুবোজু (Bearish Marubozu)
    if b1 / t1 > 0.9 and not g1: p.append({"n": "বেয়ারিশ মারুবোজু &#128308;", "t": "bear"})
    # ৭. মর্নিং স্টার (Morning Star)
    if not g3 and b2 < (b3 * 0.3) and g1 and c1['c'] > (c3['o'] + c3['c']) / 2: p.append({"n": "মর্নিং স্টার &#127749;", "t": "bull"})
    # ৮. ইভনিং স্টার (Evening Star)
    if g3 and b2 < (b3 * 0.3) and not g1 and c1['c'] < (c3['o'] + c3['c']) / 2: p.append({"n": "ইভনিং স্টার &#127751;", "t": "bear"})
    # ৯. শুটিং স্টার (Shooting Star)
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: p.append({"n": "শুটিং স্টার &#9732;", "t": "bear"})
    # ১০. হ্যাঙ্গিং ম্যান (Hanging Man)
    if b1 > 0 and l1 >= 2 * b1 and u1 <= 0.2 * b1 and not g1: p.append({"n": "হ্যাঙ্গিং ম্যান &#128128;", "t": "bear"})
    # ১১. পিয়ার্সিং লাইন (Piercing Line)
    if not g2 and g1 and c1['o'] < c2['l'] and c1['c'] > (c2['o'] + c2['c']) / 2: p.append({"n": "পিয়ার্সিং লাইন &#128200;", "t": "bull"})
    # ১২. ডার্ক ক্লাউড কভার (Dark Cloud Cover)
    if g2 and not g1 and c1['o'] > c2['h'] and c1['c'] < (c2['o'] + c2['c']) / 2: p.append({"n": "ডার্ক ক্লাউড কভার &#9729;", "t": "bear"})
    # ১৩. টুইজার বটম (Tweezer Bottom)
    if abs(c1['l'] - c2['l']) < (t1 * 0.05) and not g2 and g1: p.append({"n": "টুইজার বটম &#128205;", "t": "bull"})
    # ১৪. টুইজার টপ (Tweezer Top)
    if abs(c1['h'] - c2['h']) < (t1 * 0.05) and g2 and not g1: p.append({"n": "টুইজার টপ &#128205;", "t": "bear"})
    # ১৫. ডোজি (Doji)
    if b1 <= (t1 * 0.1): p.append({"n": "ডোজি &#9878;", "t": "neut"})
    # ১৬. স্পিনিং টপ (Spinning Top)
    if b1 > 0 and b1 < (t1 * 0.3) and u1 > b1 and l1 > b1: p.append({"n": "স্পিনিং টপ &#129352;", "t": "neut"})
    # ১৭. স্পিনিং বটম (Spinning Bottom)
    if b1 > 0 and b1 < (t1 * 0.3) and l1 > u1 and l1 > b1: p.append({"n": "স্পিনিং বটম &#129352;", "t": "bull"})
    
    return p

# --- ট্রেডিং ইঞ্জিন লজিক (সংশোধিত: ফিক্সড টিপি ও ট্রেলিং এসএল) ---
def bot_engine():
    wins, total, net_pnl, pnl_hist, in_pos, entry_p, peak_p = 0, 0, 0.0, [0], False, 0.0, 0.0
    while True:
        try:
            bars1, bars3 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=200), exchange.fetch_ohlcv(SYMBOL, '3m', limit=200)
            df1, df3 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v']), pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            p = df1['c'].iloc[-1]
            r1, e20 = ta.momentum.rsi(df1['c']).iloc[-1], ta.trend.ema_indicator(df1['c'], 20).iloc[-1]
            e50 = ta.trend.ema_indicator(df1['c'], 50).iloc[-1]
            r3, m_obj = ta.momentum.rsi(df3['c']).iloc[-1], ta.trend.MACD(df3['c'])
            mv, ms = m_obj.macd().iloc[-1], m_obj.macd_signal().iloc[-1]
            
            cur = load_state()
            l_pnl = ((p/entry_p)-1)*100 if in_pos else 0.0
            l_val = (100.0/entry_p*p)-100.0 if in_pos else 0.0

            # ট্রেলিং এসএল আপডেট (টিপি ফিক্সড থাকবে)
            if in_pos:
                if p > peak_p:
                    peak_p = p
                    cur.update({"sl_level": round(peak_p * (1 - DEF_SL), 2)})

            cur.update({"price":round(p,2),"last_update":datetime.now(timezone.utc).strftime("%H:%M:%S"),"in_position":in_pos,"live_pnl_pct":round(l_pnl,2),"live_pnl_val":round(l_val,2),"entry_price":round(entry_p,2),"analysis_1m":{"rsi":round(r1,1),"ema":round(e20,2),"ema50":round(e50,2),"sig":"বুলিশ ✅" if p>e20 else "বেয়ারিশ ❌","pats":get_advanced_pats(df1)},"analysis_3m":{"rsi":round(r3,1),"macd":round(mv,3),"sig":"বুলিশ ✅" if mv>ms else "অপেক্ষা","pats":get_advanced_pats(df3)}})

            if not in_pos and p>e20 and r1<65 and mv>ms:
                entry_p, peak_p, in_pos, total = p, p, True, total+1
                cur.update({"trades":total,"balance":0.0,"sl_level":round(p*(1-DEF_SL),2),"tp_level":round(p*(1+DEF_TP),2),"last_action":"BUY"})
                cur["history"].insert(0,{"t":datetime.now().strftime("%H:%M"),"a":"BUY","p":round(p,2),"r":"---"})
                cur["log"].insert(0,{"t":datetime.now().strftime("%H:%M"),"m":f"🟢 BUY @ ${p:.2f}"})
            elif in_pos and (p >= cur["tp_level"] or p <= cur["sl_level"]):
                in_pos = False
                net_pnl += l_val
                pnl_hist.append(net_pnl)
                if p > entry_p: wins += 1
                cur.update({"balance":round(100.0+net_pnl,2),"total_pnl":round(net_pnl,2),"win_rate":round((wins/total)*100,1),"best":round(max(pnl_hist),2),"worst":round(min(pnl_hist),2),"last_action":"SELL"})
                cur["history"].insert(0,{"t":datetime.now().strftime("%H:%M"),"a":"SELL","p":round(p,2),"r":f"{round(l_pnl,2)}%"})
                cur["log"].insert(0,{"t":datetime.now().strftime("%H:%M"),"m":f"🔴 SELL @ ${p:.2f}"})
            
            cur["wait_reason"] = "ট্রেড লাইভ আছে" if in_pos else ("১মি ট্রেন্ড দুর্বল" if p<=e20 else "এন্ট্রি খুঁজছে...")
            save_state(cur)
        except: pass
        time.sleep(10)

threading.Thread(target=bot_engine, daemon=True).start()
@app.route('/api/data')
def api(): return jsonify(load_state())
@app.route('/')
def index(): return render_template_string(UI)
    
UI = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Master SOL Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 600000);</script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { border: 1px solid #dcfce7; color: #166534; padding: 2px 10px; border-radius: 99px; font-size: 10px; font-weight: 800; display: inline-block; margin: 2px; }
        .tag-bull { background: #f0fdf4; } .tag-bear { background: #fef2f2; color: #991b1b; border-color: #fee2e2; }
    </style>
</head>
<body class="p-3 text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6"><span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span></div>
    
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

    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4"><span id="pr" class="text-4xl font-black tracking-tighter">$0.00</span><div class="text-right text-[10px] text-slate-400 font-bold">ব্যালেন্স: <b id="bl">$100.00</b></div></div>
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg">
            <p class="text-[10px] font-bold text-slate-400 uppercase mb-1">লাইভ পজিশন প্রফিট</p>
            <p id="lp" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t pt-2"><div class="text-red-500">🛑 SL: <span id="sl">0</span></div><div class="text-green-600">✅ TP: <span id="tp">0</span></div></div>
        </div>
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 text-center uppercase tracking-wide italic">&#8987; লোড হচ্ছে...</div>
    </div>

    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="s1" class="font-bold px-2 py-0.5 rounded bg-slate-100 text-[10px]">WAIT</span></div><div class="grid grid-cols-2 text-slate-500 font-medium"><span>RSI: <b id="r1">0</b></span><span>EMA 20: <b id="e1">0</b></span></div><div id="pats1" class="mt-3 flex flex-wrap gap-1"></div></div>
    
    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="s3" class="font-bold px-2 py-0.5 rounded bg-slate-100 text-[10px]">WAIT</span></div><div class="grid grid-cols-2 text-slate-500 font-medium"><span>RSI: <b id="r3">0</b></span><span>MACD: <b id="m3">0</b></span></div><div id="pats3" class="mt-3 flex flex-wrap gap-1"></div></div>

    <div class="card overflow-hidden h-60 mb-4 border border-slate-100 shadow-inner"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>
    
    <div class="card p-4 mb-4 overflow-hidden"><h3 class="font-black text-slate-700 text-[10px] mb-3 uppercase tracking-wider">&#128203; ট্রেড হিস্ট্রি</h3><div class="overflow-x-auto"><table class="w-full text-[10px] text-left"><thead class="text-slate-400 border-b"><tr><th class="pb-2">সময়</th><th class="pb-2 text-center">ধরন</th><th class="pb-2 text-right">মূল্য</th><th class="pb-2 text-right">P&L</th></tr></thead><tbody id="hb" class="divide-y divide-slate-50"></tbody></table></div></div>
    
    <div class="card p-4 mb-6"><h3 class="font-bold text-slate-700 text-xs mb-2 uppercase tracking-widest">&#128214; লাইভ লগ</h3><div id="lg" class="space-y-1 text-[10px]"></div></div>
</div>
<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price; document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                document.getElementById('t').innerText = d.trades; document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = (d.total_pnl >= 0 ? '+$' : '$') + d.total_pnl.toFixed(2);
                document.getElementById('bt').innerText = '$' + d.best.toFixed(2); document.getElementById('wt').innerText = '$' + d.worst.toFixed(2);
                document.getElementById('la').innerText = d.last_action; document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                if(d.in_position) {
                    const disp = document.getElementById('pnl_display'); disp.classList.remove('hidden');
                    document.getElementById('lp').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl').innerText = d.sl_level; document.getElementById('tp').innerText = d.tp_level;
                    const col = d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
                    document.getElementById('lp').className = 'text-4xl font-black ' + col;
                    disp.className = 'mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg ' + (d.live_pnl_pct >= 0 ? 'border-green-100' : 'border-red-100');
                } else { document.getElementById('pnl_display').classList.add('hidden'); }
                
                document.getElementById('r1').innerText = d.analysis_1m.rsi; document.getElementById('e1').innerText = '$' + d.analysis_1m.ema;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.sig;
                s1.className = 'font-bold px-2 py-0.5 rounded ' + (d.analysis_1m.sig.includes('বুলিশ')?'bg-green-50 text-green-600':'bg-red-50 text-red-400');
                
                document.getElementById('r3').innerText = d.analysis_3m.rsi; document.getElementById('m3').innerText = d.analysis_3m.macd;
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.sig;
                s3.className = 'font-bold px-2 py-0.5 rounded ' + (d.analysis_3m.sig.includes('বুলিশ')?'bg-green-50 text-green-600':'bg-slate-50 text-slate-400');

                // প্যাটার্ন ডিসপ্লে আপডেট লজিক
                const tag = (p) => `<span class="tag ${p.t==='bull'?'tag-bull':'tag-bear'}">${p.n}</span>`;
                const no_pat = '<p class="text-gray-400 italic text-[10px]">কোনো ক্যান্ডেলস্টিক প্যাটার্ন নেই</p>';
                
                document.getElementById('pats1').innerHTML = d.analysis_1m.pats.length > 0 ? d.analysis_1m.pats.map(tag).join('') : no_pat;
                document.getElementById('pats3').innerHTML = d.analysis_3m.pats.length > 0 ? d.analysis_3m.pats.map(tag).join('') : no_pat;

                document.getElementById('hb').innerHTML = d.history.slice(0,5).map(h => `<tr class="border-b border-slate-50"><td class="py-2 text-slate-400 font-bold">${h.t}</td><td class="font-black ${h.a=='BUY'?'text-blue-500':'text-orange-500'}">${h.a}</td><td class="text-right font-black">$${h.p}</td><td class="text-right font-black ${h.r.includes('-')?'text-red-400':'text-green-500'}">${h.r}</td></tr>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between text-slate-500 pb-1"><span>${l.t}</span><span>${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 3000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
