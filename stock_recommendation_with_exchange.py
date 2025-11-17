#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 저평가 종목 추천 시스템 (최종 버전)
- 기술적 분석 기반 종목 선별
- Top 30 + 카테고리별 Top 5
- 환율 정보 통합
- GitHub Actions 자동화
"""

import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime, timedelta
import pytz
import warnings
import os
import requests
import time

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
        if current.weekday() < 5:  
            count += 1
    
    return current.strftime("%Y%m%d")

def get_last_trading_date():
    """가장 최근 영업일 반환"""
    korea_tz = pytz.timezone('Asia/Seoul')
    current = datetime.now(korea_tz)
    
    # 토요일(5) 또는 일요일(6)이면 금요일로
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    
    return current.strftime("%Y%m%d")

# ===========================================
# 2. 시장 지수 조회
# ===========================================
def get_market_indices():
    """코스피, 코스닥 지수 조회 (20영업일까지 확장)"""
    print("\n" + "="*60)
    print("📊 시장 지수 수집 시작")
    print("="*60)
    
    korea_time = get_korean_date()
    print(f"🕐 한국시간: {korea_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    indices = {}
    
    # 20영업일까지 시도
    for i in range(20):
        try_date = get_business_days_ago(i)
        print(f"🔍 시도 {i+1}/20: {try_date}")
        
        try:
            kospi_df = stock.get_index_ohlcv(try_date, try_date, "1001")
            kosdaq_df = stock.get_index_ohlcv(try_date, try_date, "2001")
            
            if not kospi_df.empty and not kosdaq_df.empty:
                kospi_current = kospi_df['종가'].iloc[0]
                kospi_prev = kospi_df['시가'].iloc[0]
                kospi_change = ((kospi_current - kospi_prev) / kospi_prev * 100)
                
                kosdaq_current = kosdaq_df['종가'].iloc[0]
                kosdaq_prev = kosdaq_df['시가'].iloc[0]
                kosdaq_change = ((kosdaq_current - kosdaq_prev) / kosdaq_prev * 100)
                
                indices = {
                    'kospi': kospi_current,
                    'kospi_change': kospi_change,
                    'kosdaq': kosdaq_current,
                    'kosdaq_change': kosdaq_change,
                    'date': try_date
                }
                
                print(f"✅ 코스피: {kospi_current:,.2f} ({kospi_change:+.2f}%)")
                print(f"✅ 코스닥: {kosdaq_current:,.2f} ({kosdaq_change:+.2f}%)")
                print(f"✨ 지수 데이터 수집 성공! (기준일: {try_date})")
                break
                
        except Exception as e:
            if i < 19:
                continue
            else:
                print(f"⚠️ 20영업일 내 데이터 없음. 참고값으로 표시됩니다.")
                indices = {
                    'kospi': 0,
                    'kospi_change': 0,
                    'kosdaq': 0,
                    'kosdaq_change': 0,
                    'date': try_date
                }
    
    return indices

# ===========================================
# 3. 환율 정보 조회 (ExchangeRate-API)
# ===========================================
def get_exchange_rates():
    """ExchangeRate-API에서 환율 정보 조회"""
    print("\n" + "="*60)
    print("💱 환율 정보 수집 시작 (ExchangeRate-API)")
    print("="*60)
    
    try:
        print("🔍 환율 데이터 요청 중...")
        url = "https://api.exchangerate-api.com/v4/latest/KRW"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            rates = data['rates']
            
            # KRW 기준이므로 역수 계산
            usd_rate = 1 / rates['USD']
            jpy_rate = (1 / rates['JPY']) * 100  # 100엔 기준
            eur_rate = 1 / rates['EUR']
            
            exchange_data = {
                'usd': round(usd_rate, 2),
                'jpy': round(jpy_rate, 2),
                'eur': round(eur_rate, 2),
                'date': data['date']
            }
            
            print(f"✅ USD: {exchange_data['usd']:,.2f}원")
            print(f"✅ JPY (100엔): {exchange_data['jpy']:,.2f}원")
            print(f"✅ EUR: {exchange_data['eur']:,.2f}원")
            print(f"✨ 환율 데이터 수집 성공! (기준일: {exchange_data['date']})")
            
            return exchange_data
        else:
            print(f"⚠️ API 오류: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ 환율 조회 실패: {str(e)}")
        return None

# ===========================================
# 4. 기술적 지표 계산 및 점수화
# ===========================================
def calculate_technical_indicators(ticker, ticker_name, start_date, end_date, timeout=5):
    """개별 종목의 기술적 지표 계산 및 종합점수 산출"""
    try:
        start_time = time.time()
        
        # 타임아웃 체크
        if time.time() - start_time > timeout:
            return None
        
        df = stock.get_market_ohlcv(start_date, end_date, ticker)
        
        if len(df) < 20:
            return None
        
        # 타임아웃 체크
        if time.time() - start_time > timeout:
            return None
        
        current_price = df['종가'].iloc[-1]
        prev_price = df['종가'].iloc[-2] if len(df) >= 2 else current_price
        price_change = ((current_price - prev_price) / prev_price * 100) if prev_price > 0 else 0
        
        # RSI 계산
        delta = df['종가'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # 이격도 계산
        ma20 = df['종가'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20 * 100) if ma20 > 0 else 100
        
        # 거래량 비율
        avg_volume = df['거래량'].rolling(window=20).mean().iloc[-1]
        current_volume = df['거래량'].iloc[-1]
        volume_ratio = (current_volume / avg_volume * 100) if avg_volume > 0 else 100
        
        # 타임아웃 체크
        if time.time() - start_time > timeout:
            return None
        
        # PBR 조회
        pbr = 0
        try:
            fundamental = stock.get_market_fundamental(end_date, end_date, ticker)
            if not fundamental.empty and 'PBR' in fundamental.columns:
                pbr = fundamental['PBR'].iloc[0]
                if pd.isna(pbr) or pbr < 0:
                    pbr = 0
        except:
            pbr = 0
        
        # 종합점수 계산 (100점 만점)
        score = 0
        
        # RSI 점수 (30점)
        if current_rsi <= 30:
            score += 30
        elif current_rsi <= 40:
            score += 20
        elif current_rsi <= 50:
            score += 10
        
        # 이격도 점수 (25점)
        if disparity <= 90:
            score += 25
        elif disparity <= 95:
            score += 20
        elif disparity <= 100:
            score += 10
        
        # 거래량 점수 (25점)
        if volume_ratio >= 200:
            score += 25
        elif volume_ratio >= 150:
            score += 20
        elif volume_ratio >= 120:
            score += 15
        elif volume_ratio >= 100:
            score += 10
        
        # PBR 점수 (20점)
        if 0 < pbr <= 0.5:
            score += 20
        elif 0 < pbr <= 0.8:
            score += 15
        elif 0 < pbr <= 1.0:
            score += 10
        elif 0 < pbr <= 1.5:
            score += 5
        
        # 위험도 평가
        risk_factors = []
        if pbr > 0 and pbr < 0.3:
            risk_factors.append("극저PBR")
        if current_price < 5000:
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
            '전일대비': round(price_change, 2),
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
def scan_all_stocks(end_date):
    """모든 종목 스캔 및 분석"""
    print("\n" + "="*60)
    print("🔍 전체 시장 스캔 시작")
    print("="*60)
    
    start_date = get_business_days_ago(30)
    print(f"📅 데이터 기준일: {end_date}")
    
    # 전체 종목 리스트
    kospi_tickers = stock.get_market_ticker_list(end_date, market="KOSPI")
    kosdaq_tickers = stock.get_market_ticker_list(end_date, market="KOSDAQ")
    all_tickers = list(kospi_tickers) + list(kosdaq_tickers)
    
    print(f"📊 총 {len(all_tickers)}개 종목 스캔 예정")
    print(f"⏱️ 예상 소요시간: 약 {len(all_tickers) * 1 / 60:.0f}분")
    
    results = []
    processed = 0
    failed = 0
    start_time = time.time()
    
    for ticker in all_tickers:
        processed += 1
        
        try:
            ticker_name = stock.get_market_ticker_name(ticker)
            result = calculate_technical_indicators(ticker, ticker_name, start_date, end_date)
            
            if result:
                results.append(result)
            else:
                failed += 1
                
        except Exception as e:
            failed += 1
            if processed % 100 == 0:
                print(f"⚠️ {ticker} 에러: {str(e)[:50]}")
            continue
        
        # 진행 로깅
        if processed % 50 == 0:
            elapsed = time.time() - start_time
            avg_time = elapsed / processed
            remaining = (len(all_tickers) - processed) * avg_time
            print(f"⏳ 진행률: {processed}/{len(all_tickers)} ({processed/len(all_tickers)*100:.1f}%)")
            print(f"   성공: {len(results)}개, 실패: {failed}개")
            print(f"   경과시간: {elapsed/60:.1f}분, 남은시간: {remaining/60:.1f}분")
    
    total_time = time.time() - start_time
    print(f"\n✅ 스캔 완료: {len(results)}개 종목 수집 성공")
    print(f"⚠️ 실패: {failed}개 종목")
    print(f"⏱️ 총 소요시간: {total_time/60:.1f}분")
    
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
    """Top 30 + 카테고리별 Top 5 선별"""
    recommendations = {}
    
    if len(df) == 0:
        return recommendations
    
    # Top 30 선별
    top_30 = df.head(30).copy()
    top_30.index = range(1, len(top_30) + 1)
    recommendations['top_30'] = top_30
    
    # 시장 상황 판단
    avg_score = top_30['종합점수'].mean()
    if avg_score >= 80:
        market_status = "🟢 강한 저평가 신호 (평균: {:.1f}점)".format(avg_score)
    elif avg_score >= 60:
        market_status = "🟡 보통 수준 (평균: {:.1f}점)".format(avg_score)
    else:
        market_status = "🔴 저평가 종목 부족 (평균: {:.1f}점)".format(avg_score)
    
    recommendations['market_status'] = market_status
    
    # 카테고리별 Top 5
    recommendations['rsi_top5'] = df.nsmallest(5, 'RSI')[['종목명', '현재가', 'RSI', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['rsi_top5'].index = range(1, 6)
    
    recommendations['disparity_top5'] = df.nsmallest(5, '이격도')[['종목명', '현재가', '이격도', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['disparity_top5'].index = range(1, 6)
    
    recommendations['volume_top5'] = df.nlargest(5, '거래량비율')[['종목명', '현재가', '거래량비율', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['volume_top5'].index = range(1, 6)
    
    # 카테고리별 인사이트
    recommendations['rsi_insight'] = {
        'avg': df['RSI'].head(30).mean(),
        'min': df['RSI'].head(30).min(),
        'count_oversold': len(df[df['RSI'] <= 30])
    }
    
    recommendations['disparity_insight'] = {
        'avg': df['이격도'].head(30).mean(),
        'min': df['이격도'].head(30).min(),
        'count_undervalued': len(df[df['이격도'] <= 95])
    }
    
    recommendations['volume_insight'] = {
        'avg': df['거래량비율'].head(30).mean(),
        'max': df['거래량비율'].head(30).max(),
        'count_surge': len(df[df['거래량비율'] >= 150])
    }
    
    print("\n" + "="*60)
    print("📊 추천 종목 선별 완료")
    print("="*60)
    print(f"✅ 종합 Top 30: {len(top_30)}개")
    print(f"✅ 과매도 Top 5: 5개")
    print(f"✅ 저평가 Top 5: 5개")
    print(f"✅ 거래량 Top 5: 5개")
    print(f"📈 시장 상황: {recommendations['market_status']}")
    
    return recommendations

# ===========================================
# 7. HTML 생성
# ===========================================
def generate_html(recommendations, indices, exchange_data):
    """HTML 페이지 생성"""
    
    # 종목이 없을 때 처리
    if 'top_30' not in recommendations or len(recommendations['top_30']) == 0:
        html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 주식 저평가 종목 추천</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }}
        h1 {{
            color: #2d3748;
            margin-bottom: 20px;
        }}
        .message {{
            font-size: 1.2em;
            color: #718096;
            margin: 20px 0;
        }}
        .update-time {{
            color: #a0aec0;
            font-size: 0.9em;
            margin-top: 40px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 한국 주식 저평가 종목 추천</h1>
        <div class="message">
            <p>현재 기준을 만족하는 추천 종목이 없습니다.</p>
            <p>시장 상황이 변경되면 새로운 종목이 추천됩니다.</p>
        </div>
        <div class="update-time">
            마지막 업데이트: {get_korean_date().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
</body>
</html>
"""
        return html
    
    top_30 = recommendations['top_30']
    
    # Top 30 테이블 생성
    top_30_rows = ""
    for idx, row in top_30.iterrows():
        price_change_class = "positive" if row['전일대비'] > 0 else "negative"
        price_change_sign = "+" if row['전일대비'] > 0 else ""
        
        risk_class = "risk-low"
        if row['위험도'] == "높음":
            risk_class = "risk-high"
        elif row['위험도'] == "중간":
            risk_class = "risk-medium"
        
        top_30_rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td class="{price_change_class}">{price_change_sign}{row['전일대비']:.2f}%</td>
                <td>{row['RSI']:.1f}</td>
                <td>{row['이격도']:.1f}%</td>
                <td>{row['거래량비율']:.1f}%</td>
                <td>{row['PBR']:.2f}</td>
                <td><strong>{row['종합점수']:.0f}점</strong></td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
                <td class="risk-factors">{row['위험요인']}</td>
            </tr>
            """
    
    # 카테고리별 테이블 생성 함수
    def generate_category_table(df, columns):
        rows = ""
        for idx, row in df.iterrows():
            risk_class = "risk-low"
            if row['위험도'] == "높음":
                risk_class = "risk-high"
            elif row['위험도'] == "중간":
                risk_class = "risk-medium"
            
            value_col = columns[2]  # RSI, 이격도, 거래량비율
            value = row[value_col]
            
            rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td><strong>{value:.1f}{'%' if value_col != 'RSI' else ''}</strong></td>
                <td>{row['종합점수']:.0f}점</td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
            </tr>
            """
        return rows
    
    rsi_rows = generate_category_table(recommendations['rsi_top5'], ['종목명', '현재가', 'RSI', '종합점수', '위험도'])
    disparity_rows = generate_category_table(recommendations['disparity_top5'], ['종목명', '현재가', '이격도', '종합점수', '위험도'])
    volume_rows = generate_category_table(recommendations['volume_top5'], ['종목명', '현재가', '거래량비율', '종합점수', '위험도'])
    
    # 인사이트 생성
    rsi_insight = recommendations['rsi_insight']
    rsi_insight_text = ""
    if rsi_insight['avg'] <= 30:
        rsi_insight_text = f"→ RSI가 {rsi_insight['avg']:.1f}로 극단적 과매도 구간. 단기 반등 가능성 높음"
    elif rsi_insight['avg'] <= 40:
        rsi_insight_text = f"→ RSI가 {rsi_insight['avg']:.1f}로 과매도 구간. 반등 관찰 필요"
    else:
        rsi_insight_text = f"→ RSI가 {rsi_insight['avg']:.1f}로 안정적 수준"
    
    disparity_insight = recommendations['disparity_insight']
    disparity_insight_text = ""
    if disparity_insight['avg'] <= 90:
        disparity_insight_text = f"→ 평균 대비 {100-disparity_insight['avg']:.1f}% 저평가. 강한 가치 투자 기회"
    elif disparity_insight['avg'] <= 95:
        disparity_insight_text = f"→ 평균 대비 {100-disparity_insight['avg']:.1f}% 이상 저평가. 가치 투자 기회"
    else:
        disparity_insight_text = f"→ 적정 가격 범위 (평균: {disparity_insight['avg']:.1f}%)"
    
    volume_insight = recommendations['volume_insight']
    volume_insight_text = ""
    if volume_insight['avg'] >= 150:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 강한 관심 집중"
    elif volume_insight['avg'] >= 120:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 적정 수준"
    else:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 보통 수준"
    
    # 환율 정보 HTML
    exchange_html = ""
    if exchange_data:
        exchange_html = f"""
        <div class="exchange-info">
            <h3>💱 환율 정보</h3>
            <div class="exchange-grid">
                <div class="exchange-item">
                    <span class="currency">🇺🇸 USD</span>
                    <span class="rate">{exchange_data['usd']:,.2f}원</span>
                </div>
                <div class="exchange-item">
                    <span class="currency">🇯🇵 JPY (100엔)</span>
                    <span class="rate">{exchange_data['jpy']:,.2f}원</span>
                </div>
                <div class="exchange-item">
                    <span class="currency">🇪🇺 EUR</span>
                    <span class="rate">{exchange_data['eur']:,.2f}원</span>
                </div>
            </div>
            <p class="update-time">업데이트: {exchange_data['date']}</p>
        </div>
        """
    
    # 지수 정보 HTML
    kospi_change_class = "positive" if indices['kospi_change'] > 0 else "negative"
    kosdaq_change_class = "positive" if indices['kosdaq_change'] > 0 else "negative"
    
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        
        header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 3px solid #667eea;
        }}
        
        h1 {{
            font-size: 2.5em;
            color: #2d3748;
            margin-bottom: 10px;
        }}
        
        .update-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 20px 0;
            padding: 15px;
            background: #f7fafc;
            border-radius: 10px;
        }}
        
        .market-indices {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        
        .index-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }}
        
        .index-name {{
            font-size: 0.9em;
            opacity: 0.9;
            margin-bottom: 5px;
        }}
        
        .index-value {{
            font-size: 1.8em;
            font-weight: bold;
            margin: 5px 0;
        }}
        
        .index-change {{
            font-size: 1.1em;
        }}
        
        .positive {{ color: #48bb78; }}
        .negative {{ color: #f56565; }}
        
        .exchange-info {{
            background: #f7fafc;
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
        }}
        
        .exchange-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        
        .exchange-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            background: white;
            border-radius: 8px;
            border: 2px solid #e2e8f0;
        }}
        
        .currency {{
            font-weight: 600;
            color: #2d3748;
        }}
        
        .rate {{
            font-size: 1.2em;
            font-weight: bold;
            color: #667eea;
        }}
        
        .market-status {{
            text-align: center;
            padding: 20px;
            background: #edf2f7;
            border-radius: 10px;
            margin: 20px 0;
            font-size: 1.3em;
            font-weight: bold;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        th, td {{
            padding: 15px;
            text-align: center;
            border-bottom: 1px solid #e2e8f0;
        }}
        
        th {{
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }}
        
        tbody tr:hover {{
            background: #f7fafc;
            transition: background 0.3s;
        }}
        
        .risk-low {{
            background: #c6f6d5;
            color: #22543d;
            padding: 5px 10px;
            border-radius: 5px;
            font-weight: 600;
        }}
        
        .risk-medium {{
            background: #feebc8;
            color: #7c2d12;
            padding: 5px 10px;
            border-radius: 5px;
            font-weight: 600;
        }}
        
        .risk-high {{
            background: #fed7d7;
            color: #742a2a;
            padding: 5px 10px;
            border-radius: 5px;
            font-weight: 600;
        }}
        
        .risk-factors {{
            font-size: 0.85em;
            color: #718096;
        }}
        
        .positive {{
            color: #e53e3e;
            font-weight: 600;
        }}
        
        .negative {{
            color: #3182ce;
            font-weight: 600;
        }}
        
        .guide-section {{
            margin-top: 40px;
            background: #f7fafc;
            padding: 30px;
            border-radius: 15px;
        }}
        
        .guide-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}
        
        .guide-box {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            border: 2px solid #e2e8f0;
        }}
        
        .guide-box h3 {{
            color: #2d3748;
            margin-bottom: 10px;
            font-size: 1.1em;
        }}
        
        .guide-desc {{
            color: #718096;
            font-size: 0.9em;
            margin-bottom: 15px;
            font-style: italic;
        }}
        
        .guide-list {{
            list-style: none;
            padding: 0;
        }}
        
        .guide-list li {{
            padding: 8px 0;
            border-bottom: 1px solid #e2e8f0;
            font-size: 0.9em;
        }}
        
        .guide-list li:last-child {{
            border-bottom: none;
        }}
        
        .insight-box {{
            background: #edf2f7;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 15px;
            font-size: 0.9em;
        }}
        
        .insight-box p {{
            margin: 5px 0;
            color: #2d3748;
        }}
        
        .insight-text {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 2px solid #cbd5e0;
            font-weight: 600;
            color: #667eea;
        }}
        
        .category-section {{
            margin-top: 40px;
        }}
        
        .category-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}
        
        .category-box {{
            background: #f7fafc;
            padding: 20px;
            border-radius: 10px;
            border: 2px solid #e2e8f0;
        }}
        
        .category-box h3 {{
            color: #2d3748;
            margin-bottom: 10px;
        }}
        
        .category-desc {{
            color: #718096;
            font-size: 0.9em;
            margin-bottom: 15px;
        }}
        
        .refresh-btn {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            font-size: 1em;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        
        .refresh-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }}
        
        .update-time {{
            color: #718096;
            font-size: 0.9em;
            margin-top: 10px;
        }}
        
        footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 2px solid #e2e8f0;
            color: #718096;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                padding: 15px;
            }}
            
            h1 {{
                font-size: 1.8em;
            }}
            
            table {{
                font-size: 0.85em;
            }}
            
            th, td {{
                padding: 10px 5px;
            }}
            
            .category-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 한국 주식 저평가 종목 추천</h1>
            <p style="color: #718096; margin-top: 10px;">기술적 분석 기반 자동 종목 선별 시스템</p>
        </header>
        
        <div class="update-info">
            <div>
                <strong>마지막 업데이트:</strong> {get_korean_date().strftime('%Y-%m-%d %H:%M:%S')}
                <br>
                <strong>데이터 기준일:</strong> {indices['date']}
            </div>
            <button class="refresh-btn" onclick="location.reload()">🔄 새로고침</button>
        </div>
        
        <div class="market-indices">
            <div class="index-card">
                <div class="index-name">KOSPI</div>
                <div class="index-value">{indices['kospi']:,.2f}</div>
                <div class="index-change {kospi_change_class}">
                    {indices['kospi_change']:+.2f}%
                </div>
            </div>
            <div class="index-card">
                <div class="index-name">KOSDAQ</div>
                <div class="index-value">{indices['kosdaq']:,.2f}</div>
                <div class="index-change {kosdaq_change_class}">
                    {indices['kosdaq_change']:+.2f}%
                </div>
            </div>
        </div>
        
        {exchange_html}
        
        <div class="market-status">
            {recommendations['market_status']}
        </div>
        
    <div class="guide-section">
        <h2>📚 지표 해석 가이드</h2>
        <div class="guide-grid">
            <div class="guide-box">
                <h3>🔵 RSI (Relative Strength Index)</h3>
                <p class="guide-desc">상대강도지수 - 과매도/과매수 판단</p>
                <ul class="guide-list">
                    <li><strong>30 이하:</strong> 과매도 구간 → 반등 가능성 높음</li>
                    <li><strong>30-70:</strong> 중립 구간 → 안정적 흘름</li>
                    <li><strong>70 이상:</strong> 과매수 구간 → 조정 가능성</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>📊 이격도 (Disparity)</h3>
                <p class="guide-desc">현재가 대비 이동평균선 비율</p>
                <ul class="guide-list">
                    <li><strong>95% 이하:</strong> 평균 대비 저평가 → 매수 기회</li>
                    <li><strong>95-105%:</strong> 적정 범위 → 평균 근처</li>
                    <li><strong>105% 이상:</strong> 고평가 구간 → 조정 주의</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>📈 거래량비율</h3>
                <p class="guide-desc">20일 평균 대비 거래량 증가율</p>
                <ul class="guide-list">
                    <li><strong>150% 이상:</strong> 거래량 폭발 → 관심 집중</li>
                    <li><strong>100-150%:</strong> 적정 거래량 → 안정적</li>
                    <li><strong>100% 미만:</strong> 저조한 거래 → 관심 부족</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>💰 PBR (Price to Book Ratio)</h3>
                <p class="guide-desc">주가순자산비율 - 가치 평가</p>
                <ul class="guide-list">
                    <li><strong>0.8 이하:</strong> 저평가 → 가치 투자 기회</li>
                    <li><strong>0.8-1.5:</strong> 적정 범위 → 평균적</li>
                    <li><strong>1.5 이상:</strong> 고평가 → 성장주 가능</li>
                </ul>
            </div>
        </div>
    </div>
    
        
        <h2 style="margin-top: 40px; color: #2d3748;">🏆 종합 추천 Top 30</h2>
        <table>
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>현재가</th>
                    <th>전일대비</th>
                    <th>RSI</th>
                    <th>이격도</th>
                    <th>거래량비율</th>
                    <th>PBR</th>
                    <th>종합점수</th>
                    <th>위험도</th>
                    <th>위험요인</th>
                </tr>
            </thead>
            <tbody>
                {top_30_rows}
            </tbody>
        </table>
        
        
        <div class="category-section">
            <h2>📊 카테고리별 추천</h2>
            
            <div class="category-grid">
                <div class="category-box">
                    <h3>🔴 과매도 Top 5</h3>
                    <p class="category-desc">RSI 기준 가장 낮은 종목 (반등 가능성)</p>
                    
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 RSI:</strong> {rsi_insight['avg']:.1f}</p>
            <p><strong>🔻 최저 RSI:</strong> {rsi_insight['min']:.1f} {'(극단적 과매도)' if rsi_insight['min'] <= 20 else '(과매도)'}</p>
            <p><strong>📊 과매도 종목수:</strong> {rsi_insight['count_oversold']}개 (RSI ≤30)</p>
            <p class="insight-text">{rsi_insight_text}</p>
        </div>
        
                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>현재가</th>
                                <th>RSI</th>
                                <th>점수</th>
                                <th>위험도</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rsi_rows}
                        </tbody>
                    </table>
                </div>
                
                <div class="category-box">
                    <h3>💰 저평가 Top 5</h3>
                    <p class="category-desc">이격도 기준 가장 낮은 종목 (저평가)</p>
                    
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 이격도:</strong> {disparity_insight['avg']:.1f}%</p>
            <p><strong>🔻 최저 이격도:</strong> {disparity_insight['min']:.1f}%</p>
            <p><strong>📊 저평가 종목수:</strong> {disparity_insight['count_undervalued']}개 (≤95%)</p>
            <p class="insight-text">{disparity_insight_text}</p>
        </div>
        
                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>현재가</th>
                                <th>이격도</th>
                                <th>점수</th>
                                <th>위험도</th>
                            </tr>
                        </thead>
                        <tbody>
                            {disparity_rows}
                        </tbody>
                    </table>
                </div>
                
                <div class="category-box">
                    <h3>📈 거래량 급증 Top 5</h3>
                    <p class="category-desc">거래량 증가율 가장 높은 종목</p>
                    
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 거래량:</strong> {volume_insight['avg']:.1f}%</p>
            <p><strong>🚀 최고 거래량:</strong> {volume_insight['max']:.1f}%</p>
            <p><strong>📊 거래량 급증:</strong> {volume_insight['count_surge']}개 (≥150%)</p>
            <p class="insight-text">{volume_insight_text}</p>
        </div>
        
                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>현재가</th>
                                <th>거래량비율</th>
                                <th>점수</th>
                                <th>위험도</th>
                            </tr>
                        </thead>
                        <tbody>
                            {volume_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        
        <footer>
            <p>⚠️ 본 정보는 투자 참고용이며, 투자 판단의 책임은 투자자 본인에게 있습니다.</p>
            <p style="margin-top: 10px;">📊 데이터 출처: KRX (한국거래소) via pykrx</p>
            <p style="margin-top: 5px;">💱 환율 출처: ExchangeRate-API</p>
        </footer>
    </div>
</body>
</html>
"""
    
    return html

# ===========================================
# 8. 메인 실행
# ===========================================
def main():
    """메인 실행 함수"""
    print("\n" + "="*60)
    print("🚀 한국 주식 저평가 종목 추천 시스템 시작 (최종 버전)")
    print("="*60)
    
    # 1. 시장 지수 조회
    indices = get_market_indices()
    
    # 2. 환율 정보 조회
    exchange_data = get_exchange_rates()
    
    # 3. 전체 시장 스캔
    end_date = get_last_trading_date()
    df = scan_all_stocks(end_date)
    
    # 4. 추천 종목 선별
    recommendations = select_recommendations(df)
    
    # 5. HTML 생성
    html = generate_html(recommendations, indices, exchange_data)
    
    # 6. 파일 저장
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, "index.html")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print("\n" + "="*60)
    print("✅ HTML 파일 생성 완료")
    print("="*60)
    print(f"📁 저장 위치: {output_file}")
    print(f"🌐 GitHub Pages에 배포됩니다")
    print("\n🎉 프로세스 완료!")

if __name__ == "__main__":
    main()
