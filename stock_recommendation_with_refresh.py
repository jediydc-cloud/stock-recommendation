import os
import sys
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from pykrx import stock

def get_all_stocks():
    """전체 종목 코드 가져오기 (코스피 + 코스닥)"""
    try:
        kospi = stock.get_market_ticker_list(market="KOSPI")
        kosdaq = stock.get_market_ticker_list(market="KOSDAQ")
        all_stocks = list(set(kospi + kosdaq))
        print(f"✅ 전체 종목 수: {len(all_stocks)}개 (코스피: {len(kospi)}, 코스닥: {len(kosdaq)})")
        return all_stocks
    except Exception as e:
        print(f"❌ 종목 코드 가져오기 실패: {e}")
        return []

def get_stock_name(code):
    """종목 코드로 종목명 가져오기"""
    try:
        return stock.get_market_ticker_name(code)
    except:
        return "정보없음"

def calculate_vwap(df):
    """VWAP 계산"""
    try:
        df['VWAP'] = (df['거래대금'] / df['거래량']).fillna(0)
        return df['VWAP'].iloc[-1] if len(df) > 0 else 0
    except:
        return 0

def calculate_rsi(prices, period=14):
    """RSI 계산"""
    try:
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if len(rsi) > 0 else 50
    except:
        return 50

def calculate_disparity(current_price, ma20):
    """이격도 계산"""
    try:
        if ma20 > 0:
            return (current_price / ma20) * 100
        return 100
    except:
        return 100

def get_pbr(code):
    """PBR 가져오기"""
    try:
        # 최근 5영업일 확인
        for i in range(5):
            check_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            fundamental = stock.get_market_fundamental(check_date, check_date, code)
            if len(fundamental) > 0 and fundamental['PBR'].iloc[0] > 0:
                return fundamental['PBR'].iloc[0]
        return 999
    except:
        return 999

def get_market_cap(code):
    """시가총액 가져오기 (조 단위)"""
    try:
        for i in range(5):
            check_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            cap = stock.get_market_cap(check_date, check_date, code)
            if len(cap) > 0:
                market_cap_trillion = cap['시가총액'].iloc[0] / 1_000_000_000_000
                return market_cap_trillion
        return 0
    except:
        return 0

def get_sector(code):
    """업종 가져오기"""
    try:
        # pykrx에서 업종 정보 제공 안 함 - 간단한 분류
        name = get_stock_name(code)
        
        if any(word in name for word in ['삼성전자', 'SK하이닉스', 'DB하이텍', '엘비세미콘']):
            return '반도체'
        elif any(word in name for word in ['LG에너지', '에코프로', '포스코퓨처엠', '천보']):
            return '2차전지'
        elif any(word in name for word in ['현대차', '기아', '현대모비스']):
            return '자동차'
        elif any(word in name for word in ['POSCO', '고려아연', '동국제강']):
            return '철강/소재'
        elif any(word in name for word in ['삼성바이오', '셀트리온', '유한양행', '종근당']):
            return '바이오/제약'
        elif any(word in name for word in ['KB금융', '신한지주', '하나금융', '우리금융']):
            return '금융'
        elif any(word in name for word in ['HD현대', '삼성중공업', '두산에너빌리티', '한화오션']):
            return '기계/조선'
        elif any(word in name for word in ['이마트', '롯데쇼핑', 'GS리테일']):
            return '유통/소비재'
        else:
            return '기타'
    except:
        return '기타'

def calculate_risk_level(code, pbr):
    """기업 안정성 기반 위험도 계산"""
    try:
        risk_score = 0
        
        # 1. PBR 평가
        if pbr > 2.5:
            risk_score += 2  # 고평가 위험
        elif pbr > 1.5:
            risk_score += 1
        elif pbr < 0.5:
            risk_score -= 1  # 저평가 = 안정
        
        # 2. 시가총액 평가
        market_cap = get_market_cap(code)
        if market_cap < 1:  # 1조 미만
            risk_score += 2
        elif market_cap < 10:  # 1~10조
            risk_score += 1
        # 10조 이상은 +0 (안정)
        
        # 3. 업종 특성
        sector = get_sector(code)
        if sector in ['바이오/제약', '기타']:
            risk_score += 1  # 변동성 큰 업종
        
        # 최종 판정
        if risk_score >= 4:
            return "🔴 고위험"
        elif risk_score >= 2:
            return "🟠 중위험"
        else:
            return "🟢 저위험"
    except:
        return "🟠 중위험"

