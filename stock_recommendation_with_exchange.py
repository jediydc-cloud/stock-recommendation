#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 단기 반등 스윙 트레이드 종목 선별 시스템 (v2 최적화 버전)
- 보유 기간: 3~10 영업일
- 목표: 단기 5~15% 수준 반등 구간
- 개잡주 필터링 강화 (거래대금, 시총, 관리종목)
- GitHub Actions 자동화
- PBR 조회 최적화 (전체 한 번 조회)
- timeout 로직 제거 (코드 단순화)
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
# 블랙리스트 (관리종목, 투자주의, 상폐 위험 등)
# ===========================================
# 관리/투자주의/상폐 위험 등 개인적으로 제외하고 싶은 종목은 여기 추가
# 예시: "005930" (삼성전자), "000660" (SK하이닉스) 등
BLACKLIST_TICKERS = set([
    # "A123456",  # 예시: 관리종목
    # "A234567",  # 예시: 투자주의
])

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
# 3. 환율 정보 조회
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

            usd_rate = 1 / rates['USD']
            jpy_rate = (1 / rates['JPY']) * 100
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
# 4. 기술적 지표 계산 및 점수화 (스윙 트레이드용)
# ===========================================
def calculate_swing_indicators(ticker, ticker_name, start_date, end_date, cap_df=None, fundamental_df=None):
    """
    단기 스윙 트레이드용 기술적 지표 계산 (v2 최적화 버전)
    
    Parameters:
        ticker: 종목코드
        ticker_name: 종목명
        start_date: 시작일
        end_date: 종료일
        cap_df: 시가총액 DataFrame (최적화용)
        fundamental_df: PBR 등 펀더멘털 DataFrame (최적화용)
    
    Returns:
        dict: 종목 분석 결과 또는 None
    """
    try:
        # OHLCV 데이터
        df = stock.get_market_ohlcv(start_date, end_date, ticker)

        if len(df) < 30:
            return None

        current_price = df['종가'].iloc[-1]
        prev_price = df['종가'].iloc[-2] if len(df) >= 2 else current_price
        price_change = ((current_price - prev_price) / prev_price * 100) if prev_price > 0 else 0

        # === 개잡주 필터링 1: 20일 평균 거래대금 ===
        df['거래대금'] = df['종가'] * df['거래량']
        avg_trading_value = df['거래대금'].rolling(window=20).mean().iloc[-1]

        # 5억 미만 제외
        if avg_trading_value < 500_000_000:
            return None

        # === RSI 계산 ===
        delta = df['종가'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        # === 이격도 계산 (20일선) ===
        ma20 = df['종가'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20 * 100) if ma20 > 0 else 100

        # === 거래량 비율 ===
        avg_volume = df['거래량'].rolling(window=20).mean().iloc[-1]
        current_volume = df['거래량'].iloc[-1]
        volume_ratio = (current_volume / avg_volume * 100) if avg_volume > 0 else 100

        # === PBR 조회 (최적화: fundamental_df 재사용) ===
        pbr = 0
        if fundamental_df is not None and ticker in fundamental_df.index:
            try:
                pbr_value = fundamental_df.loc[ticker, 'PBR']
                if not pd.isna(pbr_value):
                    pbr = float(pbr_value)
            except:
                pass

        # === 시가총액 조회 (최적화: cap_df 재사용) ===
        market_cap = 0
        if cap_df is not None and ticker in cap_df.index and '시가총액' in cap_df.columns:
            market_cap = cap_df.loc[ticker, '시가총액']

        # === 최근 5일/20일 수익률 ===
        price_5d_ago = df['종가'].iloc[-6] if len(df) >= 6 else current_price
        price_20d_ago = df['종가'].iloc[-21] if len(df) >= 21 else current_price

        return_5d = ((current_price - price_5d_ago) / price_5d_ago * 100) if price_5d_ago > 0 else 0
        return_20d = ((current_price - price_20d_ago) / price_20d_ago * 100) if price_20d_ago > 0 else 0

        # === 최근 20일 고점/저점 ===
        high_20d = df['고가'].iloc[-20:].max()
        low_20d = df['저가'].iloc[-20:].min()

        position_from_low = ((current_price - low_20d) / low_20d * 100) if low_20d > 0 else 0
        position_from_high = ((current_price - high_20d) / high_20d * 100) if high_20d > 0 else 0

        # === 급등 이력 체크 (최근 3개월 내 2배 이상) ===
        price_90d_ago = df['종가'].iloc[-min(90, len(df))]
        max_90d = df['고가'].iloc[-min(90, len(df)):].max()
        surge_history = (max_90d / price_90d_ago) >= 2.0 if price_90d_ago > 0 else False

        # === 종합점수 계산 (100점 만점, 세분화) ===
        score = 0

        # [RSI (최대 30점)]
        if 20 <= current_rsi <= 25:
            score += 30
        elif 25 < current_rsi <= 35:
            score += 20
        elif 35 < current_rsi <= 45:
            score += 10

        # [이격도 (최대 25점)]
        if 80 <= disparity <= 90:
            score += 25
        elif 90 < disparity <= 95:
            score += 20
        elif 95 < disparity <= 100:
            score += 10

        # [거래량비율 (최대 25점)]
        if 150 <= volume_ratio <= 300:
            score += 25
        elif 120 <= volume_ratio < 150:
            score += 20
        elif 100 <= volume_ratio < 120:
            score += 15
        elif volume_ratio > 300:
            score += 15

        # [PBR (최대 20점)]
        if 0.3 < pbr <= 0.7:
            score += 20
        elif 0.7 < pbr <= 1.0:
            score += 15
        elif 0 < pbr <= 0.3:
            score += 10

        # === 리스크 태그 ===
        risk_tags = []

        if pbr <= 0 or pd.isna(pbr):
            risk_tags.append("자본잠식/적자")
        elif 0 < pbr <= 0.3:
            risk_tags.append("저PBR리스크")

        if avg_trading_value < 1_000_000_000:
            risk_tags.append("유동성부족")

        if 0 < market_cap < 100_000_000_000:
            risk_tags.append("소형주")

        if current_price < 5000:
            risk_tags.append("저가주")

        if surge_history:
            risk_tags.append("단기과열이력")

        # === 위험도 레벨 ===
        risk_count = len(risk_tags)
        if risk_count == 0:
            risk_level = "낮음"
        elif risk_count <= 2:
            risk_level = "중간"
        else:
            risk_level = "높음"

        # === 스윙 트레이드용 정보 ===
        stop_loss = int(current_price * 0.95)
        target_1 = int(current_price * 1.10)

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
            '위험태그': ', '.join(risk_tags) if risk_tags else '-',
            '20일평균거래대금': int(avg_trading_value),
            '시가총액': int(market_cap) if market_cap > 0 else 0,
            '5일수익률': round(return_5d, 2),
            '20일수익률': round(return_20d, 2),
            '20일저점대비': round(position_from_low, 2),
            '20일고점대비': round(position_from_high, 2),
            '손절가': stop_loss,
            '목표가': target_1
        }

    except Exception as e:
        return None

