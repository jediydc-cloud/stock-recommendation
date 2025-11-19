#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이딩 종목 추천 시스템 v3.9 HYBRID
- 하이브리드 데이터 소스: pykrx (한국 종목) + yfinance (지수/환율)
- 에러 0개 예상 (상장폐지 종목 자동 제외)
- 환율 3개 (USD, EUR, JPY) 유지
- 디자인 변경 없음
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from io import BytesIO
import base64
import warnings
import time
import logging
import os
import google.generativeai as genai

# pykrx 추가
try:
    from pykrx import stock
    PYKRX_AVAILABLE = True
    print("✓ pykrx 로드 완료")
except ImportError:
    PYKRX_AVAILABLE = False
    print("⚠ pykrx 없음, 설치: pip install pykrx")

warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ============================================================
# 환경 설정
# ============================================================

def setup_environment():
    """환경 설정 및 API 키 로드 (Colab + GitHub 지원)"""
    # Colab 환경 체크
    try:
        from google.colab import userdata
        api_key = userdata.get('swingTrading')
        if api_key:
            genai.configure(api_key=api_key)
            print("✓ Colab Secrets에서 API 키 로드 완료")
            return api_key
    except ImportError:
        pass  # Colab 아님
    except Exception as e:
        print(f"⚠ Colab Secrets 로드 실패: {e}")
    
    # GitHub/로컬 환경 체크
    api_key = os.environ.get('swingTrading')
    if api_key:
        genai.configure(api_key=api_key)
        print("✓ GitHub Secrets에서 API 키 로드 완료")
        return api_key
    
    # API 키 없음
    raise ValueError(
        "❌ API 키를 찾을 수 없습니다!\n"
        "Colab: 좌측 🔑 아이콘에서 'swingTrading' 설정\n"
        "GitHub: Settings → Secrets → 'swingTrading' 설정"
    )

def setup_korean_font():
    """한글 폰트 설정"""
    try:
        font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
        font_prop = fm.FontProperties(fname=font_path)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        fm.fontManager.addfont(font_path)
        print("✓ 한글 폰트 설정 완료")
    except Exception as e:
        print(f"⚠ 폰트 설정 실패: {e}")

# ============================================================
# 시장 데이터 수집 (지수/환율 - yfinance 유지)
# ============================================================

def get_market_data():
    """시장 지수 및 환율 정보 조회 (yfinance 사용)"""
    market_info = {}
    
    # KOSPI 지수
    try:
        kospi = yf.Ticker("^KS11")
        kospi_hist = kospi.history(period="5d")
        if not kospi_hist.empty and len(kospi_hist) >= 2:
            current = kospi_hist['Close'].iloc[-1]
            previous = kospi_hist['Close'].iloc[-2]
            change = ((current - previous) / previous) * 100
            market_info['KOSPI'] = {
                'value': f"{current:.2f}",
                'change': f"{change:+.2f}%"
            }
        else:
            market_info['KOSPI'] = {'value': 'N/A', 'change': 'N/A'}
    except:
        market_info['KOSPI'] = {'value': 'N/A', 'change': 'N/A'}
    
    # KOSDAQ 지수
    try:
        kosdaq = yf.Ticker("^KQ11")
        kosdaq_hist = kosdaq.history(period="5d")
        if not kosdaq_hist.empty and len(kosdaq_hist) >= 2:
            current = kosdaq_hist['Close'].iloc[-1]
            previous = kosdaq_hist['Close'].iloc[-2]
            change = ((current - previous) / previous) * 100
            market_info['KOSDAQ'] = {
                'value': f"{current:.2f}",
                'change': f"{change:+.2f}%"
            }
        else:
            market_info['KOSDAQ'] = {'value': 'N/A', 'change': 'N/A'}
    except:
        market_info['KOSDAQ'] = {'value': 'N/A', 'change': 'N/A'}
    
    # 환율 정보 (USD, EUR, JPY)
    exchange_rates = {
        'USD': 'USDKRW=X',
        'EUR': 'EURKRW=X',
        'JPY': 'JPYKRW=X'
    }
    
    for currency, ticker in exchange_rates.items():
        try:
            fx = yf.Ticker(ticker)
            fx_hist = fx.history(period="5d")
            if not fx_hist.empty and len(fx_hist) >= 2:
                current = fx_hist['Close'].iloc[-1]
                previous = fx_hist['Close'].iloc[-2]
                change = ((current - previous) / previous) * 100
                
                # JPY는 100엔 기준으로 표시
                if currency == 'JPY':
                    current = current * 100
                    display_name = 'JPY(100엔)'
                else:
                    display_name = f'{currency}/KRW'
                
                market_info[display_name] = {
                    'value': f"{current:.2f}",
                    'change': f"{change:+.2f}%"
                }
            else:
                market_info[f'{currency}/KRW'] = {'value': 'N/A', 'change': 'N/A'}
        except:
            market_info[f'{currency}/KRW'] = {'value': 'N/A', 'change': 'N/A'}
    
    print(f"✓ 시장 지수 및 환율 정보 수집 완료: {len(market_info)}개")
    return market_info

