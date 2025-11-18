#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이드 추천 시스템 v3.5 - 프리미엄 디자인 에디션
- v3.4 기능 유지 + 디자인 전면 개선
- 틀고정 완전 수정
- 카드형 레이아웃 (Top30 인사이트, 지표별 Top5)
- 지수/환율 시각적 구분
- 보수/공격 최대 8개 제한
- 우측 공백 제거, 레이아웃 최적화
- 전문적이고 고급스러운 디자인
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

# 한국 거래소 데이터
try:
    from pykrx import stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    print("⚠️ pykrx 없음 - yfinance만 사용")

# 차트 생성
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, rc
    import matplotlib.patches as mpatches
    CHART_AVAILABLE = True
    
    # 한글 폰트 설정
    try:
        font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
        font_name = font_manager.FontProperties(fname=font_path).get_name()
        rc('font', family=font_name)
    except:
        try:
            plt.rcParams['font.family'] = 'NanumGothic'
        except:
            plt.rcParams['font.family'] = 'DejaVu Sans'
    
    plt.rcParams['axes.unicode_minus'] = False
    
except ImportError:
    CHART_AVAILABLE = False
    print("⚠️ matplotlib 없음 - 차트 생략")

import os
import glob


def get_krx_tickers():
    """한국 거래소 전체 티커 수집"""
    if not PYKRX_AVAILABLE:
        return []
    
    try:
        today = datetime.now().strftime('%Y%m%d')
        kospi = stock.get_market_ticker_list(today, market="KOSPI")
        kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")
        
        all_tickers = []
        for code in kospi + kosdaq:
            all_tickers.append(f"{code}.KS" if code in kospi else f"{code}.KQ")
        
        return all_tickers
    except Exception as e:
        print(f"⚠️ KRX 티커 수집 실패: {e}")
        return []


def fetch_market_data(ticker, period='6mo'):
    """개별 종목 데이터 수집"""
    try:
        stock_data = yf.Ticker(ticker)
        hist = stock_data.history(period=period)
        info = stock_data.info
        
        if hist.empty or len(hist) < 60:
            return None
        
        return {
            'history': hist,
            'info': info,
            'ticker': ticker
        }
    except:
        return None


def calculate_technical_score(hist):
    """기술적 지표 점수 (40점)"""
    score = 0
    details = {}
    
    close = hist['Close'].values
    volume = hist['Volume'].values
    
    # RSI (10점)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[-14:]) if len(gain) >= 14 else 0
    avg_loss = np.mean(loss[-14:]) if len(loss) >= 14 else 0
    
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    
    details['RSI'] = round(rsi, 1)
    if 25 <= rsi <= 35:
        score += 10
    elif 20 <= rsi <= 40:
        score += 7
    elif rsi < 45:
        score += 3
    
    # 이격도 (10점)
    ma20 = np.mean(close[-20:])
    disparity = (close[-1] / ma20 - 1) * 100
    details['이격도'] = f"{disparity:.1f}%"
    
    if -15 <= disparity <= -8:
        score += 10
    elif -20 <= disparity <= -5:
        score += 7
    elif disparity < 0:
        score += 3
    
    # 거래량 (10점)
    vol_ma20 = np.mean(volume[-20:])
    vol_ratio = volume[-1] / vol_ma20 if vol_ma20 > 0 else 0
    details['거래량비율'] = f"{vol_ratio:.1f}배"
    
    if vol_ratio >= 2.5:
        score += 10
    elif vol_ratio >= 1.8:
        score += 7
    elif vol_ratio >= 1.3:
        score += 4
    
    # 모멘텀 (10점) - 단기 반등 신호
    returns_5d = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0
    returns_20d = (close[-1] / close[-21] - 1) * 100 if len(close) >= 21 else 0
    details['5일수익률'] = f"{returns_5d:.1f}%"
    details['20일수익률'] = f"{returns_20d:.1f}%"
    
    # 반등 강도 계산
    if len(close) >= 20:
        min_20d = np.min(close[-20:])
        bounce_strength = (close[-1] / min_20d - 1) * 100
        details['반등강도'] = f"{bounce_strength:.1f}%"
    else:
        bounce_strength = 0
        details['반등강도'] = "N/A"
    
    # 단기 상승 & 중기 조정 = 반등 타이밍
    if returns_5d > 2 and -10 < returns_20d < 0:
        score += 10
    elif returns_5d > 0 and returns_20d < 0:
        score += 6
    elif bounce_strength > 5:
        score += 4
    
    return score, details


