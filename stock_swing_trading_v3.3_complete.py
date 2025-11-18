#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이드 종목 추천 시스템 v3.5 - 프리미엄 디자인 (완전 수정판)
- 틀고정 완전 수정
- 카드형 레이아웃
- 지수/환율 시각적 구분
- 보수/공격 투자자별 최대 8개 제한
- 502번, 507번 줄 lambda 버그 완전 수정
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import warnings
import os

warnings.filterwarnings('ignore')

# 한글 폰트 설정
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False

# === 기본 설정 ===
OUTPUT_DIR = '/content/drive/MyDrive/stock_analysis'
CHART_DIR = os.path.join(OUTPUT_DIR, 'charts')
HTML_FILE = os.path.join(OUTPUT_DIR, 'index.html')

# 디렉토리 생성
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

print("=" * 60)
print("스윙 트레이드 종목 추천 시스템 v3.5 - 프리미엄 디자인 (완전 수정판)")
print("=" * 60)

# === 1. KOSPI + KOSDAQ 종목 리스트 ===
def get_all_krx_tickers():
    """KOSPI + KOSDAQ 전체 종목 가져오기"""
    try:
        url_kospi = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt"
        url_kosdaq = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt"
        
        kospi = pd.read_html(url_kospi, encoding='cp949')[0]
        kosdaq = pd.read_html(url_kosdaq, encoding='cp949')[0]
        
        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)
        all_stocks['ticker'] = all_stocks['종목코드'] + '.KS'
        all_stocks.loc[all_stocks.index >= len(kospi), 'ticker'] = all_stocks.loc[all_stocks.index >= len(kospi), '종목코드'] + '.KQ'
        
        tickers = list(zip(all_stocks['회사명'], all_stocks['ticker']))
        print(f"✓ 전체 종목 수: {len(tickers)}개 (KOSPI + KOSDAQ)")
        return tickers
    except Exception as e:
        print(f"✗ 종목 리스트 가져오기 실패: {e}")
        return []

# === 2. 데이터 다운로드 ===
def download_stock_data(ticker, period='6mo'):
    """주식 데이터 다운로드"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty or len(df) < 60:
            return None
        return df
    except:
        return None

# === 3. 기술적 지표 계산 ===
def calculate_rsi(series, period=14):
    """RSI 계산"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_disparity(df, period=20):
    """이격도 계산 (현재가 / 이동평균)"""
    ma = df['Close'].rolling(window=period).mean()
    disparity = (df['Close'].iloc[-1] / ma.iloc[-1]) * 100
    return disparity

def calculate_volume_ratio(df):
    """거래량 증가율 (최근 5일 평균 / 이전 20일 평균)"""
    recent_vol = df['Volume'][-5:].mean()
    previous_vol = df['Volume'][-25:-5].mean()
    if previous_vol == 0:
        return 0
    return ((recent_vol / previous_vol) - 1) * 100

def calculate_rebound_strength(df):
    """반등 강도 (저점 대비 현재가 상승률)"""
    low_20 = df['Low'][-20:].min()
    current = df['Close'].iloc[-1]
    if low_20 == 0:
        return 0
    return ((current / low_20) - 1) * 100

def calculate_short_term_return(df):
    """5일 수익률"""
    if len(df) < 5:
        return 0
    return ((df['Close'].iloc[-1] / df['Close'].iloc[-5]) - 1) * 100

