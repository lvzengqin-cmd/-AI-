#!/usr/bin/env python3
"""
Kimi AI 5分钟事件合约策略系统 v3.1 - Windows 命令行版本
轻量级，无需GUI，适合后台运行
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

# ============ 配置 ============
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

# ============ 状态追踪 ============
start_time = time.time()
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 10
call_count = 0
running = True

def log(msg, log_type="INFO"):
    """打印并记录日志"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] [{log_type}] {msg}"
    print(full_msg)
    
    if log_type == "ERROR":
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
            if attempt == 2:
                log(f"数据获取失败: {str(e)[:50]}", "ERROR")
            time.sleep(2 ** attempt)
    
    consecutive_errors += 1
    return None

def technical_screening(df):
    """技术指标筛选"""
    c = np.array([d['close'] for d in df])
    h = np.array([d['high'] for d in df])
    l = np.array([d['low'] for d in df])
    v = np.array([d['vol'] for d in df])
    
    price = float(c[-1])
    
    rsi = float(talib.RSI(c, 14)[-1])
    macd_line, macd_signal, macd_hist = talib.MACD(c, 12, 26, 9)
    ema9_arr = talib.EMA(c, 9)
    ema21_arr = talib.EMA(c, 21)
    ema55_arr = talib.EMA(c, 55)
    bb_upper_arr, bb_middle_arr, bb_lower_arr = talib.BBANDS(c, 20, 2, 2)
    atr = float(talib.ATR(h, l, c, 14)[-1])
    
    vol_avg = mean(v[-20:]) if len(v) >= 20 else 1
    vol_ratio = float(v[-1]) / vol_avg if vol_avg > 0 else 1
    
    ema9 = float(ema9_arr[-1])
    ema21 = float(ema21_arr[-1])
    ema55 = float(ema55_arr[-1])
    trend_up = ema9 > ema21 > ema55
    trend_down = ema9 < ema21 < ema55
    
    bb_upper = float(bb_upper_arr[-1])
    bb_lower = float(bb_lower_arr[-1])
    bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
    
    macd_val = float(macd_line[-1])
    macd_sig = float(macd_signal[-1])
    macd_prev = float(macd_line[-2]) if len(macd_line) > 1 else macd_val
    macd_sig_prev = float(macd_signal[-2]) if len(macd_signal) > 1 else macd_sig
    
    momentum_5m = (c[-1] - c[-5]) / c[-5] * 100 if len(c) >= 5 else 0
    
    details = {
        "price": price, "rsi": rsi, "macd": macd_val, 
        "ema9": ema9, "ema21": ema21, "bb_pos": bb_pos,
        "vol_ratio": vol_ratio, "atr": atr, "momentum_5m": momentum_5m,
        "trend_up": trend_up, "trend_down": trend_down
    }
    
    long_conditions = []
    short_conditions = []
    
    # 做多条件
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
    
    # 做空条件
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
    
    if len(long_conditions) >= 2:
        return "UP", long_conditions, details
    elif len(short_conditions) >= 2:
        return "DOWN", short_conditions, details
    else:
        return "NEUTRAL", [], details

