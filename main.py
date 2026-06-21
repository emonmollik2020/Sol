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

# ফিউচার্স পেয়ার এবং সেটিংস
SYMBOL = "SOL/USDT:USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0

# ১০x লিভারেজ এবং প্রফেশনাল রিস্ক পার্সেন্টেজ
LEVERAGE = 10
RISK_FRACTION = 0.02 # প্রতিটি ট্রেডে মোট ফান্ডের সর্বোচ্চ ২% রিস্ক নেবে

# সুইং ও ট্রেন্ড ট্রেডিংয়ের জন্য স্ট্যান্ডার্ড স্টপ লস ও টেক প্রফিট
DEF_TP = 0.035  
DEF_SL = 0.020  

# থ্রেড লক
STATE_LOCK = threading.Lock()

# এক্সচেঞ্জ কানেকশন (সিমুলেশন/পেপার ট্রেডিং এর জন্য API Key ছাড়া)
exchange = ccxt.bitget({'enableRateLimit': True})

# ডিফল্ট স্টেট
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
    "position_type": "NONE", # "LONG", "SHORT", "NONE"
    "peak_p": 0.0,            
    "valley_p": 0.0,          
    "live_pnl_pct": 0.0,
    "live_pnl_val": 0.0,
    "entry_price": 0.0,
    "sl_level": 0.0,
    "tp_level": 0.0,
    "pos_size": 0.0,  
    "margin": 0.0,    
    "entry_sl_pct": 0.0, 
    "analysis_15m": {"rsi": 0, "ema20": 0, "ema50": 0, "vwap": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_1h": {"rsi": 0, "ema200": 0, "btc_price": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "confluences": {
        "macro_bullish": False, "btc_bullish": False, "vwap_long": False, "volume_confirmed": False,
        "ema_long": False, "macd_long": False, "bull_signal": False,
        "macro_bearish": False, "btc_bearish": False, "vwap_short": False,
        "ema_short": False, "macd_short": False, "bear_signal": False
    },
    "exit_conditions": { 
        "long_smart_sell_safe": True, "short_smart_sell_safe": True,
        "is_breakeven": False
    },
    "wait_reason": "লোড হচ্ছে...",
    "log": [],
    "history": []
}

LAST_LOADED_TIME = 0
CACHED_STATE = DEFAULT_STATE.copy()


# =====================================================================
# SECTION 2: ফাইল ম্যানেজমেন্ট
# =====================================================================
def save_state(d):
    with STATE_LOCK:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(d, f)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error saving state: {e}")


def load_state():
    global LAST_LOADED_TIME, CACHED_STATE
    with STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            return DEFAULT_STATE.copy()
        try:
            mtime = os.path.getmtime(STATE_FILE)
            if mtime > LAST_LOADED_TIME:
                with open(STATE_FILE, "r") as f:
                    CACHED_STATE = json.load(f)
                LAST_LOADED_TIME = mtime
            return CACHED_STATE.copy()
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error loading state: {e}")
            return DEFAULT_STATE.copy()


app = Flask(__name__)