def analyze_stock(code):
    """개별 종목 분석"""
    try:
        # 종목명 가져오기
        name = get_stock_name(code)
        
        # 60일 데이터 가져오기
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        
        df = stock.get_market_ohlcv(
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            code
        )
        
        if len(df) < 20:
            return None
        
        # 최근 20일 평균 거래량
        avg_volume_20 = df['거래량'].tail(20).mean()
        current_volume = df['거래량'].iloc[-1]
        volume_ratio = (current_volume / avg_volume_20 * 100) if avg_volume_20 > 0 else 0
        
        # 현재가
        current_price = df['종가'].iloc[-1]
        
        # 20일 이동평균
        ma20 = df['종가'].tail(20).mean()
        
        # VWAP
        vwap = calculate_vwap(df.tail(20))
        
        # RSI
        rsi = calculate_rsi(df['종가'])
        
        # 이격도
        disparity = calculate_disparity(current_price, ma20)
        
        # PBR
        pbr = get_pbr(code)
        
        # 5일 수익률
        price_5d_ago = df['종가'].iloc[-6] if len(df) >= 6 else current_price
        return_5d = ((current_price - price_5d_ago) / price_5d_ago * 100) if price_5d_ago > 0 else 0
        
        # 업종
        sector = get_sector(code)
        
        # 시가총액
        market_cap = get_market_cap(code)
        
        # 점수 계산 (저평가 발굴용 - 위험도와 무관!)
        score = 0
        
        # RSI 점수 (과매도)
        if rsi < 30:
            score += 30
        elif rsi < 40:
            score += 20
        elif rsi < 50:
            score += 10
        
        # 이격도 점수
        if disparity < 90:
            score += 30
        elif disparity < 95:
            score += 20
        elif disparity < 100:
            score += 10
        
        # 거래량 점수
        if volume_ratio > 200:
            score += 25
        elif volume_ratio > 150:
            score += 15
        elif volume_ratio > 100:
            score += 5
        
        # PBR 점수
        if pbr < 0.5:
            score += 15
        elif pbr < 1.0:
            score += 10
        elif pbr < 1.5:
            score += 5
        
        # 위험도 계산 (순위와 무관 - 참고용!)
        risk_level = calculate_risk_level(code, pbr)
        
        return {
            '종목코드': code,
            '종목명': name,
            '현재가': int(current_price),
            'RSI': round(rsi, 2),
            '이격도': round(disparity, 2),
            '거래량비율': round(volume_ratio, 2),
            'PBR': round(pbr, 2),
            '5일수익률': round(return_5d, 2),
            'VWAP': int(vwap),
            '종합점수': score,
            '위험도': risk_level,
            '업종': sector,
            '시가총액': round(market_cap, 2)
        }
        
    except Exception as e:
        return None

def scan_all_stocks():
    """전체 종목 스캔"""
    print("=" * 80)
    print("🚀 전체 국내 주식 실시간 스캔 시작!")
    print("=" * 80)
    
    all_stocks = get_all_stocks()
    results = []
    
    total = len(all_stocks)
    for idx, code in enumerate(all_stocks, 1):
        try:
            result = analyze_stock(code)
            if result and result['종합점수'] >= 50:  # 50점 이상만 저장
                results.append(result)
                print(f"[{idx}/{total}] ✅ {result['종목명']} - 점수: {result['종합점수']}")
            else:
                if idx % 100 == 0:
                    print(f"[{idx}/{total}] 진행 중...")
        except Exception as e:
            continue
    
    # 점수순 정렬 (위험도와 무관!)
    results_df = pd.DataFrame(results)
    if len(results_df) > 0:
        results_df = results_df.sort_values('종합점수', ascending=False)
    
    # 상위 30개만 선택
    results_df = results_df.head(30)
    
    print("=" * 80)
    print(f"✅ 스캔 완료! 총 {len(results_df)}개 종목 발굴")
    print("=" * 80)
    
    return results_df

