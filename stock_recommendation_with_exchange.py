#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 저평가 종목 추천 시스템 (GitHub Actions 최적화 버전)
- 타임아웃 설정 강화
- 에러 핸들링 개선
- 진행 상황 로깅 강화
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
# 2. 시장 지수 수집
# ===========================================
def get_market_indices():
    """코스피/코스닥 지수 안정적 수집"""
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
    
    for days_back in range(1, 21):
        try:
            target_date = get_business_days_ago(days_back)
            prev_date = get_business_days_ago(days_back + 1)
            
            print(f"🔍 시도 {days_back}/20: {target_date}")
            
            kospi_df = stock.get_index_ohlcv(target_date, target_date, "1001")
            if not kospi_df.empty and len(kospi_df) > 0:
                indices['kospi']['value'] = float(kospi_df['종가'].iloc[-1])
                
                prev_kospi_df = stock.get_index_ohlcv(prev_date, prev_date, "1001")
                if not prev_kospi_df.empty:
                    prev_close = float(prev_kospi_df['종가'].iloc[-1])
                    curr_close = indices['kospi']['value']
                    indices['kospi']['change'] = ((curr_close - prev_close) / prev_close) * 100
            
            kosdaq_df = stock.get_index_ohlcv(target_date, target_date, "2001")
            if not kosdaq_df.empty and len(kosdaq_df) > 0:
                indices['kosdaq']['value'] = float(kosdaq_df['종가'].iloc[-1])
                
                prev_kosdaq_df = stock.get_index_ohlcv(prev_date, prev_date, "2001")
                if not prev_kosdaq_df.empty:
                    prev_close = float(prev_kosdaq_df['종가'].iloc[-1])
                    curr_close = indices['kosdaq']['value']
                    indices['kosdaq']['change'] = ((curr_close - prev_close) / prev_close) * 100
            
            if indices['kospi']['value'] > 0 and indices['kosdaq']['value'] > 0:
                print(f"✅ 코스피: {indices['kospi']['value']:,.2f} ({indices['kospi']['change']:+.2f}%)")
                print(f"✅ 코스닥: {indices['kosdaq']['value']:,.2f} ({indices['kosdaq']['change']:+.2f}%)")
                print(f"✨ 지수 데이터 수집 성공! (기준일: {target_date})")
                return indices, target_date
        
        except Exception as e:
            continue
    
    indices['kospi'] = {'value': 2500.0, 'change': 0.0, 'is_reference': True}
    indices['kosdaq'] = {'value': 800.0, 'change': 0.0, 'is_reference': True}
    
    return indices, None