def calculate_fundamental_score(info):
    """재무 지표 점수 (30점)"""
    score = 0
    details = {}
    
    # PER (10점)
    per = info.get('trailingPE', 999)
    details['PER'] = f"{per:.1f}" if per != 999 else "N/A"
    
    if 0 < per < 8:
        score += 10
    elif 0 < per < 12:
        score += 7
    elif 0 < per < 15:
        score += 4
    
    # PBR (10점)
    pbr = info.get('priceToBook', 999)
    details['PBR'] = f"{pbr:.2f}" if pbr != 999 else "N/A"
    
    if 0 < pbr < 0.8:
        score += 10
    elif 0 < pbr < 1.2:
        score += 7
    elif 0 < pbr < 1.5:
        score += 4
    
    # 부채비율 (10점)
    debt_ratio = info.get('debtToEquity', 999)
    details['부채비율'] = f"{debt_ratio:.0f}%" if debt_ratio != 999 else "N/A"
    
    if 0 <= debt_ratio < 100:
        score += 10
    elif debt_ratio < 150:
        score += 6
    elif debt_ratio < 200:
        score += 3
    
    return score, details


def calculate_market_timing_score(hist):
    """시장 타이밍 점수 (30점)"""
    score = 0
    details = {}
    
    close = hist['Close'].values
    high = hist['High'].values
    low = hist['Low'].values
    
    # 52주 저점 근접도 (15점)
    week52_high = np.max(high[-252:]) if len(high) >= 252 else np.max(high)
    week52_low = np.min(low[-252:]) if len(low) >= 252 else np.min(low)
    
    current_pos = (close[-1] - week52_low) / (week52_high - week52_low) * 100 if week52_high != week52_low else 50
    details['52주위치'] = f"{current_pos:.0f}%"
    
    if current_pos < 20:
        score += 15
    elif current_pos < 30:
        score += 12
    elif current_pos < 40:
        score += 8
    
    # 이동평균 배열 (15점)
    ma5 = np.mean(close[-5:])
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    
    details['MA배열'] = "정배열" if ma5 > ma20 > ma60 else "역배열" if ma5 < ma20 < ma60 else "혼조"
    
    # 골든크로스 임박
    prev_ma5 = np.mean(close[-6:-1])
    prev_ma20 = np.mean(close[-21:-1])
    
    if prev_ma5 < prev_ma20 and ma5 > ma20:
        score += 15  # 골든크로스 발생
        details['MA배열'] += " (골든크로스)"
    elif ma5 > ma20 and ma20 > ma60:
        score += 12  # 정배열
    elif ma5 < ma20 and abs(ma5 - ma20) / ma20 < 0.02:
        score += 10  # 골든크로스 임박
        details['MA배열'] += " (크로스 임박)"
    elif ma5 > ma20:
        score += 6
    
    return score, details


def calculate_stop_loss_target(current_price, score):
    """손절가 및 목표가 계산"""
    # 손절: -7% 고정
    stop_loss = current_price * 0.93
    
    # 목표가: 점수 기반
    if score >= 80:
        target_ratio = 1.25  # +25%
    elif score >= 70:
        target_ratio = 1.20  # +20%
    elif score >= 60:
        target_ratio = 1.15  # +15%
    else:
        target_ratio = 1.10  # +10%
    
    target_price = current_price * target_ratio
    
    return stop_loss, target_price


def determine_risk_level(details):
    """위험도 판단"""
    risk_score = 0
    
    # 변동성 체크
    rsi = details.get('RSI', 50)
    if rsi < 25 or rsi > 75:
        risk_score += 1
    
    # 거래량 급등 체크
    vol_ratio = float(details.get('거래량비율', '1배').replace('배', ''))
    if vol_ratio > 3:
        risk_score += 1
    
    # 부채비율 체크
    debt = details.get('부채비율', 'N/A')
    if debt != 'N/A':
        debt_val = float(debt.replace('%', ''))
        if debt_val > 200:
            risk_score += 1
    
    if risk_score == 0:
        return "낮음", "🟢"
    elif risk_score == 1:
        return "보통", "🟡"
    else:
        return "높음", "🔴"


def analyze_stock(ticker):
    """종목 종합 분석"""
    data = fetch_market_data(ticker)
    if not data:
        return None
    
    hist = data['history']
    info = data['info']
    
    # 거래대금 필터 (5억 이상)
    recent_volume = hist['Volume'].iloc[-1]
    recent_price = hist['Close'].iloc[-1]
    trading_value = recent_volume * recent_price
    
    if trading_value < 500_000_000:
        return None
    
    tech_score, tech_details = calculate_technical_score(hist)
    fund_score, fund_details = calculate_fundamental_score(info)
    timing_score, timing_details = calculate_market_timing_score(hist)
    
    total_score = tech_score + fund_score + timing_score
    
    # 40점 이상만 통과
    if total_score < 40:
        return None
    
    all_details = {**tech_details, **fund_details, **timing_details}
    
    current_price = hist['Close'].iloc[-1]
    stop_loss, target_price = calculate_stop_loss_target(current_price, total_score)
    risk_level, risk_icon = determine_risk_level(all_details)
    
    # 환율 정보
    krw_code = ticker.split('.')[0]
    
    return {
        'ticker': ticker,
        'code': krw_code,
        'name': info.get('longName', info.get('shortName', ticker)),
        'current_price': current_price,
        'score': total_score,
        'tech_score': tech_score,
        'fund_score': fund_score,
        'timing_score': timing_score,
        'details': all_details,
        'stop_loss': stop_loss,
        'target_price': target_price,
        'risk_level': risk_level,
        'risk_icon': risk_icon,
        'trading_value': trading_value,
        'history': hist
    }