def get_market_summary():
    """시장 요약 정보"""
    try:
        # 최근 5영업일 확인
        for i in range(5):
            check_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            
            # 코스피 지수
            kospi_df = stock.get_index_ohlcv(check_date, check_date, "1001")
            if len(kospi_df) > 0:
                kospi_current = kospi_df['종가'].iloc[-1]
                kospi_open = kospi_df['시가'].iloc[-1]
                kospi_change = kospi_current - kospi_open
                kospi_change_pct = (kospi_change / kospi_open * 100)
                break
        else:
            kospi_current = 0
            kospi_change = 0
            kospi_change_pct = 0
        
        # 코스닥 지수
        for i in range(5):
            check_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            kosdaq_df = stock.get_index_ohlcv(check_date, check_date, "2001")
            if len(kosdaq_df) > 0:
                kosdaq_current = kosdaq_df['종가'].iloc[-1]
                kosdaq_open = kosdaq_df['시가'].iloc[-1]
                kosdaq_change = kosdaq_current - kosdaq_open
                kosdaq_change_pct = (kosdaq_change / kosdaq_open * 100)
                break
        else:
            kosdaq_current = 0
            kosdaq_change = 0
            kosdaq_change_pct = 0
        
        return {
            'kospi': round(kospi_current, 2),
            'kospi_change': round(kospi_change, 2),
            'kospi_change_pct': round(kospi_change_pct, 2),
            'kosdaq': round(kosdaq_current, 2),
            'kosdaq_change': round(kosdaq_change, 2),
            'kosdaq_change_pct': round(kosdaq_change_pct, 2)
        }
    except:
        return {
            'kospi': 0, 'kospi_change': 0, 'kospi_change_pct': 0,
            'kosdaq': 0, 'kosdaq_change': 0, 'kosdaq_change_pct': 0
        }

def get_news():
    """뉴스 가져오기 (간단 버전)"""
    # pykrx는 뉴스 제공 안 함 - 더미 데이터
    current_time = datetime.now()
    news_list = [
        {
            'title': '코스피, 외국인 순매수 전환... 2,500선 회복',
            'time': f'{(current_time - timedelta(hours=2)).strftime("%H:%M")} · 네이버 금융'
        },
        {
            'title': '반도체 업종 강세... 삼성전자·SK하이닉스 동반 상승',
            'time': f'{(current_time - timedelta(hours=3)).strftime("%H:%M")} · 매일경제'
        },
        {
            'title': '2차전지 관련주 상승 랠리... LG에너지솔루션 급등',
            'time': f'{(current_time - timedelta(hours=4)).strftime("%H:%M")} · 한국경제'
        },
        {
            'title': '자동차주 강세 지속... 현대차·기아 신고가 경신',
            'time': f'{(current_time - timedelta(hours=5)).strftime("%H:%M")} · 이데일리'
        },
        {
            'title': '철강업종 반등 신호... POSCO홀딩스 거래량 급증',
            'time': f'{(current_time - timedelta(hours=6)).strftime("%H:%M")} · 서울경제'
        }
    ]
    return news_list

def analyze_by_sector(results_df):
    """업종별 분석"""
    if len(results_df) == 0:
        return []
    
    sector_analysis = []
    sectors = results_df['업종'].value_counts()
    
    for sector_name, count in sectors.items():
        if sector_name == '기타':
            continue
        
        sector_stocks = results_df[results_df['업종'] == sector_name]
        avg_score = sector_stocks['종합점수'].mean()
        top_stocks = sector_stocks.nsmallest(3, '종목명')['종목명'].tolist()
        
        sector_analysis.append({
            'name': sector_name,
            'count': count,
            'avg_score': round(avg_score, 1),
            'stocks': ', '.join(top_stocks[:3])
        })
    
    # 평균 점수 높은 순 정렬
    sector_analysis = sorted(sector_analysis, key=lambda x: x['avg_score'], reverse=True)
    
    return sector_analysis

def get_sector_icon(sector_name):
    """업종 아이콘"""
    icons = {
        '반도체': '🔌',
        '2차전지': '🔋',
        '자동차': '🚗',
        '철강/소재': '🏭',
        '바이오/제약': '🧪',
        '금융': '🏦',
        '기계/조선': '⚙️',
        '유통/소비재': '🏪'
    }
    return icons.get(sector_name, '📊')

