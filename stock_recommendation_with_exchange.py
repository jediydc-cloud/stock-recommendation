#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 저평가 종목 추천 시스템 (최종 완성판)
- ExchangeRate-API 사용 (안정적)
- 토요일/주말 대응
- 종합 Top 30 + 카테고리별 Top 5
"""

import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime, timedelta
import pytz
import warnings
import os
import requests

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
# 4. 종목별 기술적 지표 계산
# ===========================================
def calculate_technical_indicators(ticker, ticker_name, end_date):
    """개별 종목의 기술적 지표 계산"""
    try:
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
        
        if df.empty or len(df) < 20:
            return None
        
        current_price = df['종가'].iloc[-1]
        
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
    
    for ticker in all_tickers:
        processed += 1
        
        if processed % 100 == 0:
            print(f"⏳ 진행률: {processed}/{total_count} ({processed/total_count*100:.1f}%)")
        
        try:
            ticker_name = stock.get_market_ticker_name(ticker)
            result = calculate_technical_indicators(ticker, ticker_name, end_date)
            
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
# 7. 메인 실행
# ===========================================
def main():
    """메인 실행 함수"""
    print("\n" + "="*60)
    print("🚀 한국 주식 저평가 종목 추천 시스템 시작 (최종판)")
    print("="*60)
    
    # 1. 시장 지수 수집
    indices, index_date = get_market_indices()
    
    # 2. 환율 정보 수집
    exchange_rates = get_exchange_rates()
    
    # 3. 전체 시장 스캔
    df = scan_all_stocks()
    
    # 4. 추천 종목 선별
    recommendations = select_recommendations(df)
    
    # 5. 결과 요약
    print("\n" + "="*60)
    print("📊 실행 결과 요약")
    print("="*60)
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
        
        return recommendations
    
    return None

if __name__ == "__main__":
    results = main()
    
    # Colab에서 DataFrame 표시
    if results:
        print("\n" + "="*60)
        print("✨ 종합 추천 Top 30")
        print("="*60)
        display(results['top_30'])