def get_exchange_rates():
    """주요 환율 정보"""
    pairs = {
        'USD': 'KRW=X',
        'JPY': 'JPYKRW=X',
        'EUR': 'EURKRW=X'
    }
    
    rates = {}
    for currency, ticker in pairs.items():
        try:
            data = yf.Ticker(ticker).history(period='5d')
            if not data.empty:
                current = data['Close'].iloc[-1]
                prev = data['Close'].iloc[-2] if len(data) >= 2 else current
                change = ((current - prev) / prev) * 100
                
                rates[currency] = {
                    'rate': current,
                    'change': change
                }
        except:
            pass
    
    return rates


def get_market_indices():
    """주요 지수 정보"""
    indices = {
        'KOSPI': '^KS11',
        'KOSDAQ': '^KQ11'
    }
    
    index_data = {}
    for name, ticker in indices.items():
        try:
            data = yf.Ticker(ticker).history(period='5d')
            if not data.empty:
                current = data['Close'].iloc[-1]
                prev = data['Close'].iloc[-2] if len(data) >= 2 else current
                change = ((current - prev) / prev) * 100
                
                index_data[name] = {
                    'value': current,
                    'change': change
                }
        except:
            pass
    
    return index_data


def create_sparkline(hist, ticker, output_dir='charts'):
    """스파크라인 차트 생성"""
    if not CHART_AVAILABLE:
        return None
    
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        close_prices = hist['Close'].values[-30:]
        
        fig, ax = plt.subplots(figsize=(3, 0.8))
        
        colors = ['#ef4444' if close_prices[i] < close_prices[i-1] else '#10b981' 
                  for i in range(1, len(close_prices))]
        colors.insert(0, '#6b7280')
        
        for i in range(len(close_prices) - 1):
            ax.plot([i, i+1], [close_prices[i], close_prices[i+1]], 
                   color=colors[i], linewidth=1.5, alpha=0.8)
        
        ax.fill_between(range(len(close_prices)), close_prices, 
                        alpha=0.2, color='#3b82f6')
        
        ax.axis('off')
        ax.set_xlim(0, len(close_prices)-1)
        
        y_margin = (max(close_prices) - min(close_prices)) * 0.1
        ax.set_ylim(min(close_prices) - y_margin, max(close_prices) + y_margin)
        
        plt.tight_layout(pad=0)
        
        filename = f"{output_dir}/{ticker.replace('.', '_')}.png"
        plt.savefig(filename, dpi=80, bbox_inches='tight', 
                   pad_inches=0, facecolor='white', edgecolor='none')
        plt.close()
        
        return filename
    except Exception as e:
        print(f"차트 생성 실패 ({ticker}): {e}")
        return None