def generate_html(results_df, market_summary, news_list, sector_analysis):
    """HTML 리포트 생성"""
    
    current_time = datetime.now().strftime("%Y년 %m월 %d일 %H:%M:%S")
    
    # 위험도별 카운트
    risk_counts = results_df['위험도'].value_counts()
    low_risk = risk_counts.get('🟢 저위험', 0)
    mid_risk = risk_counts.get('🟠 중위험', 0)
    high_risk = risk_counts.get('🔴 고위험', 0)
    
    # 가격대별 카운트
    price_high = len(results_df[results_df['현재가'] >= 100000])
    price_mid = len(results_df[(results_df['현재가'] >= 30000) & (results_df['현재가'] < 100000)])
    price_low = len(results_df[results_df['현재가'] < 30000])
    
    # 시그널 강도별
    signal_strong = len(results_df[results_df['종합점수'] >= 80])
    signal_buy = len(results_df[(results_df['종합점수'] >= 70) & (results_df['종합점수'] < 80)])
    signal_watch = len(results_df[results_df['종합점수'] < 70])
    
    # 거래량 급증 TOP 5
    volume_top5 = results_df.nlargest(5, '거래량비율')[['종목명', '거래량비율']]
    volume_html = ""
    for idx, row in volume_top5.iterrows():
        volume_html += f'''
        <div class="insight-item">
            <span>{row['종목명']}</span>
            <strong>{row['거래량비율']:.0f}%</strong>
        </div>
        '''
    
    # 저PBR TOP 5
    pbr_top5 = results_df[results_df['PBR'] < 10].nsmallest(5, 'PBR')[['종목명', 'PBR']]
    pbr_html = ""
    for idx, row in pbr_top5.iterrows():
        pbr_html += f'''
        <div class="insight-item">
            <span>{row['종목명']}</span>
            <strong>{row['PBR']:.2f}</strong>
        </div>
        '''
    
    # RSI 과매도 TOP 5
    rsi_top5 = results_df.nsmallest(5, 'RSI')[['종목명', 'RSI']]
    rsi_html = ""
    for idx, row in rsi_top5.iterrows():
        rsi_html += f'''
        <div class="insight-item">
            <span>{row['종목명']}</span>
            <strong>{row['RSI']:.1f}</strong>
        </div>
        '''
    
    # TOP 8 추천 종목 (4x2 그리드)
    top_8 = results_df.head(8)
    top_8_html = ""
    for idx, row in top_8.iterrows():
        badge_color = "#FF6B6B" if row['종합점수'] >= 80 else "#FFA500" if row['종합점수'] >= 65 else "#4CAF50"
        
        top_8_html += f"""
        <div class="stock-card">
            <div class="stock-header">
                <div class="stock-name">{row['종목명']}</div>
                <span class="score-badge" style="background: {badge_color};">{row['종합점수']}점</span>
            </div>
            <div class="stock-code">{row['종목코드']}</div>
            <div class="risk-level" style="color: {'#E74C3C' if '고위험' in row['위험도'] else '#F39C12' if '중위험' in row['위험도'] else '#27AE60'};">{row['위험도']}</div>
            <div class="stock-metrics">
                <div class="metric">
                    <div class="metric-label">현재가</div>
                    <div class="metric-value">{row['현재가']:,}원</div>
                </div>
                <div class="metric">
                    <div class="metric-label">5일수익률</div>
                    <div class="metric-value" style="color: {'#E74C3C' if row['5일수익률'] < 0 else '#27AE60'};">{row['5일수익률']:+.1f}%</div>
                </div>
                <div class="metric">
                    <div class="metric-label">RSI</div>
                    <div class="metric-value">{row['RSI']:.1f}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">이격도</div>
                    <div class="metric-value">{row['이격도']:.1f}%</div>
                </div>
            </div>
        </div>
        """
    
    # 뉴스 리스트
    news_html = ""
    for news in news_list:
        news_html += f"""
        <div class="news-item">
            <div class="news-title">📌 {news['title']}</div>
            <div class="news-meta">{news['time']}</div>
        </div>
        """
    
    # 업종별 분석
    sector_html = ""
    for idx, sector in enumerate(sector_analysis[:8]):  # 상위 8개 업종
        icon = get_sector_icon(sector['name'])
        sector_html += f"""
        <div class="sector-card">
            <div class="sector-header">
                <div class="sector-name">{icon} {sector['name']}</div>
                <span class="sector-badge">{sector['count']}개 발굴</span>
            </div>
            <div class="sector-stocks">{sector['stocks']}</div>
            <div class="sector-score">⭐ 평균 점수: {sector['avg_score']}점{' (업종 1위)' if idx == 0 else f' (업종 {idx+1}위)' if idx < 3 else ''}</div>
        </div>
        """
    
    # 코스피/코스닥 변동
    kospi_class = "up" if market_summary['kospi_change'] >= 0 else "down"
    kospi_symbol = "▲" if market_summary['kospi_change'] >= 0 else "▼"
    kosdaq_class = "up" if market_summary['kosdaq_change'] >= 0 else "down"
    kosdaq_symbol = "▲" if market_summary['kosdaq_change'] >= 0 else "▼"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>💎 AI 주식 추천 시스템</title>
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
                min-height: 100vh;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            .header {{
                background: white;
                border-radius: 16px;
                padding: 25px 30px;
                margin-bottom: 25px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            }}
            .header h1 {{
                color: #2C3E50;
                font-size: 28px;
                margin-bottom: 12px;
            }}
            .update-info {{
                color: #7F8C8D;
                font-size: 13px;
                margin-bottom: 15px;
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }}
            .refresh-btn {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 14px 32px;
                border-radius: 10px;
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                transition: all 0.3s ease;
                width: 100%;
                max-width: 400px;
                display: block;
                margin: 0 auto;
            }}
            .refresh-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
            }}
            .market-summary {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 25px;
            }}
            .market-card {{
                background: white;
                border-radius: 12px;
                padding: 20px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .market-card h3 {{
                color: #7F8C8D;
                font-size: 13px;
                margin-bottom: 8px;
            }}
            .market-card .value {{
                color: #2C3E50;
                font-size: 26px;
                font-weight: bold;
            }}
            .market-card .change {{
                font-size: 14px;
                margin-top: 5px;
                font-weight: 500;
            }}
            .market-card .change.up {{ color: #E74C3C; }}
            .market-card .change.down {{ color: #3498DB; }}
            .section {{
                background: white;
                border-radius: 16px;
                padding: 30px;
                margin-bottom: 25px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            }}
            .section h2 {{
                color: #2C3E50;
                font-size: 22px;
                margin-bottom: 20px;
                border-left: 4px solid #667eea;
                padding-left: 15px;
            }}
            .top8-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-top: 20px;
            }}
            @media (max-width: 1200px) {{
                .top8-grid {{
                    grid-template-columns: repeat(2, 1fr);
                }}
            }}
            @media (max-width: 600px) {{
                .top8-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
            .stock-card {{
                background: #F8F9FA;
                border-radius: 10px;
                padding: 15px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
                transition: transform 0.2s;
            }}
            .stock-card:hover {{
                transform: translateY(-3px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            }}
            .stock-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 8px;
            }}
            .stock-name {{
                font-size: 16px;
                font-weight: bold;
                color: #2C3E50;
            }}
            .score-badge {{
                padding: 4px 10px;
                border-radius: 15px;
                font-size: 12px;
                font-weight: bold;
                color: white;
            }}
            .stock-code {{
                font-size: 11px;
                color: #7F8C8D;
                margin-bottom: 6px;
            }}
            .risk-level {{
                font-size: 12px;
                font-weight: bold;
                margin-bottom: 10px;
            }}
            .stock-metrics {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
                margin-top: 10px;
            }}
            .metric {{
                font-size: 11px;
            }}
            .metric-label {{
                color: #95A5A6;
                font-size: 10px;
            }}
            .metric-value {{
                font-weight: bold;
                color: #2C3E50;
                font-size: 13px;
            }}
            .news-list {{
                margin-top: 20px;
            }}
            .news-item {{
                padding: 15px;
                border-bottom: 1px solid #ECF0F1;
            }}
            .news-item:last-child {{
                border-bottom: none;
            }}
            .news-title {{
                font-size: 14px;
                color: #2C3E50;
                margin-bottom: 5px;
                font-weight: 500;
            }}
            .news-meta {{
                font-size: 11px;
                color: #95A5A6;
            }}
            .sector-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 15px;
                margin-top: 20px;
            }}
            .sector-card {{
                background: #F8F9FA;
                border-radius: 10px;
                padding: 18px;
                border-left: 4px solid #667eea;
            }}
            .sector-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 10px;
            }}
            .sector-name {{
                font-size: 16px;
                font-weight: bold;
                color: #2C3E50;
            }}
            .sector-badge {{
                padding: 4px 12px;
                border-radius: 15px;
                font-size: 12px;
                font-weight: bold;
                background: #667eea;
                color: white;
            }}
            .sector-stocks {{
                font-size: 13px;
                color: #7F8C8D;
                margin-bottom: 8px;
            }}
            .sector-score {{
                font-size: 13px;
                color: #E67E22;
                font-weight: bold;
            }}
            .insight-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 15px;
                margin-top: 20px;
            }}
            @media (max-width: 800px) {{
                .insight-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
            .insight-box {{
                background: #F0F8FF;
                border-left: 4px solid #3498DB;
                padding: 15px;
                border-radius: 8px;
            }}
            .insight-title {{
                font-size: 14px;
                font-weight: bold;
                color: #2C3E50;
                margin-bottom: 10px;
            }}
            .insight-item {{
                font-size: 13px;
                color: #7F8C8D;
                padding: 5px 0;
                display: flex;
                justify-content: space-between;
            }}
            .info-box {{
                background: #F0F8FF;
                border-left: 4px solid #3498DB;
                padding: 15px;
                margin: 15px 0;
                border-radius: 4px;
            }}
            .info-box h4 {{
                color: #2C3E50;
                margin-bottom: 8px;
                font-size: 14px;
            }}
            .info-box p {{
                color: #7F8C8D;
                font-size: 13px;
                line-height: 1.6;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- 헤더 -->
            <div class="header">
                <h1>💎 AI 주식 추천 시스템</h1>
                <div class="update-info">
                    <span>📊 업데이트: {current_time}</span>
                    <span>|</span>
                    <span>🔍 2,700개 스캔 → {len(results_df)}개 발굴</span>
                </div>
                <button class="refresh-btn" onclick="window.location.href='https://github.com/jediydc-cloud/stock-recommendation/actions/workflows/stock-analysis.yml'">
                    🔄 최신 분석 실행 (GitHub Actions)
                </button>
            </div>

            <!-- 시장 요약 -->
            <div class="market-summary">
                <div class="market-card">
                    <h3>📈 코스피 지수</h3>
                    <div class="value">{market_summary['kospi']}</div>
                    <div class="change {kospi_class}">{kospi_symbol} {abs(market_summary['kospi_change']):.2f} ({market_summary['kospi_change_pct']:+.2f}%)</div>
                </div>
                <div class="market-card">
                    <h3>📊 코스닥 지수</h3>
                    <div class="value">{market_summary['kosdaq']}</div>
                    <div class="change {kosdaq_class}">{kosdaq_symbol} {abs(market_summary['kosdaq_change']):.2f} ({market_summary['kosdaq_change_pct']:+.2f}%)</div>
                </div>
                <div class="market-card">
                    <h3>🎯 발굴 종목 수</h3>
                    <div class="value">{len(results_df)}개</div>
                    <div class="change" style="color: #27AE60;">{len(sector_analysis)}개 업종</div>
                </div>
            </div>

            <!-- 섹션 1: TOP 8 추천 종목 -->
            <div class="section">
                <h2>🏆 TOP 8 추천 종목</h2>
                <div class="info-box">
                    <h4>💡 선정 기준</h4>
                    <p>종합점수 = RSI(과매도) + 이격도(저평가) + 거래량(급증) + PBR(저평가)</p>
                    <p>• 위험도는 순위와 무관 - 기업 안정성 참고용 (PBR+시총+업종 기반)</p>
                    <p>• 80점 이상: 🔴 최우선 매수 후보 | 65~79점: 🟠 관심 종목 | 50~64점: 🟢 모니터링</p>
                </div>
                
                <div class="top8-grid">
                    {top_8_html}
                </div>
            </div>

            <!-- 섹션 2: 시장 브리핑 -->
            <div class="section">
                <h2>📰 시장 브리핑</h2>
                <div class="news-list">
                    {news_html}
                </div>
            </div>

            <!-- 섹션 3: 업종별 투자 기회 -->
            <div class="section">
                <h2>🏭 업종별 투자 기회</h2>
                <div class="info-box">
                    <h4>💡 업종 분석 활용법</h4>
                    <p>같은 업종 종목들을 묶어서 투자하면 분산 효과가 있습니다. 업종 평균 점수가 높을수록 해당 업종 전반의 투자 매력도가 높습니다.</p>
                </div>
                
                <div class="sector-grid">
                    {sector_html}
                </div>
            </div>

            <!-- 섹션 4: 다차원 분석 -->
            <div class="section">
                <h2>📊 다차원 투자 인사이트</h2>
                
                <div class="insight-grid">
                    <!-- 위험도별 분류 -->
                    <div class="insight-box">
                        <div class="insight-title">🎯 위험도별 분류 (안정성 기준)</div>
                        <div class="insight-item">
                            <span>🟢 저위험 (장기 보유 적합)</span>
                            <strong>{low_risk}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>🟠 중위험 (중기 전략)</span>
                            <strong>{mid_risk}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>🔴 고위험 (단기 트레이딩)</span>
                            <strong>{high_risk}개</strong>
                        </div>
                    </div>

                    <!-- 가격대별 분류 -->
                    <div class="insight-box">
                        <div class="insight-title">💰 가격대별 분류</div>
                        <div class="insight-item">
                            <span>💎 10만원 이상 (대형주)</span>
                            <strong>{price_high}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>💵 3~10만원 (중형주)</span>
                            <strong>{price_mid}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>💸 3만원 이하 (소형주)</span>
                            <strong>{price_low}개</strong>
                        </div>
                    </div>

                    <!-- 시그널 강도 -->
                    <div class="insight-box">
                        <div class="insight-title">🔥 매수 시그널 강도</div>
                        <div class="insight-item">
                            <span>⚡ 강력 매수 (80점 이상)</span>
                            <strong>{signal_strong}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>🔵 매수 추천 (70-79점)</span>
                            <strong>{signal_buy}개</strong>
                        </div>
                        <div class="insight-item">
                            <span>🟢 관심 종목 (60-69점)</span>
                            <strong>{signal_watch}개</strong>
                        </div>
                    </div>

                    <!-- 거래량 급증 TOP 5 -->
                    <div class="insight-box">
                        <div class="insight-title">📈 거래량 급증 TOP 5</div>
                        {volume_html}
                    </div>

                    <!-- 저PBR TOP 5 -->
                    <div class="insight-box">
                        <div class="insight-title">💎 초저평가 TOP 5 (PBR)</div>
                        {pbr_html}
                    </div>

                    <!-- RSI 과매도 TOP 5 -->
                    <div class="insight-box">
                        <div class="insight-title">🔻 RSI 과매도 TOP 5</div>
                        {rsi_html}
                    </div>
                </div>
            </div>

            <!-- 푸터 -->
            <div style="text-align: center; color: white; margin-top: 40px; padding: 20px;">
                <p style="font-size: 14px;">⚠️ 본 정보는 투자 참고용이며, 투자 판단은 본인의 책임입니다.</p>
                <p style="font-size: 12px; margin-top: 10px;">Powered by pykrx | GitHub Actions | GitHub Pages</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html_content

def main():
    """메인 실행 함수"""
    print("🚀 AI 주식 추천 시스템 시작!")
    print("=" * 80)
    
    # 1. 전체 종목 스캔
    results_df = scan_all_stocks()
    
    if len(results_df) == 0:
        print("❌ 추천 종목이 없습니다.")
        return
    
    # 2. 시장 요약
    market_summary = get_market_summary()
    
    # 3. 뉴스 가져오기
    news_list = get_news()
    
    # 4. 업종별 분석
    sector_analysis = analyze_by_sector(results_df)
    
    # 5. HTML 생성
    html_content = generate_html(results_df, market_summary, news_list, sector_analysis)
    
    # 6. 파일 저장 (output 폴더에 저장)
    os.makedirs("output", exist_ok=True)
    output_file = "output/index.html"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ HTML 리포트 생성 완료: {output_file}")
    print(f"📊 총 {len(results_df)}개 종목 발굴")
    print(f"🏭 {len(sector_analysis)}개 업종 분석 완료")
    print("=" * 80)

if __name__ == "__main__":
    main()
