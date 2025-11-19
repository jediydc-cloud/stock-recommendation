#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
스윙 트레이드 종목 추천 시스템 v3.8 (환경변수 통합 버전)
================================================================================

[주요 변경사항 v3.7 → v3.8]
- API 키: 하드코딩 제거 → 환경변수(GEMINI_API_KEY)만 사용
- Gemini 모델: models/gemini-2.5-flash
- API 키 없을 때: 예외 대신 안내 문구 반환
- 뉴스 링크: 종목명 + 종목코드 기반 검색
- Colab/GitHub 호환: 동일 코드로 양쪽 작동

[환경변수 설정 안내]

- Colab에서:
  1) 런타임 시작 후, 왼쪽의 '환경 변수(Variables)' 메뉴에서 GEMINI_API_KEY를 추가하거나
  2) 또는 첫 셀에서:
     import os
     os.environ["GEMINI_API_KEY"] = input("Gemini API Key 입력: ").strip()

- GitHub에서:
  1) GitHub Repo → Settings → Secrets and variables → Actions → New repository secret
  2) Name: GEMINI_API_KEY, Value: (발급받은 키)
  3) GitHub Actions 워크플로우에서:
     env:
       GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}

이 코드는 GEMINI_API_KEY 환경변수만 존재하면 Colab과 GitHub 양쪽에서 동일하게 작동합니다.

[기능 개요]
- KOSPI + KOSDAQ 전체 종목 분석 (거래대금 5억 이상)
- 6가지 지표 기반 100점 만점 점수 계산
- Top 6: 프리미엄 카드형 (차트 포함)
- Top 7~30: 테이블형
- 지표별 Top 5 (6개 지표)
- 투자자별 추천 (보수/공격 각 8개)
- Gemini AI 종합 분석 (1000자)

[실행 시간]
- 약 18분 (2,650개 종목 → 필터링 후 분석)