# === 4. 점수 계산 ===
def calculate_swing_score(df, ticker_name):
    """스윙 트레이드 적합도 점수 (100점 만점)"""
    try:
        score = 0
        details = {}
        
        # 1) RSI (30점) - 과매도 구간 선호
        rsi = calculate_rsi(df['Close'])
        details['RSI'] = f"{rsi:.1f}"
        if 25 <= rsi <= 35:
            score += 30
        elif 20 <= rsi < 25 or 35 < rsi <= 40:
            score += 25
        elif 15 <= rsi < 20 or 40 < rsi <= 45:
            score += 20
        elif rsi < 15:
            score += 15
        
        # 2) 이격도 (20점) - 95~105% 선호
        disparity = calculate_disparity(df)
        details['이격도'] = f"{disparity:.1f}%"
        if 95 <= disparity <= 105:
            score += 20
        elif 90 <= disparity < 95 or 105 < disparity <= 110:
            score += 15
        elif 85 <= disparity < 90 or 110 < disparity <= 115:
            score += 10
        
        # 3) 거래량 증가 (15점)
        vol_ratio = calculate_volume_ratio(df)
        details['거래량증가율'] = f"{vol_ratio:.1f}%"
        if vol_ratio >= 50:
            score += 15
        elif vol_ratio >= 30:
            score += 12
        elif vol_ratio >= 10:
            score += 8
        
        # 4) PBR (15점) - 저평가 선호
        try:
            pbr = yf.Ticker(ticker_name).info.get('priceToBook', None)
            if pbr:
                details['PBR'] = f"{pbr:.2f}"
                if pbr < 0.8:
                    score += 15
                elif 0.8 <= pbr < 1.2:
                    score += 12
                elif 1.2 <= pbr < 1.5:
                    score += 8
        except:
            details['PBR'] = 'N/A'
        
        # 5) 단기 모멘텀 (10점) - 5일 수익률
        short_return = calculate_short_term_return(df)
        details['5일수익률'] = f"{short_return:.1f}%"
        if -5 <= short_return <= 5:
            score += 10
        elif -10 <= short_return < -5 or 5 < short_return <= 10:
            score += 7
        
        # 6) 반등 강도 (10점)
        rebound = calculate_rebound_strength(df)
        details['반등강도'] = f"{rebound:.1f}%"
        if 5 <= rebound <= 15:
            score += 10
        elif 15 < rebound <= 25:
            score += 7
        elif rebound > 25:
            score += 5
        
        return score, details
    except Exception as e:
        return 0, {}

