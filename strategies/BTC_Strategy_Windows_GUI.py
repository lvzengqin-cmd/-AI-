#!/usr/bin/env python3
"""
Kimi AI 5分钟事件合约策略系统 v3.1 - Windows 版本
 standalone executable for Windows 10
"""
import json
import urllib.request
import numpy as np
import talib
from datetime import datetime
from statistics import mean
import time
import sys
import os
import threading
import queue

# ============ 配置 ============
# Windows 用户使用系统代理或 Clash/V2Ray 代理
PROXY = os.environ.get('HTTP_PROXY', 'http://127.0.0.1:10809')
KIMI_API_KEY = "sk-SbvcckeTtsj3097DggA52DNltijS64zdTIY5MjQBlpbIsF2w"
KIMI_API_URL = "https://api.moonshot.cn/v1/chat/completions"

WEBHOOK_LONG = "https://fwalert.com/a56f6ad8-f487-42a0-a781-b6edb1532c0f"
WEBHOOK_SHORT = "https://fwalert.com/acb6f3e6-56cb-46fb-bc2a-dde48a655d48"

# Windows 日志路径
LOG_DIR = os.path.join(os.path.expanduser('~'), 'Documents', 'BTC_Strategy_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

SIGNAL_LOG = os.path.join(LOG_DIR, 'btc_signals.log')
ERROR_LOG = os.path.join(LOG_DIR, 'btc_errors.log')
STATUS_LOG = os.path.join(LOG_DIR, 'strategy_status.log')

# ============ GUI 状态队列 ============
status_queue = queue.Queue()
signal_queue = queue.Queue()

# ============ 运行状态追踪 ============
start_time = time.time()
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 10
call_count = 0

signal_stats = {"UP": 0, "DOWN": 0, "last_signal_time": 0, "last_signal_direction": None}

running = True

def log_status(msg):
    """记录状态日志"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    status_queue.put(full_msg)
    try:
        with open(STATUS_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except:
        pass

def log_error(msg):
    """记录错误日志"""
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except:
        pass

def get_proxy_handler():
    """获取代理处理器"""
    try:
        return urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY})
    except:
        return urllib.request.ProxyHandler({})

# ============ 数据获取 ============
def get_klines(symbol="BTCUSDT", interval="1m", limit=100):
    """获取币安K线数据"""
    global consecutive_errors
    
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    
    for attempt in range(3):
        try:
            proxy_handler = get_proxy_handler()
            opener = urllib.request.build_opener(proxy_handler)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            
            with opener.open(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))
                consecutive_errors = 0
                return [
                    {
                        'time': d[0],
                        'open': float(d[1]),
                        'high': float(d[2]),
                        'low': float(d[3]),
                        'close': float(d[4]),
                        'vol': float(d[5])
                    }
                    for d in data
                ]
        except Exception as e:
            log_error(f"数据获取失败(尝试{attempt+1}/3): {str(e)}")
            time.sleep(2 ** attempt)
    
    consecutive_errors += 1
    return None

def get_current_price(symbol="BTCUSDT"):
    """获取当前价格"""
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        proxy_handler = get_proxy_handler()
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with opener.open(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return float(data['price'])
    except:
        return None

# ============ 技术指标筛选 ============
def technical_screening(df):
    """第一层筛选: 技术指标组合，返回方向和条件列表"""
    c = np.array([d['close'] for d in df])
    h = np.array([d['high'] for d in df])
    l = np.array([d['low'] for d in df])
    v = np.array([d['vol'] for d in df])
    
    price = float(c[-1])
    
    # 基础指标
    rsi = float(talib.RSI(c, 14)[-1])
    macd_line, macd_signal, macd_hist = talib.MACD(c, 12, 26, 9)
    ema9_arr = talib.EMA(c, 9)
    ema21_arr = talib.EMA(c, 21)
    ema55_arr = talib.EMA(c, 55)
    bb_upper_arr, bb_middle_arr, bb_lower_arr = talib.BBANDS(c, 20, 2, 2)
    atr = float(talib.ATR(h, l, c, 14)[-1])
    
    vol_avg = mean(v[-20:]) if len(v) >= 20 else 1
    vol_ratio = float(v[-1]) / vol_avg if vol_avg > 0 else 1
    
    # 趋势判断
    ema9 = float(ema9_arr[-1])
    ema21 = float(ema21_arr[-1])
    ema55 = float(ema55_arr[-1])
    trend_up = ema9 > ema21 > ema55
    trend_down = ema9 < ema21 < ema55
    
    # 布林带
    bb_upper = float(bb_upper_arr[-1])
    bb_lower = float(bb_lower_arr[-1])
    bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
    
    # MACD
    macd_val = float(macd_line[-1])
    macd_sig = float(macd_signal[-1])
    macd_prev = float(macd_line[-2]) if len(macd_line) > 1 else macd_val
    macd_sig_prev = float(macd_signal[-2]) if len(macd_signal) > 1 else macd_sig
    
    # 动量
    momentum_5m = (c[-1] - c[-5]) / c[-5] * 100 if len(c) >= 5 else 0
    momentum_15m = (c[-1] - c[-15]) / c[-15] * 100 if len(c) >= 15 else 0
    
    details = {
        "price": price, "rsi": rsi, "macd": macd_val, 
        "ema9": ema9, "ema21": ema21, "bb_pos": bb_pos,
        "vol_ratio": vol_ratio, "atr": atr, "momentum_5m": momentum_5m,
        "trend_up": trend_up, "trend_down": trend_down
    }
    
    # ========== 做多条件 ==========
    long_conditions = []
    
    if rsi < 35 and vol_ratio > 1.3 and momentum_5m > 0:
        long_conditions.append("RSI超卖反弹")
    
    if macd_val > macd_sig and macd_prev <= macd_sig_prev:
        long_conditions.append("MACD金叉")
    
    if price < bb_lower * 1.003 and momentum_5m > 0:
        long_conditions.append("布林带下轨反弹")
    
    if trend_up and abs(price - ema21) / price < 0.003 and price > ema21 and bb_pos < 0.5:
        long_conditions.append("趋势回调到位")
    
    recent_high = max([d['high'] for d in df[-20:]])
    if price > recent_high * 0.998 and vol_ratio > 2.0 and trend_up and bb_pos > 0.6:
        long_conditions.append("放量突破")
    
    # ========== 做空条件 ==========
    short_conditions = []
    
    if rsi > 65 and vol_ratio > 1.3 and momentum_5m < 0 and bb_pos > 0.7:
        short_conditions.append("RSI超买回调")
    
    if macd_val < macd_sig and macd_prev >= macd_sig_prev and bb_pos > 0.4:
        short_conditions.append("MACD死叉")
    
    if price > bb_upper * 0.998 and momentum_5m < 0 and bb_pos > 0.8:
        short_conditions.append("布林带上轨回调")
    
    if trend_down and abs(price - ema21) / price < 0.003 and price < ema21 and bb_pos > 0.5:
        short_conditions.append("趋势反弹到位")
    
    recent_low = min([d['low'] for d in df[-20:]])
    if price < recent_low * 1.002 and vol_ratio > 2.0 and trend_down and bb_pos < 0.4:
        short_conditions.append("放量跌破")
    
    # 决策
    if len(long_conditions) >= 2:
        return "UP", long_conditions, details
    elif len(short_conditions) >= 2:
        return "DOWN", short_conditions, details
    else:
        return "NEUTRAL", [], details

# ============ AI验证层 ============
def ai_verify(direction, price, conditions, details):
    """AI二次确认 - 必须≥90%置信度"""
    global call_count
    
    try:
        import socket
        original_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(70)
        
        trend_str = "上涨" if details['trend_up'] else ("下跌" if details['trend_down'] else "震荡")
        bb_pos_pct = details['bb_pos']*100
        
        bb_warning = ""
        if direction == 'DOWN' and bb_pos_pct < 30:
            bb_warning = "⚠️ 警告：价格在布林带下轨附近(低位)，此时做空是逆势操作，胜率极低！"
        elif direction == 'UP' and bb_pos_pct > 70:
            bb_warning = "⚠️ 警告：价格在布林带上轨附近(高位)，此时做多是追高，胜率极低！"
        
        prompt = f"""你是顶级加密货币交易员，验证以下交易信号。

【信号信息】
方向: {'做多' if direction == 'UP' else '做空'}
价格: ${price:.2f}
技术条件: {', '.join(conditions)}

【市场数据】
RSI: {details['rsi']:.1f} | MACD: {details['macd']:.2f} | ATR: {details['atr']:.1f}
成交量: {details['vol_ratio']:.2f}x均值 | 5分钟动量: {details['momentum_5m']:.2f}%
趋势: {trend_str} | 布林带位置: {bb_pos_pct:.1f}%
{bb_warning}

【你的任务】
1. 判断这些技术信号是否可靠
2. 识别是否有假突破/假跌破风险
3. 检查价格位置是否合理(低位不做空，高位不做多)
4. 给出明确的YES/NO判断和置信度

严格标准: 只有确定性≥90%且位置合理的信号才确认YES
输出JSON: {{"confirm":"YES/NO","confidence":0-100,"risk":"风险说明","reason":"确认或拒绝的核心理由"}}"""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KIMI_API_KEY}"
        }
        data = {
            "model": "moonshot-v1-8k",
            "messages": [
                {"role": "system", "content": "你是保守型交易员，宁可错过也不做错。核心原则：低位不做空，高位不做多。布林带下轨(bb_pos<30%)附近绝不做空，布林带上轨(bb_pos>70%)附近绝不做多。对任何位置不合理的信号直接拒绝。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 150
        }
        
        proxy_handler = get_proxy_handler()
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(
            KIMI_API_URL,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        with opener.open(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            
            try:
                json_start = content.find('{')
                json_end = content.rfind('}')
                if json_start >= 0 and json_end > json_start:
                    ai_result = json.loads(content[json_start:json_end+1])
                else:
                    ai_result = {"confirm": "NO", "confidence": 0, "reason": "无法解析AI响应"}
            except:
                ai_result = {"confirm": "NO", "confidence": 0, "reason": "JSON解析失败"}
            
            call_count += 1
            socket.setdefaulttimeout(original_timeout)
            
            return ai_result.get("confirm", "NO").upper() == "YES" and ai_result.get("confidence", 0) >= 90, ai_result
            
    except Exception as e:
        log_error(f"AI验证异常: {str(e)}")
        socket.setdefaulttimeout(original_timeout)
        return False, {"confirm": "NO", "confidence": 0, "reason": f"验证失败: {str(e)}"}

# ============ Webhook通知 ============
def send_webhook(direction, price, confidence, reason):
    """发送webhook通知"""
    webhook_url = WEBHOOK_LONG if direction == "UP" else WEBHOOK_SHORT
    
    try:
        proxy_handler = get_proxy_handler()
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(webhook_url, method='POST')
        
        with opener.open(req, timeout=10) as response:
            return response.status == 200
    except:
        return False

# ============ 信号记录 ============
def log_signal(direction, price, conditions, details, ai_confidence, ai_reason, ai_risk, webhook_success):
    """记录信号到文件"""
    global signal_stats
    
    signal_data = {
        "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "signal": "做多" if direction == "UP" else "做空",
        "direction": direction,
        "price": price,
        "tech_conditions": conditions,
        "tech_details": details,
        "ai_confidence": ai_confidence,
        "ai_reason": ai_reason,
        "ai_risk": ai_risk,
        "webhook_success": webhook_success,
        "call_count": call_count
    }
    
    try:
        with open(SIGNAL_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(signal_data, ensure_ascii=False) + '\n')
    except:
        pass
    
    signal_stats[direction] += 1
    signal_stats["last_signal_time"] = time.time()
    signal_stats["last_signal_direction"] = direction
    
    # 发送到GUI队列
    signal_queue.put(signal_data)

# ============ 主策略循环 ============
def run_strategy():
    """主策略循环"""
    global running, consecutive_errors, call_count
    
    log_status("🚀 Kimi AI v3.1 Windows 版启动")
    log_status(f"📁 日志目录: {LOG_DIR}")
    log_status(f"🔑 API Key: {KIMI_API_KEY[:10]}...")
    
    signal_count = 0
    check_count = 0
    
    while running:
        try:
            check_count += 1
            
            # 每分钟检查一次
            df = get_klines()
            if df is None:
                log_status("⚠️ 数据获取失败，跳过本轮")
                time.sleep(60)
                continue
            
            price = df[-1]['close']
            
            # 技术指标筛选
            direction, conditions, details = technical_screening(df)
            
            if direction == "NEUTRAL":
                if check_count % 10 == 0:  # 每10分钟输出一次监控信息
                    log_status(f"💤 监控: ${price:.2f} RSI:{details['rsi']:.1f} 无信号")
                time.sleep(60)
                continue
            
            # 有技术信号，进行AI验证
            log_status(f"📊 技术信号: {'做多' if direction=='UP' else '做空'} | {conditions}")
            log_status("🤖 AI验证中...")
            
            confirmed, ai_result = ai_verify(direction, price, conditions, details)
            
            if not confirmed:
                log_status(f"❌ AI拒绝: {ai_result.get('reason', '置信度不足')}")
                time.sleep(60)
                continue
            
            # 信号确认，触发webhook
            signal_count += 1
            confidence = ai_result.get('confidence', 90)
            
            log_status(f"🚨 信号 #{signal_count}: {'做多' if direction=='UP' else '做空'} @ ${price:.2f} (置信度:{confidence}%)")
            
            # 发送webhook
            webhook_success = send_webhook(direction, price, confidence, ai_result.get('reason', ''))
            
            # 记录信号
            log_signal(
                direction, price, conditions, details,
                confidence, 
                ai_result.get('reason', ''),
                ai_result.get('risk', ''),
                webhook_success
            )
            
            log_status(f"✅ Webhook: {'成功' if webhook_success else '失败'}")
            
            # 连续错误检查
            if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                log_status("⚠️ 连续错误过多，策略暂停5分钟后重试")
                time.sleep(300)
                consecutive_errors = 0
            
            time.sleep(60)
            
        except Exception as e:
            log_error(f"策略异常: {str(e)}")
            log_status(f"❌ 策略异常: {str(e)[:50]}")
            time.sleep(60)
    
    log_status("🛑 策略已停止")

# ============ GUI 界面 ============
def run_gui():
    """运行GUI界面"""
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk
        
        root = tk.Tk()
        root.title("Kimi AI BTC 策略 v3.1")
        root.geometry("800x600")
        root.configure(bg='#1a1a2e')
        
        # 标题
        title = tk.Label(root, text="Kimi AI 5分钟事件合约策略", 
                        font=('微软雅黑', 16, 'bold'), 
                        fg='#00ff88', bg='#1a1a2e')
        title.pack(pady=10)
        
        # 状态栏
        status_frame = tk.Frame(root, bg='#16213e')
        status_frame.pack(fill='x', padx=10, pady=5)
        
        status_label = tk.Label(status_frame, text="状态: 运行中", 
                               font=('微软雅黑', 10),
                               fg='#00ff88', bg='#16213e')
        status_label.pack(side='left', padx=10)
        
        # 日志显示区
        log_frame = tk.LabelFrame(root, text="运行日志", 
                                 font=('微软雅黑', 10),
                                 fg='white', bg='#1a1a2e')
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        log_text = scrolledtext.ScrolledText(log_frame, 
                                            font=('Consolas', 9),
                                            bg='#0f0f23', fg='#00ff88',
                                            height=15)
        log_text.pack(fill='both', expand=True, padx=5, pady=5)
        
        # 信号显示区
        signal_frame = tk.LabelFrame(root, text="最新信号",
                                    font=('微软雅黑', 10),
                                    fg='white', bg='#1a1a2a')
        signal_frame.pack(fill='x', padx=10, pady=5)
        
        signal_text = tk.Text(signal_frame, font=('Consolas', 10),
                             bg='#0f0f23', fg='#ff6b6b',
                             height=5)
        signal_text.pack(fill='x', padx=5, pady=5)
        
        # 按钮
        btn_frame = tk.Frame(root, bg='#1a1a2e')
        btn_frame.pack(fill='x', padx=10, pady=10)
        
        def stop_strategy():
            global running
            running = False
            status_label.config(text="状态: 已停止", fg='#ff6b6b')
            log_status("🛑 用户停止策略")
        
        def open_log_dir():
            import subprocess
            subprocess.run(['explorer', LOG_DIR])
        
        stop_btn = tk.Button(btn_frame, text="停止策略", command=stop_strategy,
                            font=('微软雅黑', 10),
                            bg='#ff6b6b', fg='white',
                            width=15)
        stop_btn.pack(side='left', padx=5)
        
        log_btn = tk.Button(btn_frame, text="打开日志目录", command=open_log_dir,
                           font=('微软雅黑', 10),
                           bg='#4ecdc4', fg='white',
                           width=15)
        log_btn.pack(side='left', padx=5)
        
        # 更新函数
        def update_ui():
            # 更新日志
            while not status_queue.empty():
                try:
                    msg = status_queue.get_nowait()
                    log_text.insert('end', msg + '\n')
                    log_text.see('end')
                except:
                    pass
            
            # 更新信号
            while not signal_queue.empty():
                try:
                    sig = signal_queue.get_nowait()
                    signal_text.delete('1.0', 'end')
                    signal_text.insert('end', 
                        f"时间: {sig['time']}\n"
                        f"信号: {sig['signal']} @ ${sig['price']}\n"
                        f"置信度: {sig['ai_confidence']}%\n"
                        f"条件: {', '.join(sig['tech_conditions'])}\n"
                    )
                except:
                    pass
            
            root.after(100, update_ui)
        
        update_ui()
        
        # 启动策略线程
        strategy_thread = threading.Thread(target=run_strategy, daemon=True)
        strategy_thread.start()
        
        root.mainloop()
        
    except ImportError:
        log_status("⚠️ 未安装 tkinter，使用命令行模式")
        run_strategy()

# ============ 入口 ============
if __name__ == "__main__":
    try:
        run_gui()
    except KeyboardInterrupt:
        running = False
        log_status("🛑 程序被用户中断")
    except Exception as e:
        log_error(f"程序异常: {str(e)}")
        print(f"❌ 程序异常: {e}")
        input("按回车键退出...")