================================================================================
"""

import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime, timedelta
import os
import base64
from io import BytesIO
import time
import warnings
warnings.filterwarnings('ignore')
import urllib.parse

# Google Gemini API 설정
import google.generativeai as genai

# API 키 읽기 (Colab Secrets 우선, 환경변수 폴백)
GEMINI_API_KEY = None

# 1순위: Colab Secrets 시도
try:
    from google.colab import userdata
    # swingTrading 이름으로 통합 관리 (Colab + GitHub 동일)
    GEMINI_API_KEY = userdata.get('swingTrading')
    print("=" * 70)
    print("✓ Colab Secrets에서 API 키 로드 완료 (swingTrading)")
    print("=" * 70)
except:
    # 2순위: 환경변수 시도 (GitHub Actions용 - 동일한 이름)
    GEMINI_API_KEY = os.environ.get("swingTrading")
    if GEMINI_API_KEY:
        print("=" * 70)
        print("✓ 환경변수에서 API 키 로드 완료 (swingTrading)")
        print("=" * 70)

# API 키 확인 및 설정
if not GEMINI_API_KEY:
    print("=" * 70)
    print("✗ API 키를 찾을 수 없습니다.")
    print("=" * 70)
    print("\n[해결 방법]")
    print("- Colab: 왼쪽 사이드바 🔑 Secrets에서 'swingTrading' 이름으로 추가")
    print("- GitHub: Settings → Secrets → Actions → GEMINI_API_KEY 추가")
    print("\n⚠️ AI 종합 분석은 스킵되며, 나머지 기능은 정상 작동합니다.\n")
else:
    genai.configure(api_key=GEMINI_API_KEY)

# ============================================================================
# 한글 폰트 설정
# ============================================================================
font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
if os.path.exists(font_path):
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['font.family'] = font_prop.get_name()
    plt.rcParams['axes.unicode_minus'] = False
    print("✓ 한글 폰트 설정 완료: NanumGothic")
else:
    print("⚠️ 한글 폰트를 찾을 수 없습니다. 차트에 한글이 깨질 수 있습니다.")

# ============================================================================
# KOSPI + KOSDAQ 전체 티커 가져오기
# ============================================================================
def get_all_kr_tickers():
    """KOSPI + KOSDAQ 전체 종목 티커 목록 반환"""
    print("\n" + "=" * 70)
    print("📊 KOSPI + KOSDAQ 전체 종목 수집 중...")
    print("=" * 70)
    
    kospi_url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt"
    kosdaq_url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt"
    
    try:
        kospi_df = pd.read_html(kospi_url, encoding='cp949')[0]
        kosdaq_df = pd.read_html(kosdaq_url, encoding='cp949')[0]
        
        kospi_df['ticker'] = kospi_df['종목코드'].apply(lambda x: f"{str(x).zfill(6)}.KS")
        kosdaq_df['ticker'] = kosdaq_df['종목코드'].apply(lambda x: f"{str(x).zfill(6)}.KQ")
        
        kospi_df['market'] = 'KOSPI'
        kosdaq_df['market'] = 'KOSDAQ'
        
        all_stocks = pd.concat([
            kospi_df[['회사명', 'ticker', 'market']],
            kosdaq_df[['회사명', 'ticker', 'market']]
        ], ignore_index=True)
        
        all_stocks.columns = ['name', 'ticker', 'market']
        
        print(f"✓ KOSPI: {len(kospi_df)}개 종목")
        print(f"✓ KOSDAQ: {len(kosdaq_df)}개 종목")
        print(f"✓ 전체: {len(all_stocks)}개 종목")
        
        return all_stocks
    
    except Exception as e:
        print(f"✗ 종목 목록 수집 실패: {e}")
        return pd.DataFrame(columns=['name', 'ticker', 'market'])

# ============================================================================
# 주식 데이터 수집
# ============================================================================
def get_stock_data(ticker, period='3mo'):
    """특정 종목의 주가 데이터 수집"""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        
        if hist.empty or len(hist) < 20:
            return None
        
        info = stock.info
        return {
            'hist': hist,
            'info': info
        }
    except:
        return None

# ============================================================================
# 지표 계산 함수들
# ============================================================================
def calculate_rsi(hist, period=14):
    """RSI 계산 (0~100)"""
    try:
        close = hist['Close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    except:
        return None

def calculate_disparity(hist, period=20):
    """이격도 계산 (현재가 / 이동평균 * 100)"""
    try:
        close = hist['Close'].iloc[-1]
        ma = hist['Close'].rolling(window=period).mean().iloc[-1]
        return (close / ma) * 100
    except:
        return None

def calculate_volume_ratio(hist, period=20):
    """거래량 비율 계산 (최근 거래량 / 평균 거래량 * 100)"""
    try:
        recent_volume = hist['Volume'].iloc[-5:].mean()
        avg_volume = hist['Volume'].iloc[-period:].mean()
        return (recent_volume / avg_volume) * 100
    except:
        return None

def calculate_pbr(info):
    """PBR 추출"""
    try:
        pbr = info.get('priceToBook', None)
        return pbr if pbr and pbr > 0 else None
    except:
        return None

def calculate_5day_return(hist):
    """5일 수익률 계산"""
    try:
        if len(hist) < 6:
            return None
        current_price = hist['Close'].iloc[-1]
        price_5d_ago = hist['Close'].iloc[-6]
        return ((current_price - price_5d_ago) / price_5d_ago) * 100
    except:
        return None

def calculate_rebound_strength(hist):
    """반등 강도 계산 (20일 최저가 대비 현재가 회복률)"""
    try:
        if len(hist) < 20:
            return None
        current_price = hist['Close'].iloc[-1]
        min_price_20d = hist['Close'].iloc[-20:].min()
        max_price_20d = hist['Close'].iloc[-20:].max()
        
        if max_price_20d == min_price_20d:
            return 0
        
        return ((current_price - min_price_20d) / (max_price_20d - min_price_20d)) * 100
    except:
        return None

# ============================================================================
# 점수 계산 (100점 만점)
# ============================================================================
def calculate_score(rsi, disparity, volume_ratio, pbr, return_5d, rebound):
    """
    6가지 지표 기반 점수 계산 (100점 만점)
    
    - RSI: 30점 (과매도 구간에 높은 점수)
    - 이격도: 20점 (저평가 구간에 높은 점수)
    - 거래량: 15점 (거래량 증가에 높은 점수)
    - PBR: 15점 (저PBR에 높은 점수)
    - 5일 수익률: 10점 (상승 추세에 높은 점수)
    - 반등 강도: 10점 (강한 반등에 높은 점수)
    """
    score = 0
    details = {}
    
    # 1. RSI 점수 (30점)
    if rsi is not None:
        if rsi <= 30:
            rsi_score = 30
        elif rsi <= 40:
            rsi_score = 25
        elif rsi <= 50:
            rsi_score = 15
        elif rsi <= 60:
            rsi_score = 10
        else:
            rsi_score = 5
        score += rsi_score
        details['rsi_score'] = rsi_score
    else:
        details['rsi_score'] = 0
    
    # 2. 이격도 점수 (20점)
    if disparity is not None:
        if disparity <= 95:
            disp_score = 20
        elif disparity <= 98:
            disp_score = 15
        elif disparity <= 102:
            disp_score = 10
        elif disparity <= 105:
            disp_score = 5
        else:
            disp_score = 2
        score += disp_score
        details['disp_score'] = disp_score
    else:
        details['disp_score'] = 0
    
    # 3. 거래량 점수 (15점)
    if volume_ratio is not None:
        if volume_ratio >= 150:
            vol_score = 15
        elif volume_ratio >= 120:
            vol_score = 12
        elif volume_ratio >= 100:
            vol_score = 8
        elif volume_ratio >= 80:
            vol_score = 5
        else:
            vol_score = 2
        score += vol_score
        details['vol_score'] = vol_score
    else:
        details['vol_score'] = 0
    
    # 4. PBR 점수 (15점)
    if pbr is not None:
        if pbr <= 0.5:
            pbr_score = 15
        elif pbr <= 1.0:
            pbr_score = 12
        elif pbr <= 1.5:
            pbr_score = 8
        elif pbr <= 2.0:
            pbr_score = 5
        else:
            pbr_score = 2
        score += pbr_score
        details['pbr_score'] = pbr_score
    else:
        details['pbr_score'] = 0
    
    # 5. 5일 수익률 점수 (10점)
    if return_5d is not None:
        if return_5d >= 10:
            ret_score = 10
        elif return_5d >= 5:
            ret_score = 8
        elif return_5d >= 0:
            ret_score = 5
        elif return_5d >= -5:
            ret_score = 3
        else:
            ret_score = 1
        score += ret_score
        details['ret_score'] = ret_score
    else:
        details['ret_score'] = 0
    
    # 6. 반등 강도 점수 (10점)
    if rebound is not None:
        if rebound >= 80:
            reb_score = 10
        elif rebound >= 60:
            reb_score = 8
        elif rebound >= 40:
            reb_score = 5
        elif rebound >= 20:
            reb_score = 3
        else:
            reb_score = 1
        score += reb_score
        details['reb_score'] = reb_score
    else:
        details['reb_score'] = 0
    
    return score, details

# ============================================================================
# 차트 생성 (Base64 인코딩)
# ============================================================================
def create_chart_base64(hist, ticker, name):
    """주가 차트를 생성하고 Base64로 인코딩"""
    try:
        fig, ax = plt.subplots(figsize=(8, 4))
        
        ax.plot(hist.index, hist['Close'], color='#2196F3', linewidth=2)
        ax.fill_between(hist.index, hist['Close'], alpha=0.3, color='#2196F3')
        
        ax.set_title(f"{name} ({ticker})", fontsize=14, fontweight='bold', pad=15)
        ax.set_xlabel('날짜', fontsize=10)
        ax.set_ylabel('종가 (원)', fontsize=10)
        ax.grid(True, alpha=0.3, linestyle='--')
        
        # 최근 20일 데이터만 표시
        if len(hist) > 20:
            recent_hist = hist.iloc[-20:]
            ax.set_xlim(recent_hist.index[0], recent_hist.index[-1])
        
        plt.tight_layout()
        
        # Base64 인코딩
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
        plt.close(fig)
        
        return f"data:image/png;base64,{image_base64}"
    
    except Exception as e:
        print(f"   ✗ 차트 생성 실패 ({name}): {e}")
        plt.close('all')
        return None

# ============================================================================
# 전체 종목 분석
# ============================================================================
def analyze_all_stocks(tickers_df, min_volume=500_000_000):
    """
    전체 종목 분석 및 필터링
    
    Parameters:
    - tickers_df: 종목 정보 DataFrame
    - min_volume: 최소 거래대금 (기본값: 5억)
    
    Returns:
    - 분석 결과 DataFrame
    """
    print("\n" + "=" * 70)
    print("🔍 전체 종목 분석 시작")
    print("=" * 70)
    print(f"분석 대상: {len(tickers_df)}개 종목")
    print(f"필터 조건: 거래대금 {min_volume:,}원 이상")
    print("=" * 70)
    
    results = []
    total = len(tickers_df)
    
    for idx, row in tickers_df.iterrows():
        ticker = row['ticker']
        name = row['name']
        market = row['market']
        
        if (idx + 1) % 100 == 0:
            print(f"진행률: {idx + 1}/{total} ({(idx + 1) / total * 100:.1f}%)")
        
        # 주가 데이터 수집
        data = get_stock_data(ticker)
        if data is None:
            continue
        
        hist = data['hist']
        info = data['info']
        
        # 거래대금 필터링 (최근 5일 평균)
        try:
            recent_volume = hist['Volume'].iloc[-5:].mean()
            recent_price = hist['Close'].iloc[-5:].mean()
            trading_value = recent_volume * recent_price
            
            if trading_value < min_volume:
                continue
        except:
            continue
        
        # 지표 계산
        rsi = calculate_rsi(hist)
        disparity = calculate_disparity(hist)
        volume_ratio = calculate_volume_ratio(hist)
        pbr = calculate_pbr(info)
        return_5d = calculate_5day_return(hist)
        rebound = calculate_rebound_strength(hist)
        
        # 점수 계산
        score, details = calculate_score(rsi, disparity, volume_ratio, pbr, return_5d, rebound)
        
        # 현재가 정보
        current_price = hist['Close'].iloc[-1]
        price_change = hist['Close'].pct_change().iloc[-1] * 100
        
        results.append({
            'ticker': ticker,
            'name': name,
            'market': market,
            'current_price': current_price,
            'price_change': price_change,
            'rsi': rsi,
            'disparity': disparity,
            'volume_ratio': volume_ratio,
            'pbr': pbr,
            'return_5d': return_5d,
            'rebound': rebound,
            'score': score,
            'trading_value': trading_value,
            'hist': hist,
            **details
        })
    
    df = pd.DataFrame(results)
    
    print("\n" + "=" * 70)
    print("✓ 분석 완료")
    print("=" * 70)
    print(f"필터 통과: {len(df)}개 종목")
    print("=" * 70)
    
    return df

# ============================================================================
# 시장 지수 및 환율 정보 수집
# ============================================================================
def get_market_indices():
    """KOSPI, KOSDAQ, 달러/원 환율 정보 수집 (3번 재시도)"""
    print("\n" + "=" * 70)
    print("📈 시장 지수 및 환율 정보 수집 중...")
    print("=" * 70)
    
    data = {}
    max_retries = 3
    
    # KOSPI 지수
    print("\n[1/3] KOSPI 지수 수집 중...")
    for attempt in range(max_retries):
        try:
            kospi = yf.Ticker("^KS11")
            kospi_hist = kospi.history(period="5d")
            if not kospi_hist.empty and len(kospi_hist) >= 2:
                kospi_current = kospi_hist['Close'].iloc[-1]
                kospi_prev = kospi_hist['Close'].iloc[-2]
                kospi_change = ((kospi_current - kospi_prev) / kospi_prev) * 100
                data['kospi'] = {
                    'value': f"{kospi_current:,.2f}",
                    'change': f"{kospi_change:+.2f}%"
                }
                print(f"   ✓ KOSPI: {kospi_current:,.2f} ({kospi_change:+.2f}%)")
                break
        except Exception as e:
            print(f"   ⚠️ KOSPI 수집 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    if 'kospi' not in data:
        print("   → 데이터 부족으로 KOSPI 지수 N/A 처리")
        data['kospi'] = {'value': 'N/A', 'change': 'N/A'}
    
    # KOSDAQ 지수
    print("\n[2/3] KOSDAQ 지수 수집 중...")
    for attempt in range(max_retries):
        try:
            kosdaq = yf.Ticker("^KQ11")
            kosdaq_hist = kosdaq.history(period="5d")
            if not kosdaq_hist.empty and len(kosdaq_hist) >= 2:
                kosdaq_current = kosdaq_hist['Close'].iloc[-1]
                kosdaq_prev = kosdaq_hist['Close'].iloc[-2]
                kosdaq_change = ((kosdaq_current - kosdaq_prev) / kosdaq_prev) * 100
                data['kosdaq'] = {
                    'value': f"{kosdaq_current:,.2f}",
                    'change': f"{kosdaq_change:+.2f}%"
                }
                print(f"   ✓ KOSDAQ: {kosdaq_current:,.2f} ({kosdaq_change:+.2f}%)")
                break
        except Exception as e:
            print(f"   ⚠️ KOSDAQ 수집 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    if 'kosdaq' not in data:
        print("   → 데이터 부족으로 KOSDAQ 지수 N/A 처리")
        data['kosdaq'] = {'value': 'N/A', 'change': 'N/A'}
    
    # 달러/원 환율
    print("\n[3/3] 달러/원 환율 수집 중...")
    for attempt in range(max_retries):
        try:
            usd_krw = yf.Ticker("KRW=X")
            usd_hist = usd_krw.history(period="5d")
            if not usd_hist.empty and len(usd_hist) >= 2:
                usd_current = usd_hist['Close'].iloc[-1]
                usd_prev = usd_hist['Close'].iloc[-2]
                usd_change = ((usd_current - usd_prev) / usd_prev) * 100
                data['usd_krw'] = {
                    'value': f"{usd_current:,.2f}",
                    'change': f"{usd_change:+.2f}%"
                }
                print(f"   ✓ USD/KRW: {usd_current:,.2f} ({usd_change:+.2f}%)")
                break
        except Exception as e:
            print(f"   ⚠️ USD/KRW 수집 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    if 'usd_krw' not in data:
        print("   → 데이터 부족으로 USD/KRW 환율 N/A 처리")
        data['usd_krw'] = {'value': 'N/A', 'change': 'N/A'}
    
    print("\n" + "=" * 70)
    print("✓ 시장 지수 및 환율 정보 수집 완료")
    print("=" * 70)
    
    return data

# ============================================================================
# Gemini AI 종합 분석 생성
# ============================================================================
def generate_gemini_analysis(top_stocks_df, market_data):
    """
    Gemini AI를 사용하여 Top 30 종목에 대한 종합 분석 생성
    
    Parameters:
    - top_stocks_df: Top 30 종목 DataFrame
    - market_data: 시장 지수 및 환율 정보
    
    Returns:
    - 분석 텍스트 (str)
    """
    print("\n" + "=" * 70)
    print("🤖 Gemini AI 종합 분석 생성 중...")
    print("=" * 70)
    
    # API 키 확인
    if not GEMINI_API_KEY:
        return (
            "🤖 AI 분석을 생성할 수 없습니다.\n\n"
            "GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.\n"
            "Colab 또는 GitHub Secrets에 키를 등록한 후 다시 실행해주세요.\n\n"
            "그 전까지는 위 Top 30 종목과 지표별 Top 5를 참고해서 수동으로 판단하시기 바랍니다."
        )
    
    try:
        # Top 10 종목 정보 요약
        top_10_summary = []
        for idx, row in top_stocks_df.head(10).iterrows():
            summary = (
                f"{idx + 1}. {row['name']} ({row['market']})\n"
                f"   - 점수: {row['score']:.1f}점\n"
                f"   - 현재가: {row['current_price']:,.0f}원 ({row['price_change']:+.2f}%)\n"
                f"   - RSI: {row['rsi']:.1f if row['rsi'] else 'N/A'}\n"
                f"   - 이격도: {row['disparity']:.2f if row['disparity'] else 'N/A'}\n"
                f"   - 거래량비율: {row['volume_ratio']:.1f if row['volume_ratio'] else 'N/A'}%\n"
                f"   - PBR: {row['pbr']:.2f if row['pbr'] else 'N/A'}\n"
            )
            top_10_summary.append(summary)
        
        # 시장 상황 요약
        market_summary = (
            f"시장 현황:\n"
            f"- KOSPI: {market_data['kospi']['value']} ({market_data['kospi']['change']})\n"
            f"- KOSDAQ: {market_data['kosdaq']['value']} ({market_data['kosdaq']['change']})\n"
            f"- USD/KRW: {market_data['usd_krw']['value']} ({market_data['usd_krw']['change']})\n"
        )
        
        # Gemini 프롬프트 구성
        prompt = f"""
