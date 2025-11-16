#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 종합 추천 시스템 (환율 연동 버전)
- 코스피/코스닥 지수 (10영업일 확인)
- 실시간 환율 (USD, JPY, EUR) - 한국은행 API
- 종목 0개여도 페이지 생성
"""

import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime, timedelta
import warnings
import os
import requests

warnings.filterwarnings('ignore')

# 한국은행 API 키
BOK_API_KEY = "GVEYC4C6R9ZM5JFAQ2FY"

# ==================== 1. 데이터 수집 ====================

def get_stock_list():
    """코스피 + 코스닥 전체 종목 리스트"""
    today = datetime.now().strftime('%Y%m%d')
    kospi = stock.get_market_ticker_list(today, market="KOSPI")
    kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")
    return kospi + kosdaq

def get_stock_data(ticker, days=30):
    """개별 종목 데이터 수집"""
    try:
        end = datetime.now()
        start = end - timedelta(days=days)
        
        df = stock.get_market_ohlcv_by_date(
            start.strftime('%Y%m%d'),
            end.strftime('%Y%m%d'),
            ticker
        )
        
        if df.empty or len(df) < 20:
            return None
            
        return df
    except:
        return None

def get_fundamental_data(ticker):
    """기본적 분석 데이터"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        fundamental = stock.get_market_fundamental(today, today, ticker)
        
        if fundamental.empty:
            return None
            
        return fundamental.iloc[0]
    except:
        return None

# ==================== 2. 기술적 지표 계산 ====================

def calculate_rsi(prices, period=14):
    """RSI 계산"""
    try:
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi.iloc[-1]
    except:
        return 50

def calculate_disparity(prices, period=20):
    """이격도 계산"""
    try:
        ma = prices.rolling(window=period).mean()
        disparity = (prices / ma) * 100
        return disparity.iloc[-1]
    except:
        return 100

def calculate_volume_ratio(volumes, period=20):
    """거래량 비율"""
    try:
        avg_volume = volumes.iloc[:-1].tail(period).mean()
        current_volume = volumes.iloc[-1]
        
        if avg_volume == 0:
            return 100
            
        return (current_volume / avg_volume) * 100
    except:
        return 100

# ==================== 3. 종합 점수 계산 ====================

def calculate_comprehensive_score(rsi, disparity, volume_ratio, pbr):
    """
    종합점수 = RSI 점수(30) + 이격도 점수(25) + 거래량 점수(25) + PBR 점수(20)
    """
    score = 0
    
    # 1. RSI 점수 (0-30점)
    if pd.notna(rsi):
        if rsi < 20:
            score += 30
        elif rsi < 25:
            score += 25
        elif rsi < 30:
            score += 20
        elif rsi < 35:
            score += 15
        elif rsi < 40:
            score += 10
        elif rsi < 50:
            score += 5
    
    # 2. 이격도 점수 (0-25점)
    if pd.notna(disparity):
        if disparity < 85:
            score += 25
        elif disparity < 90:
            score += 20
        elif disparity < 95:
            score += 15
        elif disparity < 98:
            score += 10
        elif disparity < 100:
            score += 5
    
    # 3. 거래량 점수 (0-25점)
    if pd.notna(volume_ratio):
        if volume_ratio > 300:
            score += 25
        elif volume_ratio > 250:
            score += 20
        elif volume_ratio > 200:
            score += 15
        elif volume_ratio > 150:
            score += 10
        elif volume_ratio > 120:
            score += 5
    
    # 4. PBR 점수 (0-20점)
    if pd.notna(pbr) and pbr > 0:
        if pbr < 0.3:
            score += 20
        elif pbr < 0.5:
            score += 15
        elif pbr < 0.7:
            score += 10
        elif pbr < 1.0:
            score += 5
    
    return score

