#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 스윙 트레이드 스캐너 v3.3 - 완전판
- 환율 정보 확장 (USD/JPY/EUR)
- 손절가/목표가 컬럼 추가
- 단기 모멘텀 분석 (5일 수익률, 20일 저점대비)
- 위험 태그 기능 복원
- 뉴스 링크 표시 오류 수정
"""

from pykrx import stock
from datetime import datetime, timedelta
import pandas as pd
import time
import os
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# 차트 생성용
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# 환율 API용
import requests

# ==================== 설정 ====================
TARGET_COUNT = 30
MIN_TRADING_VALUE = 500_000_000  # 5억원
LOOKBACK_DAYS = 100
SPARKLINE_DAYS = 60  # 스파크라인용 데이터 기간

# 점수 체계 (100점 만점)
SCORE_WEIGHTS = {
    'rsi': 30,      # RSI 가중치
    'disparity': 25, # 이격도 가중치
    'volume': 25,    # 거래량 가중치
    'pbr': 20        # PBR 가중치
}

# ==================== 유틸리티 함수 ====================

def get_business_days(end_date: datetime, days: int) -> datetime:
    """영업일 기준으로 days만큼 이전 날짜 반환"""
    current = end_date
    count = 0
    while count < days:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 월~금
            count += 1
    return current

def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    """RSI 계산"""
    if len(prices) < period + 1:
        return 50.0
    
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50.0

def calculate_disparity(prices: pd.Series, period: int = 20) -> float:
    """이격도 계산 (현재가 / 이동평균 * 100)"""
    if len(prices) < period:
        return 100.0
    
    ma = prices.rolling(window=period).mean()
    disparity = (prices.iloc[-1] / ma.iloc[-1]) * 100
    return disparity if not pd.isna(disparity) else 100.0

def calculate_volume_ratio(volumes: pd.Series, period: int = 20) -> float:
    """거래량 비율 계산 (최근 5일 평균 / 20일 평균 * 100)"""
    if len(volumes) < period:
        return 100.0
    
    recent_avg = volumes.iloc[-5:].mean()
    period_avg = volumes.rolling(window=period).mean().iloc[-1]
    
    if period_avg == 0:
        return 100.0
    
    ratio = (recent_avg / period_avg) * 100
    return ratio if not pd.isna(ratio) else 100.0

# ==================== 점수 계산 함수 ====================

def calculate_rsi_score(rsi: float) -> float:
    """RSI 점수 계산 (30점 만점)"""
    if 20 <= rsi <= 25:
        return SCORE_WEIGHTS['rsi']
    elif 25 < rsi <= 35:
        return SCORE_WEIGHTS['rsi'] * 0.67
    elif 35 < rsi <= 45:
        return SCORE_WEIGHTS['rsi'] * 0.33
    else:
        return 0

def calculate_disparity_score(disparity: float) -> float:
    """이격도 점수 계산 (25점 만점)"""
    if 80 <= disparity <= 90:
        return SCORE_WEIGHTS['disparity']
    elif 90 < disparity <= 95:
        return SCORE_WEIGHTS['disparity'] * 0.8
    elif 95 < disparity <= 100:
        return SCORE_WEIGHTS['disparity'] * 0.4
    else:
        return 0

def calculate_volume_score(volume_ratio: float) -> float:
    """거래량 점수 계산 (25점 만점)"""
    if 150 <= volume_ratio <= 300:
        return SCORE_WEIGHTS['volume']
    elif 120 <= volume_ratio < 150:
        return SCORE_WEIGHTS['volume'] * 0.8
    elif volume_ratio >= 300:
        return SCORE_WEIGHTS['volume'] * 0.6
    else:
        return 0

def calculate_pbr_score(pbr: float) -> float:
    """PBR 점수 계산 (20점 만점)"""
    if pbr <= 0:
        return 0
    elif 0.3 <= pbr <= 0.7:
        return SCORE_WEIGHTS['pbr']
    elif 0.7 < pbr <= 1.0:
        return SCORE_WEIGHTS['pbr'] * 0.75
    elif 0 < pbr < 0.3:
        return SCORE_WEIGHTS['pbr'] * 0.5
    else:
        return 0

# ==================== 위험도 평가 ====================

def assess_risk(ticker: str, market_cap: int, current_price: int, 
                df_recent: pd.DataFrame) -> str:
    """위험도 평가: 낮음/중간/높음"""
    risk_factors = 0
    
    # 1. 시가총액 (500억 미만: +1)
    if market_cap < 50_000_000_000:
        risk_factors += 1
    
    # 2. 주가 (5천원 미만: +1)
    if current_price < 5000:
        risk_factors += 1
    
    # 3. 최근 급등 이력 (20일 내 20% 이상 상승: +1)
    if len(df_recent) >= 20:
        max_price = df_recent['종가'].iloc[-20:].max()
        min_price = df_recent['종가'].iloc[-20:].min()
        if min_price > 0 and (max_price - min_price) / min_price > 0.2:
            risk_factors += 1
    
    if risk_factors == 0:
        return "낮음"
    elif risk_factors == 1:
        return "중간"
    else:
        return "높음"

### NEW: 위험 태그 생성 함수
def generate_risk_tags(market_cap: int, current_price: int, df_recent: pd.DataFrame) -> str:
    """위험 태그 생성: 소형주, 저가주, 최근급등"""
    tags = []
    
    # 1. 시가총액 < 500억
    if market_cap < 50_000_000_000:
        tags.append("소형주")
    
    # 2. 현재가 < 5,000원
    if current_price < 5000:
        tags.append("저가주")
    
    # 3. 최근 20일 내 20% 이상 급등
    if len(df_recent) >= 20:
        max_price = df_recent['종가'].iloc[-20:].max()
        min_price = df_recent['종가'].iloc[-20:].min()
        if min_price > 0 and (max_price - min_price) / min_price > 0.2:
            tags.append("최근급등")
    
    return ", ".join(tags) if tags else "-"

# ==================== 종목 분석 ====================

def analyze_stock(ticker: str, date_str: str, market_caps: Dict[str, int], 
                  pbr_data: Dict[str, float]) -> Optional[Dict]:
    """개별 종목 분석"""
    try:
        # 날짜 계산
        end_date = datetime.strptime(date_str, '%Y%m%d')
        start_date = get_business_days(end_date, LOOKBACK_DAYS)
        start_str = start_date.strftime('%Y%m%d')
        
        # 가격 데이터 조회
        df = stock.get_market_ohlcv_by_date(start_str, date_str, ticker)
        
        if df is None or len(df) < 30:
            return None
        
        # 시가총액 및 PBR (미리 조회한 데이터 사용)
        market_cap = market_caps.get(ticker, 0)
        pbr = pbr_data.get(ticker, 0)
        
        if market_cap == 0:
            return None
        
        # 거래대금 필터링
        recent_20_days = df.iloc[-20:]
        avg_trading_value = (recent_20_days['종가'] * recent_20_days['거래량']).mean()
        
        if avg_trading_value < MIN_TRADING_VALUE:
            return None
        
        # 기술적 지표 계산
        closes = df['종가']
        volumes = df['거래량']
        
        rsi = calculate_rsi(closes)
        disparity = calculate_disparity(closes)
        volume_ratio = calculate_volume_ratio(volumes)
        
        # 점수 계산
        rsi_score = calculate_rsi_score(rsi)
        disparity_score = calculate_disparity_score(disparity)
        volume_score = calculate_volume_score(volume_ratio)
        pbr_score = calculate_pbr_score(pbr)
        
        total_score = rsi_score + disparity_score + volume_score + pbr_score
        
        # 최소 점수 필터 (40점 이상만)
        if total_score < 40:
            return None
        
        # 종목명 조회
        stock_name = stock.get_market_ticker_name(ticker)
        
        # 현재가
        current_price = int(closes.iloc[-1])
        
        # 위험도 평가
        risk_level = assess_risk(ticker, market_cap, current_price, df)
        
        ### NEW: 위험 태그 생성
        risk_tags = generate_risk_tags(market_cap, current_price, df)
        
        ### NEW: 손절가/목표가 계산
        stop_loss_price = int(current_price * 0.95)
        target_price = int(current_price * 1.10)
        
        ### NEW: 5일 수익률 계산
        ret_5d = 0.0
        if len(closes) >= 6:
            price_5d_ago = closes.iloc[-6]
            if price_5d_ago > 0:
                ret_5d = ((current_price / price_5d_ago) - 1) * 100
        
        ### NEW: 20일 저점 대비 상승률
        from_20d_low = 0.0
        if len(closes) >= 20:
            min_20d = closes.iloc[-20:].min()
            if min_20d > 0:
                from_20d_low = ((current_price / min_20d) - 1) * 100
        
        return {
            'ticker': ticker,
            'name': stock_name,
            'score': round(total_score, 1),
            'rsi': round(rsi, 1),
            'disparity': round(disparity, 1),
            'volume_ratio': round(volume_ratio, 1),
            'pbr': round(pbr, 2) if pbr > 0 else 'N/A',
            'market_cap': market_cap,
            'current_price': current_price,
            'risk_level': risk_level,
            'risk_tags': risk_tags,  ### NEW
            'stop_loss_price': stop_loss_price,  ### NEW
            'target_price': target_price,  ### NEW
            'ret_5d': round(ret_5d, 1),  ### NEW
            'from_20d_low': round(from_20d_low, 1),  ### NEW
            'rsi_score': round(rsi_score, 1),
            'disparity_score': round(disparity_score, 1),
            'volume_score': round(volume_score, 1),
            'pbr_score': round(pbr_score, 1)
        }
        
    except Exception as e:
        return None

# ==================== 시장 정보 조회 ====================

def get_market_info(date_str: str) -> Dict:
    """시장 지수 및 환율 정보 조회 (USD/JPY/EUR)"""
    info = {
        'kospi': {'close': 0, 'change': 0},
        'kosdaq': {'close': 0, 'change': 0},
        'usd_krw': 0,
        'jpy_krw': 0,  ### NEW
        'eur_krw': 0   ### NEW
    }
    
    try:
        # KOSPI 지수
        df_kospi = stock.get_index_ohlcv_by_date(
            (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d'),
            date_str,
            "1001"
        )
        if df_kospi is not None and len(df_kospi) >= 2:
            info['kospi']['close'] = df_kospi['종가'].iloc[-1]
            info['kospi']['change'] = ((df_kospi['종가'].iloc[-1] - df_kospi['종가'].iloc[-2]) / df_kospi['종가'].iloc[-2]) * 100
        
        # KOSDAQ 지수
        df_kosdaq = stock.get_index_ohlcv_by_date(
            (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d'),
            date_str,
            "2001"
        )
        if df_kosdaq is not None and len(df_kosdaq) >= 2:
            info['kosdaq']['close'] = df_kosdaq['종가'].iloc[-1]
            info['kosdaq']['change'] = ((df_kosdaq['종가'].iloc[-1] - df_kosdaq['종가'].iloc[-2]) / df_kosdaq['종가'].iloc[-2]) * 100
        
        ### NEW: 환율 정보 확장 (USD/JPY/EUR)
        try:
            response = requests.get('https://api.exchangerate-api.com/v4/latest/USD', timeout=5)
            if response.status_code == 200:
                data = response.json()
                rates = data['rates']
                
                # USD/KRW
                info['usd_krw'] = rates.get('KRW', 1300)
                
                # JPY/KRW = USD/KRW ÷ USD/JPY
                usd_jpy = rates.get('JPY', 145)
                info['jpy_krw'] = info['usd_krw'] / usd_jpy
                
                # EUR/KRW = USD/KRW × EUR/USD
                eur_usd = 1 / rates.get('EUR', 0.92)
                info['eur_krw'] = info['usd_krw'] * eur_usd
        except:
            # Fallback 값
            info['usd_krw'] = 1300
            info['jpy_krw'] = 9
            info['eur_krw'] = 1500
        
    except Exception as e:
        print(f"  경고: 시장 정보 조회 실패 - {e}")
    
    return info

# ==================== 업종 정보 조회 ====================

def get_sector_info(tickers: List[str], date_str: str) -> Dict[str, str]:
    """종목별 업종 정보 조회 (KOSPI/KOSDAQ 구분)"""
    sector_map = {}
    try:
        for market in ["KOSPI", "KOSDAQ"]:
            market_tickers = stock.get_market_ticker_list(date_str, market=market)
            for ticker in market_tickers:
                if ticker in tickers:
                    sector_map[ticker] = market
    except:
        pass
    return sector_map

# ==================== Top30 인사이트 계산 ====================

def calculate_top30_insights(recommendations: List[Dict], sector_map: Dict[str, str]) -> Dict:
    """Top30 종목의 종합 인사이트 계산"""
    insights = {
        'sector_distribution': {},
        'market_cap_distribution': {'1조원 이상': 0, '5천억~1조원': 0, '5천억 미만': 0},
        'pbr_stats': {'평균': 0.0, '최소': 0.0, '최대': 0.0},
        'rsi_stats': {'평균': 0.0, '최소': 0.0, '최대': 0.0},
        'disparity_stats': {'평균': 0.0, '최소': 0.0, '최대': 0.0},
        'risk_distribution': {'낮음': 0, '중간': 0, '높음': 0},
        'summary': ''
    }
    
    if not recommendations:
        insights['summary'] = "분석된 추천 종목이 없습니다."
        return insights
    
    # 업종 분포
    for rec in recommendations:
        sector = sector_map.get(rec['ticker'], '기타')
        insights['sector_distribution'][sector] = insights['sector_distribution'].get(sector, 0) + 1
    
    # 시가총액 분포
    for rec in recommendations:
        cap = rec['market_cap']
        if cap >= 1_000_000_000_000:
            insights['market_cap_distribution']['1조원 이상'] += 1
        elif cap >= 500_000_000_000:
            insights['market_cap_distribution']['5천억~1조원'] += 1
        else:
            insights['market_cap_distribution']['5천억 미만'] += 1
    
    # PBR 통계
    pbr_values = []
    for rec in recommendations:
        pbr = rec['pbr']
        if pbr != 'N/A':
            try:
                pbr_values.append(float(pbr))
            except:
                pass
    
    if pbr_values:
        insights['pbr_stats']['평균'] = round(sum(pbr_values) / len(pbr_values), 2)
        insights['pbr_stats']['최소'] = round(min(pbr_values), 2)
        insights['pbr_stats']['최대'] = round(max(pbr_values), 2)
    
    # RSI 통계
    rsi_values = [float(rec['rsi']) for rec in recommendations]
    if rsi_values:
        insights['rsi_stats']['평균'] = round(sum(rsi_values) / len(rsi_values), 1)
        insights['rsi_stats']['최소'] = round(min(rsi_values), 1)
        insights['rsi_stats']['최대'] = round(max(rsi_values), 1)
    
    # 이격도 통계
    disparity_values = [float(rec['disparity']) for rec in recommendations]
    if disparity_values:
        insights['disparity_stats']['평균'] = round(sum(disparity_values) / len(disparity_values), 1)
        insights['disparity_stats']['최소'] = round(min(disparity_values), 1)
        insights['disparity_stats']['최대'] = round(max(disparity_values), 1)
    
    # 위험도 분포
    for rec in recommendations:
        risk = rec['risk_level']
        insights['risk_distribution'][risk] += 1
    
    # 한 줄 요약
    kospi_count = insights['sector_distribution'].get('KOSPI', 0)
    kosdaq_count = insights['sector_distribution'].get('KOSDAQ', 0)
    avg_rsi = insights['rsi_stats']['평균']
    risk_high_pct = round(insights['risk_distribution']['높음'] / len(recommendations) * 100, 0)
    
    if avg_rsi <= 30:
        rsi_desc = "과매도 구간"
    elif avg_rsi <= 40:
        rsi_desc = "저평가 구간"
    else:
        rsi_desc = "중립 구간"
    
    market_desc = "KOSPI 중심" if kospi_count > kosdaq_count else "KOSDAQ 중심" if kosdaq_count > kospi_count else "균형형"
    
    insights['summary'] = f"오늘 Top30은 {market_desc}이며, 평균 RSI {avg_rsi:.1f}의 {rsi_desc}입니다. (공격적 종목 비중 {risk_high_pct:.0f}%)"
    
    return insights

# ==================== 추천 종목 선정 ====================

def select_recommendations(all_results: List[Dict], sector_map: Dict[str, str]) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], Dict]:
    """추천 종목 선정 및 카테고리 분류"""
    sorted_results = sorted(all_results, key=lambda x: x['score'], reverse=True)
    top_30 = sorted_results[:TARGET_COUNT]
    insights = calculate_top30_insights(top_30, sector_map)
    
    high_score = [r for r in top_30 if r['score'] >= 70]
    medium_score = [r for r in top_30 if 60 <= r['score'] < 70]
    conservative = [r for r in top_30 if r['score'] < 60 and r['risk_level'] == '낮음']
    aggressive = [r for r in top_30 if r['score'] < 60 and r['risk_level'] != '낮음']
    
    return top_30, high_score, medium_score, conservative, aggressive, insights

# ==================== 스파크라인 차트 생성 ====================

def create_sparkline_chart(ticker: str, date_str: str, output_dir: str = 'charts') -> bool:
    """Top30 종목의 가격 스파크라인 차트 생성"""
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        end_date = datetime.strptime(date_str, '%Y%m%d')
        start_date = get_business_days(end_date, SPARKLINE_DAYS)
        start_str = start_date.strftime('%Y%m%d')
        
        df = stock.get_market_ohlcv_by_date(start_str, date_str, ticker)
        
        if df is None or len(df) < 10:
            return False
        
        closes = df['종가'].values
        
        fig, ax = plt.subplots(figsize=(2.2, 0.7), dpi=100)
        ax.plot(closes, linewidth=1.5, color='#667eea')
        ax.axis('off')
        ax.set_xlim(0, len(closes) - 1)
        ax.margins(0, 0.1)
        plt.tight_layout(pad=0)
        
        output_path = os.path.join(output_dir, f'{ticker}_spark.png')
        plt.savefig(output_path, bbox_inches='tight', pad_inches=0, 
                    facecolor='white', edgecolor='none', dpi=100)
        plt.close(fig)
        
        return True
        
    except:
        return False

def generate_sparklines_for_top30(top_30: List[Dict], date_str: str) -> Dict[str, bool]:
    """Top30 전체 스파크라인 생성"""
    print(f"\n  스파크라인 차트 생성 중 (Top30): ", end="", flush=True)
    results = {}
    
    for idx, stock_data in enumerate(top_30):
        ticker = stock_data['ticker']
        success = create_sparkline_chart(ticker, date_str)
        results[ticker] = success
        
        if (idx + 1) % 10 == 0:
            print(f"{idx + 1}/30 ", end="", flush=True)
    
    success_count = sum(1 for v in results.values() if v)
    print(f"\n  ✓ {success_count}/{len(top_30)}개 차트 생성 완료")
    
    return results

# ==================== HTML 생성 ====================

def generate_html(top_30: List[Dict], high_score: List[Dict], medium_score: List[Dict],
                  conservative: List[Dict], aggressive: List[Dict], 
                  insights: Dict, stats: Dict, date_str: str, 
                  market_info: Dict, sparkline_results: Dict[str, bool]) -> str:
    """HTML 리포트 생성 - v3.3 완전판"""
    
    current_date = datetime.strptime(date_str, '%Y%m%d').strftime('%Y년 %m월 %d일')
    
    avg_score = stats['avg_score']
    if avg_score >= 65:
        market_signal = "🟢 강한 스윙 기회"
        market_desc = "고점수 종목이 많아 단기 반등 가능성이 높은 시장"
    elif avg_score >= 55:
        market_signal = "🟡 적당한 스윙 기회"
        market_desc = "선별적 접근이 필요한 시장"
    else:
        market_signal = "🔴 약한 스윙 기회"
        market_desc = "관망이 유리한 시장"
    
    kospi_count = insights['sector_distribution'].get('KOSPI', 0)
    kosdaq_count = insights['sector_distribution'].get('KOSDAQ', 0)
    market_cap_large = insights['market_cap_distribution']['1조원 이상']
    market_cap_mid = insights['market_cap_distribution']['5천억~1조원']
    market_cap_small = insights['market_cap_distribution']['5천억 미만']
    risk_low = insights['risk_distribution']['낮음']
    risk_mid = insights['risk_distribution']['중간']
    risk_high = insights['risk_distribution']['높음']
    risk_high_pct = round(risk_high / len(top_30) * 100, 0) if top_30 else 0
    
    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스윙 트레이드 스캐너 - {current_date}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1600px;
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
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        
        .header p {{
            font-size: 1.2em;
            opacity: 0.9;
        }}
        
        .market-info {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid rgba(255,255,255,0.3);
            flex-wrap: wrap;
        }}
        
        .market-info-item {{
            text-align: center;
            min-width: 120px;
        }}
        
        .market-info-label {{
            font-size: 0.9em;
            opacity: 0.8;
            margin-bottom: 5px;
        }}
        
        .market-info-value {{
            font-size: 1.3em;
            font-weight: bold;
        }}
        
        .stats-container {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            padding: 40px;
            background: #f8f9fa;
        }}
        
        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.3s ease;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }}
        
        .stat-label {{
            font-size: 0.9em;
            color: #666;
            margin-bottom: 8px;
        }}
        
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}
        
        .market-signal {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 30px 40px;
            margin: 0 40px 30px 40px;
            border-radius: 15px;
            text-align: center;
        }}
        
        .market-signal h2 {{
            font-size: 1.8em;
            margin-bottom: 10px;
        }}
        
        .market-signal p {{
            font-size: 1.1em;
            opacity: 0.95;
        }}
        
        .insight-box {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            margin: 0 40px 30px 40px;
            border-radius: 15px;
            box-shadow: 0 6px 12px rgba(0,0,0,0.2);
        }}
        
        .insight-box h2 {{
            font-size: 1.8em;
            margin-bottom: 15px;
            border-bottom: 2px solid rgba(255,255,255,0.3);
            padding-bottom: 10px;
        }}
        
        .insight-summary {{
            font-size: 1.15em;
            font-weight: 600;
            margin-bottom: 20px;
            padding: 15px;
            background: rgba(255,255,255,0.15);
            border-radius: 10px;
            border-left: 4px solid white;
        }}
        
        .insight-box ul {{
            list-style: none;
            padding: 0;
        }}
        
        .insight-box li {{
            padding: 8px 0;
            font-size: 1.05em;
            line-height: 1.6;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        
        .insight-box li:last-child {{
            border-bottom: none;
        }}
        
        .insight-box strong {{
            color: #fff;
            font-weight: 600;
        }}
        
        .section {{
            padding: 40px;
        }}
        
        .section-title {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 3px solid #667eea;
        }}
        
        .table-container {{
            overflow-x: auto;
            margin-bottom: 30px;
        }}
        
        table {{
            width: 100%;
            min-width: 1800px;
            border-collapse: collapse;
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-radius: 10px;
            overflow: hidden;
            font-size: 0.85em;
        }}
        
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        th {{
            padding: 12px 8px;
            text-align: center;
            font-weight: 600;
            font-size: 0.9em;
            white-space: nowrap;
        }}
        
        td {{
            padding: 10px 8px;
            text-align: center;
            border-bottom: 1px solid #eee;
        }}
        
        tbody tr:hover {{
            background: #f8f9fa;
        }}
        
        .score-high {{
            background: #d4edda;
            color: #155724;
            font-weight: bold;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .score-medium {{
            background: #fff3cd;
            color: #856404;
            font-weight: bold;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .score-low {{
            background: #f8d7da;
            color: #721c24;
            font-weight: bold;
            padding: 5px 10px;
            border-radius: 5px;
        }}
        
        .risk-low {{
            color: #28a745;
            font-weight: bold;
        }}
        
        .risk-medium {{
            color: #ffc107;
            font-weight: bold;
        }}
        
        .risk-high {{
            color: #dc3545;
            font-weight: bold;
        }}
        
        .news-link {{
            color: #667eea;
            text-decoration: none;
            font-weight: 600;
            padding: 5px 10px;
            border: 2px solid #667eea;
            border-radius: 5px;
            transition: all 0.3s ease;
            display: inline-block;
            white-space: nowrap;
        }}
        
        .news-link:hover {{
            background: #667eea;
            color: white;
        }}
        
        .sparkline-img {{
            max-width: 180px;
            height: auto;
            vertical-align: middle;
        }}
        
        .no-chart {{
            color: #999;
            font-size: 0.85em;
        }}
        
        .risk-tags {{
            font-size: 0.8em;
            color: #e53e3e;
        }}
        
        .guide {{
            background: #f8f9fa;
            padding: 30px;
            border-radius: 15px;
            margin-top: 20px;
        }}
        
        .guide h3 {{
            color: #667eea;
            margin-bottom: 15px;
        }}
        
        .guide ul {{
            list-style-position: inside;
            color: #555;
        }}
        
        .guide li {{
            margin-bottom: 8px;
        }}
        
        .risk-criteria {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 20px;
            margin-top: 20px;
            border-radius: 5px;
        }}
        
        .risk-criteria h4 {{
            color: #856404;
            margin-bottom: 10px;
        }}
        
        .risk-criteria ul {{
            color: #856404;
        }}
        
        .footer {{
            background: #333;
            color: white;
            text-align: center;
            padding: 20px;
            margin-top: 40px;
        }}
        
        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8em;
            }}
            
            .stats-container {{
                grid-template-columns: 1fr;
                padding: 20px;
            }}
            
            .section {{
                padding: 20px;
            }}
            
            table {{
                font-size: 0.75em;
            }}
            
            th, td {{
                padding: 6px 4px;
            }}
            
            .sparkline-img {{
                max-width: 120px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 스윙 트레이드 스캐너</h1>
            <p>{current_date} 기준</p>
            <p style="font-size: 0.9em; margin-top: 10px;">보유기간 3~10일 | 목표 수익 5~15% | 손절 -5%</p>
            
            <div class="market-info">
                <div class="market-info-item">
                    <div class="market-info-label">KOSPI</div>
                    <div class="market-info-value">{market_info['kospi']['close']:.2f} <span style="font-size:0.8em">({market_info['kospi']['change']:+.2f}%)</span></div>
                </div>
                <div class="market-info-item">
                    <div class="market-info-label">KOSDAQ</div>
                    <div class="market-info-value">{market_info['kosdaq']['close']:.2f} <span style="font-size:0.8em">({market_info['kosdaq']['change']:+.2f}%)</span></div>
                </div>
                <div class="market-info-item">
                    <div class="market-info-label">USD/KRW</div>
                    <div class="market-info-value">{market_info['usd_krw']:.2f}원</div>
                </div>
                <div class="market-info-item">
                    <div class="market-info-label">JPY/KRW</div>
                    <div class="market-info-value">{market_info['jpy_krw']:.2f}원</div>
                </div>
                <div class="market-info-item">
                    <div class="market-info-label">EUR/KRW</div>
                    <div class="market-info-value">{market_info['eur_krw']:.2f}원</div>
                </div>
            </div>
        </div>
        
        <div class="stats-container">
            <div class="stat-card">
                <div class="stat-label">분석 종목 수</div>
                <div class="stat-value">{stats['total_scanned']:,}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">분석 성공</div>
                <div class="stat-value">{stats['analyzed']:,}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Top 30 평균 점수</div>
                <div class="stat-value">{stats['avg_score']:.1f}점</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">70점 이상</div>
                <div class="stat-value">{stats['score_70_plus']}개</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">60점 이상</div>
                <div class="stat-value">{stats['score_60_plus']}개</div>
            </div>
        </div>
        
        <div class="market-signal">
            <h2>{market_signal}</h2>
            <p>{market_desc}</p>
        </div>
        
        <div class="insight-box">
            <h2>📊 오늘의 Top30 인사이트</h2>
            <div class="insight-summary">
                {insights['summary']}
            </div>
            <ul>
                <li><strong>🏢 시장 분포:</strong> KOSPI {kospi_count}개, KOSDAQ {kosdaq_count}개</li>
                <li><strong>💰 시가총액 구간:</strong> 대형주(1조↑) {market_cap_large}개 | 중형주(5천억~1조) {market_cap_mid}개 | 소형주(5천억↓) {market_cap_small}개</li>
                <li><strong>📉 평균 지표:</strong> PBR {insights['pbr_stats']['평균']:.2f} | RSI {insights['rsi_stats']['평균']:.1f} | 이격도 {insights['disparity_stats']['평균']:.1f}%</li>
                <li><strong>📊 RSI 범위:</strong> 최소 {insights['rsi_stats']['최소']:.1f} ~ 최대 {insights['rsi_stats']['최대']:.1f}</li>
                <li><strong>📈 이격도 범위:</strong> 최소 {insights['disparity_stats']['최소']:.1f}% ~ 최대 {insights['disparity_stats']['최대']:.1f}%</li>
                <li><strong>⚠️ 위험도 분포:</strong> 낮음 {risk_low}개 / 중간 {risk_mid}개 / 높음 {risk_high}개 (공격적 종목 비중 {risk_high_pct:.0f}%)</li>
            </ul>
        </div>
        
        <div class="section">
            <h2 class="section-title">🏆 Top 30 추천 종목</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>순위</th>
                            <th>종목명</th>
                            <th>코드</th>
                            <th>점수</th>
                            <th>현재가</th>
                            <th>손절가<br/>(-5%)</th>
                            <th>목표가<br/>(+10%)</th>
                            <th>RSI</th>
                            <th>이격도</th>
                            <th>거래량<br/>비율</th>
                            <th>PBR</th>
                            <th>5일<br/>수익률</th>
                            <th>20일<br/>저점대비</th>
                            <th>시총</th>
                            <th>위험도</th>
                            <th>위험<br/>태그</th>
                            <th>가격추세</th>
                            <th>뉴스</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    # Top 30 테이블 생성
    for idx, stock_data in enumerate(top_30, 1):
        score_class = 'score-high' if stock_data['score'] >= 70 else 'score-medium' if stock_data['score'] >= 60 else 'score-low'
        risk_class = f"risk-{stock_data['risk_level'].replace('낮음', 'low').replace('중간', 'medium').replace('높음', 'high')}"
        market_cap_display = f"{stock_data['market_cap'] / 100000000:.0f}억"
        
        news_url = f"https://finance.naver.com/item/news_news.naver?code={stock_data['ticker']}"
        
        ticker = stock_data['ticker']
        if sparkline_results.get(ticker, False):
            sparkline_html = f'<img src="charts/{ticker}_spark.png" alt="차트" class="sparkline-img">'
        else:
            sparkline_html = '<span class="no-chart">-</span>'
        
        ### NEW: 5일 수익률, 20일 저점대비 색상
        ret_5d_color = '#48bb78' if stock_data['ret_5d'] >= 0 else '#e53e3e'
        from_20d_low_color = '#48bb78' if stock_data['from_20d_low'] >= 0 else '#e53e3e'
        
        html += f"""
                        <tr>
                            <td>{idx}</td>
                            <td><strong>{stock_data['name']}</strong></td>
                            <td>{stock_data['ticker']}</td>
                            <td><span class="{score_class}">{stock_data['score']}</span></td>
                            <td>{stock_data['current_price']:,}원</td>
                            <td style="color:#e53e3e;">{stock_data['stop_loss_price']:,}원</td>
                            <td style="color:#48bb78;">{stock_data['target_price']:,}원</td>
                            <td>{stock_data['rsi']}</td>
                            <td>{stock_data['disparity']}%</td>
                            <td>{stock_data['volume_ratio']}%</td>
                            <td>{stock_data['pbr']}</td>
                            <td style="color:{ret_5d_color};">{stock_data['ret_5d']:+.1f}%</td>
                            <td style="color:{from_20d_low_color};">{stock_data['from_20d_low']:+.1f}%</td>
                            <td>{market_cap_display}</td>
                            <td><span class="{risk_class}">{stock_data['risk_level']}</span></td>
                            <td class="risk-tags">{stock_data['risk_tags']}</td>
                            <td>{sparkline_html}</td>
                            <td><a href="{news_url}" target="_blank" class="news-link">뉴스</a></td>
                        </tr>
        """
    
    html += """
                    </tbody>
                </table>
            </div>
        </div>
    """
    
    # 카테고리별 추천 섹션들
    categories = [
        ("🌟 최고 점수 종목 (70점 이상)", high_score),
        ("⭐ 우수 종목 (60~69점)", medium_score),
        ("🛡️ 보수적 선택 (안정형)", conservative),
        ("🚀 공격적 선택 (고수익형)", aggressive)
    ]
    
    for title, stocks in categories:
        if stocks:
            html += f"""
        <div class="section">
            <h2 class="section-title">{title}</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>종목명</th>
                            <th>코드</th>
                            <th>점수</th>
                            <th>현재가</th>
                            <th>손절가</th>
                            <th>목표가</th>
                            <th>RSI</th>
                            <th>이격도</th>
                            <th>거래량</th>
                            <th>PBR</th>
                            <th>5일수익률</th>
                            <th>20일저점</th>
                            <th>위험도</th>
                            <th>위험태그</th>
                            <th>가격추세</th>
                            <th>뉴스</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for stock_data in stocks:
                score_class = 'score-high' if stock_data['score'] >= 70 else 'score-medium' if stock_data['score'] >= 60 else 'score-low'
                risk_class = f"risk-{stock_data['risk_level'].replace('낮음', 'low').replace('중간', 'medium').replace('높음', 'high')}"
                news_url = f"https://finance.naver.com/item/news_news.naver?code={stock_data['ticker']}"
                
                ticker = stock_data['ticker']
                if sparkline_results.get(ticker, False):
                    sparkline_html = f'<img src="charts/{ticker}_spark.png" alt="차트" class="sparkline-img">'
                else:
                    sparkline_html = '<span class="no-chart">-</span>'
                
                ret_5d_color = '#48bb78' if stock_data['ret_5d'] >= 0 else '#e53e3e'
                from_20d_low_color = '#48bb78' if stock_data['from_20d_low'] >= 0 else '#e53e3e'
                
                html += f"""
                        <tr>
                            <td><strong>{stock_data['name']}</strong></td>
                            <td>{stock_data['ticker']}</td>
                            <td><span class="{score_class}">{stock_data['score']}</span></td>
                            <td>{stock_data['current_price']:,}원</td>
                            <td style="color:#e53e3e;">{stock_data['stop_loss_price']:,}원</td>
                            <td style="color:#48bb78;">{stock_data['target_price']:,}원</td>
                            <td>{stock_data['rsi']}</td>
                            <td>{stock_data['disparity']}%</td>
                            <td>{stock_data['volume_ratio']}%</td>
                            <td>{stock_data['pbr']}</td>
                            <td style="color:{ret_5d_color};">{stock_data['ret_5d']:+.1f}%</td>
                            <td style="color:{from_20d_low_color};">{stock_data['from_20d_low']:+.1f}%</td>
                            <td><span class="{risk_class}">{stock_data['risk_level']}</span></td>
                            <td class="risk-tags">{stock_data['risk_tags']}</td>
                            <td>{sparkline_html}</td>
                            <td><a href="{news_url}" target="_blank" class="news-link">뉴스</a></td>
                        </tr>
                """
            
            html += """
                    </tbody>
                </table>
            </div>
        </div>
            """
    
    # 투자 가이드
    html += """
        <div class="section">
            <div class="guide">
                <h3>💡 투자 가이드</h3>
                <ul>
                    <li><strong>진입 시점:</strong> 오전 장 시작 후 30분~1시간 뒤 추세 확인 후 진입</li>
                    <li><strong>목표가 설정:</strong> 1차 목표 +10%, 최종 목표 +15%</li>
                    <li><strong>손절라인:</strong> -5% 엄수 (예외 없음)</li>
                    <li><strong>보유기간:</strong> 3~10 영업일 (목표가 도달 시 조기 청산)</li>
                    <li><strong>분할 매수:</strong> 50% 진입 → 추가 하락 시 30% 추가 → 마지막 20% 여유 자금</li>
                    <li><strong>고점수 종목 우선:</strong> 70점 이상 종목 우선 배분, 60점 이상까지 분산</li>
                    <li><strong>위험도 관리:</strong> '높음' 등급은 소액만 배분 (전체 포트폴리오의 20% 이내)</li>
                </ul>
                
                <div class="risk-criteria">
                    <h4>⚠️ 위험도 산출 기준</h4>
                    <p style="margin-bottom: 10px; color: #856404;">각 종목은 다음 3가지 요소를 평가하여 위험도를 산출합니다:</p>
                    <ul>
                        <li><strong>시가총액:</strong> 500억원 미만 → 리스크 팩터 +1</li>
                        <li><strong>현재가:</strong> 5,000원 미만 → 리스크 팩터 +1</li>
                        <li><strong>급등 이력:</strong> 최근 20일 내 20% 이상 변동 → 리스크 팩터 +1</li>
                    </ul>
                    <p style="margin-top: 10px; color: #856404;">
                        <strong>위험도 등급:</strong><br>
                        • 낮음 (0개) - 안정적 대형주<br>
                        • 중간 (1개) - 소형주 또는 변동성 있음<br>
                        • 높음 (2개 이상) - 고위험 고수익 종목
                    </p>
                    <p style="margin-top: 10px; color: #856404;">
                        <strong>위험 태그:</strong> 소형주, 저가주, 최근급등 등 구체적 리스크 요인 표시
                    </p>
                </div>
                
                <h3 style="margin-top: 20px;">⚠️ 주의사항</h3>
                <ul>
                    <li>본 리포트는 참고용이며, 투자 결정은 본인의 판단과 책임입니다</li>
                    <li>시장 상황에 따라 전략을 유연하게 조정하세요</li>
                    <li>반드시 손절라인을 지켜주세요</li>
                    <li>과도한 레버리지나 집중 투자를 피하세요</li>
                    <li>가격 추세 차트는 최근 60영업일 데이터 기준입니다</li>
                </ul>
            </div>
        </div>
        
        <div class="footer">
            <p>© 2024 스윙 트레이드 스캐너 v3.3 | 매일 오전 8시 업데이트</p>
            <p style="font-size: 0.9em; margin-top: 5px;">Data: pykrx (한국거래소) | Exchange Rate: exchangerate-api.com</p>
        </div>
    </div>
</body>
</html>
    """
    
    return html