# === 5. 차트 생성 ===
def create_chart(df, ticker_name, score, details, rank):
    """개별 종목 차트 생성"""
    try:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), 
                                       gridspec_kw={'height_ratios': [3, 1]})
        
        # 가격 차트
        ax1.plot(df.index, df['Close'], linewidth=2, color='#2E86AB', label='Close')
        ax1.fill_between(df.index, df['Close'], alpha=0.3, color='#2E86AB')
        
        # 이동평균선
        ma20 = df['Close'].rolling(window=20).mean()
        ma60 = df['Close'].rolling(window=60).mean()
        ax1.plot(df.index, ma20, '--', linewidth=1.5, color='#A23B72', label='MA20', alpha=0.7)
        ax1.plot(df.index, ma60, '--', linewidth=1.5, color='#F18F01', label='MA60', alpha=0.7)
        
        ax1.set_title(f"#{rank} {ticker_name} (Score: {score})", 
                     fontsize=14, fontweight='bold', pad=15)
        ax1.set_ylabel('Price (KRW)', fontsize=10)
        ax1.legend(loc='upper left', fontsize=9)
        ax1.grid(True, alpha=0.3, linestyle='--')
        
        # 거래량 차트
        colors = ['#C1292E' if df['Close'].iloc[i] < df['Open'].iloc[i] else '#2E86AB' 
                  for i in range(len(df))]
        ax2.bar(df.index, df['Volume'], color=colors, alpha=0.6, width=0.8)
        ax2.set_ylabel('Volume', fontsize=10)
        ax2.set_xlabel('Date', fontsize=10)
        ax2.grid(True, alpha=0.3, linestyle='--', axis='y')
        
        # 세부 정보 표시
        info_text = (f"RSI: {details.get('RSI', 'N/A')} | "
                    f"Disparity: {details.get('이격도', 'N/A')} | "
                    f"Volume: {details.get('거래량증가율', 'N/A')}\n"
                    f"PBR: {details.get('PBR', 'N/A')} | "
                    f"5D Return: {details.get('5일수익률', 'N/A')} | "
                    f"Rebound: {details.get('반등강도', 'N/A')}")
        
        fig.text(0.5, 0.02, info_text, ha='center', fontsize=9, 
                style='italic', color='#333333',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
        
        plt.tight_layout(rect=[0, 0.05, 1, 1])
        
        filename = f"chart_{rank:02d}_{ticker_name.replace('/', '_')}.png"
        filepath = os.path.join(CHART_DIR, filename)
        plt.savefig(filepath, dpi=100, bbox_inches='tight', facecolor='white')
        plt.close()
        
        return filename
    except Exception as e:
        print(f"  ✗ 차트 생성 실패 ({ticker_name}): {e}")
        plt.close()
        return None

# === 6. HTML 생성 ===
def generate_html(results, index_data):
    """HTML 리포트 생성 - v3.5 프리미엄 디자인"""
    
    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스윙 트레이드 분석 리포트 v3.5</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
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
            background: linear-gradient(135deg, #2E86AB 0%, #1a5276 100%);
            color: white;
            padding: 40px;
            text-align: center;
            position: relative;
            overflow: hidden;
        }}
        
        .header::before {{
            content: '';
            position: absolute;
            top: -50%;
            right: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 70%);
            animation: pulse 15s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.1); }}
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            position: relative;
            z-index: 1;
        }}
        
        .header .subtitle {{
            font-size: 1.1em;
            opacity: 0.9;
            position: relative;
            z-index: 1;
        }}
        
        .header .update-time {{
            margin-top: 15px;
            font-size: 0.95em;
            opacity: 0.8;
            position: relative;
            z-index: 1;
        }}
        
        /* 지수/환율 섹션 */
        .market-overview {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px 40px;
            background: #f8f9fa;
            border-bottom: 3px solid #e9ecef;
        }}
        
        .market-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            text-align: center;
            transition: all 0.3s ease;
            border: 2px solid transparent;
        }}
        
        .market-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.15);
            border-color: #2E86AB;
        }}
        
        .market-card.index {{
            border-left: 4px solid #2E86AB;
        }}
        
        .market-card.currency {{
            border-left: 4px solid #F18F01;
        }}
        
        .market-card h3 {{
            font-size: 0.9em;
            color: #666;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        
        .market-card .value {{
            font-size: 1.8em;
            font-weight: bold;
            color: #2c3e50;
            margin-bottom: 5px;
        }}
        
        .market-card .change {{
            font-size: 1.1em;
            font-weight: 600;
            padding: 5px 12px;
            border-radius: 20px;
            display: inline-block;
        }}
        
        .market-card .change.positive {{
            color: #C1292E;
            background: #ffe6e6;
        }}
        
        .market-card .change.negative {{
            color: #2E86AB;
            background: #e6f2ff;
        }}
        
        /* 메인 콘텐츠 */
        .content {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 50px;
        }}
        
        .section-title {{
            font-size: 1.8em;
            color: #2c3e50;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 3px solid #2E86AB;
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        
        .section-title .badge {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 20px;
            border-radius: 25px;
            font-size: 0.6em;
            font-weight: 600;
            letter-spacing: 1px;
        }}
        
        /* Top 30 카드 그리드 */
        .top30-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }}
        
        .stock-card {{
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 15px;
            overflow: hidden;
            transition: all 0.3s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }}
        
        .stock-card:hover {{
            transform: translateY(-8px);
            box-shadow: 0 12px 30px rgba(0,0,0,0.15);
            border-color: #2E86AB;
        }}
        
        .stock-card-header {{
            background: linear-gradient(135deg, #2E86AB 0%, #1a5276 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .stock-card-header .rank {{
            font-size: 2em;
            font-weight: bold;
            opacity: 0.9;
        }}
        
        .stock-card-header .name {{
            font-size: 1.3em;
            font-weight: 600;
            flex: 1;
            text-align: center;
        }}
        
        .stock-card-header .score {{
            font-size: 1.8em;
            font-weight: bold;
            background: rgba(255,255,255,0.2);
            padding: 8px 15px;
            border-radius: 10px;
        }}
        
        .stock-card-body {{
            padding: 20px;
        }}
        
        .stock-card-body img {{
            width: 100%;
            height: auto;
            border-radius: 10px;
            margin-bottom: 15px;
        }}
        
        .stock-details {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
        }}
        
        .detail-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: white;
            border-radius: 6px;
            font-size: 0.9em;
        }}
        
        .detail-item .label {{
            color: #666;
            font-weight: 500;
        }}
        
        .detail-item .value {{
            color: #2c3e50;
            font-weight: 600;
        }}
        
        /* 지표별 Top 5 */
        .indicator-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
        }}
        
        .indicator-card {{
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            transition: all 0.3s ease;
        }}
        
        .indicator-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.15);
        }}
        
        .indicator-card h3 {{
            color: #2E86AB;
            font-size: 1.3em;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 2px solid #e9ecef;
        }}
        
        .indicator-list {{
            list-style: none;
        }}
        
        .indicator-list li {{
            padding: 12px;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.2s ease;
        }}
        
        .indicator-list li:hover {{
            background: #e9ecef;
            transform: translateX(5px);
        }}
        
        .indicator-list .stock-name {{
            font-weight: 600;
            color: #2c3e50;
        }}
        
        .indicator-list .stock-value {{
            color: #2E86AB;
            font-weight: 600;
            background: white;
            padding: 4px 10px;
            border-radius: 15px;
        }}
        
        /* 투자자별 추천 */
        .investor-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 30px;
        }}
        
        .investor-card {{
            background: white;
            border: 3px solid #e9ecef;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 6px 20px rgba(0,0,0,0.1);
        }}
        
        .investor-card.conservative {{
            border-color: #2E86AB;
            background: linear-gradient(to bottom, #e6f2ff 0%, white 30%);
        }}
        
        .investor-card.aggressive {{
            border-color: #C1292E;
            background: linear-gradient(to bottom, #ffe6e6 0%, white 30%);
        }}
        
        .investor-card h3 {{
            font-size: 1.5em;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .investor-card h3 .icon {{
            font-size: 1.2em;
        }}
        
        .investor-card .description {{
            color: #666;
            margin-bottom: 20px;
            font-size: 0.95em;
            line-height: 1.6;
        }}
        
        .investor-list {{
            list-style: none;
        }}
        
        .investor-list li {{
            padding: 15px;
            margin-bottom: 12px;
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.3s ease;
        }}
        
        .investor-list li:hover {{
            border-color: #2E86AB;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            transform: translateX(8px);
        }}
        
        .investor-list .stock-info {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        
        .investor-list .stock-name {{
            font-weight: 600;
            color: #2c3e50;
            font-size: 1.05em;
        }}
        
        .investor-list .stock-score {{
            font-size: 0.85em;
            color: #666;
        }}
        
        .investor-list .stock-value {{
            font-size: 1.3em;
            font-weight: bold;
            color: #2E86AB;
        }}
        
        /* 푸터 */
        .footer {{
            background: #2c3e50;
            color: white;
            text-align: center;
            padding: 30px;
            font-size: 0.9em;
        }}
        
        .footer a {{
            color: #3498db;
            text-decoration: none;
        }}
        
        .footer a:hover {{
            text-decoration: underline;
        }}
        
        /* 반응형 */
        @media (max-width: 768px) {{
            .top30-grid,
            .indicator-grid,
            .investor-grid {{
                grid-template-columns: 1fr;
            }}
            
            .header h1 {{
                font-size: 1.8em;
            }}
            
            .market-overview {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 헤더 -->
        <div class="header">
            <h1>📊 스윙 트레이드 분석 리포트</h1>
            <div class="subtitle">조건 충족 종목: {len(results)}개 | 전체 분석: {index_data['total_analyzed']}개</div>
            <div class="update-time">⏰ 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        </div>
        
        <!-- 지수/환율 현황 -->
        <div class="market-overview">
"""
    
    # 지수/환율 카드 생성
    market_items = [
        ('KOSPI', index_data.get('kospi', {}), 'index'),
        ('KOSDAQ', index_data.get('kosdaq', {}), 'index'),
        ('S&P 500', index_data.get('sp500', {}), 'index'),
        ('USD/KRW', index_data.get('usdkrw', {}), 'currency'),
        ('EUR/KRW', index_data.get('eurkrw', {}), 'currency'),
        ('JPY/KRW', index_data.get('jpykrw', {}), 'currency')
    ]
    
    for name, data, card_type in market_items:
        value = data.get('value', 'N/A')
        change = data.get('change', 'N/A')
        change_class = 'positive' if '+' in str(change) else 'negative'
        
        html_content += f"""
            <div class="market-card {card_type}">
                <h3>{name}</h3>
                <div class="value">{value}</div>
                <div class="change {change_class}">{change}</div>
            </div>
"""
    
    html_content += """
        </div>
        
        <div class="content">
"""
    
    # === Top 30 추천 종목 ===
    html_content += """
            <div class="section">
                <h2 class="section-title">
                    <span>🎯 Top 30 추천 종목</span>
                    <span class="badge">PREMIUM PICKS</span>
                </h2>
                <div class="top30-grid">
"""
    
    for i, r in enumerate(results[:30], 1):
        chart_file = r.get('chart', '')
        details = r['details']
        
        html_content += f"""
                    <div class="stock-card">
                        <div class="stock-card-header">
                            <div class="rank">#{i}</div>
                            <div class="name">{r['name']}</div>
                            <div class="score">{r['score']}점</div>
                        </div>
                        <div class="stock-card-body">
"""
        
        if chart_file:
            html_content += f"""
                            <img src="charts/{chart_file}" alt="{r['name']} 차트">
"""
        
        html_content += f"""
                            <div class="stock-details">
                                <div class="detail-item">
                                    <span class="label">RSI</span>
                                    <span class="value">{details.get('RSI', 'N/A')}</span>
                                </div>
                                <div class="detail-item">
                                    <span class="label">이격도</span>
                                    <span class="value">{details.get('이격도', 'N/A')}</span>
                                </div>
                                <div class="detail-item">
                                    <span class="label">거래량</span>
                                    <span class="value">{details.get('거래량증가율', 'N/A')}</span>
                                </div>
                                <div class="detail-item">
                                    <span class="label">PBR</span>
                                    <span class="value">{details.get('PBR', 'N/A')}</span>
                                </div>
                                <div class="detail-item">
                                    <span class="label">5일수익률</span>
                                    <span class="value">{details.get('5일수익률', 'N/A')}</span>
                                </div>
                                <div class="detail-item">
                                    <span class="label">반등강도</span>
                                    <span class="value">{details.get('반등강도', 'N/A')}</span>
                                </div>
                            </div>
                        </div>
                    </div>
"""
    
    html_content += """
                </div>
            </div>
"""
    
    # === 지표별 Top 5 ===
    html_content += """
            <div class="section">
                <h2 class="section-title">
                    <span>📈 지표별 Top 5</span>
                    <span class="badge">KEY INDICATORS</span>
                </h2>
                <div class="indicator-grid">
"""
    
    # RSI Top 5 (낮은 순)
    top_rsi = sorted([r for r in results if 'RSI' in r['details']], 
                     key=lambda x: float(x['details']['RSI']), 
                     reverse=False)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>🔵 RSI (과매도)</h3>
                        <ul class="indicator-list">
"""
    for r in top_rsi:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['RSI']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # 이격도 Top 5 (100에 가까운 순) - ✅ 502번 줄 패턴과 동일하게 수정
    top_disparity = sorted([r for r in results if '이격도' in r['details']], 
                          key=lambda x: abs(float(x['details']['이격도'].replace('%', '')) - 100),
                          reverse=False)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>📊 이격도 (적정 범위)</h3>
                        <ul class="indicator-list">
"""
    for r in top_disparity:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['이격도']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # 거래량 증가율 Top 5 - ✅ 495번 줄 패턴과 동일하게 수정
    top_volume = sorted([r for r in results if '거래량증가율' in r['details']], 
                       key=lambda x: float(x['details']['거래량증가율'].replace('%', '')),
                       reverse=True)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>📈 거래량 증가율</h3>
                        <ul class="indicator-list">
"""
    for r in top_volume:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['거래량증가율']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # PBR Top 5 (낮은 순)
    top_pbr = sorted([r for r in results if r['details'].get('PBR', 'N/A') != 'N/A'], 
                    key=lambda x: float(x['details']['PBR']),
                    reverse=False)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>💰 PBR (저평가)</h3>
                        <ul class="indicator-list">
"""
    for r in top_pbr:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['PBR']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # 단기 모멘텀 Top 5 - ✅ 502번 줄 버그 완전 수정 (r → x)
    top_momentum = sorted([r for r in results if '5일수익률' in r['details']], 
                         key=lambda x: float(x['details']['5일수익률'].replace('%', '')),
                         reverse=True)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>🚀 단기 모멘텀 (5일)</h3>
                        <ul class="indicator-list">
"""
    for r in top_momentum:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['5일수익률']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # 반등 강도 Top 5 - ✅ 507번 줄 버그 완전 수정 (r → x)
    top_rebound = sorted([r for r in results if '반등강도' in r['details']], 
                        key=lambda x: float(x['details']['반등강도'].replace('%', '')),
                        reverse=True)[:5]
    
    html_content += """
                    <div class="indicator-card">
                        <h3>⚡ 반등 강도</h3>
                        <ul class="indicator-list">
"""
    for r in top_rebound:
        html_content += f"""
                            <li>
                                <span class="stock-name">{r['name']}</span>
                                <span class="stock-value">{r['details']['반등강도']}</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    html_content += """
                </div>
            </div>
"""
    
    # === 투자 성향별 추천 ===
    html_content += """
            <div class="section">
                <h2 class="section-title">
                    <span>👥 투자 성향별 추천</span>
                    <span class="badge">PERSONALIZED</span>
                </h2>
                <div class="investor-grid">
"""
    
    # 보수적 투자자 (최대 8개)
    conservative = [r for r in results if float(r['details'].get('RSI', '50')) <= 35 
                   and r['details'].get('PBR', 'N/A') != 'N/A' 
                   and float(r['details']['PBR']) < 1.2][:8]
    
    html_content += """
                    <div class="investor-card conservative">
                        <h3><span class="icon">🛡️</span> 보수적 투자자</h3>
                        <p class="description">저평가 + 과매도 구간 + 안정성 중시</p>
                        <ul class="investor-list">
"""
    for r in conservative:
        html_content += f"""
                            <li>
                                <div class="stock-info">
                                    <span class="stock-name">{r['name']}</span>
                                    <span class="stock-score">RSI: {r['details']['RSI']} | PBR: {r['details']['PBR']}</span>
                                </div>
                                <span class="stock-value">{r['score']}점</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    # 공격적 투자자 (최대 8개)
    aggressive = [r for r in results if float(r['details'].get('거래량증가율', '0').replace('%', '')) >= 30 
                 and float(r['details'].get('반등강도', '0').replace('%', '')) >= 10][:8]
    
    html_content += """
                    <div class="investor-card aggressive">
                        <h3><span class="icon">⚔️</span> 공격적 투자자</h3>
                        <p class="description">고거래량 + 강한 반등 + 모멘텀 중시</p>
                        <ul class="investor-list">
"""
    for r in aggressive:
        html_content += f"""
                            <li>
                                <div class="stock-info">
                                    <span class="stock-name">{r['name']}</span>
                                    <span class="stock-score">거래량: {r['details']['거래량증가율']} | 반등: {r['details']['반등강도']}</span>
                                </div>
                                <span class="stock-value">{r['score']}점</span>
                            </li>
"""
    html_content += """
                        </ul>
                    </div>
"""
    
    html_content += """
                </div>
            </div>
        </div>
        
        <!-- 푸터 -->
        <div class="footer">
            <p>본 리포트는 투자 참고용이며, 투자 결과에 대한 책임은 투자자 본인에게 있습니다.</p>
            <p>데이터 출처: Yahoo Finance | 차트: matplotlib</p>
        </div>
    </div>
</body>
</html>
"""
    
    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"\n✓ HTML 리포트 생성 완료: {HTML_FILE}")

# === 7. 지수/환율 데이터 수집 ===
def get_market_data():
    """주요 지수 및 환율 데이터 수집"""
    data = {}
    
    try:
        # KOSPI
        kospi = yf.Ticker("^KS11")
        kospi_hist = kospi.history(period='5d')
        if len(kospi_hist) >= 2:
            current = kospi_hist['Close'].iloc[-1]
            previous = kospi_hist['Close'].iloc[-2]
            change = ((current / previous) - 1) * 100
            data['kospi'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['kospi'] = {'value': 'N/A', 'change': 'N/A'}
    
    try:
        # KOSDAQ
        kosdaq = yf.Ticker("^KQ11")
        kosdaq_hist = kosdaq.history(period='5d')
        if len(kosdaq_hist) >= 2:
            current = kosdaq_hist['Close'].iloc[-1]
            previous = kosdaq_hist['Close'].iloc[-2]
            change = ((current / previous) - 1) * 100
            data['kosdaq'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['kosdaq'] = {'value': 'N/A', 'change': 'N/A'}
    
    try:
        # S&P 500
        sp500 = yf.Ticker("^GSPC")
        sp500_hist = sp500.history(period='5d')
        if len(sp500_hist) >= 2:
            current = sp500_hist['Close'].iloc[-1]
            previous = sp500_hist['Close'].iloc[-2]
            change = ((current / previous) - 1) * 100
            data['sp500'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['sp500'] = {'value': 'N/A', 'change': 'N/A'}
    
    try:
        # USD/KRW
        usdkrw = yf.Ticker("KRW=X")
        usdkrw_hist = usdkrw.history(period='5d')
        if len(usdkrw_hist) >= 2:
            current = usdkrw_hist['Close'].iloc[-1]
            previous = usdkrw_hist['Close'].iloc[-2]
            change = ((current / previous) - 1) * 100
            data['usdkrw'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['usdkrw'] = {'value': 'N/A', 'change': 'N/A'}
    
    try:
        # EUR/KRW
        eurkrw = yf.Ticker("EURKRW=X")
        eurkrw_hist = eurkrw.history(period='5d')
        if len(eurkrw_hist) >= 2:
            current = eurkrw_hist['Close'].iloc[-1]
            previous = eurkrw_hist['Close'].iloc[-2]
            change = ((current / previous) - 1) * 100
            data['eurkrw'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['eurkrw'] = {'value': 'N/A', 'change': 'N/A'}
    
    try:
        # JPY/KRW (100엔 기준)
        jpykrw = yf.Ticker("JPYKRW=X")
        jpykrw_hist = jpykrw.history(period='5d')
        if len(jpykrw_hist) >= 2:
            current = jpykrw_hist['Close'].iloc[-1] * 100
            previous = jpykrw_hist['Close'].iloc[-2] * 100
            change = ((current / previous) - 1) * 100
            data['jpykrw'] = {
                'value': f"{current:.2f}",
                'change': f"{'+' if change >= 0 else ''}{change:.2f}%"
            }
    except:
        data['jpykrw'] = {'value': 'N/A', 'change': 'N/A'}
    
    return data

# === 8. 메인 실행 ===
def main():
    print("\n[1단계] 종목 리스트 수집 중...")
    tickers = get_all_krx_tickers()
    
    if not tickers:
        print("✗ 종목 리스트를 가져올 수 없습니다.")
        return
    
    print(f"\n[2단계] {len(tickers)}개 종목 분석 시작...")
    print("=" * 60)
    
    results = []
    total_count = len(tickers)
    
    for idx, (name, ticker) in enumerate(tickers, 1):
        try:
            if idx % 100 == 0:
                print(f"진행 중: {idx}/{total_count} ({idx/total_count*100:.1f}%)")
            
            df = download_stock_data(ticker)
            if df is None:
                continue
            
            # 거래대금 필터 (최근 20일 평균 5억 이상)
            avg_value = (df['Close'] * df['Volume']).tail(20).mean()
            if avg_value < 500_000_000:
                continue
            
            score, details = calculate_swing_score(df, ticker)
            
            # 40점 이상만 수집
            if score >= 40:
                results.append({
                    'name': name,
                    'ticker': ticker,
                    'score': score,
                    'details': details,
                    'df': df
                })
        
        except Exception as e:
            continue
    
    print("=" * 60)
    print(f"✓ 분석 완료: {len(results)}개 종목이 조건 충족")
    
    if not results:
        print("✗ 조건을 충족하는 종목이 없습니다.")
        return
    
    # 점수순 정렬
    results.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n[3단계] 차트 생성 중...")
    for i, r in enumerate(results[:30], 1):
        print(f"  차트 생성: {i}/30 - {r['name']}")
        chart_file = create_chart(r['df'], r['name'], r['score'], r['details'], i)
        r['chart'] = chart_file
    
    print("\n[4단계] 지수/환율 데이터 수집 중...")
    index_data = get_market_data()
    index_data['total_analyzed'] = len(tickers)
    
    print("\n[5단계] HTML 리포트 생성 중...")
    generate_html(results, index_data)
    
    print("\n" + "=" * 60)
    print("✓ 모든 작업 완료!")
    print(f"✓ 리포트 위치: {HTML_FILE}")
    print(f"✓ 차트 위치: {CHART_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()