def calculate_risk_level(pbr, market_cap, sector):
    """
    위험도 계산 (기업 안정성 기반 - 참고용)
    """
    risk = 0
    
    # 1. PBR 위험도
    if pd.notna(pbr) and pbr > 0:
        if pbr < 0.3:
            risk += 30
        elif pbr < 0.5:
            risk += 20
        elif pbr < 0.7:
            risk += 10
        elif pbr < 1.0:
            risk += 5
    else:
        risk += 25
    
    # 2. 시가총액 위험도
    if pd.notna(market_cap):
        if market_cap < 500:
            risk += 30
        elif market_cap < 1000:
            risk += 20
        elif market_cap < 5000:
            risk += 10
        elif market_cap < 10000:
            risk += 5
    else:
        risk += 25
    
    # 3. 업종 위험도
    high_risk_sectors = ['제약', '바이오', '반도체', '2차전지']
    medium_risk_sectors = ['IT', '통신', '화학']
    
    if pd.notna(sector):
        if any(keyword in str(sector) for keyword in high_risk_sectors):
            risk += 20
        elif any(keyword in str(sector) for keyword in medium_risk_sectors):
            risk += 10
        else:
            risk += 5
    else:
        risk += 15
    
    if risk >= 60:
        return "매우 높음"
    elif risk >= 45:
        return "높음"
    elif risk >= 30:
        return "중간"
    elif risk >= 15:
        return "낮음"
    else:
        return "매우 낮음"

# ==================== 4. 전체 종목 분석 ====================

def analyze_all_stocks():
    """2,700개 종목 스캔"""
    tickers = get_stock_list()
    results = []
    
    print(f"총 {len(tickers)}개 종목 분석 시작...")
    
    for i, ticker in enumerate(tickers):
        if (i + 1) % 100 == 0:
            print(f"진행 중: {i+1}/{len(tickers)}")
        
        try:
            # 기본 정보
            name = stock.get_market_ticker_name(ticker)
            
            # 가격/거래량 데이터
            df = get_stock_data(ticker)
            if df is None or df.empty:
                continue
            
            # 기본적 분석 데이터
            fundamental = get_fundamental_data(ticker)
            if fundamental is None:
                continue
            
            # 지표 계산
            current_price = df['종가'].iloc[-1]
            rsi = calculate_rsi(df['종가'])
            disparity = calculate_disparity(df['종가'])
            volume_ratio = calculate_volume_ratio(df['거래량'])
            
            pbr = fundamental['PBR'] if 'PBR' in fundamental.index else np.nan
            market_cap = fundamental['시가총액'] / 100000000 if '시가총액' in fundamental.index else np.nan
            per = fundamental['PER'] if 'PER' in fundamental.index else np.nan
            
            sector = '기타'
            
            # 종합점수 계산
            score = calculate_comprehensive_score(rsi, disparity, volume_ratio, pbr)
            
            # 위험도 계산
            risk = calculate_risk_level(pbr, market_cap, sector)
            
            # 30점 이상만 저장
            if score >= 30:
                results.append({
                    '종목코드': ticker,
                    '종목명': name,
                    '현재가': int(current_price),
                    'RSI': round(rsi, 1) if pd.notna(rsi) else '-',
                    '이격도': round(disparity, 1) if pd.notna(disparity) else '-',
                    '거래량비율': round(volume_ratio, 0) if pd.notna(volume_ratio) else '-',
                    'PBR': round(pbr, 2) if pd.notna(pbr) else '-',
                    'PER': round(per, 1) if pd.notna(per) else '-',
                    '시가총액': int(market_cap) if pd.notna(market_cap) else '-',
                    '종합점수': score,
                    '위험도': risk
                })
        
        except Exception as e:
            continue
    
    print(f"분석 완료! {len(results)}개 종목 선정")
    return pd.DataFrame(results)

# ==================== 5. 시장 데이터 수집 ====================

def get_market_indices():
    """코스피/코스닥 지수 (10영업일 확인)"""
    try:
        # 최근 10영업일 확인
        for i in range(10):
            target_date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
            try:
                kospi_df = stock.get_index_ohlcv(target_date, target_date, "1001")
                kosdaq_df = stock.get_index_ohlcv(target_date, target_date, "2001")
                
                if not kospi_df.empty and not kosdaq_df.empty:
                    kospi_value = kospi_df['종가'].iloc[0]
                    kospi_change = kospi_df['등락률'].iloc[0]
                    kosdaq_value = kosdaq_df['종가'].iloc[0]
                    kosdaq_change = kosdaq_df['등락률'].iloc[0]
                    
                    return {
                        'kospi': {'value': kospi_value, 'change': kospi_change, 'date': target_date},
                        'kosdaq': {'value': kosdaq_value, 'change': kosdaq_change, 'date': target_date}
                    }
            except:
                continue
        
        return {
            'kospi': {'value': 0, 'change': 0, 'date': 'N/A'},
            'kosdaq': {'value': 0, 'change': 0, 'date': 'N/A'}
        }
    except:
        return {
            'kospi': {'value': 0, 'change': 0, 'date': 'N/A'},
            'kosdaq': {'value': 0, 'change': 0, 'date': 'N/A'}
        }