def generate_html_report(results, total_analyzed, total_success):
    """HTML 리포트 생성 - v3.5 프리미엄 디자인"""
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 시장 정보
    indices = get_market_indices()
    rates = get_exchange_rates()
    
    # Top 30
    top_30 = sorted(results, key=lambda x: x['score'], reverse=True)[:30]
    avg_score = np.mean([r['score'] for r in top_30])
    
    # 차트 생성
    chart_files = {}
    if CHART_AVAILABLE:
        print("\n📊 차트 생성 중...")
        for i, result in enumerate(top_30, 1):
            chart_path = create_sparkline(result['history'], result['ticker'])
            if chart_path:
                chart_files[result['ticker']] = chart_path
            print(f"  [{i}/30] {result['name']} 완료")
    
    # 지표별 Top 5
    top_rsi = sorted([r for r in results if r['details'].get('RSI', 100) < 35], 
                     key=lambda x: x['details']['RSI'])[:5]
    top_disparity = sorted([r for r in results if '이격도' in r['details']], 
                          key=lambda x: float(r['details']['이격도'].replace('%', '')))[:5]
    top_volume = sorted([r for r in results if '거래량비율' in r['details']], 
                       key=lambda x: float(r['details']['거래량비율'].replace('배', '')), 
                       reverse=True)[:5]
    top_pbr = sorted([r for r in results if r['details'].get('PBR', 'N/A') != 'N/A'], 
                    key=lambda x: float(r['details']['PBR']))[:5]
    
    # 단기 모멘텀 Top 5
    top_momentum = sorted([r for r in results if '5일수익률' in r['details']], 
                         key=lambda x: float(r['details']['5일수익률'].replace('%', '')), 
                         reverse=True)[:5]
    
    # 반등 강도 Top 5
    top_bounce = sorted([r for r in results if r['details'].get('반등강도', 'N/A') != 'N/A'], 
                       key=lambda x: float(r['details']['반등강도'].replace('%', '')), 
                       reverse=True)[:5]
    
    # 투자 성향별 (최대 8개)
    conservative = [r for r in top_30 if r['risk_level'] == '낮음'][:8]
    aggressive = [r for r in top_30 if r['risk_level'] == '높음'][:8]
    
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스윙 트레이드 추천 리포트 v3.5</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        
        /* 헤더 */
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 700;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        
        .header .subtitle {{
            font-size: 1.1em;
            opacity: 0.95;
            font-weight: 300;
        }}
        
        .header .version {{
            display: inline-block;
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            margin-top: 15px;
            font-size: 0.9em;
            backdrop-filter: blur(10px);
        }}
        
        /* 시장 정보 - 개선된 레이아웃 */
        .market-info {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 25px 40px;
            border-bottom: 3px solid #667eea;
        }}
        
        .market-section {{
            display: flex;
            gap: 40px;
            align-items: center;
            justify-content: center;
            flex-wrap: wrap;
        }}
        
        .market-group {{
            display: flex;
            gap: 25px;
            align-items: center;
        }}
        
        .market-group-title {{
            font-size: 1.1em;
            font-weight: 700;
            color: #495057;
            padding-right: 15px;
            border-right: 2px solid #dee2e6;
        }}
        
        .market-item {{
            text-align: center;
            padding: 10px 20px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            min-width: 140px;
        }}
        
        .market-item .label {{
            font-size: 1.1em;
            font-weight: 700;
            color: #495057;
            margin-bottom: 5px;
        }}
        
        .market-item .value {{
            font-size: 1.3em;
            font-weight: 700;
            color: #212529;
            margin-bottom: 3px;
        }}
        
        .market-item .change {{
            font-size: 0.95em;
            font-weight: 600;
        }}
        
        .market-item .change.positive {{
            color: #dc3545;
        }}
        
        .market-item .change.negative {{
            color: #0d6efd;
        }}
        
        /* 메인 컨텐츠 */
        .content {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 50px;
        }}
        
        .section-title {{
            font-size: 1.8em;
            font-weight: 700;
            color: #212529;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 3px solid #667eea;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .section-title .icon {{
            font-size: 1.2em;
        }}
        
        /* 통계 카드 */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 15px;
            text-align: center;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            transition: transform 0.3s ease;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
        }}
        
        .stat-card .value {{
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 5px;
        }}
        
        .stat-card .label {{
            font-size: 1em;
            opacity: 0.9;
        }}
        
        /* Top30 인사이트 - 카드형 그리드 */
        .insights-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .insight-card {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 20px;
            border-radius: 12px;
            border-left: 4px solid #667eea;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            transition: all 0.3s ease;
        }}
        
        .insight-card:hover {{
            transform: translateX(5px);
            box-shadow: 0 4px 20px rgba(102, 126, 234, 0.3);
        }}
        
        .insight-card .icon {{
            font-size: 1.8em;
            margin-bottom: 10px;
        }}
        
        .insight-card .title {{
            font-size: 1.1em;
            font-weight: 700;
            color: #495057;
            margin-bottom: 8px;
        }}
        
        .insight-card .value {{
            font-size: 1.5em;
            font-weight: 700;
            color: #667eea;
        }}
        
        /* 테이블 - 틀고정 완전 수정 */
        .table-container {{
            overflow-x: auto;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}
        
        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            background: white;
        }}
        
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        th {{
            padding: 15px 12px;
            text-align: center;
            font-weight: 600;
            font-size: 0.95em;
            position: sticky;
            top: 0;
            z-index: 10;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }}
        
        /* 종목명 틀고정 - 완전 수정 */
        th:nth-child(2), td:nth-child(2) {{
            position: sticky;
            left: 0;
            z-index: 5;
            background: white;
            box-shadow: 2px 0 5px rgba(0,0,0,0.1);
        }}
        
        th:nth-child(2) {{
            z-index: 15 !important;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            box-shadow: 2px 0 5px rgba(0,0,0,0.2);
        }}
        
        td {{
            padding: 12px;
            text-align: center;
            border-bottom: 1px solid #e9ecef;
            font-size: 0.9em;
        }}
        
        tbody tr {{
            transition: background 0.2s ease;
        }}
        
        tbody tr:hover {{
            background: #f8f9fa;
        }}
        
        .stock-name {{
            font-weight: 600;
            color: #212529;
            text-align: left !important;
            padding-left: 15px !important;
        }}
        
        .score {{
            font-weight: 700;
            font-size: 1.1em;
        }}
        
        .score.excellent {{ color: #dc3545; }}
        .score.good {{ color: #fd7e14; }}
        .score.fair {{ color: #ffc107; }}
        .score.normal {{ color: #20c997; }}
        
        .price {{
            font-weight: 600;
            color: #495057;
        }}
        
        .risk-tag {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        
        .risk-low {{ background: #d1e7dd; color: #0f5132; }}
        .risk-medium {{ background: #fff3cd; color: #856404; }}
        .risk-high {{ background: #f8d7da; color: #842029; }}
        
        .chart-cell {{
            padding: 5px !important;
        }}
        
        .chart-cell img {{
            display: block;
            width: 120px;
            height: auto;
            margin: 0 auto;
        }}
        
        /* 지표별 Top5 - 카드형 */
        .indicator-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        
        .indicator-card {{
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            padding: 15px;
            transition: all 0.3s ease;
        }}
        
        .indicator-card:hover {{
            border-color: #667eea;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.2);
            transform: translateY(-3px);
        }}
        
        .indicator-card .rank {{
            display: inline-block;
            width: 30px;
            height: 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 50%;
            text-align: center;
            line-height: 30px;
            font-weight: 700;
            margin-right: 10px;
        }}
        
        .indicator-card .stock-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        
        .indicator-card .stock-name-card {{
            font-weight: 700;
            font-size: 1.1em;
            color: #212529;
        }}
        
        .indicator-card .stock-score {{
            font-weight: 700;
            color: #667eea;
            font-size: 1.2em;
        }}
        
        .indicator-card .highlight-value {{
            background: linear-gradient(135deg, #ffeaa7 0%, #fdcb6e 100%);
            padding: 8px 15px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 1.1em;
            color: #2d3436;
            text-align: center;
            margin: 10px 0;
        }}
        
        .indicator-card .detail-row {{
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            font-size: 0.9em;
            color: #6c757d;
        }}
        
        /* 투자 가이드 - 2열 레이아웃 */
        .guide-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 25px;
            margin-bottom: 30px;
        }}
        
        .guide-card {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 25px;
            border-radius: 12px;
            border-left: 4px solid #667eea;
        }}
        
        .guide-card h3 {{
            font-size: 1.3em;
            color: #212529;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .guide-card ul {{
            list-style: none;
            padding: 0;
        }}
        
        .guide-card li {{
            padding: 8px 0;
            padding-left: 20px;
            position: relative;
            color: #495057;
        }}
        
        .guide-card li:before {{
            content: "▸";
            position: absolute;
            left: 0;
            color: #667eea;
            font-weight: 700;
        }}
        
        /* 푸터 */
        .footer {{
            background: #f8f9fa;
            padding: 30px;
            text-align: center;
            color: #6c757d;
            font-size: 0.9em;
            border-top: 3px solid #667eea;
        }}
        
        .footer .timestamp {{
            font-weight: 600;
            color: #495057;
            margin-bottom: 10px;
        }}
        
        /* 반응형 */
        @media (max-width: 768px) {{
            .insights-grid {{
                grid-template-columns: 1fr;
            }}
            
            .indicator-cards {{
                grid-template-columns: 1fr;
            }}
            
            .guide-grid {{
                grid-template-columns: 1fr;
            }}
            
            .market-section {{
                flex-direction: column;
                gap: 20px;
            }}
            
            .market-group {{
                flex-direction: column;
                gap: 15px;
            }}
            
            .market-group-title {{
                border-right: none;
                border-bottom: 2px solid #dee2e6;
                padding-bottom: 10px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 헤더 -->
        <div class="header">
            <h1>📈 스윙 트레이드 추천 리포트</h1>
            <p class="subtitle">AI 기반 종목 분석 시스템</p>
            <span class="version">v3.5 프리미엄 에디션</span>
        </div>
"""
    
    # 시장 정보
    html += """
        <!-- 시장 정보 -->
        <div class="market-info">
            <div class="market-section">
"""
    
    # 지수 그룹
    if indices:
        html += """
                <div class="market-group">
                    <div class="market-group-title">📊 주요 지수</div>
"""
        for name, data in indices.items():
            change_class = 'positive' if data['change'] > 0 else 'negative'
            change_symbol = '▲' if data['change'] > 0 else '▼'
            html += f"""
                    <div class="market-item">
                        <div class="label">{name}</div>
                        <div class="value">{data['value']:,.2f}</div>
                        <div class="change {change_class}">{change_symbol} {abs(data['change']):.2f}%</div>
                    </div>
"""
        html += """
                </div>
"""
    
    # 환율 그룹
    if rates:
        html += """
                <div class="market-group">
                    <div class="market-group-title">💱 주요 환율</div>
"""
        for currency, data in rates.items():
            change_class = 'positive' if data['change'] > 0 else 'negative'
            change_symbol = '▲' if data['change'] > 0 else '▼'
            html += f"""
                    <div class="market-item">
                        <div class="label">{currency}/KRW</div>
                        <div class="value">{data['rate']:,.2f}</div>
                        <div class="change {change_class}">{change_symbol} {abs(data['change']):.2f}%</div>
                    </div>
"""
        html += """
                </div>
"""
    
    html += """
            </div>
        </div>
"""
    
    # 메인 컨텐츠
    html += f"""
        <div class="content">
            <!-- 통계 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">📊</span>
                    분석 통계
                </div>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="value">{total_analyzed:,}</div>
                        <div class="label">분석 종목수</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{total_success:,}</div>
                        <div class="label">조건 충족 종목<br><small>(40점 이상 + 거래대금 5억 이상)</small></div>
                    </div>
                    <div class="stat-card">
                        <div class="value">30</div>
                        <div class="label">추천 종목수</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{avg_score:.1f}점</div>
                        <div class="label">평균 점수</div>
                    </div>
                </div>
            </div>
            
            <!-- Top30 인사이트 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">💡</span>
                    오늘의 Top 30 인사이트
                </div>
                <div class="insights-grid">
"""
    
    # 인사이트 계산
    high_scores = len([r for r in top_30 if r['score'] >= 70])
    low_risk = len([r for r in top_30 if r['risk_level'] == '낮음'])
    oversold = len([r for r in top_30 if r['details'].get('RSI', 100) < 30])
    high_volume = len([r for r in top_30 if float(r['details'].get('거래량비율', '0배').replace('배', '')) >= 2])
    low_position = len([r for r in top_30 if float(r['details'].get('52주위치', '100%').replace('%', '')) < 30])
    golden_cross = len([r for r in top_30 if '골든크로스' in r['details'].get('MA배열', '')])
    
    insights = [
        ("🎯", "고득점 종목", f"{high_scores}개", "70점 이상"),
        ("🟢", "저위험 종목", f"{low_risk}개", "안정적 투자"),
        ("📉", "과매도 구간", f"{oversold}개", "RSI 30 이하"),
        ("📊", "거래량 급증", f"{high_volume}개", "2배 이상"),
        ("💎", "저점 근접", f"{low_position}개", "52주 하위 30%"),
        ("✨", "골든크로스", f"{golden_cross}개", "단기 상승 신호")
    ]
    
    for icon, title, value, desc in insights:
        html += f"""
                    <div class="insight-card">
                        <div class="icon">{icon}</div>
                        <div class="title">{title}</div>
                        <div class="value">{value}</div>
                        <div style="color: #6c757d; font-size: 0.9em; margin-top: 5px;">{desc}</div>
                    </div>
"""
    
    html += """
                </div>
            </div>
            
            <!-- Top 30 테이블 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">🏆</span>
                    Top 30 추천 종목
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>점수</th>
                                <th>현재가</th>
                                <th>손절가</th>
                                <th>목표가</th>
                                <th>위험도</th>
                                <th>RSI</th>
                                <th>이격도</th>
                                <th>거래량</th>
                                <th>52주위치</th>
                                <th>30일 추세</th>
                            </tr>
                        </thead>
                        <tbody>
"""
    
    for i, stock in enumerate(top_30, 1):
        score_class = 'excellent' if stock['score'] >= 80 else 'good' if stock['score'] >= 70 else 'fair' if stock['score'] >= 60 else 'normal'
        risk_class = f"risk-{stock['risk_level'].lower()}"
        
        # 차트
        chart_html = ""
        if stock['ticker'] in chart_files:
            chart_html = f'<img src="{chart_files[stock["ticker"]]}" alt="차트">'
        
        # 네이버 뉴스 링크
        news_url = f"https://finance.naver.com/item/news.nhn?code={stock['code']}"
        
        html += f"""
                            <tr>
                                <td><strong>{i}</strong></td>
                                <td class="stock-name">
                                    <a href="{news_url}" target="_blank" style="color: #212529; text-decoration: none;">
                                        {stock['name']} 📰
                                    </a>
                                </td>
                                <td class="score {score_class}">{stock['score']}</td>
                                <td class="price">{stock['current_price']:,.0f}원</td>
                                <td style="color: #dc3545;">{stock['stop_loss']:,.0f}원</td>
                                <td style="color: #198754;">{stock['target_price']:,.0f}원</td>
                                <td>
                                    <span class="risk-tag {risk_class}">
                                        {stock['risk_icon']} {stock['risk_level']}
                                    </span>
                                </td>
                                <td>{stock['details'].get('RSI', 'N/A')}</td>
                                <td>{stock['details'].get('이격도', 'N/A')}</td>
                                <td>{stock['details'].get('거래량비율', 'N/A')}</td>
                                <td>{stock['details'].get('52주위치', 'N/A')}</td>
                                <td class="chart-cell">{chart_html}</td>
                            </tr>
"""
    
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
"""
    
    # 지표별 Top 5 (6개 섹션)
    indicator_sections = [
        ("RSI 과매도 Top 5", "🔻", top_rsi, "RSI", "과매도 강도"),
        ("이격도 저점 Top 5", "📉", top_disparity, "이격도", "저점 수준"),
        ("거래량 급증 Top 5", "📊", top_volume, "거래량비율", "급증 비율"),
        ("PBR 저평가 Top 5", "💎", top_pbr, "PBR", "저평가 정도"),
        ("단기 모멘텀 Top 5", "🚀", top_momentum, "5일수익률", "단기 상승률"),
        ("반등 강도 Top 5", "⚡", top_bounce, "반등강도", "반등 수준")
    ]
    
    for title, icon, stocks, key_metric, metric_label in indicator_sections:
        if not stocks:
            continue
            
        html += f"""
            <div class="section">
                <div class="section-title">
                    <span class="icon">{icon}</span>
                    {title}
                </div>
                <div class="indicator-cards">
"""
        
        for i, stock in enumerate(stocks, 1):
            metric_value = stock['details'].get(key_metric, 'N/A')
            
            html += f"""
                    <div class="indicator-card">
                        <div class="stock-info">
                            <div>
                                <span class="rank">{i}</span>
                                <span class="stock-name-card">{stock['name']}</span>
                            </div>
                            <span class="stock-score">{stock['score']}점</span>
                        </div>
                        <div class="highlight-value">
                            {metric_label}: {metric_value}
                        </div>
                        <div class="detail-row">
                            <span>현재가</span>
                            <strong>{stock['current_price']:,.0f}원</strong>
                        </div>
                        <div class="detail-row">
                            <span>목표가</span>
                            <strong style="color: #198754;">{stock['target_price']:,.0f}원</strong>
                        </div>
                        <div class="detail-row">
                            <span>위험도</span>
                            <strong>{stock['risk_icon']} {stock['risk_level']}</strong>
                        </div>
                    </div>
"""
        
        html += """
                </div>
            </div>
"""
    
    # 투자 성향별
    html += """
            <!-- 투자 성향별 추천 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">🎯</span>
                    투자 성향별 추천 (각 최대 8개)
                </div>
                <div class="guide-grid">
"""
    
    # 보수적
    html += """
                    <div class="guide-card">
                        <h3>🟢 보수적 투자자용</h3>
                        <ul>
"""
    for stock in conservative:
        html += f"""
                            <li><strong>{stock['name']}</strong> - {stock['score']}점 (RSI: {stock['details'].get('RSI', 'N/A')})</li>
"""
    html += """
                        </ul>
                    </div>
"""
    
    # 공격적
    html += """
                    <div class="guide-card">
                        <h3>🔴 공격적 투자자용</h3>
                        <ul>
"""
    for stock in aggressive:
        html += f"""
                            <li><strong>{stock['name']}</strong> - {stock['score']}점 (거래량: {stock['details'].get('거래량비율', 'N/A')})</li>
"""
    html += """
                        </ul>
                    </div>
"""
    
    html += """
                </div>
            </div>
            
            <!-- 투자 가이드 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">📚</span>
                    투자 가이드
                </div>
                <div class="guide-grid">
                    <div class="guide-card">
                        <h3>💡 점수 해석</h3>
                        <ul>
                            <li><strong>80점 이상:</strong> 매우 우수 (목표가 +25%)</li>
                            <li><strong>70-79점:</strong> 우수 (목표가 +20%)</li>
                            <li><strong>60-69점:</strong> 양호 (목표가 +15%)</li>
                            <li><strong>40-59점:</strong> 보통 (목표가 +10%)</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>⚠️ 위험도 분류</h3>
                        <ul>
                            <li><strong>🟢 낮음:</strong> 안정적, 초보자 적합</li>
                            <li><strong>🟡 보통:</strong> 적정 위험, 일반 투자자</li>
                            <li><strong>🔴 높음:</strong> 변동성 큼, 경험자 권장</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>📈 진입 전략</h3>
                        <ul>
                            <li>분할 매수 권장 (3회 이상)</li>
                            <li>손절가 도달 시 즉시 청산</li>
                            <li>목표가 도달 시 50% 익절</li>
                            <li>뉴스 및 공시 필수 확인</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>🎯 보유 기간</h3>
                        <ul>
                            <li><strong>단기:</strong> 2-5일 (고득점 + 고거래량)</li>
                            <li><strong>중기:</strong> 1-2주 (저평가 + 반등)</li>
                            <li><strong>장기:</strong> 1개월+ (우량주 + 저위험)</li>
                        </ul>
                    </div>
                </div>
            </div>
            
            <!-- 산출 기준 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">🔬</span>
                    점수 산출 기준
                </div>
                <div class="guide-grid">
                    <div class="guide-card">
                        <h3>📊 기술적 지표 (40점)</h3>
                        <ul>
                            <li><strong>RSI (10점):</strong> 25-35 과매도 구간</li>
                            <li><strong>이격도 (10점):</strong> -15% ~ -8% 저점</li>
                            <li><strong>거래량 (10점):</strong> 평균 대비 2.5배 이상</li>
                            <li><strong>모멘텀 (10점):</strong> 단기 반등 + 중기 조정</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>💼 재무 지표 (30점)</h3>
                        <ul>
                            <li><strong>PER (10점):</strong> 8 미만 저평가</li>
                            <li><strong>PBR (10점):</strong> 0.8 미만 우량</li>
                            <li><strong>부채비율 (10점):</strong> 100% 미만 안정</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>⏰ 시장 타이밍 (30점)</h3>
                        <ul>
                            <li><strong>52주 위치 (15점):</strong> 하위 20% 저점</li>
                            <li><strong>MA 배열 (15점):</strong> 골든크로스 신호</li>
                        </ul>
                    </div>
                    
                    <div class="guide-card">
                        <h3>🎲 필터링 조건</h3>
                        <ul>
                            <li>최소 점수: 40점 이상</li>
                            <li>최소 거래대금: 5억 원 이상</li>
                            <li>데이터 충분성: 60일 이상</li>
                        </ul>
                    </div>
                </div>
            </div>
            
            <!-- 주의사항 -->
            <div class="section">
                <div class="section-title">
                    <span class="icon">⚠️</span>
                    주의사항
                </div>
                <div style="background: linear-gradient(135deg, #fff5f5 0%, #ffe5e5 100%); padding: 25px; border-radius: 12px; border-left: 4px solid #dc3545;">
                    <ul style="list-style: none; padding: 0; color: #495057;">
                        <li style="padding: 8px 0; padding-left: 20px; position: relative;">
                            <span style="position: absolute; left: 0; color: #dc3545; font-weight: 700;">⚠️</span>
                            <strong>본 리포트는 투자 참고용이며, 투자 판단과 책임은 투자자 본인에게 있습니다.</strong>
                        </li>
                        <li style="padding: 8px 0; padding-left: 20px; position: relative;">
                            <span style="position: absolute; left: 0; color: #dc3545; font-weight: 700;">⚠️</span>
                            손절가는 반드시 준수하시고, 추가 하락 시 과감히 청산하세요.
                        </li>
                        <li style="padding: 8px 0; padding-left: 20px; position: relative;">
                            <span style="position: absolute; left: 0; color: #dc3545; font-weight: 700;">⚠️</span>
                            뉴스, 공시, 재무제표를 반드시 확인한 후 투자하세요.
                        </li>
                        <li style="padding: 8px 0; padding-left: 20px; position: relative;">
                            <span style="position: absolute; left: 0; color: #dc3545; font-weight: 700;">⚠️</span>
                            분산 투자 원칙을 지키고, 한 종목에 과도한 집중을 피하세요.
                        </li>
                    </ul>
                </div>
            </div>
        </div>
        
        <!-- 푸터 -->
        <div class="footer">
            <div class="timestamp">생성 시간: {timestamp}</div>
            <div>스윙 트레이드 추천 시스템 v3.5 프리미엄 에디션</div>
            <div style="margin-top: 10px; font-size: 0.85em;">
                Data: Yahoo Finance & KRX | Analysis: AI-Powered Multi-Factor Model
            </div>
        </div>
    </div>
</body>
</html>
"""
    
    return html


def main():
    """메인 실행"""
    print("=" * 60)
    print("🚀 스윙 트레이드 추천 시스템 v3.5 - 프리미엄 디자인")
    print("=" * 60)
    
    start_time = time.time()
    
    # 1. 티커 수집
    print("\n📋 Step 1: 한국 거래소 티커 수집 중...")
    tickers = get_krx_tickers()
    
    if not tickers:
        print("❌ 티커 수집 실패 - 종료")
        return
    
    print(f"✅ 총 {len(tickers)}개 종목 수집 완료")
    
    # 2. 종목 분석
    print(f"\n📊 Step 2: {len(tickers)}개 종목 분석 중...")
    print("⏱️  예상 소요 시간: 15-20분")
    
    results = []
    failed = 0
    
    for i, ticker in enumerate(tickers, 1):
        if i % 100 == 0:
            elapsed = time.time() - start_time
            print(f"  진행률: {i}/{len(tickers)} ({i/len(tickers)*100:.1f}%) | 경과: {elapsed/60:.1f}분")
        
        result = analyze_stock(ticker)
        if result:
            results.append(result)
        else:
            failed += 1
    
    print(f"\n✅ 분석 완료:")
    print(f"  - 조건 충족 종목: {len(results)}개")
    print(f"  - 필터링: {failed}개 (조건 미충족)")
    
    if not results:
        print("❌ 조건 충족 종목 없음 - 종료")
        return
    
    # 3. HTML 생성
    print("\n📝 Step 3: HTML 리포트 생성 중...")
    html_content = generate_html_report(results, len(tickers), len(results))
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ index.html 생성 완료")
    
    # 4. 완료
    total_time = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"🎉 모든 작업 완료! (총 소요 시간: {total_time/60:.1f}분)")
    print("=" * 60)
    print(f"\n📊 결과:")
    print(f"  - 분석 종목: {len(tickers)}개")
    print(f"  - 조건 충족: {len(results)}개")
    print(f"  - Top 30 평균 점수: {np.mean([r['score'] for r in sorted(results, key=lambda x: x['score'], reverse=True)[:30]]):.1f}점")
    print(f"\n📁 생성 파일:")
    print(f"  - index.html ({os.path.getsize('index.html')/1024:.1f} KB)")
    if os.path.exists('charts'):
        chart_count = len(glob.glob('charts/*.png'))
        print(f"  - charts/*.png ({chart_count}개)")


if __name__ == "__main__":
    main()