# =====================================================================
# SECTION 3: ক্যান্ডেলস্টিক প্যাটার্ন ডিটেক্টর
# =====================================================================
def get_advanced_pats(df):
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

    # বুলিশ প্যাটার্নস
    if b1 > 0 and l1 >= 1.8 * b1 and u1 <= 0.2 * b1: p.append({"n": "হ্যামার 🔨", "t": "bull"})
    if b1 > 0 and u1 >= 1.8 * b1 and l1 <= 0.2 * b1 and g1: p.append({"n": "ইনভার্টেড হ্যামার 🔨", "t": "bull"})
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: p.append({"n": "বুলিশ এনগালফিং 📈", "t": "bull"})
    if not g3 and b2 < (b3 * 0.3) and g1 and c1['c'] > (c3['o'] + c3['c']) / 2: p.append({"n": "মর্নিং স্টার 🌅", "t": "bull"})
    if b1 / t1 > 0.85 and g1: p.append({"n": "বুলিশ মারুবোজু 💪", "t": "bull"})
    if not g2 and g1 and c1['o'] < c2['c'] and c1['c'] > (c2['o'] + c2['c']) / 2 and c1['c'] < c2['o']: p.append({"n": "পিয়ার্সিং লাইন ⚡", "t": "bull"})
    if not g2 and g1 and c1['c'] < c2['o'] and c1['o'] > c2['c'] and b1 < b2: p.append({"n": "বুলিশ হারামি 🤰", "t": "bull"})
    if g1 and g2 and g3 and c1['c'] > c2['c'] and c2['c'] > c3['c'] and b1 > 0.3 * t1 and b2 > 0.3 * t2: p.append({"n": "থ্রি হোয়াইট সোলজার্স 💂‍♂️", "t": "bull"})
    if abs(c1['l'] - c2['l']) / max(0.001, c1['l']) < 0.001 and not g2 and g1: p.append({"n": "টুইজার বটম 🧲", "t": "bull"})

    # বেয়ারিশ প্যাটার্নস
    if b1 > 0 and u1 >= 1.8 * b1 and l1 <= 0.2 * b1 and not g1: p.append({"n": "শুটিং স্টার ☄️", "t": "bear"})
    if b1 > 0 and l1 >= 1.8 * b1 and u1 <= 0.2 * b1 and not g1: p.append({"n": "হ্যাঙ্গিং ম্যান 🕴️", "t": "bear"})
    if g2 and not g1 and c1['c'] <= c2['o'] and c1['o'] >= c2['c']: p.append({"n": "বেয়ারিশ এনগালফিং 📉", "t": "bear"})
    if g3 and b2 < (b3 * 0.3) and not g1 and c1['c'] < (c3['o'] + c3['c']) / 2: p.append({"n": "ইভনিং স্টার 🌅", "t": "bear"})
    if b1 / t1 > 0.85 and not g1: p.append({"n": "বেয়ারিশ মারুবোজু 🔴", "t": "bear"})
    if g2 and not g1 and c1['o'] > c2['c'] and c1['c'] < (c2['o'] + c2['c']) / 2 and c1['c'] > c2['o']: p.append({"n": "ডার্ক ক্লাউড কভার ⛈️", "t": "bear"})
    if g2 and not g1 and c1['c'] > c2['o'] and c1['o'] < c2['c'] and b1 < b2: p.append({"n": "বেয়ারিশ হারামি 🤰", "t": "bear"})
    if not g1 and not g2 and not g3 and c1['c'] < c2['c'] and c2['c'] < c3['c'] and b1 > 0.3 * t1 and b2 > 0.3 * t2: p.append({"n": "থ্রি ব্ল্যাক ক্রোস 🐦", "t": "bear"})
    return p