# ==================== 메인 실행 ====================

def main():
    """메인 실행 함수"""
    print("=" * 80)
    print("스윙 트레이드 스캐너 v3.3 - 실행 시작")
    print("=" * 80)
    
    start_time = time.time()
    
    today = datetime.now()
    if today.weekday() >= 5:
        days_to_subtract = today.weekday() - 4
        today = today - timedelta(days=days_to_subtract)
    
    date_str = today.strftime('%Y%m%d')
    print(f"기준일: {today.strftime('%Y년 %m월 %d일')}")
    print("-" * 80)
    
    # 1단계: 시장 지수 및 환율 조회
    print("\n[1단계] 시장 지수 및 환율 조회 중...")
    market_info = get_market_info(date_str)
    print(f"✓ KOSPI: {market_info['kospi']['close']:.2f} ({market_info['kospi']['change']:+.2f}%)")
    print(f"✓ KOSDAQ: {market_info['kosdaq']['close']:.2f} ({market_info['kosdaq']['change']:+.2f}%)")
    print(f"✓ USD/KRW: {market_info['usd_krw']:.2f}원")
    print(f"✓ JPY/KRW: {market_info['jpy_krw']:.2f}원")
    print(f"✓ EUR/KRW: {market_info['eur_krw']:.2f}원")
    
    # 2단계: 전체 종목 리스트 조회
    print("\n[2단계] 전체 종목 리스트 조회 중...")
    all_tickers = []
    for market in ["KOSPI", "KOSDAQ"]:
        tickers = stock.get_market_ticker_list(date_str, market=market)
        all_tickers.extend(tickers)
    print(f"✓ 총 {len(all_tickers)}개 종목 발견")
    
    # 3단계: 시가총액 일괄 조회
    print("\n[3단계] 시가총액 일괄 조회 중...")
    market_caps = {}
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df_cap = stock.get_market_cap(date_str, market=market)
            if df_cap is not None and not df_cap.empty:
                market_caps.update(df_cap['시가총액'].to_dict())
        except:
            pass
    print(f"✓ {len(market_caps)}개 종목 시가총액 조회 완료")
    
    # 4단계: PBR 일괄 조회
    print("\n[4단계] PBR 데이터 일괄 조회 중...")
    pbr_data = {}
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df_fundamental = stock.get_market_fundamental(date_str, market=market)
            if df_fundamental is not None and not df_fundamental.empty:
                pbr_data.update(df_fundamental['PBR'].to_dict())
        except:
            pass
    print(f"✓ {len(pbr_data)}개 종목 PBR 조회 완료")
    
    # 5단계: 업종 정보 조회
    print("\n[5단계] 업종 정보 조회 중...")
    sector_map = get_sector_info(all_tickers, date_str)
    print(f"✓ {len(sector_map)}개 종목 업종 정보 조회 완료")
    
    # 6단계: 개별 종목 분석
    print(f"\n[6단계] 개별 종목 분석 중 (총 {len(all_tickers)}개)...")
    print("진행률: ", end="", flush=True)
    
    all_results = []
    analyzed = 0
    failed = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 100 == 0 and idx > 0:
            print(f"{idx}/{len(all_tickers)} ", end="", flush=True)
        
        result = analyze_stock(ticker, date_str, market_caps, pbr_data)
        if result:
            all_results.append(result)
            analyzed += 1
        else:
            failed += 1
    
    print(f"\n✓ 분석 완료: 성공 {analyzed}개, 필터링 {failed}개")
    
    # 7단계: 추천 종목 선정
    print("\n[7단계] Top 30 선정 및 인사이트 계산 중...")
    top_30, high_score, medium_score, conservative, aggressive, insights = select_recommendations(all_results, sector_map)
    
    avg_score = sum(r['score'] for r in top_30) / len(top_30) if top_30 else 0
    score_70_plus = len([r for r in all_results if r['score'] >= 70])
    score_60_plus = len([r for r in all_results if r['score'] >= 60])
    
    stats = {
        'total_scanned': len(all_tickers),
        'analyzed': analyzed,
        'avg_score': avg_score,
        'score_70_plus': score_70_plus,
        'score_60_plus': score_60_plus
    }
    
    print(f"✓ Top 30 평균 점수: {avg_score:.1f}점")
    print(f"✓ 70점 이상: {score_70_plus}개")
    print(f"✓ 60점 이상: {score_60_plus}개")
    
    # 8단계: 스파크라인 차트 생성
    print("\n[8단계] 가격 스파크라인 차트 생성 중...")
    sparkline_results = generate_sparklines_for_top30(top_30, date_str)
    
    # 9단계: HTML 생성
    print("\n[9단계] HTML 리포트 생성 중...")
    html_content = generate_html(top_30, high_score, medium_score, conservative, aggressive, 
                                   insights, stats, date_str, market_info, sparkline_results)
    
    output_file = "index.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✓ 리포트 저장 완료: {output_file}")
    
    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print(f"✅ 전체 실행 완료 (소요 시간: {elapsed/60:.1f}분)")
    print("=" * 80)
    print(f"\n📁 생성된 파일:")
    print(f"  - index.html (메인 리포트)")
    print(f"  - charts/ 폴더 ({len([v for v in sparkline_results.values() if v])}개 차트)")

if __name__ == "__main__":
    main()