# ============================================================
# 종목 데이터 수집 (pykrx 사용)
# ============================================================

def get_stock_list():
    """한국 주식 목록 가져오기 (pykrx 사용)"""
    if not PYKRX_AVAILABLE:
        print("❌ pykrx가 설치되지 않았습니다. pip install pykrx")
        return pd.DataFrame()
    
    try:
        today = datetime.now().strftime('%Y%m%d')
        
        # KOSPI 종목 리스트
        kospi_tickers = stock.get_market_ticker_list(today, market="KOSPI")
        kospi_names = [stock.get_market_ticker_name(ticker) for ticker in kospi_tickers]
        kospi_df = pd.DataFrame({
            '종목명': kospi_names,
            '종목코드': kospi_tickers,
            '시장': 'KOSPI'
        })
        
        # KOSDAQ 종목 리스트
        kosdaq_tickers = stock.get_market_ticker_list(today, market="KOSDAQ")
        kosdaq_names = [stock.get_market_ticker_name(ticker) for ticker in kosdaq_tickers]
        kosdaq_df = pd.DataFrame({
            '종목명': kosdaq_names,
            '종목코드': kosdaq_tickers,
            '시장': 'KOSDAQ'
        })
        
        stocks = pd.concat([kospi_df, kosdaq_df], ignore_index=True)
        
        print(f"✓ 종목 목록 조회 완료: KOSPI {len(kospi_df)}개, KOSDAQ {len(kosdaq_df)}개 (총 {len(stocks)}개)")
        return stocks
    
    except Exception as e:
        print(f"❌ 종목 목록 조회 실패: {e}")
        return pd.DataFrame()