당신은 한국 주식 시장 전문 애널리스트입니다.

다음은 오늘 기준 스윙 트레이딩 관점에서 선정된 Top 10 종목입니다:

{chr(10).join(top_10_summary)}

{market_summary}

위 데이터를 바탕으로 다음 내용을 포함하여 1000자 이내로 종합 분석을 작성해주세요:

1. 전체 시장 흐름 분석 (KOSPI, KOSDAQ, 환율 고려)
2. Top 10 종목의 공통점과 특징
3. 각 지표별 특징적인 패턴 (RSI, 이격도, 거래량 등)
4. 스윙 트레이딩 관점에서의 투자 전략 제안
5. 주의사항 및 리스크 요인

분석은 객관적이고 구체적으로 작성하되, 투자 권유가 아닌 정보 제공 목적임을 명시해주세요.
"""
        
        # Gemini API 호출
        model = genai.GenerativeModel("models/gemini-2.0-flash-exp")
        response = model.generate_content(prompt)
        
        analysis_text = response.text
        
        print("✓ AI 분석 생성 완료")
        print(f"   길이: {len(analysis_text)}자")
        print("=" * 70)
        
        return analysis_text
    
    except Exception as e:
        print(f"✗ AI 분석 생성 실패: {e}")
        print("=" * 70)
        return (
            "🤖 AI 분석 생성 중 오류가 발생했습니다.\n\n"
            f"오류 내용: {str(e)}\n\n"
            "위 Top 30 종목과 지표별 Top 5를 참고해서 수동으로 판단하시기 바랍니다."
        )

# ============================================================================
# HTML 생성 (프리미엄 디자인)
# ============================================================================
def generate_html(df, market_data, gemini_analysis, output_path):
    """
    분석 결과를 프리미엄 디자인 HTML로 생성
    
    Parameters:
    - df: 전체 분석 결과 DataFrame
    - market_data: 시장 지수 및 환율 정보
    - gemini_analysis: Gemini AI 분석 텍스트
    - output_path: 출력 파일 경로
    """
    print("\n" + "=" * 70)
    print("📄 HTML 보고서 생성 중...")
    print("=" * 70)
    
    # 40점 이상 필터링
    df_filtered = df[df['score'] >= 40].copy()
    df_filtered = df_filtered.sort_values('score', ascending=False).reset_index(drop=True)
    
    print(f"40점 이상 종목: {len(df_filtered)}개")
    
    # Top 30
    top_30 = df_filtered.head(30).copy()
    
    # Top 6 차트 생성
    print("\n차트 생성 중 (Top 6)...")
    chart_data = []
    for idx, row in top_30.head(6).iterrows():
        print(f"  {idx + 1}/6: {row['name']}")
        chart_base64 = create_chart_base64(row['hist'], row['ticker'], row['name'])
        chart_data.append(chart_base64)
    
    # 지표별 Top 5
    top_rsi = df_filtered.nsmallest(5, 'rsi')[['name', 'ticker', 'market', 'rsi', 'score']]
    top_disparity = df_filtered.nsmallest(5, 'disparity')[['name', 'ticker', 'market', 'disparity', 'score']]
    top_volume = df_filtered.nlargest(5, 'volume_ratio')[['name', 'ticker', 'market', 'volume_ratio', 'score']]
    top_pbr = df_filtered[df_filtered['pbr'].notna()].nsmallest(5, 'pbr')[['name', 'ticker', 'market', 'pbr', 'score']]
    top_return = df_filtered.nlargest(5, 'return_5d')[['name', 'ticker', 'market', 'return_5d', 'score']]
    top_rebound = df_filtered.nlargest(5, 'rebound')[['name', 'ticker', 'market', 'rebound', 'score']]
    
    # 투자자별 추천
    conservative = df_filtered.nsmallest(8, 'rsi')[['name', 'ticker', 'market', 'rsi', 'pbr', 'score']]
    aggressive = df_filtered.nlargest(8, 'rebound')[['name', 'ticker', 'market', 'rebound', 'volume_ratio', 'score']]
    
    # 현재 시간
    now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M:%S")
    
    # HTML 생성
    html_content = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스윙 트레이드 종목 추천 - {datetime.now().strftime("%Y.%m.%d")}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Malgun Gothic', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
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
        
        .header .subtitle {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        
        .market-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            padding: 30px 40px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
        }}
        
        .market-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            transition: transform 0.2s;
        }}
        
        .market-card:hover {{
            transform: translateY(-5px);
        }}
        
        .market-card .label {{
            font-size: 0.9em;
            color: #6c757d;
            margin-bottom: 8px;
        }}
        
        .market-card .value {{
            font-size: 1.8em;
            font-weight: 700;
            color: #2c3e50;
        }}
        
        .market-card .change {{
            font-size: 1em;
            font-weight: 600;
            margin-top: 5px;
        }}
        
        .change.positive {{ color: #e74c3c; }}
        .change.negative {{ color: #3498db; }}
        
        .content {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 50px;
        }}
        
        .section-title {{
            font-size: 1.8em;
            font-weight: 700;
            color: #2c3e50;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 3px solid #667eea;
        }}
        
        /* Top 6 카드 스타일 */
        .top-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }}
        
        .stock-card {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            overflow: hidden;
            transition: all 0.3s;
            border: 2px solid #e9ecef;
        }}
        
        .stock-card:hover {{
            transform: translateY(-10px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.15);
        }}
        
        .card-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
        }}
        
        .card-rank {{
            font-size: 3em;
            font-weight: 700;
            opacity: 0.3;
            position: absolute;
            right: 20px;
            top: 10px;
        }}
        
        .card-title {{
            font-size: 1.4em;
            font-weight: 700;
            margin-bottom: 5px;
        }}
        
        .card-subtitle {{
            font-size: 0.9em;
            opacity: 0.8;
        }}
        
        .card-body {{
            padding: 20px;
        }}
        
        .card-chart {{
            width: 100%;
            height: auto;
            margin-bottom: 15px;
            border-radius: 8px;
        }}
        
        .card-metrics {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 15px;
        }}
        
        .metric {{
            background: #f8f9fa;
            padding: 10px;
            border-radius: 8px;
        }}
        
        .metric-label {{
            font-size: 0.85em;
            color: #6c757d;
            margin-bottom: 3px;
        }}
        
        .metric-value {{
            font-size: 1.1em;
            font-weight: 600;
            color: #2c3e50;
        }}
        
        .card-score {{
            text-align: center;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
            margin-bottom: 15px;
        }}
        
        .card-score .score-label {{
            font-size: 0.9em;
            opacity: 0.8;
            margin-bottom: 5px;
        }}
        
        .card-score .score-value {{
            font-size: 2.5em;
            font-weight: 700;
        }}
        
        .card-actions {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }}
        
        .btn {{
            padding: 12px 20px;
            border: none;
            border-radius: 8px;
            font-size: 0.95em;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            text-align: center;
            transition: all 0.2s;
        }}
        
        .btn-primary {{
            background: #667eea;
            color: white;
        }}
        
        .btn-primary:hover {{
            background: #5568d3;
        }}
        
        .btn-secondary {{
            background: #6c757d;
            color: white;
        }}
        
        .btn-secondary:hover {{
            background: #5a6268;
        }}
        
        /* Top 7-30 테이블 스타일 */
        .table-container {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        th {{
            padding: 15px 10px;
            text-align: center;
            font-weight: 600;
            font-size: 0.9em;
        }}
        
        tbody tr {{
            border-bottom: 1px solid #e9ecef;
            transition: background 0.2s;
        }}
        
        tbody tr:hover {{
            background: #f8f9fa;
        }}
        
        td {{
            padding: 12px 10px;
            text-align: center;
            font-size: 0.9em;
        }}
        
        .rank-cell {{
            font-weight: 700;
            font-size: 1.1em;
            color: #667eea;
        }}
        
        .name-cell {{
            font-weight: 600;
            color: #2c3e50;
        }}
        
        .score-cell {{
            font-weight: 700;
            font-size: 1.1em;
            color: #764ba2;
        }}
        
        /* 지표별 Top 5 스타일 */
        .indicators-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 25px;
        }}
        
        .indicator-card {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        
        .indicator-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            font-weight: 600;
        }}
        
        .indicator-body {{
            padding: 15px;
        }}
        
        .indicator-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            margin-bottom: 8px;
            background: #f8f9fa;
            border-radius: 8px;
            transition: all 0.2s;
        }}
        
        .indicator-item:hover {{
            background: #e9ecef;
            transform: translateX(5px);
        }}
        
        .indicator-item .name {{
            font-weight: 600;
            color: #2c3e50;
        }}
        
        .indicator-item .value {{
            font-weight: 700;
            color: #667eea;
        }}
        
        /* 투자자별 추천 스타일 */
        .investor-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 25px;
        }}
        
        .investor-card {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        
        .investor-header {{
            padding: 20px;
            color: white;
            font-weight: 600;
            font-size: 1.2em;
        }}
        
        .conservative-header {{
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        }}
        
        .aggressive-header {{
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
        }}
        
        .investor-body {{
            padding: 20px;
        }}
        
        .investor-item {{
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 1fr;
            gap: 10px;
            padding: 12px;
            margin-bottom: 8px;
            background: #f8f9fa;
            border-radius: 8px;
            align-items: center;
        }}
        
        .investor-item:hover {{
            background: #e9ecef;
        }}
        
        /* AI 분석 스타일 */
        .ai-analysis {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            overflow: hidden;
            margin-top: 30px;
        }}
        
        .ai-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            font-weight: 600;
            font-size: 1.3em;
        }}
        
        .ai-body {{
            padding: 30px;
            line-height: 1.8;
            white-space: pre-wrap;
            font-size: 1.05em;
            color: #2c3e50;
        }}
        
        .footer {{
            text-align: center;
            padding: 30px;
            background: #f8f9fa;
            color: #6c757d;
            font-size: 0.9em;
            border-top: 2px solid #e9ecef;
        }}
        
        @media (max-width: 768px) {{
            .header h1 {{ font-size: 1.8em; }}
            .top-cards {{ grid-template-columns: 1fr; }}
            .indicators-grid {{ grid-template-columns: 1fr; }}
            .investor-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 헤더 -->
        <div class="header">
            <h1>📈 스윙 트레이드 종목 추천</h1>
            <div class="subtitle">AI 기반 종목 분석 시스템 v3.8 | {now}</div>
        </div>
        
        <!-- 시장 정보 -->
        <div class="market-info">
            <div class="market-card">
                <div class="label">KOSPI 지수</div>
                <div class="value">{market_data['kospi']['value']}</div>
                <div class="change {'positive' if '+' in market_data['kospi']['change'] else 'negative'}">{market_data['kospi']['change']}</div>
            </div>
            <div class="market-card">
                <div class="label">KOSDAQ 지수</div>
                <div class="value">{market_data['kosdaq']['value']}</div>
                <div class="change {'positive' if '+' in market_data['kosdaq']['change'] else 'negative'}">{market_data['kosdaq']['change']}</div>
            </div>
            <div class="market-card">
                <div class="label">USD/KRW 환율</div>
                <div class="value">{market_data['usd_krw']['value']}</div>
                <div class="change {'positive' if '+' in market_data['usd_krw']['change'] else 'negative'}">{market_data['usd_krw']['change']}</div>
            </div>
        </div>
        
        <!-- 메인 컨텐츠 -->
        <div class="content">
            <!-- Top 6 카드 -->
            <div class="section">
                <div class="section-title">🏆 Top 6 추천 종목</div>
                <div class="top-cards">
"""
    
    # Top 6 카드 생성
    for idx, row in top_30.head(6).iterrows():
        code = row['ticker'].split('.')[0]
        chart_img = chart_data[idx] if chart_data[idx] else ""
        
        # 뉴스 링크 (종목명 + 종목코드)
        search_query = f"{row['name']} {code}"
        news_url = f"https://search.naver.com/search.naver?where=news&query={urllib.parse.quote(search_query)}"
        
        html_content += f"""
                    <div class="stock-card">
                        <div class="card-header" style="position: relative;">
                            <div class="card-rank">#{idx + 1}</div>
                            <div class="card-title">{row['name']}</div>
                            <div class="card-subtitle">{row['market']} | {code}</div>
                        </div>
                        <div class="card-body">
                            {"<img src='" + chart_img + "' class='card-chart' />" if chart_img else ""}
                            <div class="card-score">
                                <div class="score-label">종합 점수</div>
                                <div class="score-value">{row['score']:.1f}점</div>
                            </div>
                            <div class="card-metrics">
                                <div class="metric">
                                    <div class="metric-label">현재가</div>
                                    <div class="metric-value">{row['current_price']:,.0f}원</div>
                                </div>
                                <div class="metric">
                                    <div class="metric-label">등락률</div>
                                    <div class="metric-value" style="color: {'#e74c3c' if row['price_change'] > 0 else '#3498db'};">{row['price_change']:+.2f}%</div>
                                </div>
                                <div class="metric">
                                    <div class="metric-label">RSI</div>
                                    <div class="metric-value">{row['rsi']:.1f if row['rsi'] else 'N/A'}</div>
                                </div>
                                <div class="metric">
                                    <div class="metric-label">이격도</div>
                                    <div class="metric-value">{row['disparity']:.2f if row['disparity'] else 'N/A'}</div>
                                </div>
                                <div class="metric">
                                    <div class="metric-label">거래량비율</div>
                                    <div class="metric-value">{row['volume_ratio']:.1f if row['volume_ratio'] else 'N/A'}%</div>
                                </div>
                                <div class="metric">
                                    <div class="metric-label">PBR</div>
                                    <div class="metric-value">{row['pbr']:.2f if row['pbr'] else 'N/A'}</div>
                                </div>
                            </div>
                            <div class="card-actions">
                                <a href="https://finance.naver.com/item/main.naver?code={code}" target="_blank" class="btn btn-primary">종목 상세</a>
                                <a href="{news_url}" target="_blank" class="btn btn-secondary">뉴스</a>
                            </div>
                        </div>
                    </div>
"""
    
    html_content += """
                </div>
            </div>
            
            <!-- Top 7-30 테이블 -->
            <div class="section">
                <div class="section-title">📊 Top 7-30 추천 종목</div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>순위</th>
                                <th>종목명</th>
                                <th>시장</th>
                                <th>현재가</th>
                                <th>등락률</th>
                                <th>RSI</th>
                                <th>이격도</th>
                                <th>거래량비율</th>
                                <th>PBR</th>
                                <th>점수</th>
                                <th>액션</th>
                            </tr>
                        </thead>
                        <tbody>
"""
    
    # Top 7-30 테이블 생성
    for idx, row in top_30.iloc[6:].iterrows():
        code = row['ticker'].split('.')[0]
        
        # 뉴스 링크 (종목명 + 종목코드)
        search_query = f"{row['name']} {code}"
        news_url = f"https://search.naver.com/search.naver?where=news&query={urllib.parse.quote(search_query)}"
        
        html_content += f"""
                            <tr>
                                <td class="rank-cell">{idx + 1}</td>
                                <td class="name-cell">{row['name']}</td>
                                <td>{row['market']}</td>
                                <td>{row['current_price']:,.0f}원</td>
                                <td style="color: {'#e74c3c' if row['price_change'] > 0 else '#3498db'}; font-weight: 600;">{row['price_change']:+.2f}%</td>
                                <td>{row['rsi']:.1f if row['rsi'] else 'N/A'}</td>
                                <td>{row['disparity']:.2f if row['disparity'] else 'N/A'}</td>
                                <td>{row['volume_ratio']:.1f if row['volume_ratio'] else 'N/A'}%</td>
                                <td>{row['pbr']:.2f if row['pbr'] else 'N/A'}</td>
                                <td class="score-cell">{row['score']:.1f}</td>
                                <td>
                                    <a href="https://finance.naver.com/item/main.naver?code={code}" target="_blank" class="btn btn-primary" style="padding: 8px 12px; font-size: 0.85em; display: inline-block; margin-right: 5px;">상세</a>
                                    <a href="{news_url}" target="_blank" class="btn btn-secondary" style="padding: 8px 12px; font-size: 0.85em; display: inline-block;">뉴스</a>
                                </td>
                            </tr>
"""
    
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>
            
            <!-- 지표별 Top 5 -->
            <div class="section">
                <div class="section-title">🎯 지표별 Top 5 종목</div>
                <div class="indicators-grid">
