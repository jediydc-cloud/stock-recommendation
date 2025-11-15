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
        today = datetime.now().strftime("%Y%m%d")
        fundamental = stock.get_market_fundamental(today, today, code)
        if len(fundamental) > 0:
            return fundamental['PBR'].iloc[0]
        return 999
    except:
        return 999

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
        
        # 점수 계산
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
            '종합점수': score
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
    
    # 점수순 정렬
    results_df = pd.DataFrame(results)
    if len(results_df) > 0:
        results_df = results_df.sort_values('종합점수', ascending=False)
    
    print("=" * 80)
    print(f"✅ 스캔 완료! 총 {len(results_df)}개 종목 발굴")
    print("=" * 80)
    
    return results_df

def get_market_summary():
    """시장 요약 정보"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        
        # 코스피 지수
        kospi_df = stock.get_index_ohlcv(today, today, "1001")
        kospi_current = kospi_df['종가'].iloc[-1] if len(kospi_df) > 0 else 0
        
        # 코스닥 지수
        kosdaq_df = stock.get_index_ohlcv(today, today, "2001")
        kosdaq_current = kosdaq_df['종가'].iloc[-1] if len(kosdaq_df) > 0 else 0
        
        return {
            'kospi': round(kospi_current, 2),
            'kosdaq': round(kosdaq_current, 2)
        }
    except:
        return {'kospi': 0, 'kosdaq': 0}

def get_top_sectors(results_df):
    """상위 업종 분석"""
    if len(results_df) == 0:
        return []
    
    # 업종 정보 가져오기 (간단 버전)
    sector_counts = {}
    for code in results_df['종목코드'].head(20):
        try:
            # 업종 정보는 pykrx에서 제공하지 않으므로 생략
            pass
        except:
            pass
    
    return []

def generate_html(results_df, market_summary):
    """HTML 리포트 생성 (4개 섹션 구조 + 새로고침 버튼)"""
    
    current_time = datetime.now().strftime("%Y년 %m월 %d일 %H:%M:%S")
    
    # TOP 8 추천 종목
    top_8 = results_df.head(8)
    top_8_html = ""
    for idx, row in top_8.iterrows():
        badge_color = "#FF6B6B" if row['종합점수'] >= 80 else "#FFA500" if row['종합점수'] >= 65 else "#4CAF50"
        top_8_html += f"""
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 15px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <h3 style="margin: 0; color: #2C3E50; font-size: 20px;">{row['종목명']}</h3>
                <span style="background: {badge_color}; color: white; padding: 5px 15px; border-radius: 20px; font-weight: bold;">{row['종합점수']}점</span>
            </div>
            <div style="color: #7F8C8D; font-size: 14px; margin-bottom: 10px;">종목코드: {row['종목코드']}</div>
            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-top: 15px;">
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">현재가</div>
                    <div style="font-size: 18px; font-weight: bold; color: #2C3E50;">{row['현재가']:,}원</div>
                </div>
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">5일 수익률</div>
                    <div style="font-size: 18px; font-weight: bold; color: {'#E74C3C' if row['5일수익률'] < 0 else '#27AE60'};">{row['5일수익률']:+.2f}%</div>
                </div>
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">RSI</div>
                    <div style="font-size: 16px; color: #3498DB;">{row['RSI']:.1f}</div>
                </div>
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">이격도</div>
                    <div style="font-size: 16px; color: #9B59B6;">{row['이격도']:.1f}%</div>
                </div>
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">거래량 비율</div>
                    <div style="font-size: 16px; color: #E67E22;">{row['거래량비율']:.0f}%</div>
                </div>
                <div>
                    <div style="color: #95A5A6; font-size: 12px;">PBR</div>
                    <div style="font-size: 16px; color: #1ABC9C;">{row['PBR']:.2f}</div>
                </div>
            </div>
        </div>
        """
    
    # 전체 종목 테이블
    all_stocks_html = ""
    for idx, row in results_df.iterrows():
        all_stocks_html += f"""
        <tr style="border-bottom: 1px solid #ECF0F1;">
            <td style="padding: 12px; text-align: center;">{row['종목명']}</td>
            <td style="padding: 12px; text-align: center;">{row['종목코드']}</td>
            <td style="padding: 12px; text-align: right; font-weight: bold;">{row['현재가']:,}원</td>
            <td style="padding: 12px; text-align: center; color: {'#E74C3C' if row['5일수익률'] < 0 else '#27AE60'}; font-weight: bold;">{row['5일수익률']:+.2f}%</td>
            <td style="padding: 12px; text-align: center;">{row['RSI']:.1f}</td>
            <td style="padding: 12px; text-align: center;">{row['이격도']:.1f}%</td>
            <td style="padding: 12px; text-align: center;">{row['거래량비율']:.0f}%</td>
            <td style="padding: 12px; text-align: center;">{row['PBR']:.2f}</td>
            <td style="padding: 12px; text-align: center; font-weight: bold; color: #E67E22;">{row['종합점수']}</td>
        </tr>
        """
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>💎 AI 주식 추천 시스템 - 실시간 분석</title>
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
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            }}
            .header h1 {{
                color: #2C3E50;
                font-size: 32px;
                margin-bottom: 10px;
            }}
            .update-time {{
                color: #7F8C8D;
                font-size: 14px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .refresh-btn {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                margin-top: 15px;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                transition: all 0.3s ease;
            }}
            .refresh-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
            }}
            .market-summary {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .market-card {{
                background: white;
                border-radius: 12px;
                padding: 20px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .market-card h3 {{
                color: #7F8C8D;
                font-size: 14px;
                margin-bottom: 8px;
            }}
            .market-card .value {{
                color: #2C3E50;
                font-size: 28px;
                font-weight: bold;
            }}
            .section {{
                background: white;
                border-radius: 16px;
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            }}
            .section h2 {{
                color: #2C3E50;
                font-size: 24px;
                margin-bottom: 20px;
                border-left: 4px solid #667eea;
                padding-left: 15px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}
            th {{
                background: #F8F9FA;
                color: #2C3E50;
                padding: 15px;
                text-align: center;
                font-weight: 600;
                border-bottom: 2px solid #E0E0E0;
            }}
            .info-box {{
                background: #F0F8FF;
                border-left: 4px solid #3498DB;
                padding: 15px;
                margin: 20px 0;
                border-radius: 4px;
            }}
            .info-box h4 {{
                color: #2C3E50;
                margin-bottom: 8px;
            }}
            .info-box p {{
                color: #7F8C8D;
                font-size: 14px;
                line-height: 1.6;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- 헤더 -->
            <div class="header">
                <h1>💎 AI 주식 추천 시스템</h1>
                <div class="update-time">
                    <span>📊 마지막 업데이트: {current_time}</span>
                    <span>|</span>
                    <span>🔍 전체 종목 스캔: 2,700개</span>
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
                </div>
                <div class="market-card">
                    <h3>📊 코스닥 지수</h3>
                    <div class="value">{market_summary['kosdaq']}</div>
                </div>
                <div class="market-card">
                    <h3>🎯 발굴 종목 수</h3>
                    <div class="value">{len(results_df)}개</div>
                </div>
            </div>

            <!-- 섹션 1: TOP 8 추천 종목 -->
            <div class="section">
                <h2>🏆 TOP 8 추천 종목</h2>
                <div class="info-box">
                    <h4>💡 선정 기준</h4>
                    <p>종합점수 = RSI(과매도) + 이격도(저평가) + 거래량(급증) + PBR(저평가)</p>
                    <p>• 80점 이상: 🔴 최우선 매수 후보 | 65~79점: 🟠 관심 종목 | 50~64점: 🟢 모니터링</p>
                </div>
                {top_8_html}
            </div>

            <!-- 섹션 2: 기술적 시그널 설명 -->
            <div class="section">
                <h2>📊 기술적 지표 가이드</h2>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px;">
                    <div class="info-box">
                        <h4>📉 RSI (상대강도지수)</h4>
                        <p><strong>30 미만:</strong> 과매도 구간 (반등 가능성 ⬆️)</p>
                        <p><strong>30~50:</strong> 안정적 매수 구간</p>
                        <p><strong>70 이상:</strong> 과매수 구간 (조정 가능성)</p>
                    </div>
                    <div class="info-box">
                        <h4>📐 이격도</h4>
                        <p><strong>90% 미만:</strong> 20일 평균가 대비 저평가</p>
                        <p><strong>100% 근처:</strong> 적정 가격</p>
                        <p><strong>110% 이상:</strong> 과열 구간</p>
                    </div>
                    <div class="info-box">
                        <h4>📊 거래량 비율</h4>
                        <p><strong>200% 이상:</strong> 큰 손 매집 의심 🔥</p>
                        <p><strong>150~200%:</strong> 관심 증가</p>
                        <p><strong>100% 미만:</strong> 평소 수준</p>
                    </div>
                    <div class="info-box">
                        <h4>💰 PBR (주가순자산비율)</h4>
                        <p><strong>0.5 미만:</strong> 초저평가 (자산 대비)</p>
                        <p><strong>1.0 미만:</strong> 저평가</p>
                        <p><strong>1.5 이상:</strong> 프리미엄 평가</p>
                    </div>
                </div>
            </div>

            <!-- 섹션 3: 전체 종목 리스트 -->
            <div class="section">
                <h2>📋 전체 발굴 종목 ({len(results_df)}개)</h2>
                <div style="overflow-x: auto;">
                    <table>
                        <thead>
                            <tr>
                                <th>종목명</th>
                                <th>종목코드</th>
                                <th>현재가</th>
                                <th>5일 수익률</th>
                                <th>RSI</th>
                                <th>이격도</th>
                                <th>거래량 비율</th>
                                <th>PBR</th>
                                <th>종합점수</th>
                            </tr>
                        </thead>
                        <tbody>
                            {all_stocks_html}
                        </tbody>
                    </table>
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
    
    # 3. HTML 생성
    html_content = generate_html(results_df, market_summary)
    
    # 4. 파일 저장 (output 폴더에 저장)
    os.makedirs("output", exist_ok=True)
    output_file = "output/index.html"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ HTML 리포트 생성 완료: {output_file}")
    print(f"📊 총 {len(results_df)}개 종목 발굴")
    print("=" * 80)

if __name__ == "__main__":
    main()