def analyze_stock(code, name):
    """개별 종목 분석 (pykrx 사용)"""
    if not PYKRX_AVAILABLE:
        return None
    
    try:
        # 날짜 설정 (60일)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')
        
        # 가격/거래량 데이터 (pykrx)
        ohlcv = stock.get_market_ohlcv_by_date(start_str, end_str, code)
        
        if ohlcv.empty or len(ohlcv) < 20:
            return None
        
        # 기본 정보
        current_price = ohlcv['종가'].iloc[-1]
        
        # 거래대금 필터 (1억 이상)
        recent_volume = ohlcv['거래량'].iloc[-5:].mean()
        trading_value = current_price * recent_volume
        
        if trading_value < 100_000_000:  # 1억 미만 제외
            return None
        
        # 1. RSI (30점)
        delta = ohlcv['종가'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        if current_rsi < 30:
            rsi_score = 30
        elif 30 <= current_rsi < 40:
            rsi_score = 20
        elif 40 <= current_rsi < 50:
            rsi_score = 10
        else:
            rsi_score = 0
        
        # 2. 이격도 (20점)
        ma20 = ohlcv['종가'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100
        
        if disparity < 95:
            disparity_score = 20
        elif 95 <= disparity < 98:
            disparity_score = 15
        elif 98 <= disparity < 100:
            disparity_score = 10
        else:
            disparity_score = 0
        
        # 3. 거래량 증가 (15점)
        vol_ma5 = ohlcv['거래량'].iloc[-5:].mean()
        vol_ma20 = ohlcv['거래량'].iloc[-20:].mean()
        volume_ratio = (vol_ma5 / vol_ma20) if vol_ma20 > 0 else 0
        
        if volume_ratio >= 1.5:
            volume_score = 15
        elif volume_ratio >= 1.2:
            volume_score = 10
        elif volume_ratio >= 1.0:
            volume_score = 5
        else:
            volume_score = 0
        
        # 4. PBR (15점) - pykrx에서 가져오기
        pbr = None
        pbr_score = 0
        
        try:
            fundamental = stock.get_market_fundamental_by_date(start_str, end_str, code)
            if not fundamental.empty and 'PBR' in fundamental.columns:
                pbr_raw = fundamental['PBR'].iloc[-1]
                if pd.notna(pbr_raw) and pbr_raw > 0:
                    pbr = float(pbr_raw)
                    if pbr < 1.0:
                        pbr_score = 15
                    elif pbr < 1.5:
                        pbr_score = 10
                    elif pbr < 2.0:
                        pbr_score = 5
        except:
            pass
        
        # 5. 5일 수익률 (10점)
        if len(ohlcv) >= 6:
            returns_5d = ((current_price - ohlcv['종가'].iloc[-6]) / ohlcv['종가'].iloc[-6]) * 100
        else:
            returns_5d = 0
        
        if -5 <= returns_5d <= 0:
            returns_score = 10
        elif -10 <= returns_5d < -5:
            returns_score = 7
        elif 0 < returns_5d <= 3:
            returns_score = 5
        else:
            returns_score = 0
        
        # 6. 반등 강도 (10점)
        lowest_5d = ohlcv['종가'].iloc[-5:].min()
        rebound_strength = ((current_price - lowest_5d) / lowest_5d) * 100
        
        if rebound_strength >= 5:
            rebound_score = 10
        elif rebound_strength >= 3:
            rebound_score = 7
        elif rebound_strength >= 1:
            rebound_score = 4
        else:
            rebound_score = 0
        
        # 총점 계산
        total_score = rsi_score + disparity_score + volume_score + pbr_score + returns_score + rebound_score
        
        return {
            '종목명': name,
            '종목코드': code,
            '현재가': current_price,
            '거래대금': trading_value,
            'RSI': current_rsi,
            'RSI점수': rsi_score,
            '이격도': disparity,
            '이격도점수': disparity_score,
            '거래량비율': volume_ratio,
            '거래량점수': volume_score,
            'PBR': pbr,
            'PBR점수': pbr_score,
            '5일수익률': returns_5d,
            '5일수익률점수': returns_score,
            '반등강도': rebound_strength,
            '반등점수': rebound_score,
            '총점': total_score
        }
    
    except Exception as e:
        return None

# ============================================================
# 차트 생성 (pykrx 데이터 사용)
# ============================================================

def create_chart(code, name):
    """개별 종목 차트 생성 (pykrx 사용)"""
    if not PYKRX_AVAILABLE:
        return None
    
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')
        
        ohlcv = stock.get_market_ohlcv_by_date(start_str, end_str, code)
        
        if ohlcv.empty:
            return None
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), gridspec_kw={'height_ratios': [3, 1]})
        
        # 가격 차트
        ax1.plot(ohlcv.index, ohlcv['종가'], label='종가', linewidth=2)
        ax1.plot(ohlcv.index, ohlcv['종가'].rolling(window=20).mean(), 
                 label='20일 이평선', linestyle='--', alpha=0.7)
        ax1.set_title(f'{name} ({code})', fontsize=14, fontweight='bold')
        ax1.set_ylabel('가격 (원)', fontsize=10)
        ax1.legend(loc='best')
        ax1.grid(True, alpha=0.3)
        
        # 거래량 차트
        colors = ['red' if ohlcv['종가'].iloc[i] > ohlcv['종가'].iloc[i-1] else 'blue' 
                  for i in range(1, len(ohlcv))]
        colors.insert(0, 'blue')
        ax2.bar(ohlcv.index, ohlcv['거래량'], color=colors, alpha=0.5)
        ax2.set_ylabel('거래량', fontsize=10)
        ax2.set_xlabel('날짜', fontsize=10)
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Base64 인코딩
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.read()).decode()
        plt.close()
        
        return image_base64
    
    except Exception as e:
        return None

# ============================================================
# AI 분석 (변경 없음)
# ============================================================