# =====================================================================
# SECTION 4: টু-ওয়ে ফিউচার্স ট্রেডিং বট ইঞ্জিন (১০x লিভারেজ)
# =====================================================================
def bot_engine():
    cur_init = load_state()
    total = cur_init.get("trades", 0)
    win_rate = cur_init.get("win_rate", 0)
    wins = int((win_rate / 100.0) * total) if total > 0 else 0
    net_pnl = cur_init.get("total_pnl", 0.0)
    
    position_type = cur_init.get("position_type", "NONE")
    peak_p = cur_init.get("peak_p", 0.0)
    valley_p = cur_init.get("valley_p", 0.0)
    in_pos = cur_init.get("in_position", False)
    entry_p = cur_init.get("entry_price", 0.0)
    
    last_trade_time = 0         
    COOLDOWN_SECONDS = 900      

    while True:
        try:
            bars15 = exchange.fetch_ohlcv(SYMBOL, '15m', limit=1000)
            df15 = pd.DataFrame(bars15, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            
            bars_btc = exchange.fetch_ohlcv("BTC/USDT", '15m', limit=50)
            df_btc = pd.DataFrame(bars_btc, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            
            df15['dt'] = pd.to_datetime(df15['t'], unit='ms')
            df15.set_index('dt', inplace=True)
            
            df1h = df15.resample('1h').agg({
                't': 'first',
                'o': 'first',
                'h': 'max',
                'l': 'min',
                'c': 'last',
                'v': 'sum'
            }).dropna()
            df1h.reset_index(drop=True, inplace=True)
            df15.reset_index(drop=True, inplace=True)
            
            p = df15['c'].iloc[-1]
            high_p = df15['h'].iloc[-1] 
            low_p = df15['l'].iloc[-1]
            
            btc_p = df_btc['c'].iloc[-1]
            btc_e20 = ta.trend.ema_indicator(df_btc['c'], 20).fillna(0).iloc[-1]
            btc_bullish = btc_p > btc_e20  
            btc_bearish = btc_p < btc_e20  
            
            sol_vol_ma = df15['v'].rolling(window=15).mean().fillna(0).iloc[-1]
            sol_current_vol = df15['v'].iloc[-1]
            volume_confirmed = sol_current_vol > (1.2 * sol_vol_ma)
            
            vwap_series = ta.volume.volume_weighted_average_price(high=df15['h'], low=df15['l'], close=df15['c'], volume=df15['v'], window=14)
            vwap = vwap_series.fillna(0).iloc[-1]
            vwap_long_confirmed = p > vwap   
            vwap_short_confirmed = p < vwap  
            
            atr = ta.volatility.average_true_range(high=df15['h'], low=df15['l'], close=df15['c'], window=14).fillna(0).iloc[-1]
            atr_pct = atr / p
            dynamic_tp_pct = max(0.015, min(0.060, 2.5 * atr_pct))  
            dynamic_sl_pct = max(0.010, min(0.035, 1.5 * atr_pct))  
            
            r15 = ta.momentum.rsi(df15['c'], window=14).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df15['c'], 20).fillna(0).iloc[-1]
            e50 = ta.trend.ema_indicator(df15['c'], 50).fillna(0).iloc[-1]
            
            r1h = ta.momentum.rsi(df1h['c'], window=14).fillna(0).iloc[-1]
            
            # --- কঠোর শর্তে EMA 200 লজিক (ডাটা না পেলে কোনো ফলব্যাক বা ট্রেড নয়) ---
            e200_series = ta.trend.ema_indicator(df1h['c'], 200)
            
            # ডাটা পর্যাপ্ত (কমপক্ষে ২০০ ক্যান্ডেল) এবং EMA 200 ভ্যালিড কি না তা চেক করা হচ্ছে
            if len(df1h) >= 200 and e200_series is not None and not pd.isna(e200_series.iloc[-1]) and e200_series.iloc[-1] > 0:
                e200 = e200_series.iloc[-1]
                ema_200_available = True
            else:
                e200 = 0.0
                ema_200_available = False # ডাটা ফেইল্ড বা অপর্যাপ্ত
            
            m_obj = ta.trend.MACD(df1h['c'])
            mv = m_obj.macd().iloc[-1]
            ms = m_obj.macd_signal().iloc[-1]
            
            pats15 = get_advanced_pats(df15)
            pats1h = get_advanced_pats(df1h)
            
            cur = load_state()
            in_pos = cur.get("in_position", False)
            entry_p = cur.get("entry_price", 0.0)
            
            for k, v in DEFAULT_STATE.items():
                if k not in cur:
                    cur[k] = v
                    
            if in_pos:
                pos_size_usd = cur.get("pos_size", 0.0)
                if position_type == "LONG":
                    l_pnl = ((p / entry_p) - 1) * 100 * LEVERAGE
                    l_val = pos_size_usd * ((p / entry_p) - 1)
                else: 
                    l_pnl = (1 - (p / entry_p)) * 100 * LEVERAGE
                    l_val = pos_size_usd * (1 - (p / entry_p))
            else:
                l_pnl = 0.0
                l_val = 0.0

            time_since_last_trade = time.time() - last_trade_time
            cooldown_over = time_since_last_trade >= COOLDOWN_SECONDS

            bull_signal = any(pt['t'] == 'bull' for pt in pats15) or any(pt['t'] == 'bull' for pt in pats1h)
            bear_signal = any(pt['t'] == 'bear' for pt in pats15) or any(pt['t'] == 'bear' for pt in pats1h)
            
            # ডাটা না থাকলে ট্রেন্ড কন্ডিশন দুটোই False হয়ে যাবে, অর্থাৎ কোনো এন্ট্রি হবে না
            macro_bullish = (p > e200) if ema_200_available else False
            macro_bearish = (p < e200) if ema_200_available else False
            
            ema_long_alignment = p > e20 and p > e50
            ema_short_alignment = p < e20 and p < e50
            
            can_buy_long = (macro_bullish and 
                            ema_long_alignment and 
                            btc_bullish and 
                            volume_confirmed and 
                            vwap_long_confirmed and 
                            (40 < r15 < 65) and 
                            (mv > ms) and 
                            bull_signal and 
                            cooldown_over)

            can_buy_short = (macro_bearish and 
                             ema_short_alignment and 
                             btc_bearish and 
                             volume_confirmed and 
                             vwap_short_confirmed and 
                             (35 < r15 < 60) and 
                             (mv < ms) and 
                             bear_signal and 
                             cooldown_over)

            long_smart_sell = p < e50 or r15 > 78
            short_smart_sell = p > e50 or r15 < 22

            if in_pos:
                initial_sl_dist_pct = cur.get("entry_sl_pct", DEF_SL)
                if initial_sl_dist_pct <= 0:
                    initial_sl_dist_pct = DEF_SL
                
                # LONG পজিশন ম্যানেজমেন্ট
                if position_type == "LONG":
                    breakeven_trigger = entry_p * (1 + (0.6 * initial_sl_dist_pct))
                    if p >= breakeven_trigger and cur["sl_level"] < entry_p:
                        cur.update({"sl_level": round(entry_p, 2)})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🛡️ SL Breakeven-এ উন্নীত [🟢 LONG]"})

                    if p > peak_p:
                        peak_p = p
                        new_sl = round(p * (1 - initial_sl_dist_pct), 2)
                        if new_sl > cur["sl_level"]:
                            cur.update({"sl_level": new_sl, "peak_p": peak_p})

                    if high_p >= cur["tp_level"] or low_p <= cur["sl_level"] or long_smart_sell:
                        in_pos = False
                        position_type = "NONE"
                        
                        if low_p <= cur["sl_level"]:
                            exit_p = cur["sl_level"]
                            exit_reason = "Stop Loss"
                        elif high_p >= cur["tp_level"]:
                            exit_p = cur["tp_level"]
                            exit_reason = "Take Profit"
                        else:
                            exit_p = p
                            exit_reason = "Smart Exit"
                            
                        final_pnl_val = pos_size_usd * ((exit_p / entry_p) - 1)
                        fee_usd = pos_size_usd * 0.0012
                        final_pnl_val = final_pnl_val - fee_usd
                        
                        net_pnl += final_pnl_val
                        if exit_p > entry_p: wins += 1
                        
                        best_val = cur.get("best", 0.0)
                        worst_val = cur.get("worst", 0.0)
                        if final_pnl_val > best_val: best_val = final_pnl_val
                        if final_pnl_val < worst_val: worst_val = final_pnl_val
                        
                        cur.update({
                            "balance": round(100.0 + net_pnl, 2),
                            "total_pnl": round(net_pnl, 2),
                            "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
                            "best": round(best_val, 2),
                            "worst": round(worst_val, 2),
                            "last_action": "SELL",
                            "in_position": False,
                            "position_type": "NONE",
                            "pos_size": 0.0,
                            "margin": 0.0,
                            "peak_p": 0.0,
                            "entry_sl_pct": 0.0
                        })
                        cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "SELL", "p": round(exit_p, 2), "r": f"{round((final_pnl_val / (pos_size_usd / LEVERAGE)) * 100, 2)}%"})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 LONG Exit @ ${exit_p:.2f} ({exit_reason}, Fee: ${fee_usd:.2f})"})
                        last_trade_time = time.time()

                # SHORT পজিশন ম্যানেজমেন্ট
                elif position_type == "SHORT":
                    breakeven_trigger = entry_p * (1 - (0.6 * initial_sl_dist_pct))
                    if p <= breakeven_trigger and cur["sl_level"] > entry_p:
                        cur.update({"sl_level": round(entry_p, 2)})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🛡️ SL Breakeven-এ উন্নীত [🔴 SHORT]"})

                    if valley_p == 0.0 or p < valley_p:
                        valley_p = p
                        new_sl = round(p * (1 + initial_sl_dist_pct), 2)
                        if cur["sl_level"] == 0.0 or new_sl < cur["sl_level"]:
                            cur.update({"sl_level": new_sl, "valley_p": valley_p})

                    if low_p <= cur["tp_level"] or high_p >= cur["sl_level"] or short_smart_sell:
                        in_pos = False
                        position_type = "NONE"
                        
                        if high_p >= cur["sl_level"]:
                            exit_p = cur["sl_level"]
                            exit_reason = "Stop Loss"
                        elif low_p <= cur["tp_level"]:
                            exit_p = cur["tp_level"]
                            exit_reason = "Take Profit"
                        else:
                            exit_p = p
                            exit_reason = "Smart Exit"
                            
                        final_pnl_val = pos_size_usd * (1 - (exit_p / entry_p))
                        fee_usd = pos_size_usd * 0.0012
                        final_pnl_val = final_pnl_val - fee_usd
                        
                        net_pnl += final_pnl_val
                        if exit_p < entry_p: wins += 1
                        
                        best_val = cur.get("best", 0.0)
                        worst_val = cur.get("worst", 0.0)
                        if final_pnl_val > best_val: best_val = final_pnl_val
                        if final_pnl_val < worst_val: worst_val = final_pnl_val
                        
                        cur.update({
                            "balance": round(100.0 + net_pnl, 2),
                            "total_pnl": round(net_pnl, 2),
                            "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
                            "best": round(best_val, 2),
                            "worst": round(worst_val, 2),
                            "last_action": "SELL",
                            "in_position": False,
                            "position_type": "NONE",
                            "pos_size": 0.0,
                            "margin": 0.0,
                            "valley_p": 0.0,
                            "entry_sl_pct": 0.0
                        })
                        cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "SELL", "p": round(exit_p, 2), "r": f"{round((final_pnl_val / (pos_size_usd / LEVERAGE)) * 100, 2)}%"})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SHORT Exit @ ${exit_p:.2f} ({exit_reason}, Fee: ${fee_usd:.2f})"})
                        last_trade_time = time.time()
            else:
                if can_buy_long:
                    entry_p = p
                    peak_p = p
                    in_pos = True
                    position_type = "LONG"
                    total += 1
                    
                    account_balance = cur.get("balance", INITIAL_FUND)
                    risk_amount = account_balance * RISK_FRACTION
                    pos_size_usd = risk_amount / dynamic_sl_pct
                    pos_size_usd = max(10.0, min(account_balance * LEVERAGE, pos_size_usd)) 
                    margin_usd = pos_size_usd / LEVERAGE  
                    
                    cur.update({
                        "trades": total,
                        "balance": round(account_balance, 2),
                        "in_position": True,
                        "position_type": "LONG",
                        "sl_level": round(p * (1 - dynamic_sl_pct), 2),
                        "tp_level": round(p * (1 + dynamic_tp_pct), 2),
                        "last_action": "BUY",
                        "pos_size": round(pos_size_usd, 2),
                        "margin": round(margin_usd, 2),
                        "peak_p": peak_p,
                        "entry_sl_pct": dynamic_sl_pct 
                    })
                    cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "BUY", "p": round(p, 2), "r": "---" })
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY [LONG] @ ${p:.2f} (Size: ${pos_size_usd:.2f})"})
                
                elif can_buy_short:
                    entry_p = p
                    valley_p = p
                    in_pos = True
                    position_type = "SHORT"
                    total += 1
                    
                    account_balance = cur.get("balance", INITIAL_FUND)
                    risk_amount = account_balance * RISK_FRACTION
                    pos_size_usd = risk_amount / dynamic_sl_pct
                    pos_size_usd = max(10.0, min(account_balance * LEVERAGE, pos_size_usd)) 
                    margin_usd = pos_size_usd / LEVERAGE  
                    
                    cur.update({
                        "trades": total,
                        "balance": round(account_balance, 2),
                        "in_position": True,
                        "position_type": "SHORT",
                        "sl_level": round(p * (1 + dynamic_sl_pct), 2), 
                        "tp_level": round(p * (1 - dynamic_tp_pct), 2), 
                        "last_action": "BUY",
                        "pos_size": round(pos_size_usd, 2),
                        "margin": round(margin_usd, 2),
                        "valley_p": valley_p,
                        "entry_sl_pct": dynamic_sl_pct 
                    })
                    cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "BUY", "p": round(p, 2), "r": "---" })
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY [SHORT] @ ${p:.2f} (Size: ${pos_size_usd:.2f})"})
            
            confluences = {
                "macro_bullish": bool(macro_bullish),
                "btc_bullish": bool(btc_bullish),
                "vwap_long": bool(vwap_long_confirmed),
                "volume_confirmed": bool(volume_confirmed),
                "ema_long": bool(ema_long_alignment),
                "macd_long": bool(mv > ms),
                "bull_signal": bool(bull_signal),
                "macro_bearish": bool(macro_bearish),
                "btc_bearish": bool(btc_bearish),
                "vwap_short": bool(vwap_short_confirmed),
                "ema_short": bool(ema_short_alignment),
                "macd_short": bool(mv < ms),
                "bear_signal": bool(bear_signal)
            }
            
            exit_conditions = {
                "long_smart_sell_safe": not long_smart_sell,
                "short_smart_sell_safe": not short_smart_sell,
                "is_breakeven": in_pos and (cur.get("sl_level") >= entry_p if position_type == "LONG" else cur.get("sl_level") <= entry_p)
            }

            cur.update({
                "price": round(p, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos,
                "position_type": position_type,
                "live_pnl_pct": round(l_pnl, 2),
                "live_pnl_val": round(l_val, 2),
                "entry_price": round(entry_p, 2),
                "confluences": confluences,
                "exit_conditions": exit_conditions,
                "analysis_15m": {
                    "rsi": round(r15, 1),
                    "ema20": round(e20, 2),
                    "ema50": round(e50, 2),
                    "vwap": round(vwap, 2),
                    "sig": "বুলিশ ✅" if p > e20 else "বেয়ারিশ ❌",
                    "pats": pats15
                },
                "analysis_1h": {
                    "rsi": round(r1h, 1),
                    "ema200": round(e200, 2),
                    "btc_price": round(btc_p, 1),
                    "sig": "বুলিশ ✅" if p > e200 else "বেয়ারিশ ❌" if ema_200_available else "ডাটা নেই 🛑",
                    "pats": pats1h
                }
            })
            
            # ড্যাশবোর্ডের জন্য ওয়েটিং মেসেজ সাজানো
            if in_pos:
                cur["wait_reason"] = f"পজিশন সক্রিয় [{position_type}]"
            elif not ema_200_available: # ডাটা না থাকলে প্রথম সতর্কতা
                cur["wait_reason"] = "পর্যাপ্ত ডাটা নেই (EMA 200 লোড হচ্ছে, ট্রেড বন্ধ 🛑)"
            elif not cooldown_over:
                remaining_seconds = int(COOLDOWN_SECONDS - time_since_last_trade)
                cur["wait_reason"] = f"কুলডাউন ({int(remaining_seconds/60)} মিনিট বাকি)"
            elif not btc_bullish and p > e200:
                cur["wait_reason"] = "বিটকয়েন ট্রেন্ড ডাউন (BTC Bearish)"
            elif btc_bullish and p < e200:
                cur["wait_reason"] = "বিটকয়েন ট্রেন্ড আপ (SOL SHORT এর উপযুক্ত নয়)"
            elif not vwap_long_confirmed and p > e200:
                cur["wait_reason"] = "মূল্য VWAP লাইনের নিচে (Bearish Volume Zone)"
            elif not vwap_short_confirmed and p < e200:
                cur["wait_reason"] = "মূল্য VWAP লাইনের ওপরে (Bullish Volume Zone)"
            elif not volume_confirmed:
                cur["wait_reason"] = "দুর্বল ভলিউম (ভলিউম ব্রেকআউটের অপেক্ষা)"
            elif not ema_long_alignment and p > e200:
                cur["wait_reason"] = "১৫-মিনিট চার্টে ল্যাপ বা রিট্রেসমেন্ট চলছে"
            elif not ema_short_alignment and p < e200:
                cur["wait_reason"] = "১৫-মিনিট চার্টে বাউন্স ব্যাক বা কারেকশন চলছে"
            else:
                cur["wait_reason"] = "সুইং এন্ট্রি প্যাটার্ন খুঁজছে..."
                
            save_state(cur)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot Engine Warning: {e}")
            
        time.sleep(10)


# ব্যাকগ্রাউন্ড থ্রেড চালু করা
threading.Thread(target=bot_engine, daemon=True).start()


# =====================================================================
# SECTION 5: Flask ওয়েব সার্ভার এবং এপিআই রাউটস
# =====================================================================
@app.route('/api/data')
def api():
    return jsonify(load_state())


@app.route('/')
def index():
    return render_template_string(UI)


# =====================================================================
# SECTION 6: ড্যাশবোর্ড UI টেমপ্লেট
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
    <div class="flex justify-center gap-2 mb-6 text-center">
        <span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span>
        <span class="bg-blue-100 text-blue-700 px-4 py-1 rounded-lg text-xs font-bold border border-blue-200">&#128640; ফিউচার্স ১০x লিভারেজ</span>
    </div>
    
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
        <div class="flex justify-between items-center mb-4">
            <span id="pr" class="text-4xl font-black tracking-tighter">$0.00</span>
            <div class="text-right text-[10px] text-slate-400 font-bold">ব্যালেন্স: <b id="bl">$100.00</b></div>
        </div>
        
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg">
            <div class="flex justify-between items-center mb-2">
                <p class="text-[10px] font-bold text-slate-400 uppercase">লাইভ পজিশন প্রফিট</p>
                <span id="pos_type" class="text-[10px] font-black px-2 py-0.5 rounded uppercase">NONE</span>
            </div>
            <p id="lp" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t pt-2">
                <div class="text-red-500">🛑 SL: <span id="sl">0</span></div>
                <div class="text-green-600">✅ TP: <span id="tp">0</span></div>
            </div>
        </div>
        
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 text-center uppercase tracking-wide italic">&#8987; লোড হচ্ছে...</div>
    </div>

    <div class="card p-4 mb-4 text-[11px]">
        <h3 class="font-bold text-slate-700 text-xs mb-3 flex justify-between items-center">
            <span>🛡️ প্রাতিষ্ঠানিক টু-ওয়ে চেকলিস্ট</span>
            <span class="text-[9px] px-2 py-0.5 rounded font-black bg-blue-100 text-blue-700 border border-blue-200 uppercase">2-WAY MONITOR</span>
        </h3>
        
        <div class="border-b pb-3 mb-3 border-slate-100">
            <p class="text-[10px] font-black text-green-700 mb-2 flex items-center gap-1">🟢 LONG MODE (আপট্রেন্ড কন্ডিশনস) 📈</p>
            <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="long_checklist"></div>
        </div>

        <div>
            <p class="text-[10px] font-black text-red-700 mb-2 flex items-center gap-1">🔴 SHORT MODE (ডাউনট্রেন্ড কন্ডিশনস) 📉</p>
            <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="short_checklist"></div>
        </div>
    </div>
    
    <div class="card p-4 mb-4 text-[11px]" id="exit_checklist_panel" class="hidden">
        <h3 class="font-bold text-slate-700 text-xs mb-3 flex justify-between items-center">
            <span>🚪 এক্সিট কন্ডিশনস চেকলিস্ট</span>
            <span id="exit_mode" class="text-[9px] px-2 py-0.5 rounded font-black uppercase">EXIT MONITOR</span>
        </h3>
        <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="exit_checklist"></div>
    </div>

    <div class="card p-4 mb-4 text-[11px]">
        <div class="flex justify-between mb-3 items-center">
            <h3 class="font-bold text-slate-700 text-xs">&#128202; ১৫ মিনিট বিশ্লেষণ</h3>
            <span id="s15" class="font-bold px-2 py-0.5 rounded text-[10px]">WAIT</span>
        </div>
        <div class="grid grid-cols-2 text-slate-500 font-medium">
            <span>RSI: <b id="r15">0</b></span>
            <span>EMA 20: <b id="e20">0</b></span>
            <span>EMA 50: <b id="e50">0</b></span>
            <span>VWAP: <b id="vw">0</b></span>
        </div>
        <div id="pats15" class="mt-3 flex flex-wrap gap-1"></div>
    </div>
    
    <div class="card p-4 mb-4 text-[11px]">
        <div class="flex justify-between mb-3 items-center">
            <h3 class="font-bold text-slate-700 text-xs">&#128202; ১ ঘণ্টা বিশ্লেষণ</h3>
            <span id="s1h" class="font-bold px-2 py-0.5 rounded text-[10px]">WAIT</span>
        </div>
        <div class="grid grid-cols-2 text-slate-500 font-medium">
            <span>RSI: <b id="r1h">0</b></span>
            <span>EMA 200: <b id="e200">0</b></span>
            <span>BTC Price: <b id="bp">0</b></span>
        </div>
        <div id="pats1h" class="mt-3 flex flex-wrap gap-1"></div>
    </div>

    <div class="card overflow-hidden h-60 mb-4 border border-slate-100 shadow-inner">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=15&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    
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
    
    <div class="card p-4 mb-6">
        <h3 class="font-bold text-slate-700 text-xs mb-2 uppercase tracking-widest">&#128214; লাইভ লগ</h3>
        <div id="lg" class="space-y-1 text-[10px]"></div>
    </div>
</div>

<script>
    function renderCheckItem(label, is_passed) {
        const icon = is_passed ? '✅' : '❌';
        const color = is_passed ? 'text-slate-800' : 'text-slate-400 font-normal';
        return `<div class="flex items-center gap-1.5 ${color}"><span>${icon}</span><span>${label}</span></div>`;
    }

    async function update() {
        try {
            const r = await fetch('/api/data'); 
            const d = await r.json();
            
            if (d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price; 
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                
                document.getElementById('t').innerText = d.trades; 
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = (d.total_pnl >= 0 ? '+$' : '$') + d.total_pnl.toFixed(2);
                document.getElementById('bt').innerText = '$' + d.best.toFixed(2); 
                document.getElementById('wt').innerText = '$' + d.worst.toFixed(2);
                document.getElementById('la').innerText = d.last_action; 
                document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                
                const exitPanel = document.getElementById('exit_checklist_panel');
                if (d.in_position) {
                    exitPanel.classList.remove('hidden');
                    const disp = document.getElementById('pnl_display'); 
                    disp.classList.remove('hidden');
                    
                    document.getElementById('lp').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl').innerText = d.sl_level; 
                    document.getElementById('tp').innerText = d.tp_level;
                    
                    const col = d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
                    document.getElementById('lp').className = 'text-4xl font-black ' + col;
                    disp.className = 'mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg ' + (d.live_pnl_pct >= 0 ? 'border-green-100' : 'border-red-100');
                    
                    const p_type = document.getElementById('pos_type');
                    p_type.innerText = d.position_type;
                    
                    const exit_mode = document.getElementById('exit_mode');
                    const exit_container = document.getElementById('exit_checklist');
                    let exit_html = '';
                    
                    if (d.position_type === 'LONG') {
                        p_type.className = 'text-[10px] font-black px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 uppercase';
                        exit_mode.innerText = 'LONG EXIT MONITOR 🟢';
                        exit_mode.className = 'text-[9px] px-2 py-0.5 rounded font-black bg-green-50 text-green-700 border border-green-100';
                        exit_html += renderCheckItem('স্মার্ট এক্সিট নিরাপদ', d.exit_conditions.long_smart_sell_safe);
                        exit_html += renderCheckItem('ব্রেকইভেন অর্জিত', d.exit_conditions.is_breakeven);
                    } else if (d.position_type === 'SHORT') {
                        p_type.className = 'text-[10px] font-black px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200 uppercase';
                        exit_mode.innerText = 'SHORT EXIT MONITOR 🔴';
                        exit_mode.className = 'text-[9px] px-2 py-0.5 rounded font-black bg-red-50 text-red-700 border border-red-100';
                        exit_html += renderCheckItem('স্মার্ট এক্সিট নিরাপদ', d.exit_conditions.short_smart_sell_safe);
                        exit_html += renderCheckItem('ব্রেকইভেন অর্জিত', d.exit_conditions.is_breakeven);
                    } else {
                        p_type.className = 'hidden';
                    }
                    exit_container.innerHTML = exit_html;
                } else { 
                    exitPanel.classList.add('hidden');
                    document.getElementById('pnl_display').classList.add('hidden'); 
                }
                
                const long_container = document.getElementById('long_checklist');
                const short_container = document.getElementById('short_checklist');
                const conf = d.confluences;
                
                let long_html = '';
                long_html += renderCheckItem('১ঘণ্টা ম্যাক্রো আপট্রেন্ড', conf.macro_bullish);
                long_html += renderCheckItem('বিটকয়েন ট্রেন্ড আপ', conf.btc_bullish);
                long_html += renderCheckItem('মূল্য VWAP এর ওপরে', conf.vwap_long);
                long_html += renderCheckItem('১৫মি ইএমএ এলাইনমেন্ট', conf.ema_long);
                long_html += renderCheckItem('১ঘণ্টা ম্যাকডি বুলিশ', conf.macd_long);
                long_html += renderCheckItem('ভলিউম ব্রেকআউট কনফার্ম', conf.volume_confirmed);
                long_html += renderCheckItem('সবুজ ক্যান্ডেল প্যাটার্ন', conf.bull_signal);
                long_container.innerHTML = long_html;
                
                let short_html = '';
                short_html += renderCheckItem('১ঘণ্টা ম্যাক্রো ডাউনট্রেন্ড', conf.macro_bearish);
                short_html += renderCheckItem('বিটকয়েন ট্রেন্ড ডাউন', conf.btc_bearish);
                short_html += renderCheckItem('মূল্য VWAP এর নিচে', conf.vwap_short);
                short_html += renderCheckItem('১৫মি ইএমএ ডাউন-এলাইন', conf.ema_short);
                short_html += renderCheckItem('১ঘণ্টা ম্যাকডি বেয়ারিশ', conf.macd_short);
                short_html += renderCheckItem('ভলিউম ব্রেকআউটের কনফার্ম', conf.volume_confirmed);
                short_html += renderCheckItem('লাল ক্যান্ডেল প্যাটার্ন', conf.bear_signal);
                short_container.innerHTML = short_html;
                
                document.getElementById('r15').innerText = d.analysis_15m.rsi; 
                document.getElementById('e20').innerText = '$' + d.analysis_15m.ema20;
                document.getElementById('e50').innerText = '$' + d.analysis_15m.ema50;
                document.getElementById('vw').innerText = '$' + d.analysis_15m.vwap;
                
                const s15 = document.getElementById('s15'); 
                s15.innerText = d.analysis_15m.sig;
                if (d.analysis_15m.sig.includes('বুলিশ')) {
                    s15.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-green-50 text-green-700 border border-green-200';
                } else if (d.analysis_15m.sig.includes('বেয়ারিশ')) {
                    s15.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-red-50 text-red-700 border border-red-200';
                } else {
                    s15.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200';
                }
                
                document.getElementById('r1h').innerText = d.analysis_1h.rsi; 
                document.getElementById('e200').innerText = '$' + d.analysis_1h.ema200;
                document.getElementById('bp').innerText = '$' + d.analysis_1h.btc_price;
                
                const s1h = document.getElementById('s1h'); 
                s1h.innerText = d.analysis_1h.sig;
                if (d.analysis_1h.sig.includes('বুলিশ')) {
                    s1h.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-green-50 text-green-700 border border-green-200';
                } else if (d.analysis_1h.sig.includes('বেয়ারিশ')) {
                    s1h.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-red-50 text-red-700 border border-red-200';
                } else {
                    s1h.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200';
                }

                const tag = (p) => `<span class="tag ${p.t==='bull'?'tag-bull':'tag-bear'}">${p.n}</span>`;
                const no_pat = '<p class="text-gray-400 italic text-[10px]">কোনো ক্যান্ডেলস্টিক প্যাটার্ন নেই</p>';
                
                document.getElementById('pats15').innerHTML = d.analysis_15m.pats.length > 0 ? d.analysis_15m.pats.map(tag).join('') : no_pat;
                document.getElementById('pats1h').innerHTML = d.analysis_1h.pats.length > 0 ? d.analysis_1h.pats.map(tag).join('') : no_pat;

                document.getElementById('hb').innerHTML = d.history.slice(0,5).map(h => `
                    <tr class="border-b border-slate-50">
                        <td class="py-2 text-slate-400 font-bold">${h.t}</td>
                        <td class="font-black text-center ${h.a=='BUY'?'text-blue-500':'text-orange-500'}">${h.a}</td>
                        <td class="text-right font-black">$${h.p}</td>
                        <td class="text-right font-black ${h.r.includes('-')?'text-red-400':'text-green-500'}">${h.r}</td>
                    </tr>
                `).join('');
                
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `
                    <div class="flex justify-between text-slate-500 pb-1">
                        <span>${l.t}</span>
                        <span>${l.m}</span>
                    </div>
                `).join('');
            }
        } catch (e) {}
    }
    setInterval(update, 5000); 
    update();
</script>
</body>
</html>
"""


# =====================================================================
# SECTION 7: অ্যাপ্লিকেশন এক্সিকিউশন ব্লক
# =====================================================================
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