"""
    
    # RSI Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">RSI 최저 Top 5 (과매도)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_rsi.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['rsi']:.1f if row['rsi'] else 'N/A'}</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # 이격도 Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">이격도 최저 Top 5 (저평가)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_disparity.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['disparity']:.2f if row['disparity'] else 'N/A'}</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # 거래량 Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">거래량 비율 Top 5 (거래 활발)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_volume.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['volume_ratio']:.1f if row['volume_ratio'] else 'N/A'}%</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # PBR Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">PBR 최저 Top 5 (저평가)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_pbr.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['pbr']:.2f if row['pbr'] else 'N/A'}</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # 5일 수익률 Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">5일 수익률 Top 5 (상승세)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_return.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['return_5d']:+.2f if row['return_5d'] else 'N/A'}%</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # 반등 강도 Top 5
    html_content += """
                    <div class="indicator-card">
                        <div class="indicator-header">반등 강도 Top 5 (회복세)</div>
                        <div class="indicator-body">
"""
    for idx, row in top_rebound.iterrows():
        html_content += f"""
                            <div class="indicator-item">
                                <span class="name">{row['name']} ({row['market']})</span>
                                <span class="value">{row['rebound']:.1f if row['rebound'] else 'N/A'}%</span>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    html_content += """
                </div>
            </div>
            
            <!-- 투자자별 추천 -->
            <div class="section">
                <div class="section-title">👥 투자자별 맞춤 추천</div>
                <div class="investor-grid">
"""
    
    # 보수적 투자자
    html_content += """
                    <div class="investor-card">
                        <div class="investor-header conservative-header">🛡️ 보수적 투자자 추천 (안정성 중심)</div>
                        <div class="investor-body">
"""
    for idx, row in conservative.iterrows():
        html_content += f"""
                            <div class="investor-item">
                                <div class="name">{row['name']} ({row['market']})</div>
                                <div>RSI: {row['rsi']:.1f if row['rsi'] else 'N/A'}</div>
                                <div>PBR: {row['pbr']:.2f if row['pbr'] else 'N/A'}</div>
                                <div style="font-weight: 700; color: #667eea;">{row['score']:.1f}점</div>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
"""
    
    # 공격적 투자자
    html_content += """
                    <div class="investor-card">
                        <div class="investor-header aggressive-header">⚡ 공격적 투자자 추천 (수익성 중심)</div>
                        <div class="investor-body">
"""
    for idx, row in aggressive.iterrows():
        html_content += f"""
                            <div class="investor-item">
                                <div class="name">{row['name']} ({row['market']})</div>
                                <div>반등: {row['rebound']:.1f if row['rebound'] else 'N/A'}%</div>
                                <div>거래량: {row['volume_ratio']:.1f if row['volume_ratio'] else 'N/A'}%</div>
                                <div style="font-weight: 700; color: #e74c3c;">{row['score']:.1f}점</div>
                            </div>
"""
    html_content += """
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Gemini AI 종합 분석 -->
            <div class="section">
                <div class="section-title">🤖 AI 종합 분석 (Gemini 2.0 Flash)</div>
                <div class="ai-analysis">
                    <div class="ai-header">📊 시장 및 Top 30 종목 종합 분석</div>
                    <div class="ai-body">{gemini_analysis}</div>
                </div>
            </div>
        </div>
        
        <!-- 푸터 -->
        <div class="footer">
            <p>⚠️ 본 분석은 참고용이며, 투자 판단은 본인의 책임입니다.</p>
            <p>데이터 출처: Yahoo Finance | AI 분석: Google Gemini 2.0 Flash</p>
            <p>생성 시간: {now}</p>
        </div>
    </div>
</body>
</html>
"""
    
    # HTML 파일 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("=" * 70)
    print(f"✓ HTML 보고서 생성 완료: {output_path}")
    print("=" * 70)