def get_ai_analysis(top_stocks, market_info):
    """Gemini AI를 사용한 시장 분석"""
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        stock_summary = []
        for _, stock in top_stocks.head(6).iterrows():
            pbr_text = f"{stock['PBR']:.2f}" if pd.notna(stock['PBR']) else "정보없음"
            returns_text = f"{stock['5일수익률']:.2f}%" if pd.notna(stock['5일수익률']) else "정보없음"
            
            stock_summary.append(
                f"- {stock['종목명']}: 총점 {stock['총점']:.0f}점, "
                f"RSI {stock['RSI']:.1f}, 이격도 {stock['이격도']:.1f}%, "
                f"PBR {pbr_text}, 5일수익률 {returns_text}"
            )
        
        market_summary = []
        for key, value in market_info.items():
            market_summary.append(f"- {key}: {value['value']} ({value['change']})")
        
        prompt = f"""
다음은 오늘의 한국 주식 시장 스윙 트레이딩 추천 종목 분석 결과입니다.

[시장 현황]
{chr(10).join(market_summary)}

[추천 종목 TOP 6]
{chr(10).join(stock_summary)}

위 정보를 바탕으로 다음 내용을 300자 이내로 간단명료하게 작성해주세요:
1. 오늘의 시장 분위기 (지수, 환율 포함)
2. 추천 종목들의 공통된 특징
3. 투자 시 주의사항

전문적이면서도 이해하기 쉽게 작성해주세요.
"""
        
        response = model.generate_content(prompt)
        return response.text
    
    except Exception as e:
        error_msg = str(e)
        if '429' in error_msg or 'quota' in error_msg.lower():
            return """
[AI 분석 일시 중단]

현재 Gemini API 사용량이 일일 할당량을 초과하여 AI 분석이 일시적으로 제공되지 않습니다.
분석 결과의 지표 점수와 차트를 참고하여 투자 판단을 내리시기 바랍니다.

• 총점 30점 이상: 스윙 트레이딩 후보군
• RSI < 40: 과매도 구간 (반등 가능성)
• 이격도 < 100: 평균 대비 저평가
• 거래량 증가: 시장 관심도 상승
"""
        else:
            return f"AI 분석 생성 중 오류 발생: {error_msg}"

# ============================================================
# HTML 보고서 생성 (변경 없음 - 디자인 유지)
# ============================================================

