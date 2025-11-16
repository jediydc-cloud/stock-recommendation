#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 저평가 종목 추천 시스템 (완전판)
- 2,700개 전체 종목 스캔
- 종합 Top 30 + 카테고리별 Top 5
- 한국은행 환율 정보 연동
- 코스피/코스닥 지수 안정적 수집 (20영업일 확인)
- 위험도 및 모든 기존 정보 유지
"""

import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime, timedelta
import pytz
import warnings
import os
import requests
import json

warnings.filterwarnings('ignore')

# ===========================================
# 1. 한국시간 기반 날짜 계산 함수
# ===========================================
def get_korean_date():
    """한국시간 기준 현재 날짜 반환"""
    korea_tz = pytz.timezone('Asia/Seoul')
    return datetime.now(korea_tz)

def get_business_days_ago(days_ago):
    """한국시간 기준 N영업일 전 날짜 계산"""
    korea_tz = pytz.timezone('Asia/Seoul')
    current = datetime.now(korea_tz)
    
    count = 0
    while count < days_ago:
        current -= timedelta(days=1)
        # 주말 제외 (월요일=0, 일요일=6)
        if current.weekday() < 5:  
            count += 1
    
    return current.strftime("%Y%m%d")

# ===========================================
# 2. 시장 지수 수집 (개선 버전)
# ===========================================
def get_market_indices():
    """
    코스피/코스닥 지수 안정적 수집
    - 한국시간 기준 날짜 계산
    - 20영업일까지 확장 확인
    - 데이터 없을 시 참고값 사용
    """
    print("\n" + "="*60)
    print("📊 시장 지수 수집 시작")
    print("="*60)
    
    korea_tz = pytz.timezone('Asia/Seoul')
    current_time = datetime.now(korea_tz)
    print(f"🕐 한국시간: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    indices = {
        'kospi': {'value': 0, 'change': 0, 'is_reference': False},
        'kosdaq': {'value': 0, 'change': 0, 'is_reference': False}
    }
    
    # 20영업일까지 확장 확인
    for days_back in range(1, 21):
        try:
            target_date = get_business_days_ago(days_back)
            prev_date = get_business_days_ago(days_back + 1)
            
            print(f"\n🔍 시도 {days_back}/20: {target_date} 데이터 확인 중...")
            
            # 코스피 수집
            kospi_df = stock.get_index_ohlcv(target_date, target_date, "1001")
            if not kospi_df.empty and len(kospi_df) > 0:
                indices['kospi']['value'] = float(kospi_df['종가'].iloc[-1])
                
                # 전일 대비 변동률 계산
                prev_kospi_df = stock.get_index_ohlcv(prev_date, prev_date, "1001")
                if not prev_kospi_df.empty:
                    prev_close = float(prev_kospi_df['종가'].iloc[-1])
                    curr_close = indices['kospi']['value']
                    indices['kospi']['change'] = ((curr_close - prev_close) / prev_close) * 100
                
                print(f"✅ 코스피: {indices['kospi']['value']:,.2f} ({indices['kospi']['change']:+.2f}%)")
            
            # 코스닥 수집
            kosdaq_df = stock.get_index_ohlcv(target_date, target_date, "2001")
            if not kosdaq_df.empty and len(kosdaq_df) > 0:
                indices['kosdaq']['value'] = float(kosdaq_df['종가'].iloc[-1])
                
                # 전일 대비 변동률 계산
                prev_kosdaq_df = stock.get_index_ohlcv(prev_date, prev_date, "2001")
                if not prev_kosdaq_df.empty:
                    prev_close = float(prev_kosdaq_df['종가'].iloc[-1])
                    curr_close = indices['kosdaq']['value']
                    indices['kosdaq']['change'] = ((curr_close - prev_close) / prev_close) * 100
                
                print(f"✅ 코스닥: {indices['kosdaq']['value']:,.2f} ({indices['kosdaq']['change']:+.2f}%)")
            
            # 둘 다 수집 성공 시 종료
            if indices['kospi']['value'] > 0 and indices['kosdaq']['value'] > 0:
                print(f"\n✨ 지수 데이터 수집 성공! (기준일: {target_date})")
                return indices, target_date
        
        except Exception as e:
            print(f"⚠️ {target_date} 데이터 수집 실패: {str(e)}")
            continue
    
    # 20영업일 동안 데이터 없을 시 참고값 사용
    print("\n" + "="*60)
    print("⚠️ 경고: 20영업일 동안 지수 데이터를 찾을 수 없음")
    print("📌 참고값으로 대체합니다 (실제 시장 상황과 다를 수 있음)")
    print("="*60)
    
    indices['kospi'] = {'value': 2500.0, 'change': 0.0, 'is_reference': True}
    indices['kosdaq'] = {'value': 800.0, 'change': 0.0, 'is_reference': True}
    
    return indices, None

# ===========================================
# 3. 환율 정보 수집 (한국은행 API)
# ===========================================
def get_exchange_rates():
    """
    한국은행 Open API를 통한 환율 정보 수집
    - USD/KRW, JPY(100)/KRW, EUR/KRW
    - 최대 10영업일 전까지 확인
    """
    print("\n" + "="*60)
    print("💱 환율 정보 수집 시작")
    print("="*60)
    
    API_KEY = "GVEYC4C6R9ZM5JFAQ2FY"
    rates = {'USD': None, 'JPY': None, 'EUR': None, 'date': None}
    
    # 환율 코드 (한국은행 API)
    currency_codes = {
        'USD': '0000001',  # 미국 달러
        'JPY': '0000002',  # 일본 엔 (100엔 기준)
        'EUR': '0000003'   # 유럽 유로
    }
    
    # 최대 10영업일 전까지 확인
    for days_back in range(1, 11):
        try:
            target_date = get_business_days_ago(days_back)
            print(f"\n🔍 환율 데이터 확인 중: {target_date}")
            
            success_count = 0
            
            for currency, code in currency_codes.items():
                url = f"https://ecos.bok.or.kr/api/StatisticSearch/{API_KEY}/json/kr/1/1/036Y001/DD/{target_date}/{target_date}/{code}"
                
                try:
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    
                    if 'StatisticSearch' in data and 'row' in data['StatisticSearch']:
                        rate = float(data['StatisticSearch']['row'][0]['DATA_VALUE'])
                        rates[currency] = rate
                        rates['date'] = target_date
                        success_count += 1
                        print(f"✅ {currency}: {rate:,.2f}원")
                
                except Exception as e:
                    print(f"⚠️ {currency} 수집 실패: {str(e)}")
                    continue
            
            # 3개 모두 수집 성공 시 종료
            if success_count == 3:
                print(f"\n✨ 환율 데이터 수집 성공! (기준일: {target_date})")
                return rates
        
        except Exception as e:
            print(f"⚠️ {target_date} 환율 수집 실패: {str(e)}")
            continue
    
    print("\n⚠️ 환율 정보를 가져올 수 없습니다")
    return rates

# ===========================================
# 4. 종목별 기술적 지표 계산
# ===========================================
def calculate_technical_indicators(ticker, ticker_name):
    """
    개별 종목의 기술적 지표 계산
    - RSI, 이격도, 거래량, PBR
    - 종합점수 계산
    - 위험도 평가
    """
    try:
        # 60일 데이터 수집
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
        
        if df.empty or len(df) < 20:
            return None
        
        # 현재가
        current_price = df['종가'].iloc[-1]
        
        # ===== RSI (14일) =====
        delta = df['종가'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # ===== 이격도 (20일 이동평균 대비) =====
        ma20 = df['종가'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100
        
        # ===== 거래량 비율 (20일 평균 대비) =====
        avg_volume = df['거래량'].rolling(window=20).mean().iloc[-1]
        current_volume = df['거래량'].iloc[-1]
        volume_ratio = (current_volume / avg_volume) * 100
        
        # ===== PBR (저평가 지표) =====
        fundamental = stock.get_market_fundamental(end_date, end_date, ticker)
        if fundamental.empty:
            return None
        pbr = fundamental['PBR'].iloc[0]
        
        # ===== 종합점수 계산 =====
        score = 0
        
        # RSI 점수 (30점 만점) - 과매도 구간 선호
        if current_rsi <= 30:
            score += 30
        elif current_rsi <= 40:
            score += 20
        elif current_rsi <= 50:
            score += 10
        
        # 이격도 점수 (25점 만점) - 저평가 구간 선호
        if disparity <= 95:
            score += 25
        elif disparity <= 98:
            score += 15
        elif disparity <= 100:
            score += 5
        
        # 거래량 점수 (25점 만점) - 거래량 증가 선호
        if volume_ratio >= 150:
            score += 25
        elif volume_ratio >= 120:
            score += 15
        elif volume_ratio >= 100:
            score += 5
        
        # PBR 점수 (20점 만점) - 저PBR 선호
        if 0 < pbr <= 0.8:
            score += 20
        elif pbr <= 1.0:
            score += 15
        elif pbr <= 1.5:
            score += 10
        
        # ===== 위험도 계산 (참고용) =====
        risk_factors = []
        
        # PBR 기반 위험도
        if pbr < 0.5:
            risk_factors.append("극저PBR")
        
        # 시가총액 확인
        market_cap = stock.get_market_cap(end_date, end_date, ticker)
        if not market_cap.empty:
            cap_value = market_cap['시가총액'].iloc[0] / 100000000  # 억원 단위
            if cap_value < 1000:
                risk_factors.append("소형주")
        
        risk_level = "낮음"
        if len(risk_factors) >= 2:
            risk_level = "높음"
        elif len(risk_factors) == 1:
            risk_level = "중간"
        
        return {
            '종목코드': ticker,
            '종목명': ticker_name,
            '현재가': int(current_price),
            'RSI': round(current_rsi, 2),
            '이격도': round(disparity, 2),
            '거래량비율': round(volume_ratio, 2),
            'PBR': round(pbr, 2),
            '종합점수': score,
            '위험도': risk_level,
            '위험요인': ', '.join(risk_factors) if risk_factors else '-'
        }
    
    except Exception as e:
        return None

# ===========================================
# 5. 전체 시장 스캔
# ===========================================
def scan_all_stocks():
    """
    코스피 + 코스닥 전체 종목 스캔
    - 약 2,700개 종목
    - 모든 종목 점수 계산 (필터링 없음)
    """
    print("\n" + "="*60)
    print("🔍 전체 시장 스캔 시작")
    print("="*60)
    
    # 코스피 + 코스닥 티커 수집
    kospi_tickers = stock.get_market_ticker_list(market="KOSPI")
    kosdaq_tickers = stock.get_market_ticker_list(market="KOSDAQ")
    all_tickers = kospi_tickers + kosdaq_tickers
    
    total_count = len(all_tickers)
    print(f"📊 총 {total_count}개 종목 스캔 예정")
    print(f"⏱️ 예상 소요시간: 약 {total_count // 60}분")
    
    results = []
    processed = 0
    
    for ticker in all_tickers:
        processed += 1
        
        # 진행률 표시 (매 100개)
        if processed % 100 == 0:
            print(f"⏳ 진행률: {processed}/{total_count} ({processed/total_count*100:.1f}%)")
        
        try:
            ticker_name = stock.get_market_ticker_name(ticker)
            result = calculate_technical_indicators(ticker, ticker_name)
            
            # 모든 종목 수집 (필터링 없음)
            if result:
                results.append(result)
        
        except Exception as e:
            continue
    
    print(f"\n✅ 스캔 완료: {len(results)}개 종목 데이터 수집됨")
    
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values('종합점수', ascending=False).reset_index(drop=True)
        return df
    else:
        return pd.DataFrame()

# ===========================================
# 6. 추천 종목 선별
# ===========================================
def select_recommendations(df):
    """
    Top 30 + 카테고리별 Top 5 선별
    """
    recommendations = {}
    
    if len(df) == 0:
        return recommendations
    
    # ===== 종합 Top 30 =====
    top_30 = df.head(30).copy()
    top_30.index = range(1, len(top_30) + 1)
    recommendations['top_30'] = top_30
    
    # 평균 점수 계산
    avg_score = top_30['종합점수'].mean()
    if avg_score >= 60:
        market_status = "🟢 강한 저평가 신호"
    elif avg_score >= 40:
        market_status = "🟡 적정 매수 기회"
    else:
        market_status = "🔴 신중한 접근 필요"
    
    recommendations['market_status'] = f"{market_status} (평균: {avg_score:.1f}점)"
    recommendations['avg_score'] = avg_score
    
    # ===== 카테고리별 Top 5 =====
    # 🔴 과매도 (RSI 낮은 순)
    recommendations['rsi_top5'] = df.nsmallest(5, 'RSI')[['종목명', '현재가', 'RSI', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['rsi_top5'].index = range(1, len(recommendations['rsi_top5']) + 1)
    
    # 💰 저평가 (이격도 낮은 순)
    recommendations['disparity_top5'] = df.nsmallest(5, '이격도')[['종목명', '현재가', '이격도', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['disparity_top5'].index = range(1, len(recommendations['disparity_top5']) + 1)
    
    # 📈 거래량 급증 (거래량비율 높은 순)
    recommendations['volume_top5'] = df.nlargest(5, '거래량비율')[['종목명', '현재가', '거래량비율', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['volume_top5'].index = range(1, len(recommendations['volume_top5']) + 1)
    
    print("\n" + "="*60)
    print("📊 추천 종목 선별 완료")
    print("="*60)
    print(f"✅ 종합 Top 30: {len(top_30)}개")
    print(f"✅ 과매도 Top 5: {len(recommendations['rsi_top5'])}개")
    print(f"✅ 저평가 Top 5: {len(recommendations['disparity_top5'])}개")
    print(f"✅ 거래량 Top 5: {len(recommendations['volume_top5'])}개")
    print(f"📈 시장 상황: {recommendations['market_status']}")
    
    return recommendations

# ===========================================
# 7. HTML 리포트 생성
# ===========================================
def generate_html_report(recommendations, indices, index_date, exchange_rates):
    """
    GitHub Pages용 HTML 리포트 생성
    - 6개 섹션 구조
    - 반응형 디자인
    - 모든 기존 정보 유지
    """
    korea_tz = pytz.timezone('Asia/Seoul')
    current_time = datetime.now(korea_tz)
    update_time = current_time.strftime("%Y년 %m월 %d일 %H:%M")
    
    # 참고값 여부 확인
    kospi_ref_mark = " *" if indices['kospi']['is_reference'] else ""
    kosdaq_ref_mark = " *" if indices['kosdaq']['is_reference'] else ""
    
    # 지수 기준일 표시
    index_date_display = index_date if index_date else "참고값"
    
    # 환율 날짜 표시
    exchange_date_display = exchange_rates.get('date', 'N/A')
    if exchange_date_display != 'N/A':
        exchange_date_display = f"{exchange_date_display[:4]}.{exchange_date_display[4:6]}.{exchange_date_display[6:]}"
    
    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 주식 저평가 종목 추천</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        
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
        }}
        
        .header .update-time {{
            font-size: 1em;
            opacity: 0.9;
            margin-top: 10px;
        }}
        
        .refresh-btn {{
            display: inline-block;
            margin-top: 15px;
            padding: 12px 30px;
            background: white;
            color: #667eea;
            border: none;
            border-radius: 25px;
            font-size: 1em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            text-decoration: none;
        }}
        
        .refresh-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }}
        
        .grid-container {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            padding: 30px;
        }}
        
        .section {{
            background: #f8f9fa;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }}
        
        .section-full {{
            grid-column: span 2;
        }}
        
        .section h2 {{
            color: #667eea;
            margin-bottom: 20px;
            font-size: 1.5em;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }}
        
        .market-indices {{
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }}
        
        .index-card {{
            flex: 1;
            min-width: 200px;
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        .index-card h3 {{
            color: #666;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}
        
        .index-value {{
            font-size: 2em;
            font-weight: 700;
            color: #333;
            margin-bottom: 5px;
        }}
        
        .index-change {{
            font-size: 1.1em;
            font-weight: 600;
        }}
        
        .positive {{ color: #e74c3c; }}
        .negative {{ color: #3498db; }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
        }}
        
        .info-card {{
            background: white;
            padding: 15px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        .info-card h3 {{
            color: #666;
            font-size: 0.9em;
            margin-bottom: 8px;
        }}
        
        .info-card .value {{
            font-size: 1.5em;
            font-weight: 700;
            color: #667eea;
        }}
        
        .strategy-content {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            line-height: 1.8;
            color: #444;
        }}
        
        .strategy-content ul {{
            margin-left: 20px;
            margin-top: 10px;
        }}
        
        .strategy-content li {{
            margin-bottom: 8px;
        }}
        
        .summary-stats {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }}
        
        .summary-stats .big-number {{
            font-size: 3em;
            font-weight: 700;
            color: #667eea;
            margin: 10px 0;
        }}
        
        .summary-stats .status {{
            font-size: 1.3em;
            font-weight: 600;
            margin-top: 15px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
        }}
        
        thead {{
            background: #667eea;
            color: white;
        }}
        
        th {{
            padding: 15px 10px;
            text-align: center;
            font-weight: 600;
            font-size: 0.9em;
        }}
        
        td {{
            padding: 12px 10px;
            text-align: center;
            border-bottom: 1px solid #eee;
            font-size: 0.9em;
        }}
        
        tbody tr:hover {{
            background: #f8f9ff;
        }}
        
        .stock-name {{
            font-weight: 600;
            color: #667eea;
        }}
        
        .score-high {{
            background: #e8f5e9;
            color: #2e7d32;
            font-weight: 700;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .score-medium {{
            background: #fff3e0;
            color: #ef6c00;
            font-weight: 700;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .score-low {{
            background: #ffebee;
            color: #c62828;
            font-weight: 700;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .risk-low {{ color: #2e7d32; font-weight: 600; }}
        .risk-medium {{ color: #ef6c00; font-weight: 600; }}
        .risk-high {{ color: #c62828; font-weight: 600; }}
        
        .category-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }}
        
        .category-section {{
            background: white;
            padding: 20px;
            border-radius: 10px;
        }}
        
        .category-section h3 {{
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.2em;
            text-align: center;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }}
        
        .reference-note {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin-top: 15px;
            border-radius: 5px;
            color: #856404;
            font-size: 0.95em;
            line-height: 1.6;
        }}
        
        .no-data {{
            text-align: center;
            padding: 40px;
            color: #999;
            font-size: 1.1em;
        }}
        
        @media (max-width: 1024px) {{
            .category-grid {{
                grid-template-columns: 1fr;
            }}
        }}
        
        @media (max-width: 768px) {{
            .grid-container {{
                grid-template-columns: 1fr;
            }}
            
            .section-full {{
                grid-column: span 1;
            }}
            
            .header h1 {{
                font-size: 1.8em;
            }}
            
            .info-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 한국 주식 저평가 종목 추천</h1>
            <div class="update-time">최종 업데이트: {update_time}</div>
            <a href="javascript:location.reload()" class="refresh-btn">🔄 새로고침</a>
        </div>
        
        <div class="grid-container">
            <!-- 1. 시장 현황 -->
            <div class="section">
                <h2>📊 시장 현황</h2>
                <div class="market-indices">
                    <div class="index-card">
                        <h3>코스피{kospi_ref_mark}</h3>
                        <div class="index-value">{indices['kospi']['value']:,.2f}</div>
                        <div class="index-change {'positive' if indices['kospi']['change'] > 0 else 'negative' if indices['kospi']['change'] < 0 else ''}">
                            {indices['kospi']['change']:+.2f}%
                        </div>
                    </div>
                    <div class="index-card">
                        <h3>코스닥{kosdaq_ref_mark}</h3>
                        <div class="index-value">{indices['kosdaq']['value']:,.2f}</div>
                        <div class="index-change {'positive' if indices['kosdaq']['change'] > 0 else 'negative' if indices['kosdaq']['change'] < 0 else ''}">
                            {indices['kosdaq']['change']:+.2f}%
                        </div>
                    </div>
                </div>
                <div style="margin-top: 10px; font-size: 0.85em; color: #666; text-align: center;">
                    지수 기준일: {index_date_display}
                </div>
"""
    
    # 참고값 사용 시 경고 메시지
    if indices['kospi']['is_reference'] or indices['kosdaq']['is_reference']:
        html += """
                <div class="reference-note">
                    <strong>* 참고값 안내</strong><br>
                    실시간 지수 데이터를 가져올 수 없어 참고값으로 표시됩니다.<br>
                    장 마감 후 또는 영업일에 다시 확인해주세요.
                </div>
"""
    
    html += f"""
            </div>
            
            <!-- 2. 주요 지표 -->
            <div class="section">
                <h2>💱 주요 지표</h2>
                <div class="info-grid">
                    <div class="info-card">
                        <h3>미국 달러 (USD)</h3>
                        <div class="value">{exchange_rates.get('USD', 'N/A') if exchange_rates.get('USD') else 'N/A'}원</div>
                    </div>
                    <div class="info-card">
                        <h3>일본 엔 (100JPY)</h3>
                        <div class="value">{exchange_rates.get('JPY', 'N/A') if exchange_rates.get('JPY') else 'N/A'}원</div>
                    </div>
                    <div class="info-card">
                        <h3>유럽 유로 (EUR)</h3>
                        <div class="value">{exchange_rates.get('EUR', 'N/A') if exchange_rates.get('EUR') else 'N/A'}원</div>
                    </div>
                    <div class="info-card">
                        <h3>환율 기준일</h3>
                        <div class="value" style="font-size: 1.2em;">{exchange_date_display}</div>
                    </div>
                </div>
            </div>
            
            <!-- 3. 투자 전략 -->
            <div class="section">
                <h2>💡 투자 전략</h2>
                <div class="strategy-content">
                    <strong style="color: #667eea; font-size: 1.1em;">저평가 반등주 선별 기준</strong>
                    <ul>
                        <li><strong>RSI</strong>: 30 이하 과매도 구간 (30점 만점)</li>
                        <li><strong>이격도</strong>: 95% 이하 저평가 구간 (25점 만점)</li>
                        <li><strong>거래량</strong>: 평균 대비 150% 이상 (25점 만점)</li>
                        <li><strong>PBR</strong>: 0.8 이하 저평가 (20점 만점)</li>
                    </ul>
                    <p style="margin-top: 15px; color: #e74c3c; font-weight: 600;">
                        ⚠️ 본 정보는 투자 참고용이며, 최종 투자 판단은 본인의 책임입니다.
                    </p>
                </div>
            </div>
            
            <!-- 4. 시장 요약 -->
            <div class="section">
                <h2>📈 시장 요약</h2>
                <div class="summary-stats">
"""
    
    if recommendations:
        html += f"""
                    <div style="color: #666; font-size: 1em; margin-bottom: 10px;">Top 30 평균 점수</div>
                    <div class="big-number">{recommendations['avg_score']:.1f}점</div>
                    <div class="status">{recommendations['market_status']}</div>
                    <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee;">
                        <div style="color: #666; font-size: 0.9em;">추천 종목 현황</div>
                        <div style="font-size: 1.5em; font-weight: 600; color: #667eea; margin-top: 10px;">
                            종합 30개 + 카테고리별 15개
                        </div>
                    </div>
"""
    else:
        html += """
                    <div class="no-data">데이터를 불러오는 중입니다...</div>
"""
    
    html += """
                </div>
            </div>
            
            <!-- 5. 종합 추천 Top 30 -->
            <div class="section section-full">
                <h2>⭐ 종합 추천 Top 30</h2>
"""
    
    if recommendations and 'top_30' in recommendations:
        top_30 = recommendations['top_30']
        html += """
                <table>
                    <thead>
                        <tr>
                            <th style="width: 50px;">순위</th>
                            <th style="width: 120px;">종목명</th>
                            <th style="width: 100px;">현재가</th>
                            <th style="width: 70px;">RSI</th>
                            <th style="width: 80px;">이격도</th>
                            <th style="width: 100px;">거래량비율</th>
                            <th style="width: 70px;">PBR</th>
                            <th style="width: 80px;">종합점수</th>
                            <th style="width: 80px;">위험도</th>
                            <th style="width: 150px;">위험요인</th>
                        </tr>
                    </thead>
                    <tbody>
"""
        
        for idx, row in top_30.iterrows():
            # 점수에 따른 스타일
            if row['종합점수'] >= 60:
                score_class = 'score-high'
            elif row['종합점수'] >= 40:
                score_class = 'score-medium'
            else:
                score_class = 'score-low'
            
            # 위험도 스타일
            if row['위험도'] == '낮음':
                risk_class = 'risk-low'
            elif row['위험도'] == '중간':
                risk_class = 'risk-medium'
            else:
                risk_class = 'risk-high'
            
            html += f"""
                        <tr>
                            <td><strong>{idx}</strong></td>
                            <td class="stock-name">{row['종목명']}</td>
                            <td>{row['현재가']:,}원</td>
                            <td>{row['RSI']}</td>
                            <td>{row['이격도']}%</td>
                            <td>{row['거래량비율']}%</td>
                            <td>{row['PBR']}</td>
                            <td><span class="{score_class}">{row['종합점수']}점</span></td>
                            <td class="{risk_class}">{row['위험도']}</td>
                            <td style="font-size: 0.85em; color: #666;">{row['위험요인']}</td>
                        </tr>
"""
        
        html += """
                    </tbody>
                </table>
"""
    else:
        html += """
                <div class="no-data">추천 종목 데이터를 불러오는 중입니다...</div>
"""
    
    html += """
            </div>
            
            <!-- 6. 카테고리별 인사이트 -->
            <div class="section section-full">
                <h2>🎯 카테고리별 인사이트</h2>
                <div class="category-grid">
"""
    
    # 🔴 과매도 Top 5
    html += """
                    <div class="category-section">
                        <h3>🔴 과매도 Top 5</h3>
                        <div style="font-size: 0.85em; color: #666; text-align: center; margin-bottom: 15px;">RSI 낮은 순</div>
"""
    
    if recommendations and 'rsi_top5' in recommendations:
        rsi_top5 = recommendations['rsi_top5']
        html += """
                        <table>
                            <thead style="background: #ef5350;">
                                <tr>
                                    <th>순위</th>
                                    <th>종목명</th>
                                    <th>RSI</th>
                                    <th>점수</th>
                                </tr>
                            </thead>
                            <tbody>
"""
        for idx, row in rsi_top5.iterrows():
            html += f"""
                                <tr>
                                    <td><strong>{idx}</strong></td>
                                    <td class="stock-name">{row['종목명']}</td>
                                    <td>{row['RSI']}</td>
                                    <td>{row['종합점수']}점</td>
                                </tr>
"""
        html += """
                            </tbody>
                        </table>
"""
    else:
        html += """
                        <div class="no-data">데이터 없음</div>
"""
    
    html += """
                    </div>
"""
    
    # 💰 저평가 Top 5
    html += """
                    <div class="category-section">
                        <h3>💰 저평가 Top 5</h3>
                        <div style="font-size: 0.85em; color: #666; text-align: center; margin-bottom: 15px;">이격도 낮은 순</div>
"""
    
    if recommendations and 'disparity_top5' in recommendations:
        disparity_top5 = recommendations['disparity_top5']
        html += """
                        <table>
                            <thead style="background: #66bb6a;">
                                <tr>
                                    <th>순위</th>
                                    <th>종목명</th>
                                    <th>이격도</th>
                                    <th>점수</th>
                                </tr>
                            </thead>
                            <tbody>
"""
        for idx, row in disparity_top5.iterrows():
            html += f"""
                                <tr>
                                    <td><strong>{idx}</strong></td>
                                    <td class="stock-name">{row['종목명']}</td>
                                    <td>{row['이격도']}%</td>
                                    <td>{row['종합점수']}점</td>
                                </tr>
"""
        html += """
                            </tbody>
                        </table>
"""
    else:
        html += """
                        <div class="no-data">데이터 없음</div>
"""
    
    html += """
                    </div>
"""
    
    # 📈 거래량 급증 Top 5
    html += """
                    <div class="category-section">
                        <h3>📈 거래량 급증 Top 5</h3>
                        <div style="font-size: 0.85em; color: #666; text-align: center; margin-bottom: 15px;">거래량비율 높은 순</div>
"""
    
    if recommendations and 'volume_top5' in recommendations:
        volume_top5 = recommendations['volume_top5']
        html += """
                        <table>
                            <thead style="background: #42a5f5;">
                                <tr>
                                    <th>순위</th>
                                    <th>종목명</th>
                                    <th>거래량</th>
                                    <th>점수</th>
                                </tr>
                            </thead>
                            <tbody>
"""
        for idx, row in volume_top5.iterrows():
            html += f"""
                                <tr>
                                    <td><strong>{idx}</strong></td>
                                    <td class="stock-name">{row['종목명']}</td>
                                    <td>{row['거래량비율']}%</td>
                                    <td>{row['종합점수']}점</td>
                                </tr>
"""
        html += """
                            </tbody>
                        </table>
"""
    else:
        html += """
                        <div class="no-data">데이터 없음</div>
"""
    
    html += """
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
    
    return html

# ===========================================
# 8. 메인 실행 함수
# ===========================================
def main():
    """메인 실행 함수"""
    print("\n" + "="*60)
    print("🚀 한국 주식 저평가 종목 추천 시스템 시작 (완전판)")
    print("="*60)
    
    # 1. 시장 지수 수집
    indices, index_date = get_market_indices()
    
    # 2. 환율 정보 수집
    exchange_rates = get_exchange_rates()
    
    # 3. 전체 시장 스캔
    df = scan_all_stocks()
    
    # 4. 추천 종목 선별
    recommendations = select_recommendations(df)
    
    # 5. HTML 리포트 생성
    html_content = generate_html_report(recommendations, indices, index_date, exchange_rates)
    
    # 6. 파일 저장
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, "index.html")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("\n" + "="*60)
    print(f"✅ 리포트 생성 완료: {output_file}")
    print("="*60)
    
    # 7. 결과 요약
    print("\n📊 실행 결과 요약:")
    print(f"  - 코스피: {indices['kospi']['value']:,.2f} ({indices['kospi']['change']:+.2f}%)" + 
          (" [참고값]" if indices['kospi']['is_reference'] else ""))
    print(f"  - 코스닥: {indices['kosdaq']['value']:,.2f} ({indices['kosdaq']['change']:+.2f}%)" +
          (" [참고값]" if indices['kosdaq']['is_reference'] else ""))
    print(f"  - 환율 (USD): {exchange_rates.get('USD', 'N/A')}원")
    
    if recommendations:
        print(f"  - 종합 Top 30: {len(recommendations['top_30'])}개")
        print(f"  - 평균 점수: {recommendations['avg_score']:.1f}점")
        print(f"  - 시장 상황: {recommendations['market_status']}")
        
        print(f"\n🏆 종합 TOP 3:")
        for idx, row in recommendations['top_30'].head(3).iterrows():
            print(f"  {idx}. {row['종목명']} ({row['종합점수']}점, 위험도: {row['위험도']})")
        
        print(f"\n🔴 과매도 #1: {recommendations['rsi_top5'].iloc[0]['종목명']} (RSI: {recommendations['rsi_top5'].iloc[0]['RSI']})")
        print(f"💰 저평가 #1: {recommendations['disparity_top5'].iloc[0]['종목명']} (이격도: {recommendations['disparity_top5'].iloc[0]['이격도']}%)")
        print(f"📈 거래량 #1: {recommendations['volume_top5'].iloc[0]['종목명']} (거래량: {recommendations['volume_top5'].iloc[0]['거래량비율']}%)")

if __name__ == "__main__":
    main()