# ============================================================================
# 메인 실행
# ============================================================================
def main():
    """메인 실행 함수"""
    print("\n" + "=" * 70)
    print("🚀 스윙 트레이드 종목 추천 시스템 v3.8 시작")
    print("=" * 70)
    
    start_time = time.time()
    
    # 1. 전체 종목 수집
    all_tickers = get_all_kr_tickers()
    if all_tickers.empty:
        print("✗ 종목 수집 실패. 프로그램을 종료합니다.")
        return
    
    # 2. 전체 종목 분석
    df = analyze_all_stocks(all_tickers, min_volume=500_000_000)
    if df.empty:
        print("✗ 분석 결과가 없습니다. 프로그램을 종료합니다.")
        return
    
    # 3. 시장 지수 및 환율 정보 수집
    market_data = get_market_indices()
    
    # 4. Gemini AI 종합 분석 생성
    gemini_analysis = generate_gemini_analysis(df.head(30), market_data)
    
    # 5. HTML 보고서 생성
    output_filename = f"stock_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    
    # Colab과 GitHub Actions 환경 자동 감지
    if os.path.exists('/content/drive/MyDrive'):
        # Colab 환경: Google Drive에 저장
        output_path = f"/content/drive/MyDrive/{output_filename}"
    else:
        # GitHub Actions 환경: 현재 디렉토리에 저장
        output_path = f"./{output_filename}"
    
    generate_html(df, market_data, gemini_analysis, output_path)
    
    # 실행 시간 출력
    elapsed_time = time.time() - start_time
    minutes = int(elapsed_time // 60)
    seconds = int(elapsed_time % 60)
    
    print("\n" + "=" * 70)
    print("✓ 모든 작업 완료!")
    print("=" * 70)
    print(f"실행 시간: {minutes}분 {seconds}초")
    print(f"결과 파일: {output_path}")
    print("=" * 70)

if __name__ == "__main__":
    main()