# ===========================================
# 3. 환율 정보 수집 (ExchangeRate-API)
# ===========================================
def get_exchange_rates():
    """
    ExchangeRate-API를 통한 환율 정보 수집
    - 무료, 안정적, 실시간
    - 인증키 불필요
    """
    print("\n" + "="*60)
    print("💱 환율 정보 수집 시작 (ExchangeRate-API)")
    print("="*60)
    
    rates = {'USD': None, 'JPY': None, 'EUR': None, 'date': None}
    
    try:
        # ExchangeRate-API (무료, 인증 불필요)
        url = "https://api.exchangerate-api.com/v4/latest/KRW"
        
        print(f"🔍 환율 데이터 요청 중...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if 'rates' in data:
            # KRW 기준 환율을 원화 기준으로 변환
            raw_rates = data['rates']
            
            rates['USD'] = round(1 / raw_rates['USD'], 2) if 'USD' in raw_rates else None
            rates['JPY'] = round(100 / raw_rates['JPY'], 2) if 'JPY' in raw_rates else None  # 100엔 기준
            rates['EUR'] = round(1 / raw_rates['EUR'], 2) if 'EUR' in raw_rates else None
            rates['date'] = data.get('date', datetime.now().strftime('%Y-%m-%d'))
            
            print(f"✅ USD: {rates['USD']:,.2f}원")
            print(f"✅ JPY (100엔): {rates['JPY']:,.2f}원")
            print(f"✅ EUR: {rates['EUR']:,.2f}원")
            print(f"✨ 환율 데이터 수집 성공! (기준일: {rates['date']})")
            
            return rates
        else:
            print("⚠️ API 응답에 rates 데이터 없음")
            return rates
    
    except requests.exceptions.RequestException as e:
        print(f"⚠️ 네트워크 에러: {str(e)[:100]}")
        return rates
    except Exception as e:
        print(f"⚠️ 환율 수집 실패: {str(e)[:100]}")
        return rates

# ===========================================
# 4. 종목별 기술적 지표 계산 (타임아웃 강화)
# ===========================================
def calculate_technical_indicators(ticker, ticker_name, end_date, timeout=5):
    """개별 종목의 기술적 지표 계산 (타임아웃 설정)"""
    try:
        start_time = time.time()
        
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
        
        # 타임아웃 체크
        if time.time() - start_time > timeout:
            print(f"⚠️ {ticker_name} 타임아웃 (OHLCV)")
            return None
        
        df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
        
        if df.empty or len(df) < 20:
            return None
        
        current_price = df['종가'].iloc[-1]
        prev_price = df['종가'].iloc[-2] if len(df) >= 2 else current_price
        price_change = ((current_price - prev_price) / prev_price) * 100
        
        # RSI
        delta = df['종가'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # 이격도
        ma20 = df['종가'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100
        
        # 거래량 비율
        avg_volume = df['거래량'].rolling(window=20).mean().iloc[-1]
        current_volume = df['거래량'].iloc[-1]
        volume_ratio = (current_volume / avg_volume) * 100
        
        # 타임아웃 체크
        if time.time() - start_time > timeout:
            print(f"⚠️ {ticker_name} 타임아웃 (Fundamental)")
            return None
        
        # PBR
        fundamental = stock.get_market_fundamental(end_date, end_date, ticker)
        if fundamental.empty:
            return None
        pbr = fundamental['PBR'].iloc[0]
        
        # 종합점수
        score = 0
        
        if current_rsi <= 30:
            score += 30
        elif current_rsi <= 40:
            score += 20
        elif current_rsi <= 50:
            score += 10
        
        if disparity <= 95:
            score += 25
        elif disparity <= 98:
            score += 15
        elif disparity <= 100:
            score += 5
        
        if volume_ratio >= 150:
            score += 25
        elif volume_ratio >= 120:
            score += 15
        elif volume_ratio >= 100:
            score += 5
        
        if 0 < pbr <= 0.8:
            score += 20
        elif pbr <= 1.0:
            score += 15
        elif pbr <= 1.5:
            score += 10
        
        # 위험도
        risk_factors = []
        if pbr < 0.5:
            risk_factors.append("극저PBR")
        
        market_cap = stock.get_market_cap(end_date, end_date, ticker)
        if not market_cap.empty:
            cap_value = market_cap['시가총액'].iloc[0] / 100000000
            if cap_value < 1000:
                risk_factors.append("소형주")
        
        risk_level = "낮음"
        if len(risk_factors) >= 2:
            risk_level = "높음"
        elif len(risk_factors) == 1:
            risk_level = "중간"
        
        # 업종 정보 수집
        sector = '기타'
        try:
            sector_df = stock.get_market_fundamental(end_date, end_date, ticker)
            if not sector_df.empty and '업종' in sector_df.columns:
                sector = sector_df['업종'].iloc[0]
        except:
            pass
        
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
            '위험요인': ', '.join(risk_factors) if risk_factors else '-',
            '업종': sector
        }
    
    except Exception as e:
        return None

# ===========================================
# 5. 전체 시장 스캔 (진행 상황 로깅 강화)
# ===========================================
def scan_all_stocks():
    """코스피 + 코스닥 전체 종목 스캔"""
    print("\n" + "="*60)
    print("🔍 전체 시장 스캔 시작")
    print("="*60)
    
    # 최근 영업일 가져오기
    end_date = get_last_trading_date()
    print(f"📅 데이터 기준일: {end_date}")
    
    kospi_tickers = stock.get_market_ticker_list(market="KOSPI")
    kosdaq_tickers = stock.get_market_ticker_list(market="KOSDAQ")
    all_tickers = kospi_tickers + kosdaq_tickers
    
    total_count = len(all_tickers)
    print(f"📊 총 {total_count}개 종목 스캔 예정")
    print(f"⏱️ 예상 소요시간: 약 {total_count // 60}분\n")
    
    results = []
    processed = 0
    failed = 0
    start_time = time.time()
    
    for ticker in all_tickers:
        processed += 1
        
        # 진행 상황 로깅 (50개마다)
        if processed % 50 == 0:
            elapsed = time.time() - start_time
            avg_time = elapsed / processed
            remaining = (total_count - processed) * avg_time
            
            print(f"⏳ 진행률: {processed}/{total_count} ({processed/total_count*100:.1f}%)")
            print(f"   성공: {len(results)}개, 실패: {failed}개")
            print(f"   경과시간: {elapsed/60:.1f}분, 남은시간: {remaining/60:.1f}분")
        
        try:
            ticker_name = stock.get_market_ticker_name(ticker)
            result = calculate_technical_indicators(ticker, ticker_name, end_date, timeout=5)
            
            if result:
                results.append(result)
            else:
                failed += 1
        
        except Exception as e:
            failed += 1
            if processed % 100 == 0:
                print(f"⚠️ {ticker} 에러: {str(e)[:50]}")
            continue
    
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
    
    top_30 = df.head(30).copy()
    top_30.index = range(1, len(top_30) + 1)
    recommendations['top_30'] = top_30
    
    avg_score = top_30['종합점수'].mean()
    if avg_score >= 60:
        market_status = "🟢 강한 저평가 신호"
    elif avg_score >= 40:
        market_status = "🟡 적정 매수 기회"
    else:
        market_status = "🔴 신중한 접근 필요"
    
    recommendations['market_status'] = f"{market_status} (평균: {avg_score:.1f}점)"
    recommendations['avg_score'] = avg_score
    
    recommendations['rsi_top5'] = df.nsmallest(5, 'RSI')[['종목명', '현재가', 'RSI', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['rsi_top5'].index = range(1, 6)
    
    recommendations['disparity_top5'] = df.nsmallest(5, '이격도')[['종목명', '현재가', '이격도', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['disparity_top5'].index = range(1, 6)
    
    recommendations['volume_top5'] = df.nlargest(5, '거래량비율')[['종목명', '현재가', '거래량비율', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['volume_top5'].index = range(1, 6)
    
    # 업종별 분석
    if '업종' in df.columns:
        sector_groups = df.groupby('업종').agg({
            '종목명': 'count',
            '종합점수': 'mean'
        }).sort_values('종합점수', ascending=False)
        
        sector_top3 = {}
        for sector in sector_groups.head(5).index:
            if sector != '기타':
                sector_stocks = df[df['업종'] == sector].head(3)
                sector_top3[sector] = sector_stocks[['종목명', '현재가', '종합점수', '위험도']].reset_index(drop=True)
        
        recommendations['sector_top3'] = sector_top3
        recommendations['sector_summary'] = sector_groups.head(5)
    
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
    if 'sector_top3' in recommendations:
        print(f"✅ 업종별 분석: {len(recommendations['sector_top3'])}개 업종")
    print(f"📈 시장 상황: {recommendations['market_status']}")
    
    return recommendations

# ===========================================
# 7. HTML 생성
# ===========================================
def generate_html(recommendations, indices, exchange_rates, data_date):
    """HTML 파일 생성"""
    print("\n" + "="*60)
    print("📄 HTML 파일 생성 중...")
    print("="*60)
    
    os.makedirs('output', exist_ok=True)
    
    korea_tz = pytz.timezone('Asia/Seoul')
    current_time = datetime.now(korea_tz).strftime('%Y-%m-%d %H:%M:%S')
    
    # 환율 정보 HTML
    exchange_html = ""
    if exchange_rates.get('USD'):
        exchange_html = f"""
        <div class="exchange-info">
            <h3>💱 환율 정보</h3>
            <div class="exchange-grid">
                <div class="exchange-item">
                    <span class="currency">🇺🇸 USD</span>
                    <span class="rate">{exchange_rates['USD']:,.2f}원</span>
                </div>
                <div class="exchange-item">
                    <span class="currency">🇯🇵 JPY (100엔)</span>
                    <span class="rate">{exchange_rates['JPY']:,.2f}원</span>
                </div>
                <div class="exchange-item">
                    <span class="currency">🇪🇺 EUR</span>
                    <span class="rate">{exchange_rates['EUR']:,.2f}원</span>
                </div>
            </div>
            <p class="update-time">업데이트: {exchange_rates.get('date', 'N/A')}</p>
        </div>
        """
    
    # Top 30 테이블
    top30_rows = ""
    if recommendations and 'top_30' in recommendations:
        for idx, row in recommendations['top_30'].iterrows():
            risk_class = {
                '낮음': 'risk-low',
                '중간': 'risk-medium',
                '높음': 'risk-high'
            }.get(row['위험도'], 'risk-low')
            
            change_class = 'positive' if row['전일대비'] >= 0 else 'negative'
            
            top30_rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td class="{change_class}">{row['전일대비']:+.2f}%</td>
                <td>{row['RSI']:.1f}</td>
                <td>{row['이격도']:.1f}%</td>
                <td>{row['거래량비율']:.1f}%</td>
                <td>{row['PBR']:.2f}</td>
                <td><strong>{row['종합점수']}점</strong></td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
                <td class="risk-factors">{row['위험요인']}</td>
            </tr>
            """
    
    # 카테고리별 Top 5
    category_html = ""
    if recommendations:
        # 과매도 Top 5
        rsi_rows = ""
        for idx, row in recommendations['rsi_top5'].iterrows():
            risk_class = {
                '낮음': 'risk-low',
                '중간': 'risk-medium',
                '높음': 'risk-high'
            }.get(row['위험도'], 'risk-low')
            rsi_rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td><strong>{row['RSI']:.1f}</strong></td>
                <td>{row['종합점수']}점</td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
            </tr>
            """
        
        # 저평가 Top 5
        disparity_rows = ""
        for idx, row in recommendations['disparity_top5'].iterrows():
            risk_class = {
                '낮음': 'risk-low',
                '중간': 'risk-medium',
                '높음': 'risk-high'
            }.get(row['위험도'], 'risk-low')
            disparity_rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td><strong>{row['이격도']:.1f}%</strong></td>
                <td>{row['종합점수']}점</td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
            </tr>
            """
        
        # 거래량 Top 5
        volume_rows = ""
        for idx, row in recommendations['volume_top5'].iterrows():
            risk_class = {
                '낮음': 'risk-low',
                '중간': 'risk-medium',
                '높음': 'risk-high'
            }.get(row['위험도'], 'risk-low')
            volume_rows += f"""
            <tr>
                <td>{idx}</td>
                <td><strong>{row['종목명']}</strong></td>
                <td>{row['현재가']:,}원</td>
                <td><strong>{row['거래량비율']:.1f}%</strong></td>
                <td>{row['종합점수']}점</td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
            </tr>
            """
        
        # 인사이트 HTML
        rsi_insight = recommendations.get('rsi_insight', {})
        disparity_insight = recommendations.get('disparity_insight', {})
        volume_insight = recommendations.get('volume_insight', {})
        
        rsi_insight_html = f"""
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 RSI:</strong> {rsi_insight.get('avg', 0):.1f}</p>
            <p><strong>🔻 최저 RSI:</strong> {rsi_insight.get('min', 0):.1f} (극단적 과매도)</p>
            <p><strong>📊 과매도 종목수:</strong> {rsi_insight.get('count_oversold', 0)}개 (RSI ≤30)</p>
            <p class="insight-text">→ {"RSI가 30 이하로 극단적 과매도 구간. 단기 반등 가능성 높음" if rsi_insight.get('avg', 0) < 30 else "RSI 평균적. 안정적 진입 가능"}</p>
        </div>
        """ if rsi_insight else ""
        
        disparity_insight_html = f"""
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 이격도:</strong> {disparity_insight.get('avg', 0):.1f}%</p>
            <p><strong>🔻 최저 이격도:</strong> {disparity_insight.get('min', 0):.1f}%</p>
            <p><strong>📊 저평가 종목수:</strong> {disparity_insight.get('count_undervalued', 0)}개 (≤95%)</p>
            <p class="insight-text">→ {"평균 대비 5% 이상 저평가. 가치 투자 기회" if disparity_insight.get('avg', 0) < 95 else "평균 근처. 적정 가격대"}</p>
        </div>
        """ if disparity_insight else ""
        
        volume_insight_html = f"""
        <div class="insight-box">
            <p><strong>📈 Top 30 평균 거래량:</strong> {volume_insight.get('avg', 0):.1f}%</p>
            <p><strong>🚀 최고 거래량:</strong> {volume_insight.get('max', 0):.1f}%</p>
            <p><strong>📊 급증 종목수:</strong> {volume_insight.get('count_surge', 0)}개 (≥150%)</p>
            <p class="insight-text">→ {"거래량 폭발. 시장 관심 급증" if volume_insight.get('avg', 0) > 150 else "적정 거래량. 안정적 수급"}</p>
        </div>
        """ if volume_insight else ""
        
        category_html = f"""
        <div class="category-section">
            <h2>📊 카테고리별 추천</h2>
            
            <div class="category-grid">
                <div class="category-box">
                    <h3>🔴 과매도 Top 5</h3>
                    <p class="category-desc">RSI 기준 가장 낮은 종목 (반등 가능성)</p>
                    {rsi_insight_html}
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
                    {disparity_insight_html}
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
                    {volume_insight_html}
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
        """
    
    # 지표 가이드 HTML
    guide_html = """
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
    """
    
    # 업종별 분석 HTML
    sector_html = ""
    if 'sector_top3' in recommendations and recommendations['sector_top3']:
        sector_boxes = ""
        for sector, stocks in list(recommendations['sector_top3'].items())[:3]:
            stock_rows = ""
            for idx, row in stocks.iterrows():
                risk_class = {
                    '낮음': 'risk-low',
                    '중간': 'risk-medium',
                    '높음': 'risk-high'
                }.get(row['위험도'], 'risk-low')
                stock_rows += f"""
                <tr>
                    <td><strong>{row['종목명']}</strong></td>
                    <td>{row['현재가']:,}원</td>
                    <td>{row['종합점수']}점</td>
                    <td><span class="{risk_class}">{row['위험도']}</span></td>
                </tr>
                """
            
            sector_boxes += f"""
            <div class="sector-box">
                <h3>🏭 {sector}</h3>
                <table>
                    <thead>
                        <tr>
                            <th>종목명</th>
                            <th>현재가</th>
                            <th>점수</th>
                            <th>위험도</th>
                        </tr>
                    </thead>
                    <tbody>
                        {stock_rows}
                    </tbody>
                </table>
            </div>
            """
        
        sector_html = f"""
        <div class="sector-section">
            <h2>🏭 업종별 분석 (Top 3 업종)</h2>
            <p class="section-desc">점수가 높은 업종의 주요 종목들을 보여줍니다.</p>
            <div class="sector-grid">
                {sector_boxes}
            </div>
        </div>
        """
    
    # 전체 HTML
    html_content = f"""
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
        
        .sector-section {{
            margin-top: 40px;
        }}
        
        .sector-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}
        
        .sector-box {{
            background: #f7fafc;
            padding: 20px;
            border-radius: 10px;
            border: 2px solid #e2e8f0;
        }}
        
        .sector-box h3 {{
            color: #2d3748;
            margin-bottom: 15px;
        }}
        
        .section-desc {{
            color: #718096;
            font-size: 0.95em;
            margin-top: 10px;
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
                <strong>마지막 업데이트:</strong> {current_time}
                <br>
                <strong>데이터 기준일:</strong> {data_date if data_date else 'N/A'}
            </div>
            <button class="refresh-btn" onclick="location.reload()">🔄 새로고침</button>
        </div>
        
        <div class="market-indices">
            <div class="index-card">
                <div class="index-name">KOSPI</div>
                <div class="index-value">{indices['kospi']['value']:,.2f}</div>
                <div class="index-change {'positive' if indices['kospi']['change'] >= 0 else 'negative'}">
                    {indices['kospi']['change']:+.2f}%
                </div>
            </div>
            <div class="index-card">
                <div class="index-name">KOSDAQ</div>
                <div class="index-value">{indices['kosdaq']['value']:,.2f}</div>
                <div class="index-change {'positive' if indices['kosdaq']['change'] >= 0 else 'negative'}">
                    {indices['kosdaq']['change']:+.2f}%
                </div>
            </div>
        </div>
        
        {exchange_html}
        
        <div class="market-status">
            {recommendations.get('market_status', '데이터 없음')}
        </div>
        
        {guide_html}
        
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
                {top30_rows}
            </tbody>
        </table>
        
        {category_html}
        
        {sector_html}
        
        <footer>
            <p><strong>⚠️ 투자 유의사항</strong></p>
            <p style="margin-top: 10px;">본 정보는 투자 참고용이며, 투자 판단과 결과에 대한 책임은 투자자 본인에게 있습니다.</p>
            <p style="margin-top: 5px;">위험도가 "높음"인 종목은 변동성이 크므로 신중한 접근이 필요합니다.</p>
        </footer>
    </div>
</body>
</html>
    """
    
    # 파일 저장
    with open('output/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ HTML 파일 생성 완료: output/index.html")
    print(f"📁 파일 크기: {len(html_content):,} bytes")

# ===========================================
# 8. 메인 실행
# ===========================================
def main():
    """메인 실행 함수"""
    print("\n" + "="*60)
    print("🚀 한국 주식 저평가 종목 추천 시스템 시작 (GitHub Actions 최적화)")
    print("="*60)
    
    start_time = time.time()
    
    # 1. 시장 지수 수집
    indices, index_date = get_market_indices()
    
    # 2. 환율 정보 수집
    exchange_rates = get_exchange_rates()
    
    # 3. 전체 시장 스캔
    df = scan_all_stocks()
    
    # 4. 추천 종목 선별
    recommendations = select_recommendations(df)
    
    # 5. HTML 생성
    if recommendations:
        generate_html(recommendations, indices, exchange_rates, index_date)
    else:
        # 종목 0개일 때도 페이지 생성
        print("\n⚠️ 추천 종목이 없습니다. 기본 페이지를 생성합니다.")
        empty_recommendations = {
            'top_30': pd.DataFrame(),
            'market_status': '🔴 추천 가능한 종목이 없습니다',
            'avg_score': 0
        }
        generate_html(empty_recommendations, indices, exchange_rates, index_date)
    
    total_time = time.time() - start_time
    
    # 6. 결과 요약
    print("\n" + "="*60)
    print("📊 실행 결과 요약")
    print("="*60)
    print(f"⏱️ 총 실행시간: {total_time/60:.1f}분")
    print(f"코스피: {indices['kospi']['value']:,.2f} ({indices['kospi']['change']:+.2f}%)")
    print(f"코스닥: {indices['kosdaq']['value']:,.2f} ({indices['kosdaq']['change']:+.2f}%)")
    
    if exchange_rates['USD']:
        print(f"환율(USD): {exchange_rates['USD']:,.2f}원")
    
    if recommendations:
        print(f"\n평균 점수: {recommendations['avg_score']:.1f}점")
        print(f"시장 상황: {recommendations['market_status']}")
        
        print(f"\n🏆 종합 TOP 3:")
        for idx, row in recommendations['top_30'].head(3).iterrows():
            print(f"  {idx}. {row['종목명']} ({row['종합점수']}점, 위험도: {row['위험도']})")
        
        if len(recommendations['rsi_top5']) > 0:
            print(f"\n🔴 과매도 #1: {recommendations['rsi_top5'].iloc[0]['종목명']} (RSI: {recommendations['rsi_top5'].iloc[0]['RSI']})")
            print(f"💰 저평가 #1: {recommendations['disparity_top5'].iloc[0]['종목명']} (이격도: {recommendations['disparity_top5'].iloc[0]['이격도']}%)")
            print(f"📈 거래량 #1: {recommendations['volume_top5'].iloc[0]['종목명']} (거래량: {recommendations['volume_top5'].iloc[0]['거래량비율']}%)")
    
    print("\n" + "="*60)
    print("✨ 모든 작업 완료!")
    print("="*60)
    
    return recommendations

if __name__ == "__main__":
    results = main()
