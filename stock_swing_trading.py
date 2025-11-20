#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이딩 종목 추천 시스템 v4.2
- DART + KRX 기반 정확한 펀더멘털 계산
- 5개 지표별 TOP5 카드 모두 출력 (복원)
- 투자자 유형 3가지 모두 출력 (복원)
- AI 분석 안정화 (TOP 6만 전송, 오류 처리 강화)
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import requests
import sqlite3
import time
import logging
import json
from typing import Dict, List, Optional, Tuple
import google.generativeai as genai
from google.colab import userdata

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ============================
# 1. SQLite 캐시 관리자
# ============================
class CacheManager:
    def __init__(self, db_path: str = 'financials.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """데이터베이스 및 테이블 초기화"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 재무데이터 캐시 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS financial_cache (
                stock_code TEXT PRIMARY KEY,
                equity REAL,
                net_income REAL,
                cached_at TEXT
            )
        ''')
        
        # 발행주식수 캐시 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shares_cache (
                stock_code TEXT PRIMARY KEY,
                shares_outstanding INTEGER,
                cached_at TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    def get_financial_cache(self, stock_code: str, days: int = 30) -> Optional[Tuple[float, float]]:
        """재무데이터 캐시 조회"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute('''
            SELECT equity, net_income FROM financial_cache
            WHERE stock_code = ? AND cached_at > ?
        ''', (stock_code, cutoff_date))
        
        result = cursor.fetchone()
        conn.close()
        return result

    def set_financial_cache(self, stock_code: str, equity: float, net_income: float):
        """재무데이터 캐시 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO financial_cache (stock_code, equity, net_income, cached_at)
            VALUES (?, ?, ?, ?)
        ''', (stock_code, equity, net_income, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()

    def get_shares_cache(self, stock_code: str, days: int = 7) -> Optional[int]:
        """발행주식수 캐시 조회"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute('''
            SELECT shares_outstanding FROM shares_cache
            WHERE stock_code = ? AND cached_at > ?
        ''', (stock_code, cutoff_date))
        
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def set_shares_cache(self, stock_code: str, shares: int):
        """발행주식수 캐시 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO shares_cache (stock_code, shares_outstanding, cached_at)
            VALUES (?, ?, ?)
        ''', (stock_code, shares, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()

# ============================
# 2. DART 재무제표 수집
# ============================
class DARTFinancials:
    def __init__(self, api_key: str, cache_manager: CacheManager):
        self.api_key = api_key
        self.cache = cache_manager
        self.base_url = "https://opendart.fss.or.kr/api"
        self.request_count = 0
        self.last_request_time = time.time()

    def rate_limit(self):
        """API 속도 제한 (100건/분)"""
        self.request_count += 1
        if self.request_count >= 90:
            elapsed = time.time() - self.last_request_time
            if elapsed < 60:
                sleep_time = 60 - elapsed
                logging.info(f"API 속도 제한: {sleep_time:.1f}초 대기")
                time.sleep(sleep_time)
            self.request_count = 0
            self.last_request_time = time.time()

    def get_corp_code(self, stock_code: str) -> Optional[str]:
        """종목코드로 고유번호 조회"""
        self.rate_limit()
        url = f"{self.base_url}/company.json"
        params = {
            'crtfc_key': self.api_key,
            'corp_code': stock_code.zfill(6)
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == '000':
                    return data.get('corp_code')
        except Exception as e:
            logging.warning(f"{stock_code} 고유번호 조회 실패: {e}")
        return None

    def get_financials(self, stock_code: str) -> Tuple[Optional[float], Optional[float]]:
        """재무제표에서 자본총계, 당기순이익 추출"""
        # 캐시 확인
        cached = self.cache.get_financial_cache(stock_code)
        if cached:
            return cached

        self.rate_limit()
        
        # 최근 분기 계산
        today = datetime.now()
        year = today.year if today.month > 3 else today.year - 1
        quarter = ((today.month - 1) // 3) if today.month > 3 else 4
        reprt_code_map = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}
        reprt_code = reprt_code_map[quarter]

        url = f"{self.base_url}/fnlttSinglAcntAll.json"
        params = {
            'crtfc_key': self.api_key,
            'corp_code': stock_code.zfill(6),
            'bsns_year': str(year),
            'reprt_code': reprt_code,
            'fs_div': 'CFS'  # 연결재무제표
        }

        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                return None, None

            data = response.json()
            if data.get('status') != '000':
                return None, None

            items = data.get('list', [])
            equity = None
            net_income = None

            for item in items:
                account_nm = item.get('account_nm', '')
                thstrm_amount = item.get('thstrm_amount', '').replace(',', '')
                
                # 자본총계
                if '자본총계' in account_nm:
                    try:
                        equity = float(thstrm_amount) * 1_000_000  # 백만원 → 원
                    except:
                        pass
                
                # 당기순이익
                if '당기순이익' in account_nm and '지배' in account_nm:
                    try:
                        net_income = float(thstrm_amount) * 1_000_000
                    except:
                        pass

            # 캐시 저장
            if equity or net_income:
                self.cache.set_financial_cache(stock_code, equity or 0, net_income or 0)

            return equity, net_income

        except Exception as e:
            logging.warning(f"{stock_code} 재무제표 조회 실패: {e}")
            return None, None

# ============================
# 3. KRX 발행주식수 수집
# ============================
class KRXData:
    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager
        self.shares_data = {}
        self.last_update = None

    def load_all_shares(self):
        """KRX에서 전체 종목 발행주식수 로드"""
        if self.last_update and (datetime.now() - self.last_update).days < 1:
            return  # 하루에 한 번만 로드

        url = "http://kind.krx.co.kr/corpgeneral/corpList.do"
        params = {
            'method': 'download',
            'searchType': '13'
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            df = pd.read_html(response.text, header=0)[0]
            
            for _, row in df.iterrows():
                code = str(row['종목코드']).zfill(6)
                shares = row.get('상장주식수', 0)
                if shares > 0:
                    self.shares_data[code] = int(shares)
                    self.cache.set_shares_cache(code, int(shares))

            self.last_update = datetime.now()
            logging.info(f"KRX 발행주식수 로드 완료: {len(self.shares_data)}개 종목")

        except Exception as e:
            logging.error(f"KRX 데이터 로드 실패: {e}")

    def get_shares(self, stock_code: str) -> Optional[int]:
        """발행주식수 조회 (캐시 우선)"""
        # 캐시 확인
        cached = self.cache.get_shares_cache(stock_code)
        if cached:
            return cached

        # 메모리 확인
        if stock_code in self.shares_data:
            return self.shares_data[stock_code]

        # 데이터 없으면 재로드 시도
        if not self.last_update:
            self.load_all_shares()
            return self.shares_data.get(stock_code)

        return None

# ============================
# 4. 메인 분석 로직
# ============================
def get_kospi_kosdaq_list():
    """KOSPI + KOSDAQ 전체 종목 리스트"""
    url_kospi = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt"
    url_kosdaq = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt"
    
    try:
        kospi = pd.read_html(url_kospi, header=0)[0]
        kosdaq = pd.read_html(url_kosdaq, header=0)[0]
        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)
        return all_stocks[['회사명', '종목코드']].values.tolist()
    except Exception as e:
        logging.error(f"종목 리스트 로드 실패: {e}")
        return []

def calculate_indicators(ticker_data, stock_code: str, dart: DARTFinancials, krx: KRXData):
    """6가지 지표 계산 + 펀더멘털"""
    try:
        df = ticker_data.history(period='3mo')
        if df.empty or len(df) < 20:
            return None

        current_price = df['Close'].iloc[-1]
        volume_avg = df['Volume'].iloc[-20:-1].mean()
        current_volume = df['Volume'].iloc[-1]

        # 1. RSI (30점)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        rsi_score = 0
        if current_rsi < 30:
            rsi_score = 30
        elif 30 <= current_rsi < 40:
            rsi_score = 20
        elif 40 <= current_rsi < 50:
            rsi_score = 10

        # 2. 이격도 (20점)
        ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100

        disparity_score = 0
        if disparity < 95:
            disparity_score = 20
        elif 95 <= disparity < 98:
            disparity_score = 15
        elif 98 <= disparity < 100:
            disparity_score = 10

        # 3. 거래량 증가 (15점)
        volume_ratio = current_volume / volume_avg if volume_avg > 0 else 0

        volume_score = 0
        if volume_ratio >= 1.5:
            volume_score = 15
        elif volume_ratio >= 1.2:
            volume_score = 10
        elif volume_ratio >= 1.0:
            volume_score = 5

        # 4. PBR (15점) - DART 계산
        equity, net_income = dart.get_financials(stock_code)
        shares = krx.get_shares(stock_code)

        pbr_score = 0
        per_value = None
        roe_value = None
        bps_value = None
        eps_value = None
        pbr_value = None

        if equity and shares and shares > 0:
            bps_value = equity / shares
            pbr_value = current_price / bps_value if bps_value > 0 else None

            if pbr_value:
                if pbr_value < 1.0:
                    pbr_score = 15
                elif pbr_value < 1.5:
                    pbr_score = 10
                elif pbr_value < 2.0:
                    pbr_score = 5

        if net_income and shares and shares > 0:
            eps_value = net_income / shares
            per_value = current_price / eps_value if eps_value > 0 else None

        if net_income and equity and equity > 0:
            roe_value = (net_income / equity) * 100

        # 5. 5일 수익률 (10점)
        returns_5d = ((df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100) if len(df) >= 6 else 0

        returns_score = 0
        if -5 <= returns_5d <= 0:
            returns_score = 10
        elif -10 <= returns_5d < -5:
            returns_score = 5

        # 6. 반등 강도 (10점)
        low_20d = df['Low'].iloc[-20:].min()
        rebound_strength = ((current_price - low_20d) / low_20d * 100) if low_20d > 0 else 0

        rebound_score = 0
        if rebound_strength >= 5:
            rebound_score = 10
        elif rebound_strength >= 3:
            rebound_score = 5

        total_score = rsi_score + disparity_score + volume_score + pbr_score + returns_score + rebound_score

        # 거래대금 계산
        trading_value = current_price * current_volume

        return {
            'score': total_score,
            'rsi': current_rsi,
            'rsi_score': rsi_score,
            'disparity': disparity,
            'disparity_score': disparity_score,
            'volume_ratio': volume_ratio,
            'volume_score': volume_score,
            'pbr': pbr_value,
            'pbr_score': pbr_score,
            'returns_5d': returns_5d,
            'returns_score': returns_score,
            'rebound_strength': rebound_strength,
            'rebound_score': rebound_score,
            'current_price': current_price,
            'trading_value': trading_value,
            'bps': bps_value,
            'eps': eps_value,
            'per': per_value,
            'roe': roe_value
        }

    except Exception as e:
        logging.warning(f"지표 계산 오류: {e}")
        return None

def get_market_data():
    """시장 지수 및 환율 정보"""
    try:
        kospi = yf.Ticker("^KS11").history(period='2d')
        kosdaq = yf.Ticker("^KQ11").history(period='2d')
        usd = yf.Ticker("KRW=X").history(period='1d')
        eur = yf.Ticker("EURKRW=X").history(period='1d')
        jpy = yf.Ticker("JPYKRW=X").history(period='1d')

        return {
            'kospi': kospi['Close'].iloc[-1] if not kospi.empty else 0,
            'kospi_change': ((kospi['Close'].iloc[-1] - kospi['Close'].iloc[-2]) / kospi['Close'].iloc[-2] * 100) if len(kospi) >= 2 else 0,
            'kosdaq': kosdaq['Close'].iloc[-1] if not kosdaq.empty else 0,
            'kosdaq_change': ((kosdaq['Close'].iloc[-1] - kosdaq['Close'].iloc[-2]) / kosdaq['Close'].iloc[-2] * 100) if len(kosdaq) >= 2 else 0,
            'usd': usd['Close'].iloc[-1] if not usd.empty else 0,
            'eur': eur['Close'].iloc[-1] if not eur.empty else 0,
            'jpy': jpy['Close'].iloc[-1] if not jpy.empty else 0
        }
    except:
        return {'kospi': 0, 'kospi_change': 0, 'kosdaq': 0, 'kosdaq_change': 0, 'usd': 0, 'eur': 0, 'jpy': 0}

def get_gemini_analysis(top_stocks: List[Dict]) -> str:
    """
    Gemini AI 분석 (v4.2: TOP 6만 전송, 오류 처리 강화)
    """
    try:
        api_key = userdata.get('swingTrading')
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash-exp')

        # v4.2: TOP 6만 전송
        analysis_data = []
        for stock in top_stocks[:6]:  # TOP 30 → TOP 6
            analysis_data.append({
                '종목명': stock['name'],
                '현재가': f"{stock['price']:,.0f}원",
                '총점': f"{stock['score']}점",
                'RSI': f"{stock['rsi']:.1f}",
                '이격도': f"{stock['disparity']:.1f}%",
                '거래량비율': f"{stock['volume_ratio']:.2f}배",
                'PBR': f"{stock['pbr']:.2f}" if stock['pbr'] else 'N/A',
                'PER': f"{stock['per']:.1f}" if stock['per'] else 'N/A',
                'ROE': f"{stock['roe']:.1f}%" if stock['roe'] else 'N/A'
            })

        prompt = f"""
당신은 20년 경력의 한국 주식 전문 애널리스트입니다.
아래 스윙 트레이딩 추천 종목 TOP 6을 분석하여, 투자자들이 이해하기 쉽게 요약해주세요.

📊 분석 데이터:
{json.dumps(analysis_data, ensure_ascii=False, indent=2)}

📋 분석 가이드:
1. 전체적인 시장 관점에서 이 종목들의 공통점은 무엇인가요? (업종, 테마, 기술적 특징)
2. 가장 주목해야 할 종목 1-2개와 그 이유는?
3. 단기 스윙 트레이딩 관점에서의 진입 타이밍 조언
4. 주의사항 (리스크 요인)

⚠️ 출력 형식:
- 3-5문단, 총 200자 이내
- 구체적 종목명 언급
- 전문가답게 간결하고 명확하게
"""

        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        logging.warning(f"Gemini API 오류: {e}")
        # v4.2: Fallback 메시지
        return """
        <div style='text-align:center; padding:20px; color:#888;'>
            ⚠️ 데이터가 부족하여 AI 분석을 생략합니다
        </div>
        """

def generate_html(top_stocks: List[Dict], market_data: Dict, ai_analysis: str, timestamp: str):
    """
    HTML 보고서 생성 (v4.2: 지표별 5개 + 투자자 유형 3가지 복원)
    """
    # TOP 6 차트 생성
    top6_cards = ""
    for i, stock in enumerate(top_stocks[:6], 1):
        ticker = yf.Ticker(f"{stock['code']}.KS" if stock['code'].startswith('0') else f"{stock['code']}.KQ")
        hist = ticker.history(period='3mo')
        
        chart_data = []
        if not hist.empty:
            for date, row in hist.iterrows():
                chart_data.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'close': float(row['Close'])
                })

        chart_json = json.dumps(chart_data)

        # 펀더멘털 표시
        fundamentals = f"""
        <div style='display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; font-size:13px;'>
            <div><strong>PER:</strong> {stock['per']:.1f if stock['per'] else 'N/A'}</div>
            <div><strong>PBR:</strong> {stock['pbr']:.2f if stock['pbr'] else 'N/A'}</div>
            <div><strong>ROE:</strong> {stock['roe']:.1f if stock['roe'] else 'N/A'}%</div>
            <div><strong>BPS:</strong> {stock['bps']:,.0f if stock['bps'] else 'N/A'}원</div>
        </div>
        """

        top6_cards += f"""
        <div style='background:white; padding:20px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'>
                <div>
                    <h3 style='margin:0; color:#2c3e50;'>{i}. {stock['name']}</h3>
                    <p style='margin:5px 0; color:#7f8c8d; font-size:14px;'>{stock['code']}</p>
                </div>
                <div style='text-align:right;'>
                    <div style='font-size:24px; font-weight:bold; color:#e74c3c;'>{stock['score']}점</div>
                    <div style='font-size:18px; color:#2c3e50;'>{stock['price']:,.0f}원</div>
                </div>
            </div>
            <canvas id='chart{i}' width='400' height='200'></canvas>
            {fundamentals}
            <div style='display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:15px;'>
                <div style='background:#ecf0f1; padding:10px; border-radius:5px; text-align:center;'>
                    <div style='font-size:12px; color:#7f8c8d;'>RSI</div>
                    <div style='font-size:16px; font-weight:bold; color:#e74c3c;'>{stock['rsi']:.1f}</div>
                </div>
                <div style='background:#ecf0f1; padding:10px; border-radius:5px; text-align:center;'>
                    <div style='font-size:12px; color:#7f8c8d;'>이격도</div>
                    <div style='font-size:16px; font-weight:bold; color:#e67e22;'>{stock['disparity']:.1f}%</div>
                </div>
                <div style='background:#ecf0f1; padding:10px; border-radius:5px; text-align:center;'>
                    <div style='font-size:12px; color:#7f8c8d;'>거래량</div>
                    <div style='font-size:16px; font-weight:bold; color:#27ae60;'>{stock['volume_ratio']:.2f}배</div>
                </div>
            </div>
        </div>
        <script>
        (function() {{
            var ctx = document.getElementById('chart{i}').getContext('2d');
            var data = {chart_json};
            var labels = data.map(d => d.date);
            var prices = data.map(d => d.close);
            
            var minPrice = Math.min(...prices);
            var maxPrice = Math.max(...prices);
            var range = maxPrice - minPrice;
            var padding = range * 0.1;
            
            var canvas = ctx.canvas;
            var width = canvas.width;
            var height = canvas.height;
            
            ctx.strokeStyle = '#3498db';
            ctx.lineWidth = 2;
            ctx.beginPath();
            
            prices.forEach((price, i) => {{
                var x = (i / (prices.length - 1)) * width;
                var y = height - ((price - minPrice + padding) / (range + 2 * padding)) * height;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }});
            ctx.stroke();
        }})();
        </script>
        """

    # TOP 7-30 테이블
    table_rows = ""
    for i, stock in enumerate(top_stocks[6:30], 7):
        table_rows += f"""
        <tr>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1;'>{i}</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; font-weight:bold;'>{stock['name']}</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1;'>{stock['code']}</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:right;'>{stock['price']:,.0f}원</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:center; font-weight:bold; color:#e74c3c;'>{stock['score']}점</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:center;'>{stock['rsi']:.1f}</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:center;'>{stock['disparity']:.1f}%</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:center;'>{stock['volume_ratio']:.2f}배</td>
            <td style='padding:12px; border-bottom:1px solid #ecf0f1; text-align:center;'>{stock['pbr']:.2f if stock['pbr'] else 'N/A'}</td>
        </tr>
        """

    # ============================================
    # v4.2 수정 1: 지표별 분석 5개 복원
    # ============================================
    # 각 지표별 TOP 5 추출
    top_rsi = sorted([s for s in top_stocks if s.get('rsi')], key=lambda x: x['rsi'])[:5]
    top_disparity = sorted([s for s in top_stocks if s.get('disparity')], key=lambda x: x['disparity'])[:5]
    top_volume = sorted([s for s in top_stocks if s.get('volume_ratio')], key=lambda x: x['volume_ratio'], reverse=True)[:5]
    top_rebound = sorted([s for s in top_stocks if s.get('rebound_strength')], key=lambda x: x['rebound_strength'], reverse=True)[:5]
    top_pbr = sorted([s for s in top_stocks if s.get('pbr')], key=lambda x: x['pbr'])[:5]

    # 지표별 카드 생성 함수
    def make_indicator_card(title: str, description: str, stocks: List[Dict], value_key: str, value_format: str, color: str):
        items = ""
        for rank, stock in enumerate(stocks, 1):
            value = stock.get(value_key)
            if value is None:
                continue
            if value_format == 'ratio':
                value_str = f"{value:.2f}배"
            elif value_format == 'percent':
                value_str = f"{value:.1f}%"
            elif value_format == 'score':
                value_str = f"{value:.1f}"
            else:
                value_str = f"{value:.2f}"
            
            items += f"""
            <div style='padding:10px; border-bottom:1px solid #ecf0f1;'>
                <div style='display:flex; justify-content:space-between;'>
                    <span style='font-weight:bold;'>{rank}. {stock['name']}</span>
                    <span style='color:{color}; font-weight:bold;'>{value_str}</span>
                </div>
                <div style='font-size:12px; color:#7f8c8d; margin-top:5px;'>
                    현재가: {stock['price']:,.0f}원 | 총점: {stock['score']}점
                </div>
            </div>
            """
        
        return f"""
        <div style='background:white; padding:20px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <h3 style='margin:0 0 10px 0; color:#2c3e50; border-bottom:3px solid {color}; padding-bottom:10px;'>
                {title}
            </h3>
            <p style='color:#7f8c8d; font-size:14px; margin-bottom:15px;'>{description}</p>
            {items}
        </div>
        """

    indicator_cards = f"""
    <div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px; margin-bottom:30px;'>
        {make_indicator_card(
            "📉 RSI 과매도 TOP 5",
            "RSI 30 이하로 단기 반등 가능성이 높은 종목",
            top_rsi,
            'rsi',
            'score',
            '#e74c3c'
        )}
        {make_indicator_card(
            "📊 이격도 하락 TOP 5",
            "20일 이평선 대비 5% 이상 하락한 저점 매수 기회",
            top_disparity,
            'disparity',
            'percent',
            '#e67e22'
        )}
        {make_indicator_card(
            "📈 거래량 급증 TOP 5",
            "평균 대비 1.5배 이상 거래량으로 관심 집중",
            top_volume,
            'volume_ratio',
            'ratio',
            '#27ae60'
        )}
        {make_indicator_card(
            "💪 반등 강도 TOP 5",
            "20일 저점 대비 5% 이상 반등한 모멘텀 종목",
            top_rebound,
            'rebound_strength',
            'percent',
            '#9b59b6'
        )}
        {make_indicator_card(
            "💎 저PBR 가치주 TOP 5",
            "PBR 1.0 미만 저평가 우량주",
            top_pbr,
            'pbr',
            'score',
            '#3498db'
        )}
    </div>
    """

    # ============================================
    # v4.2 수정 2: 투자자 유형 3가지 복원
    # ============================================
    # 공격적 투자자: 거래량 + 반등 강도 높은 종목
    aggressive = sorted(
        [s for s in top_stocks if s.get('volume_ratio') and s.get('rebound_strength')],
        key=lambda x: x['volume_ratio'] + x['rebound_strength'],
        reverse=True
    )[:5]

    # 균형잡힌 투자자: 총점 기준
    balanced = top_stocks[:5]

    # 보수적 투자자: PBR/PER 낮고 이격도 낮은 안정적 종목
    conservative = sorted(
        [s for s in top_stocks if s.get('pbr') and s.get('disparity')],
        key=lambda x: (x['pbr'] or 999) + (100 - x['disparity']),
    )[:5]

    def make_investor_card(title: str, description: str, stocks: List[Dict], icon: str, color: str):
        items = ""
        for rank, stock in enumerate(stocks, 1):
            items += f"""
            <div style='padding:10px; border-bottom:1px solid #ecf0f1;'>
                <div style='display:flex; justify-content:space-between;'>
                    <span style='font-weight:bold;'>{rank}. {stock['name']}</span>
                    <span style='color:{color}; font-weight:bold;'>{stock['score']}점</span>
                </div>
                <div style='font-size:12px; color:#7f8c8d; margin-top:5px;'>
                    현재가: {stock['price']:,.0f}원 | RSI: {stock['rsi']:.1f} | 이격도: {stock['disparity']:.1f}%
                </div>
            </div>
            """
        
        return f"""
        <div style='background:white; padding:20px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <h3 style='margin:0 0 10px 0; color:#2c3e50; border-bottom:3px solid {color}; padding-bottom:10px;'>
                {icon} {title}
            </h3>
            <p style='color:#7f8c8d; font-size:14px; margin-bottom:15px;'>{description}</p>
            {items}
        </div>
        """

    investor_cards = f"""
    <div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px; margin-bottom:30px;'>
        {make_investor_card(
            "공격적 투자자",
            "거래량 급증 + 강한 반등으로 단기 수익 추구",
            aggressive,
            "🔥",
            "#e74c3c"
        )}
        {make_investor_card(
            "균형잡힌 투자자",
            "종합 점수 기반 안정적 스윙 트레이딩",
            balanced,
            "⚖️",
            "#f39c12"
        )}
        {make_investor_card(
            "보수적 투자자",
            "저PBR + 안정적 이격도로 장기 가치 투자",
            conservative,
            "🛡️",
            "#27ae60"
        )}
    </div>
    """

    # 최종 HTML
    html = f"""
    <!DOCTYPE html>
    <html lang='ko'>
    <head>
        <meta charset='UTF-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1.0'>
        <title>스윙 트레이딩 종목 추천 v4.2 - {timestamp}</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                background: #f8f9fa;
                padding: 30px;
                border-radius: 15px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.3);
            }}
            h1 {{
                color: #2c3e50;
                text-align: center;
                margin-bottom: 10px;
                font-size: 32px;
            }}
            .timestamp {{
                text-align: center;
                color: #7f8c8d;
                margin-bottom: 30px;
                font-size: 14px;
            }}
            .market-overview {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 30px;
            }}
            .market-card {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                text-align: center;
            }}
            .ai-analysis {{
                background: white;
                padding: 25px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 30px;
                border-left: 5px solid #3498db;
            }}
            .top-stocks {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            table {{
                width: 100%;
                background: white;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 30px;
            }}
            th {{
                background: #34495e;
                color: white;
                padding: 15px;
                text-align: left;
            }}
        </style>
    </head>
    <body>
        <div class='container'>
            <h1>📊 스윙 트레이딩 종목 추천 리포트 v4.2</h1>
            <div class='timestamp'>생성 시간: {timestamp}</div>
            
            <div class='market-overview'>
                <div class='market-card'>
                    <h3 style='margin:0; color:#e74c3c;'>KOSPI</h3>
                    <div style='font-size:24px; font-weight:bold; margin:10px 0;'>{market_data['kospi']:.2f}</div>
                    <div style='color:{"#27ae60" if market_data['kospi_change'] >= 0 else "#e74c3c"};'>
                        {market_data['kospi_change']:+.2f}%
                    </div>
                </div>
                <div class='market-card'>
                    <h3 style='margin:0; color:#3498db;'>KOSDAQ</h3>
                    <div style='font-size:24px; font-weight:bold; margin:10px 0;'>{market_data['kosdaq']:.2f}</div>
                    <div style='color:{"#27ae60" if market_data['kosdaq_change'] >= 0 else "#e74c3c"};'>
                        {market_data['kosdaq_change']:+.2f}%
                    </div>
                </div>
                <div class='market-card'>
                    <h3 style='margin:0; color:#f39c12;'>USD/KRW</h3>
                    <div style='font-size:24px; font-weight:bold; margin:10px 0;'>{market_data['usd']:.2f}</div>
                </div>
                <div class='market-card'>
                    <h3 style='margin:0; color:#9b59b6;'>EUR/KRW</h3>
                    <div style='font-size:24px; font-weight:bold; margin:10px 0;'>{market_data['eur']:.2f}</div>
                </div>
                <div class='market-card'>
                    <h3 style='margin:0; color:#1abc9c;'>JPY/KRW</h3>
                    <div style='font-size:24px; font-weight:bold; margin:10px 0;'>{market_data['jpy']:.2f}</div>
                </div>
            </div>

            <div class='ai-analysis'>
                <h2 style='margin:0 0 15px 0; color:#2c3e50;'>🤖 AI 시장 분석</h2>
                <div style='line-height:1.8; color:#34495e;'>{ai_analysis}</div>
            </div>

            <h2 style='color:#2c3e50; margin-bottom:20px;'>🏆 TOP 6 추천 종목</h2>
            <div class='top-stocks'>
                {top6_cards}
            </div>

            <h2 style='color:#2c3e50; margin-bottom:20px;'>📋 TOP 7-30 종목 리스트</h2>
            <table>
                <thead>
                    <tr>
                        <th>순위</th>
                        <th>종목명</th>
                        <th>종목코드</th>
                        <th style='text-align:right;'>현재가</th>
                        <th style='text-align:center;'>총점</th>
                        <th style='text-align:center;'>RSI</th>
                        <th style='text-align:center;'>이격도</th>
                        <th style='text-align:center;'>거래량비율</th>
                        <th style='text-align:center;'>PBR</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>

            <h2 style='color:#2c3e50; margin-bottom:20px;'>📊 지표별 분석 (v4.2 복원)</h2>
            {indicator_cards}

            <h2 style='color:#2c3e50; margin-bottom:20px;'>👥 투자자 유형별 추천 (v4.2 복원)</h2>
            {investor_cards}

            <div style='text-align:center; margin-top:40px; padding:20px; background:white; border-radius:10px;'>
                <p style='color:#7f8c8d; margin:0;'>
                    ⚠️ 본 리포트는 투자 참고용이며, 투자 판단의 책임은 투자자 본인에게 있습니다.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

    return html

def main():
    """메인 실행 함수"""
    logging.info("=== 스윙 트레이딩 분석 시작 v4.2 ===")
    
    # API 키 로드
    dart_api_key = userdata.get('DART_API')
    
    # 모듈 초기화
    cache = CacheManager()
    dart = DARTFinancials(dart_api_key, cache)
    krx = KRXData(cache)
    
    # KRX 데이터 로드
    logging.info("KRX 발행주식수 로드 중...")
    krx.load_all_shares()
    
    # 종목 리스트 로드
    logging.info("종목 리스트 로드 중...")
    stock_list = get_kospi_kosdaq_list()
    logging.info(f"총 {len(stock_list)}개 종목 로드 완료")
    
    # 분석 실행
    results = []
    for i, (name, code) in enumerate(stock_list, 1):
        if i % 100 == 0:
            logging.info(f"진행률: {i}/{len(stock_list)} ({i/len(stock_list)*100:.1f}%)")
        
        try:
            ticker = yf.Ticker(f"{code}.KS" if code.startswith('0') else f"{code}.KQ")
            indicators = calculate_indicators(ticker, code, dart, krx)
            
            if indicators and indicators['score'] >= 30 and indicators['trading_value'] >= 100_000_000:
                results.append({
                    'name': name,
                    'code': code,
                    'score': indicators['score'],
                    'price': indicators['current_price'],
                    'rsi': indicators['rsi'],
                    'disparity': indicators['disparity'],
                    'volume_ratio': indicators['volume_ratio'],
                    'pbr': indicators['pbr'],
                    'per': indicators['per'],
                    'roe': indicators['roe'],
                    'bps': indicators['bps'],
                    'eps': indicators['eps'],
                    'rebound_strength': indicators['rebound_strength']
                })
        except Exception as e:
            logging.warning(f"{name}({code}) 분석 실패: {e}")
            continue
    
    # 정렬
    results.sort(key=lambda x: x['score'], reverse=True)
    top_stocks = results[:30]
    
    logging.info(f"✅ 총 {len(results)}개 종목 중 TOP 30 추출 완료")
    
    # 시장 데이터 및 AI 분석
    market_data = get_market_data()
    
    # v4.2: AI 분석 안정화
    logging.info("Gemini AI 분석 중 (TOP 6)...")
    ai_analysis = get_gemini_analysis(top_stocks)
    
    # HTML 생성
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(top_stocks, market_data, ai_analysis, timestamp)
    
    # 파일 저장
    filename = f"stock_result_v4.2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    logging.info(f"=== 분석 완료: {filename} ===")
    print(f"\n✅ 리포트 생성 완료: {filename}")
    print(f"📊 TOP 10 종목:")
    for i, stock in enumerate(top_stocks[:10], 1):
        print(f"  {i}. {stock['name']} ({stock['code']}) - {stock['score']}점")

if __name__ == "__main__":
    main()