# ===========================================
# 5. 전체 시장 스캔 (v2 최적화 버전)
# ===========================================
def scan_all_stocks(end_date):
    """모든 종목 스캔 및 분석 (v2 최적화: 시가총액 + PBR 한 번만 조회)"""
    print("\n" + "="*60)
    print("🔍 전체 시장 스캔 시작 (스윙 트레이드용)")
    print("="*60)

    start_date = get_business_days_ago(100)
    print(f"📅 데이터 기준일: {end_date}")

    # 전체 종목 리스트
    kospi_tickers = stock.get_market_ticker_list(end_date, market="KOSPI")
    kosdaq_tickers = stock.get_market_ticker_list(end_date, market="KOSDAQ")
    all_tickers = list(kospi_tickers) + list(kosdaq_tickers)

    # 블랙리스트 필터링
    if BLACKLIST_TICKERS:
        before_count = len(all_tickers)
        all_tickers = [t for t in all_tickers if t not in BLACKLIST_TICKERS]
        filtered_count = before_count - len(all_tickers)
        if filtered_count > 0:
            print(f"🚫 블랙리스트 필터링: {filtered_count}개 종목 제외")

    print(f"📊 총 {len(all_tickers)}개 종목 스캔 예정")
    print(f"⏱️ 예상 소요시간: 약 {len(all_tickers) * 1 / 60:.0f}분")

    # === 성능 최적화 1: 시가총액 데이터 한 번만 조회 ===
    print("📊 시가총액 데이터 조회 중...")
    cap_df = None
    try:
        cap_df = stock.get_market_cap_by_ticker(end_date)
        print(f"✅ 시가총액 데이터 조회 완료 ({len(cap_df)}개 종목)")
    except Exception as e:
        print(f"⚠️ 시가총액 데이터 조회 실패: {str(e)}")

    # === 성능 최적화 2: PBR 데이터 한 번만 조회 ===
    print("📊 PBR 데이터 조회 중...")
    fundamental_df = None
    try:
        kospi_fund = stock.get_market_fundamental(end_date, end_date, "KOSPI")
        kosdaq_fund = stock.get_market_fundamental(end_date, end_date, "KOSDAQ")
        fundamental_df = pd.concat([kospi_fund, kosdaq_fund])
        print(f"✅ PBR 데이터 조회 완료 ({len(fundamental_df)}개 종목)")
    except Exception as e:
        print(f"⚠️ PBR 데이터 조회 실패: {str(e)}")

    results = []
    processed = 0
    failed = 0
    filtered_out = 0
    start_time = time.time()

    for ticker in all_tickers:
        processed += 1

        try:
            ticker_name = stock.get_market_ticker_name(ticker)
            # cap_df와 fundamental_df를 모두 인자로 전달
            result = calculate_swing_indicators(
                ticker, ticker_name, start_date, end_date,
                cap_df=cap_df,
                fundamental_df=fundamental_df
            )

            if result:
                results.append(result)
            else:
                filtered_out += 1

        except Exception as e:
            failed += 1
            if processed % 100 == 0:
                print(f"⚠️ {ticker} 에러: {str(e)[:50]}")
            continue

        if processed % 50 == 0:
            elapsed = time.time() - start_time
            avg_time = elapsed / processed
            remaining = (len(all_tickers) - processed) * avg_time
            print(f"⏳ 진행률: {processed}/{len(all_tickers)} ({processed/len(all_tickers)*100:.1f}%)")
            print(f"   성공: {len(results)}개, 필터링: {filtered_out}개, 실패: {failed}개")
            print(f"   경과시간: {elapsed/60:.1f}분, 남은시간: {remaining/60:.1f}분")

    total_time = time.time() - start_time
    print(f"\n✅ 스캔 완료: {len(results)}개 종목 수집 성공")
    print(f"🚫 필터링: {filtered_out}개 종목 (거래대금 부족 등)")
    print(f"⚠️ 실패: {failed}개 종목")
    print(f"⏱️ 총 소요시간: {total_time/60:.1f}분")

    if results:
        df = pd.DataFrame(results)
        df = df.sort_values('종합점수', ascending=False).reset_index(drop=True)

        # === 점수 분포 요약 통계 ===
        print("\n" + "="*60)
        print("📈 점수 분포 요약")
        print("="*60)
        print(f"   전체 후보 수: {len(df)}개")
        print(f"   80점 이상: {len(df[df['종합점수'] >= 80])}개")
        print(f"   60점 이상: {len(df[df['종합점수'] >= 60])}개")
        print(f"   40점 이상: {len(df[df['종합점수'] >= 40])}개")
        print(f"   점수 범위: {df['종합점수'].min():.0f} ~ {df['종합점수'].max():.0f}점, 평균 {df['종합점수'].mean():.1f}점")
        print("="*60)

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
    if avg_score >= 70:
        market_status = "🟢 강한 스윙 기회 (평균: {:.1f}점)".format(avg_score)
    elif avg_score >= 50:
        market_status = "🟡 보통 수준 (평균: {:.1f}점)".format(avg_score)
    else:
        market_status = "🔴 스윙 후보 부족 (평균: {:.1f}점)".format(avg_score)

    recommendations['market_status'] = market_status

    recommendations['rsi_top5'] = df.nsmallest(5, 'RSI')[['종목명', '현재가', 'RSI', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['rsi_top5'].index = range(1, 6)

    recommendations['disparity_top5'] = df.nsmallest(5, '이격도')[['종목명', '현재가', '이격도', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['disparity_top5'].index = range(1, 6)

    recommendations['volume_top5'] = df.nlargest(5, '거래량비율')[['종목명', '현재가', '거래량비율', '종합점수', '위험도']].reset_index(drop=True)
    recommendations['volume_top5'].index = range(1, 6)

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

    if 'top_30' not in recommendations or len(recommendations['top_30']) == 0:
        html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 주식 스윙 트레이드 종목 선별</title>
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
        <h1>📊 한국 주식 스윙 트레이드 종목 선별</h1>
        <div class="message">
            <p>현재 기준을 만족하는 스윙 후보가 없습니다.</p>
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
                <td>{row['손절가']:,}원<br/><small style="color:#e53e3e;">(-5%)</small></td>
                <td>{row['목표가']:,}원<br/><small style="color:#48bb78;">(+10%)</small></td>
                <td>{row['5일수익률']:+.1f}%</td>
                <td>{row['20일저점대비']:+.1f}%</td>
                <td><span class="{risk_class}">{row['위험도']}</span></td>
                <td class="risk-factors" style="font-size:0.8em;">{row['위험태그']}</td>
            </tr>
            """

    # 카테고리별 테이블 생성
    def generate_category_table(df, columns):
        rows = ""
        for idx, row in df.iterrows():
            risk_class = "risk-low"
            if row['위험도'] == "높음":
                risk_class = "risk-high"
            elif row['위험도'] == "중간":
                risk_class = "risk-medium"

            value_col = columns[2]
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

    # 인사이트
    rsi_insight = recommendations['rsi_insight']
    rsi_insight_text = ""
    if rsi_insight['avg'] <= 30:
        rsi_insight_text = f"→ RSI {rsi_insight['avg']:.1f}로 극단적 과매도. 단기 반등 기회"
    elif rsi_insight['avg'] <= 40:
        rsi_insight_text = f"→ RSI {rsi_insight['avg']:.1f}로 과매도 구간. 스윙 관찰 필요"
    else:
        rsi_insight_text = f"→ RSI {rsi_insight['avg']:.1f}로 안정적"

    disparity_insight = recommendations['disparity_insight']
    disparity_insight_text = ""
    if disparity_insight['avg'] <= 90:
        disparity_insight_text = f"→ 평균 대비 {100-disparity_insight['avg']:.1f}% 저평가. 강한 반등 기회"
    elif disparity_insight['avg'] <= 95:
        disparity_insight_text = f"→ 평균 대비 {100-disparity_insight['avg']:.1f}% 저평가. 반등 가능"
    else:
        disparity_insight_text = f"→ 적정 범위 (평균: {disparity_insight['avg']:.1f}%)"

    volume_insight = recommendations['volume_insight']
    volume_insight_text = ""
    if volume_insight['avg'] >= 150:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 강한 관심"
    elif volume_insight['avg'] >= 120:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 적정"
    else:
        volume_insight_text = f"→ 평균 거래량 {volume_insight['avg']:.1f}%로 보통"

    # 환율 HTML
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

    kospi_change_class = "positive" if indices['kospi_change'] > 0 else "negative"
    kosdaq_change_class = "positive" if indices['kosdaq_change'] > 0 else "negative"

    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 주식 스윙 트레이드 종목 선별</title>
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
            max-width: 1600px;
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

        .subtitle {{
            color: #718096;
            font-size: 1.1em;
            margin-top: 10px;
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
            font-size: 0.85em;
        }}

        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}

        th, td {{
            padding: 12px 8px;
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
                font-size: 0.75em;
            }}

            th, td {{
                padding: 8px 4px;
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
            <h1>📊 한국 주식 스윙 트레이드 종목 선별</h1>
            <p class="subtitle">단기 반등 기회 (3~10일 보유 목표)</p>
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
        <h2>📚 스윙 트레이드 가이드</h2>
        <div class="guide-grid">
            <div class="guide-box">
                <h3>🔵 RSI (20~35 구간)</h3>
                <p class="guide-desc">단기 과매도 반등 신호</p>
                <ul class="guide-list">
                    <li><strong>20~25:</strong> 극단적 과매도 → 강한 반등 기회</li>
                    <li><strong>25~35:</strong> 과매도 구간 → 진입 고려</li>
                    <li><strong>35~45:</strong> 약한 과매도 → 관찰</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>📊 이격도 (80~95%)</h3>
                <p class="guide-desc">20일선 대비 저평가</p>
                <ul class="guide-list">
                    <li><strong>80~90%:</strong> 강한 저평가 → 반등 기대</li>
                    <li><strong>90~95%:</strong> 적정 저평가 → 진입 고려</li>
                    <li><strong>95~100%:</strong> 약한 저평가 → 관찰</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>📈 거래량비율 (150~300%)</h3>
                <p class="guide-desc">관심 집중 신호</p>
                <ul class="guide-list">
                    <li><strong>150~300%:</strong> 적정 관심 → 스윙 적합</li>
                    <li><strong>120~150%:</strong> 보통 → 진입 가능</li>
                    <li><strong>300% 이상:</strong> 과열 → 주의</li>
                </ul>
            </div>
            <div class="guide-box">
                <h3>💰 PBR (0.3~1.0)</h3>
                <p class="guide-desc">건전한 저평가</p>
                <ul class="guide-list">
                    <li><strong>0.3~0.7:</strong> 건전한 저PBR → 안전</li>
                    <li><strong>0.7~1.0:</strong> 적정 → 보통</li>
                    <li><strong>0~0.3:</strong> 저PBR 리스크 → 주의</li>
                </ul>
            </div>
        </div>
    </div>


        <h2 style="margin-top: 40px; color: #2d3748;">🏆 스윙 후보 Top 30</h2>
        <table>
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>현재가</th>
                    <th>전일대비</th>
                    <th>RSI</th>
                    <th>이격도</th>
                    <th>거래량비율(%)</th>
                    <th>PBR</th>
                    <th>점수</th>
                    <th>손절가</th>
                    <th>목표가</th>
                    <th>5일<br/>수익률</th>
                    <th>20일<br/>저점대비</th>
                    <th>위험도</th>
                    <th>위험태그</th>
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
            <p><strong>🔻 최저 RSI:</strong> {rsi_insight['min']:.1f}</p>
            <p><strong>📊 과매도 종목수:</strong> {rsi_insight['count_oversold']}개</p>
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
                    <p class="category-desc">이격도 기준 가장 낮은 종목</p>

        <div class="insight-box">
            <p><strong>📈 Top 30 평균 이격도:</strong> {disparity_insight['avg']:.1f}%</p>
            <p><strong>🔻 최저 이격도:</strong> {disparity_insight['min']:.1f}%</p>
            <p><strong>📊 저평가 종목수:</strong> {disparity_insight['count_undervalued']}개</p>
            <p class="insight-text">{disparity_insight_text}</p>
        </div>

                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>현재가</th>
                                <th>이격도(%)</th>
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
            <p><strong>📊 거래량 급증:</strong> {volume_insight['count_surge']}개</p>
            <p class="insight-text">{volume_insight_text}</p>
        </div>

                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>현재가</th>
                                <th>거래량비율(%)</th>
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
            <p>⚠️ <strong>스윙 트레이드 경고:</strong> 본 시스템은 단기 반등 후보 리스트를 제공하며, 실제 매수/매도 타이밍은 투자자가 결정해야 합니다.</p>
            <p style="margin-top: 10px;">📊 보유 기간: 3~10 영업일 목표 | 손절: -5% | 목표: +10%</p>
            <p style="margin-top: 5px;">📊 데이터 출처: KRX via pykrx | 💱 환율: ExchangeRate-API</p>
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
    print("🚀 한국 주식 스윙 트레이드 시스템 시작 (v2 최적화 버전)")
    print("="*60)

    indices = get_market_indices()
    exchange_data = get_exchange_rates()

    end_date = get_last_trading_date()
    df = scan_all_stocks(end_date)

    recommendations = select_recommendations(df)
    html = generate_html(recommendations, indices, exchange_data)

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