def get_exchange_rates():
    """실시간 환율 (한국은행 API)"""
    try:
        # 최근 7일 내 환율 찾기
        for i in range(7):
            target_date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
            
            # USD/KRW
            try:
                url_usd = f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/1/036Y001/DD/{target_date}/{target_date}/0000001"
                response_usd = requests.get(url_usd, timeout=3)
                data_usd = response_usd.json()
                
                if 'StatisticSearch' in data_usd and 'row' in data_usd['StatisticSearch']:
                    usd_rate = float(data_usd['StatisticSearch']['row'][0]['DATA_VALUE'])
                    
                    # JPY/KRW (100엔 기준)
                    url_jpy = f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/1/036Y001/DD/{target_date}/{target_date}/0000002"
                    response_jpy = requests.get(url_jpy, timeout=3)
                    data_jpy = response_jpy.json()
                    jpy_rate = 0
                    if 'StatisticSearch' in data_jpy and 'row' in data_jpy['StatisticSearch']:
                        jpy_rate = float(data_jpy['StatisticSearch']['row'][0]['DATA_VALUE'])
                    
                    # EUR/KRW
                    url_eur = f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/1/036Y001/DD/{target_date}/{target_date}/0000003"
                    response_eur = requests.get(url_eur, timeout=3)
                    data_eur = response_eur.json()
                    eur_rate = 0
                    if 'StatisticSearch' in data_eur and 'row' in data_eur['StatisticSearch']:
                        eur_rate = float(data_eur['StatisticSearch']['row'][0]['DATA_VALUE'])
                    
                    return {
                        'usd': {'value': usd_rate, 'name': 'USD/KRW', 'date': target_date},
                        'jpy': {'value': jpy_rate, 'name': 'JPY(100)/KRW', 'date': target_date},
                        'eur': {'value': eur_rate, 'name': 'EUR/KRW', 'date': target_date},
                        'success': True
                    }
            except:
                continue
        
        # 데이터 없으면 실패
        return {
            'usd': {'value': 0, 'name': 'USD/KRW', 'date': 'N/A'},
            'jpy': {'value': 0, 'name': 'JPY(100)/KRW', 'date': 'N/A'},
            'eur': {'value': 0, 'name': 'EUR/KRW', 'date': 'N/A'},
            'success': False
        }
    except:
        return {
            'usd': {'value': 0, 'name': 'USD/KRW', 'date': 'N/A'},
            'jpy': {'value': 0, 'name': 'JPY(100)/KRW', 'date': 'N/A'},
            'eur': {'value': 0, 'name': 'EUR/KRW', 'date': 'N/A'},
            'success': False
        }

# ==================== 6. HTML 생성 ====================