def ai_verify(direction, price, conditions, details):
    """AI验证"""
    global call_count
    
    try:
        import socket
        original_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(70)
        
        trend_str = "上涨" if details['trend_up'] else ("下跌" if details['trend_down'] else "震荡")
        bb_pos_pct = details['bb_pos']*100
        
        bb_warning = ""
        if direction == 'DOWN' and bb_pos_pct < 30:
            bb_warning = "⚠️ 警告：价格在布林带下轨附近(低位)，此时做空是逆势操作！"
        elif direction == 'UP' and bb_pos_pct > 70:
            bb_warning = "⚠️ 警告：价格在布林带上轨附近(高位)，此时做多是追高！"
        
        prompt = f"""你是顶级加密货币交易员，验证以下交易信号。

【信号信息】
方向: {'做多' if direction == 'UP' else '做空'}
价格: ${price:.2f}
技术条件: {', '.join(conditions)}

【市场数据】
RSI: {details['rsi']:.1f} | MACD: {details['macd']:.2f}
趋势: {trend_str} | 布林带位置: {bb_pos_pct:.1f}%
{bb_warning}

严格标准: 确定性≥90%且位置合理才确认
输出JSON: {{"confirm":"YES/NO","confidence":0-100,"reason":"理由"}}"""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KIMI_API_KEY}"
        }
        data = {
            "model": "moonshot-v1-8k",
            "messages": [
                {"role": "system", "content": "你是保守型交易员。核心原则：低位不做空，高位不做多。"},
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
                    ai_result = {"confirm": "NO", "confidence": 0}
            except:
                ai_result = {"confirm": "NO", "confidence": 0}
            
            call_count += 1
            socket.setdefaulttimeout(original_timeout)
            
            return ai_result.get("confirm", "NO").upper() == "YES" and ai_result.get("confidence", 0) >= 90, ai_result
            
    except Exception as e:
        log(f"AI验证异常: {str(e)[:50]}", "ERROR")
        return False, {"confirm": "NO", "confidence": 0}

def send_webhook(direction):
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

def log_signal(direction, price, conditions, details, ai_confidence, ai_reason):
    """记录信号"""
    signal_data = {
        "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "signal": "做多" if direction == "UP" else "做空",
        "price": price,
        "conditions": conditions,
        "bb_pos": details['bb_pos'],
        "confidence": ai_confidence,
        "reason": ai_reason
    }
    
    try:
        with open(SIGNAL_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(signal_data, ensure_ascii=False) + '\n')
    except:
        pass

def run_strategy():
    """主策略循环"""
    global running, consecutive_errors, call_count
    
    log("=" * 50)
    log("🚀 Kimi AI BTC 策略 v3.1 启动")
    log(f"📁 日志目录: {LOG_DIR}")
    log("=" * 50)
    
    signal_count = 0
    check_count = 0
    
    while running:
        try:
            check_count += 1
            
            df = get_klines()
            if df is None:
                log("⚠️ 数据获取失败，60秒后重试")
                time.sleep(60)
                continue
            
            price = df[-1]['close']
            direction, conditions, details = technical_screening(df)
            
            if direction == "NEUTRAL":
                if check_count % 10 == 0:
                    log(f"💤 监控: ${price:.2f} RSI:{details['rsi']:.1f} BB:{details['bb_pos']*100:.1f}% 无信号")
                time.sleep(60)
                continue
            
            log(f"📊 技术信号: {'做多' if direction=='UP' else '做空'} | {conditions}")
            log("🤖 AI验证中...")
            
            confirmed, ai_result = ai_verify(direction, price, conditions, details)
            
            if not confirmed:
                log(f"❌ AI拒绝: {ai_result.get('reason', '置信度不足')[:40]}")
                time.sleep(60)
                continue
            
            signal_count += 1
            confidence = ai_result.get('confidence', 90)
            
            log("=" * 50)
            log(f"🚨 信号 #{signal_count}: {'做多' if direction=='UP' else '做空'} @ ${price:.2f} (置信度:{confidence}%)", "SIGNAL")
            log("=" * 50)
            
            webhook_success = send_webhook(direction)
            log(f"📤 Webhook: {'✅成功' if webhook_success else '❌失败'}")
            
            log_signal(direction, price, conditions, details, confidence, ai_result.get('reason', ''))
            
            if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                log("⚠️ 连续错误过多，暂停5分钟")
                time.sleep(300)
                consecutive_errors = 0
            
            time.sleep(60)
            
        except KeyboardInterrupt:
            log("🛑 用户中断")
            running = False
        except Exception as e:
            log(f"❌ 异常: {str(e)[:50]}", "ERROR")
            time.sleep(60)
    
    log("🛑 策略已停止")
    input("按回车键退出...")

if __name__ == "__main__":
    run_strategy()