def generate_html(top_stocks, market_info, ai_analysis, timestamp):
    """HTML 보고서 생성 (디자인 변경 없음)"""
    
    # Top 6 차트 생성
    charts = {}
    print("\n차트 생성 중...")
    for idx, (_, stock) in enumerate(top_stocks.head(6).iterrows(), 1):
        chart = create_chart(stock['종목코드'], stock['종목명'])
        if chart:
            charts[stock['종목코드']] = chart
            print(f"  {idx}/6: {stock['종목명']} 차트 생성 완료")
    
    # 시장 정보 HTML
    market_html = ""
    for key, value in market_info.items():
        change_class = "positive" if "+" in value['change'] else "negative" if "-" in value['change'] else "neutral"
        market_html += f"""
        <div class="market-card">
            <div class="label">{key}</div>
            <div class="value">{value['value']}</div>
            <div class="change {change_class}">{value['change']}</div>
        </div>
        """
    
    # Top 6 카드 HTML
    top6_html = ""
    for rank, (_, stock) in enumerate(top_stocks.head(6).iterrows(), 1):
        chart_img = f'<img src="data:image/png;base64,{charts[stock["종목코드"]]}" alt="차트">' if stock['종목코드'] in charts else '<div class="no-chart">차트 없음</div>'
        
        pbr_display = f"{stock['PBR']:.2f}" if pd.notna(stock['PBR']) else "N/A"
        returns_display = f"{stock['5일수익률']:.2f}%" if pd.notna(stock['5일수익률']) else "N/A"
        
        top6_html += f"""
        <div class="stock-card">
            <div class="rank-badge">TOP {rank}</div>
            <h3>{stock['종목명']} <span class="code">({stock['종목코드']})</span></h3>
            <div class="score">총점: {stock['총점']:.0f}점</div>
            <div class="chart-container">
                {chart_img}
            </div>
            <div class="metrics">
                <div class="metric">
                    <span class="metric-label">현재가</span>
                    <span class="metric-value">{stock['현재가']:,.0f}원</span>
                </div>
                <div class="metric">
                    <span class="metric-label">RSI</span>
                    <span class="metric-value">{stock['RSI']:.1f}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">이격도</span>
                    <span class="metric-value">{stock['이격도']:.1f}%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">거래량비율</span>
                    <span class="metric-value">{stock['거래량비율']:.2f}배</span>
                </div>
                <div class="metric">
                    <span class="metric-label">PBR</span>
                    <span class="metric-value">{pbr_display}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">5일수익률</span>
                    <span class="metric-value">{returns_display}</span>
                </div>
            </div>
        </div>
        """
    
    # Top 7-30 테이블 HTML
    table_rows = ""
    for rank, (_, stock) in enumerate(top_stocks.iloc[6:30].iterrows(), 7):
        pbr_display = f"{stock['PBR']:.2f}" if pd.notna(stock['PBR']) else "N/A"
        returns_display = f"{stock['5일수익률']:.2f}%" if pd.notna(stock['5일수익률']) else "N/A"
        
        table_rows += f"""
        <tr>
            <td class="rank-col">{rank}</td>
            <td>{stock['종목명']}</td>
            <td>{stock['종목코드']}</td>
            <td class="number">{stock['총점']:.0f}</td>
            <td class="number">{stock['현재가']:,.0f}</td>
            <td class="number">{stock['RSI']:.1f}</td>
            <td class="number">{stock['이격도']:.1f}</td>
            <td class="number">{stock['거래량비율']:.2f}</td>
            <td class="number">{pbr_display}</td>
            <td class="number">{returns_display}</td>
        </tr>
        """
    
    # 지표별 TOP 5
    indicator_sections = ""
    
    # RSI 낮은 순
    rsi_top5 = top_stocks.nsmallest(5, 'RSI')
    rsi_rows = ""
    for rank, (_, stock) in enumerate(rsi_top5.iterrows(), 1):
        rsi_rows += f"""
        <tr>
            <td class="rank-col">{rank}</td>
            <td>{stock['종목명']}</td>
            <td>{stock['종목코드']}</td>
            <td class="number highlight">{stock['RSI']:.1f}</td>
            <td class="number">{stock['총점']:.0f}</td>
        </tr>
        """
    
    indicator_sections += f"""
    <div class="indicator-section">
        <h3>📊 RSI 낮은 순위 (과매도)</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>종목코드</th>
                    <th>RSI</th>
                    <th>총점</th>
                </tr>
            </thead>
            <tbody>
                {rsi_rows}
            </tbody>
        </table>
    </div>
    """
    
    # 이격도 낮은 순
    disparity_top5 = top_stocks.nsmallest(5, '이격도')
    disparity_rows = ""
    for rank, (_, stock) in enumerate(disparity_top5.iterrows(), 1):
        disparity_rows += f"""
        <tr>
            <td class="rank-col">{rank}</td>
            <td>{stock['종목명']}</td>
            <td>{stock['종목코드']}</td>
            <td class="number highlight">{stock['이격도']:.1f}%</td>
            <td class="number">{stock['총점']:.0f}</td>
        </tr>
        """
    
    indicator_sections += f"""
    <div class="indicator-section">
        <h3>📈 이격도 낮은 순위 (저평가)</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>종목코드</th>
                    <th>이격도</th>
                    <th>총점</th>
                </tr>
            </thead>
            <tbody>
                {disparity_rows}
            </tbody>
        </table>
    </div>
    """
    
    # 거래량 증가율 높은 순
    volume_top5 = top_stocks.nlargest(5, '거래량비율')
    volume_rows = ""
    for rank, (_, stock) in enumerate(volume_top5.iterrows(), 1):
        volume_rows += f"""
        <tr>
            <td class="rank-col">{rank}</td>
            <td>{stock['종목명']}</td>
            <td>{stock['종목코드']}</td>
            <td class="number highlight">{stock['거래량비율']:.2f}배</td>
            <td class="number">{stock['총점']:.0f}</td>
        </tr>
        """
    
    indicator_sections += f"""
    <div class="indicator-section">
        <h3>📊 거래량 증가 순위</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>종목코드</th>
                    <th>거래량비율</th>
                    <th>총점</th>
                </tr>
            </thead>
            <tbody>
                {volume_rows}
            </tbody>
        </table>
    </div>
    """
    
    # PBR 낮은 순 (None 값 제외)
    pbr_valid = top_stocks[top_stocks['PBR'].notna()]
    if len(pbr_valid) >= 5:
        pbr_top5 = pbr_valid.nsmallest(5, 'PBR')
        pbr_rows = ""
        for rank, (_, stock) in enumerate(pbr_top5.iterrows(), 1):
            pbr_rows += f"""
            <tr>
                <td class="rank-col">{rank}</td>
                <td>{stock['종목명']}</td>
                <td>{stock['종목코드']}</td>
                <td class="number highlight">{stock['PBR']:.2f}</td>
                <td class="number">{stock['총점']:.0f}</td>
            </tr>
            """
        
        indicator_sections += f"""
        <div class="indicator-section">
            <h3>💰 PBR 낮은 순위 (저평가)</h3>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>순위</th>
                        <th>종목명</th>
                        <th>종목코드</th>
                        <th>PBR</th>
                        <th>총점</th>
                    </tr>
                </thead>
                <tbody>
                    {pbr_rows}
                </tbody>
            </table>
        </div>
        """
    
    # 반등강도 높은 순
    rebound_top5 = top_stocks.nlargest(5, '반등강도')
    rebound_rows = ""
    for rank, (_, stock) in enumerate(rebound_top5.iterrows(), 1):
        rebound_rows += f"""
        <tr>
            <td class="rank-col">{rank}</td>
            <td>{stock['종목명']}</td>
            <td>{stock['종목코드']}</td>
            <td class="number highlight">{stock['반등강도']:.2f}%</td>
            <td class="number">{stock['총점']:.0f}</td>
        </tr>
        """
    
    indicator_sections += f"""
    <div class="indicator-section">
        <h3>🚀 반등강도 높은 순위</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>순위</th>
                    <th>종목명</th>
                    <th>종목코드</th>
                    <th>반등강도</th>
                    <th>총점</th>
                </tr>
            </thead>
            <tbody>
                {rebound_rows}
            </tbody>
        </table>
    </div>
    """
    
    # 투자자 유형별 추천
    investor_recommendations = f"""
    <div class="recommendation-section">
        <h2>💡 투자자 유형별 추천</h2>
        
        <div class="investor-type">
            <h3>🔴 공격적 투자자</h3>
            <p><strong>추천 지표:</strong> 거래량 증가율 + 반등강도</p>
            <p><strong>추천 종목:</strong> {', '.join([row['종목명'] for _, row in volume_top5.head(3).iterrows()])}</p>
            <p class="note">단기 급등 가능성이 있으나 변동성이 큰 종목들입니다.</p>
        </div>
        
        <div class="investor-type">
            <h3>🟡 균형 투자자</h3>
            <p><strong>추천 지표:</strong> 총점 기준 상위</p>
            <p><strong>추천 종목:</strong> {', '.join([row['종목명'] for _, row in top_stocks.head(3).iterrows()])}</p>
            <p class="note">여러 지표가 고르게 좋은 안정적인 종목들입니다.</p>
        </div>
        
        <div class="investor-type">
            <h3>🟢 보수적 투자자</h3>
            <p><strong>추천 지표:</strong> PBR + 이격도</p>
            <p><strong>추천 종목:</strong> {', '.join([row['종목명'] for _, row in pbr_top5.head(3).iterrows()]) if len(pbr_valid) >= 3 else '데이터 부족'}</p>
            <p class="note">저평가된 안정적인 종목으로 장기 투자에 적합합니다.</p>
        </div>
    </div>
    """
    
    # 전체 HTML 생성 (CSS 동일 - 디자인 변경 없음)
    html_content = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스윙 트레이딩 추천 종목 - {timestamp}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
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
        
        .header .timestamp {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        
        .market-overview {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
        }}
        
        .market-card {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
        }}
        
        .market-card .label {{
            font-size: 0.9em;
            color: #666;
            margin-bottom: 8px;
        }}
        
        .market-card .value {{
            font-size: 1.8em;
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }}
        
        .market-card .change {{
            font-size: 1.1em;
            font-weight: 600;
        }}
        
        .market-card .change.positive {{
            color: #d32f2f;
        }}
        
        .market-card .change.negative {{
            color: #1976d2;
        }}
        
        .market-card .change.neutral {{
            color: #666;
        }}
        
        .ai-analysis {{
            padding: 30px;
            background: #fff9e6;
            border-left: 5px solid #ffc107;
            margin: 30px;
            border-radius: 10px;
        }}
        
        .ai-analysis h2 {{
            color: #f57c00;
            margin-bottom: 15px;
            font-size: 1.5em;
        }}
        
        .ai-analysis p {{
            color: #333;
            line-height: 1.8;
            white-space: pre-wrap;
        }}
        
        .section {{
            padding: 30px;
        }}
        
        .section h2 {{
            color: #333;
            margin-bottom: 25px;
            font-size: 2em;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }}
        
        .top-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }}
        
        .stock-card {{
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 15px;
            padding: 20px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .stock-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            border-color: #667eea;
        }}
        
        .rank-badge {{
            position: absolute;
            top: 15px;
            right: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9em;
        }}
        
        .stock-card h3 {{
            font-size: 1.4em;
            color: #333;
            margin-bottom: 10px;
            padding-right: 80px;
        }}
        
        .stock-card .code {{
            font-size: 0.8em;
            color: #666;
            font-weight: normal;
        }}
        
        .stock-card .score {{
            font-size: 1.3em;
            color: #667eea;
            font-weight: bold;
            margin-bottom: 15px;
        }}
        
        .chart-container {{
            margin: 15px 0;
            border-radius: 8px;
            overflow: hidden;
            background: #f5f5f5;
        }}
        
        .chart-container img {{
            width: 100%;
            display: block;
        }}
        
        .no-chart {{
            padding: 40px;
            text-align: center;
            color: #999;
            background: #f5f5f5;
        }}
        
        .metrics {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-top: 15px;
        }}
        
        .metric {{
            padding: 10px;
            background: #f8f9fa;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .metric-label {{
            font-size: 0.85em;
            color: #666;
        }}
        
        .metric-value {{
            font-weight: 600;
            color: #333;
            font-size: 0.95em;
        }}
        
        .data-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-radius: 10px;
            overflow: hidden;
        }}
        
        .data-table thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        .data-table th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
        }}
        
        .data-table td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e0e0e0;
        }}
        
        .data-table tbody tr:hover {{
            background: #f8f9fa;
        }}
        
        .data-table tbody tr:last-child td {{
            border-bottom: none;
        }}
        
        .rank-col {{
            font-weight: bold;
            color: #667eea;
            text-align: center;
        }}
        
        .number {{
            text-align: right;
            font-family: 'Courier New', monospace;
        }}
        
        .highlight {{
            background: #fff9e6;
            font-weight: bold;
            color: #f57c00;
        }}
        
        .indicator-section {{
            margin-bottom: 40px;
        }}
        
        .indicator-section h3 {{
            color: #333;
            margin-bottom: 15px;
            font-size: 1.3em;
            padding-left: 10px;
            border-left: 4px solid #667eea;
        }}
        
        .recommendation-section {{
            background: #f8f9fa;
            padding: 30px;
            border-radius: 15px;
            margin-top: 40px;
        }}
        
        .recommendation-section h2 {{
            color: #333;
            margin-bottom: 25px;
            font-size: 1.8em;
        }}
        
        .investor-type {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            border-left: 5px solid #667eea;
        }}
        
        .investor-type h3 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 1.2em;
        }}
        
        .investor-type p {{
            margin: 8px 0;
            color: #555;
        }}
        
        .investor-type .note {{
            color: #888;
            font-size: 0.9em;
            font-style: italic;
            margin-top: 10px;
        }}
        
        .footer {{
            background: #333;
            color: white;
            text-align: center;
            padding: 20px;
            font-size: 0.9em;
        }}
        
        .footer p {{
            margin: 5px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 스윙 트레이딩 추천 종목</h1>
            <p class="timestamp">{timestamp}</p>
        </div>
        
        <div class="market-overview">
            {market_html}
        </div>
        
        <div class="ai-analysis">
            <h2>🤖 AI 시장 분석</h2>
            <p>{ai_analysis}</p>
        </div>
        
        <div class="section">
            <h2>🏆 TOP 6 추천 종목</h2>
            <div class="top-cards">
                {top6_html}
            </div>
        </div>
        
        <div class="section">
            <h2>📊 TOP 7-30 종목</h2>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>순위</th>
                        <th>종목명</th>
                        <th>종목코드</th>
                        <th>총점</th>
                        <th>현재가</th>
                        <th>RSI</th>
                        <th>이격도</th>
                        <th>거래량비율</th>
                        <th>PBR</th>
                        <th>5일수익률</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>📈 지표별 상세 분석</h2>
            {indicator_sections}
        </div>
        
        <div class="section">
            {investor_recommendations}
        </div>
        
        <div class="footer">
            <p>⚠️ 본 자료는 투자 참고용이며, 투자 결정에 대한 책임은 투자자 본인에게 있습니다.</p>
            <p>Generated by Stock Swing Trading Analysis System v3.9 HYBRID</p>
        </div>
    </div>
</body>
</html>
    """
    
    return html_content

# ============================================================
# 메인 실행
# ============================================================

def main():
    """메인 실행 함수"""
    print("="*60)
    print("스윙 트레이딩 종목 분석 시스템 v3.9 HYBRID")
    print("="*60)
    
    # pykrx 확인
    if not PYKRX_AVAILABLE:
        print("\n❌ pykrx가 필요합니다!")
        print("설치: !pip install pykrx")
        return
    
    start_time = time.time()
    
    # 1. 환경 설정
    print("\n[1단계] 환경 설정")
    api_key = setup_environment()
    setup_korean_font()
    
    # 2. 시장 정보 수집 (우선 - yfinance)
    print("\n[2단계] 시장 정보 수집 (우선)")
    market_info = get_market_data()
    
    # 3. 종목 목록 가져오기 (pykrx)
    print("\n[3단계] 종목 목록 조회")
    stocks = get_stock_list()
    
    if stocks.empty:
        print("❌ 종목 목록을 가져올 수 없습니다.")
        return
    
    # 4. 전체 종목 분석 (pykrx)
    print("\n[4단계] 종목 분석 시작")
    results = []
    
    for idx, (_, stock) in enumerate(stocks.iterrows(), 1):
        if idx % 100 == 0:
            print(f"  진행 중: {idx}/{len(stocks)} ({idx/len(stocks)*100:.1f}%)")
        
        result = analyze_stock(stock['종목코드'], stock['종목명'])
        if result:
            results.append(result)
    
    print(f"\n✓ 분석 완료: {len(results)}개 종목 필터 통과")
    
    if not results:
        print("❌ 분석 가능한 종목이 없습니다.")
        return
    
    # 5. 결과 정리
    df_results = pd.DataFrame(results)
    
    # 30점 이상만 필터링
    df_filtered = df_results[df_results['총점'] >= 30].copy()
    df_filtered = df_filtered.sort_values('총점', ascending=False).reset_index(drop=True)
    
    print(f"✓ 30점 이상 종목: {len(df_filtered)}개")
    
    if len(df_filtered) == 0:
        print("❌ 30점 이상 종목이 없습니다.")
        return
    
    # 6. AI 분석
    print("\n[5단계] AI 시장 분석")
    if api_key:
        ai_analysis = get_ai_analysis(df_filtered, market_info)
        print("✓ AI 분석 완료")
    else:
        ai_analysis = "API 키가 설정되지 않아 AI 분석을 수행할 수 없습니다."
        print("⚠ AI 분석 건너뜀 (API 키 없음)")
    
    # 7. HTML 보고서 생성
    print("\n[6단계] HTML 보고서 생성")
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(df_filtered, market_info, ai_analysis, timestamp)
    
    # 8. 파일 저장
    filename = f"stock_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    # 실행 시간 계산
    elapsed_time = time.time() - start_time
    minutes = int(elapsed_time // 60)
    seconds = int(elapsed_time % 60)
    
    print("\n" + "="*60)
    print("✅ 분석 완료!")
    print("="*60)
    print(f"📊 분석 종목 수: {len(results)}개")
    print(f"🎯 30점 이상 종목: {len(df_filtered)}개")
    print(f"📄 보고서 파일: {filename}")
    print(f"⏱️ 실행 시간: {minutes}분 {seconds}초")
    print("="*60)
    
    # Top 10 미리보기
    print("\n[TOP 10 미리보기]")
    print(df_filtered[['종목명', '종목코드', '총점', '현재가', 'RSI', '이격도']].head(10).to_string(index=False))

if __name__ == "__main__":
    main()