def generate_html(results_df, output_file='output/index.html'):
    """HTML 페이지 생성"""
    
    os.makedirs('output', exist_ok=True)
    
    # 시장 지수
    indices = get_market_indices()
    
    # 환율 정보
    print("환율 정보 수집 중...")
    exchange = get_exchange_rates()
    
    # 현재 시간
    update_time = datetime.now().strftime('%Y년 %m월 %d일 %H:%M')
    
    # 지수 데이터 날짜
    index_date = indices['kospi']['date']
    if index_date != 'N/A':
        index_date_str = f"{index_date[:4]}-{index_date[4:6]}-{index_date[6:]}"
    else:
        index_date_str = 'N/A'
    
    # 종목 있는지 확인
    has_stocks = not results_df.empty
    
    if has_stocks:
        top_results = results_df.head(30)
        top8 = top_results.head(8)
    
    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 주식 종합 추천 시스템</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        .header {{
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        
        .header .update-time {{
            font-size: 1em;
            opacity: 0.9;
        }}
        
        .refresh-btn {{
            background: white;
            color: #667eea;
            border: none;
            padding: 12px 30px;
            font-size: 1em;
            border-radius: 25px;
            cursor: pointer;
            margin-top: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            transition: transform 0.2s;
        }}
        
        .refresh-btn:hover {{
            transform: scale(1.05);
        }}
        
        .section {{
            background: white;
            border-radius: 15px;
            padding: 30px;
            margin-bottom: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }}
        
        .section-title {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 20px;
            border-left: 5px solid #667eea;
            padding-left: 15px;
        }}
        
        .no-stocks-message {{
            background: #fff3cd;
            border: 2px solid #ffc107;
            border-radius: 10px;
            padding: 30px;
            text-align: center;
            margin: 20px 0;
        }}
        
        .no-stocks-message h3 {{
            color: #856404;
            font-size: 1.5em;
            margin-bottom: 15px;
        }}
        
        .no-stocks-message p {{
            color: #856404;
            font-size: 1.1em;
            line-height: 1.8;
            margin: 10px 0;
        }}
        
        .top8-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stock-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }}
        
        .stock-card:hover {{
            transform: translateY(-5px);
        }}
        
        .stock-card .name {{
            font-size: 1.3em;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        
        .stock-card .score {{
            font-size: 2em;
            font-weight: bold;
            margin: 10px 0;
        }}
        
        .stock-card .risk {{
            font-size: 0.9em;
            opacity: 0.9;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        
        th {{
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: center;
            font-weight: bold;
        }}
        
        td {{
            padding: 12px;
            text-align: center;
            border-bottom: 1px solid #eee;
        }}
        
        tr:hover {{
            background: #f8f9ff;
        }}
        
        .rank {{
            font-weight: bold;
            color: #667eea;
            font-size: 1.1em;
        }}
        
        .indices {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            margin-top: 20px;
        }}
        
        .index-box {{
            text-align: center;
            padding: 15px;
            background: #f8f9ff;
            border-radius: 10px;
        }}
        
        .index-name {{
            font-size: 1em;
            color: #666;
            margin-bottom: 8px;
        }}
        
        .index-value {{
            font-size: 1.5em;
            font-weight: bold;
            color: #333;
        }}
        
        .index-change {{
            font-size: 0.95em;
            margin-top: 5px;
        }}
        
        .positive {{
            color: #e74c3c;
        }}
        
        .negative {{
            color: #3498db;
        }}
        
        .news-item {{
            padding: 15px;
            border-left: 3px solid #667eea;
            margin-bottom: 15px;
            background: #f8f9ff;
        }}
        
        .news-title {{
            font-size: 1.1em;
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }}
        
        .news-summary {{
            color: #666;
            line-height: 1.6;
        }}
        
        .sector-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-top: 20px;
        }}
        
        .sector-card {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 25px;
            border-radius: 12px;
            text-align: center;
        }}
        
        .sector-name {{
            font-size: 1.3em;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        
        .sector-desc {{
            font-size: 0.95em;
            line-height: 1.5;
        }}
        
        .insight-box {{
            background: #fff9e6;
            border-left: 4px solid #ffc107;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 5px;
        }}
        
        .insight-title {{
            font-weight: bold;
            color: #f57c00;
            margin-bottom: 8px;
        }}
        
        .insight-text {{
            color: #666;
            line-height: 1.6;
        }}
        
        .debug-info {{
            font-size: 0.8em;
            color: #999;
            text-align: center;
            margin-top: 10px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 한국 주식 종합 추천 시스템</h1>
            <div class="update-time">마지막 업데이트: {update_time}</div>
            <button class="refresh-btn" onclick="location.reload()">🔄 새로고침</button>
        </div>
        
        <div class="section">
            <div class="section-title">🏆 오늘의 TOP 추천</div>
"""
    
    if has_stocks:
        html += """
            <div class="top8-grid">
"""
        for idx, row in top8.iterrows():
            html += f"""
                <div class="stock-card">
                    <div class="name">{row['종목명']}</div>
                    <div class="score">{row['종합점수']}점</div>
                    <div class="risk">위험도: {row['위험도']}</div>
                </div>
"""
        
        html += """
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>순위</th>
                        <th>종목명</th>
                        <th>현재가</th>
                        <th>RSI</th>
                        <th>이격도</th>
                        <th>거래량비율</th>
                        <th>PBR</th>
                        <th>종합점수</th>
                        <th>위험도</th>
                    </tr>
                </thead>
                <tbody>
"""
        
        for idx, row in top_results.iterrows():
            html += f"""
                    <tr>
                        <td class="rank">{idx + 1}</td>
                        <td><strong>{row['종목명']}</strong></td>
                        <td>{row['현재가']:,}원</td>
                        <td>{row['RSI']}</td>
                        <td>{row['이격도']}%</td>
                        <td>{row['거래량비율']}%</td>
                        <td>{row['PBR']}</td>
                        <td><strong>{row['종합점수']}점</strong></td>
                        <td>{row['위험도']}</td>
                    </tr>
"""
        
        html += """
                </tbody>
            </table>
"""
    else:
        html += """
            <div class="no-stocks-message">
                <h3>⚠️ 현재 투자 조건에 맞는 종목이 없습니다</h3>
                <p><strong>현재 상황:</strong> 저평가 매수 기회가 부족합니다.</p>
                <p><strong>원인:</strong> 과매도(RSI<30), 저평가(이격도<90%), 거래량 급증(>150%)을 동시에 만족하는 종목이 없습니다.</p>
                <p><strong>해석:</strong> 시장이 안정적이거나 관망세입니다.</p>
                <p style="margin-top: 20px;"><strong>권장 사항:</strong> 내일 다시 확인하거나, 시장 변동성이 커질 때를 기다리세요.</p>
                <p style="margin-top: 10px; font-size: 0.95em;">💡 <strong>Tip:</strong> 조정장이 오면 저평가 종목이 많아집니다!</p>
            </div>
"""
    
    kospi_class = 'positive' if indices['kospi']['change'] >= 0 else 'negative'
    kosdaq_class = 'positive' if indices['kosdaq']['change'] >= 0 else 'negative'
    
    stock_count = len(results_df) if has_stocks else 0
    
    html += f"""
        </div>
        
        <div class="section">
            <div class="section-title">📰 시장 브리핑</div>
            
            <div class="news-item">
                <div class="news-title">🔥 오늘의 핵심 뉴스</div>
                <div class="news-summary">
"""
    
    if has_stocks:
        html += f"""
                    • 저평가 반등 종목 {stock_count}개 선정 완료<br>
                    • 종합점수 30점 이상 투자 기회 발굴<br>
                    • 현실적 기준으로 실전 투자 가능 종목 선별
"""
    else:
        html += """
                    • 현재 저평가 매수 기회 부족, 시장 안정 국면<br>
                    • 조정장 진입 시 투자 기회 포착 예정<br>
                    • 시장 지수와 업종 분석 지속 모니터링 중
"""
    
    html += f"""
                </div>
            </div>
            
            <div class="indices">
                <div class="index-box">
                    <div class="index-name">코스피</div>
                    <div class="index-value">{indices['kospi']['value']:,.2f}</div>
                    <div class="index-change {kospi_class}">{indices['kospi']['change']:+.2f}%</div>
                </div>
                <div class="index-box">
                    <div class="index-name">코스닥</div>
                    <div class="index-value">{indices['kosdaq']['value']:,.2f}</div>
                    <div class="index-change {kosdaq_class}">{indices['kosdaq']['change']:+.2f}%</div>
                </div>
"""
    
    # 환율 정보 표시
    if exchange['success']:
        html += f"""
                <div class="index-box">
                    <div class="index-name">USD/KRW</div>
                    <div class="index-value">{exchange['usd']['value']:,.2f}</div>
                </div>
                <div class="index-box">
                    <div class="index-name">JPY(100)/KRW</div>
                    <div class="index-value">{exchange['jpy']['value']:,.2f}</div>
                </div>
                <div class="index-box">
                    <div class="index-name">EUR/KRW</div>
                    <div class="index-value">{exchange['eur']['value']:,.2f}</div>
                </div>
"""
    else:
        html += """
                <div class="index-box" style="grid-column: span 3;">
                    <div class="index-name">💱 환율 정보</div>
                    <div style="font-size: 0.9em; color: #999; margin-top: 10px;">
                        환율 데이터 수집 실패<br>
                        (한국은행 API 일시 오류)
                    </div>
                </div>
"""
    
    html += f"""
            </div>
            <div class="debug-info">지수 데이터 기준일: {index_date_str}</div>
        </div>
        
        <div class="section">
            <div class="section-title">🏢 업종별 투자 기회</div>
            <div class="sector-grid">
                <div class="sector-card">
                    <div class="sector-name">IT/반도체</div>
                    <div class="sector-desc">
                        고위험 고수익 섹터<br>
                        급등 가능성 높음<br>
                        단기 투자 적합
                    </div>
                </div>
                <div class="sector-card">
                    <div class="sector-name">제조/화학</div>
                    <div class="sector-desc">
                        중위험 중수익 섹터<br>
                        안정적 성장 기대<br>
                        중장기 투자 적합
                    </div>
                </div>
                <div class="sector-card">
                    <div class="sector-name">유통/서비스</div>
                    <div class="sector-desc">
                        저위험 저수익 섹터<br>
                        현금흐름 안정<br>
                        장기 투자 적합
                    </div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">💡 다차원 인사이트</div>
            
            <div class="insight-box">
                <div class="insight-title">📊 종합점수 활용법</div>
                <div class="insight-text">
                    종합점수는 <strong>매수 타이밍</strong>을 나타냅니다. 
                    RSI(과매도), 이격도(저평가), 거래량(매집), PBR(가치)을 종합 평가하여 
                    <strong>지금 사면 유리한 종목</strong>을 순위로 보여줍니다.
                    <br><br>
                    <strong>※ 30점 기준 적용:</strong> 현재 시장 상황을 반영한 현실적 기준으로, 
                    실전 투자가 가능한 종목들을 선별합니다.
                </div>
            </div>
            
            <div class="insight-box">
                <div class="insight-title">⚠️ 위험도 이해하기</div>
                <div class="insight-text">
                    위험도는 <strong>기업 안정성</strong>을 참고용으로 제공합니다. 
                    PBR(극단적 저평가), 시가총액(기업 규모), 업종(변동성)을 기반으로 계산하며, 
                    <strong>순위와는 무관</strong>합니다. 보유 기간 결정 시 참고하세요.
                </div>
            </div>
            
            <div class="insight-box">
                <div class="insight-title">🎯 추천 투자 전략</div>
                <div class="insight-text">
"""
    
    if has_stocks:
        html += """
                    1. <strong>적극적 투자</strong>: 종합점수 40점 이상 + 위험도 높음 → 빠른 반등 노리기<br>
                    2. <strong>균형 투자</strong>: 종합점수 35점 이상 + 위험도 중간 → 안정적 상승<br>
                    3. <strong>안정 투자</strong>: 종합점수 30점 이상 + 위험도 낮음 → 가치 투자
"""
    else:
        html += """
                    1. <strong>대기 전략</strong>: 무리한 투자보다는 좋은 기회를 기다립니다.<br>
                    2. <strong>학습 시간</strong>: 투자 공부와 종목 연구에 시간을 투자하세요.<br>
                    3. <strong>관심 종목 리스트</strong>: 조정이 오면 매수할 종목을 미리 선정하세요.<br>
                    <br>
                    <strong>※ 조정장 대비:</strong> 현금 보유율을 높이고, 분할 매수 전략을 준비하세요.
"""
    
    html += """
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ HTML 생성 완료: {output_file}")
    print(f"📊 지수 데이터 기준일: {index_date_str}")
    if exchange['success']:
        print(f"💱 환율 데이터 기준일: {exchange['usd']['date'][:4]}-{exchange['usd']['date'][4:6]}-{exchange['usd']['date'][6:]}")
    else:
        print(f"💱 환율 데이터: 수집 실패")

# ==================== 7. 메인 실행 ====================

def main():
    """메인 실행 함수"""
    print("=" * 50)
    print("한국 주식 종합 추천 시스템 시작")
    print("=" * 50)
    
    # 전체 종목 분석
    results_df = analyze_all_stocks()
    
    # 종목 0개여도 HTML 생성
    if results_df.empty:
        print("⚠️ 조건에 맞는 종목이 없습니다.")
        print("📄 기본 페이지를 생성합니다...")
    else:
        # 종합점수 기준 정렬
        results_df = results_df.sort_values('종합점수', ascending=False).reset_index(drop=True)
    
    # 항상 HTML 생성
    generate_html(results_df, output_file='output/index.html')
    
    print("=" * 50)
    print("✅ 모든 작업 완료!")
    print("=" * 50)

if __name__ == "__main__":
    main()
