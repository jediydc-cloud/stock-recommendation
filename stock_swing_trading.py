#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이딩 종목 추천 시스템 v4.2.10 FINAL
- v4.2.9: Gemini 2.5 모델 적용 성공
- v4.2.10: pykrx 기반 시장 데이터 (KOSPI/KOSDAQ) + 뉴스 아이콘 추가
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
from multiprocessing import Pool
import warnings
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# yfinance 에러 로그 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ============================
# 1. SQLite 캐시 관리자
# ============================
class CacheManager:
    def __init__(self, db_path: str = 'financials.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS financial_cache (
                stock_code TEXT PRIMARY KEY,
                equity REAL,
                net_income REAL,
                cached_at TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shares_cache (
                stock_code TEXT PRIMARY KEY,
                shares_outstanding INTEGER,
                cached_at TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def get_financial_cache(self, stock_code: str, days: int = 30):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute('SELECT equity, net_income FROM financial_cache WHERE stock_code = ? AND cached_at > ?', 
                      (stock_code, cutoff_date))
        result = cursor.fetchone()
        conn.close()
        return result

    def set_financial_cache(self, stock_code: str, equity: float, net_income: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO financial_cache (stock_code, equity, net_income, cached_at) VALUES (?, ?, ?, ?)',
                      (stock_code, equity, net_income, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_shares_cache(self, stock_code: str, days: int = 7):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute('SELECT shares_outstanding FROM shares_cache WHERE stock_code = ? AND cached_at > ?',
                      (stock_code, cutoff_date))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def set_shares_cache(self, stock_code: str, shares: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO shares_cache (stock_code, shares_outstanding, cached_at) VALUES (?, ?, ?)',
                      (stock_code, shares, datetime.now().isoformat()))
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
        self.request_count += 1
        if self.request_count >= 90:
            elapsed = time.time() - self.last_request_time
            if elapsed < 60:
                sleep_time = 60 - elapsed
                time.sleep(sleep_time)
            self.request_count = 0
            self.last_request_time = time.time()

    def get_financials(self, stock_code: str):
        """DART API 호출 (실패 시 None 반환)"""
        cached = self.cache.get_financial_cache(stock_code)
        if cached:
            return cached
        
        self.rate_limit()
        
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
            'fs_div': 'CFS'
        }

        try:
            response = requests.get(url, params=params, timeout=10)
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
                
                if '자본총계' in account_nm:
                    try:
                        equity = float(thstrm_amount) * 1_000_000
                    except:
                        pass
                
                if '당기순이익' in account_nm and '지배' in account_nm:
                    try:
                        net_income = float(thstrm_amount) * 1_000_000
                    except:
                        pass

            if equity or net_income:
                self.cache.set_financial_cache(stock_code, equity or 0, net_income or 0)

            return equity, net_income
        except:
            return None, None

# ============================
# 3. KRX 발행주식수 수집
# ============================
class KRXData:
    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager
        self.shares_data = {}

    def load_all_shares(self):
        url = "http://kind.krx.co.kr/corpgeneral/corpList.do"
        params = {'method': 'download', 'searchType': '13'}

        try:
            response = requests.get(url, params=params, timeout=30)
            df = pd.read_html(response.content, header=0, encoding='euc-kr')[0]
            
            for _, row in df.iterrows():
                code = str(row['종목코드']).zfill(6)
                shares = row.get('상장주식수', 0)
                if shares > 0:
                    self.shares_data[code] = int(shares)
                    self.cache.set_shares_cache(code, int(shares))

            logging.info(f"KRX 발행주식수: {len(self.shares_data)}개")
        except Exception as e:
            logging.error(f"KRX 로드 실패: {e}")

    def get_shares(self, stock_code: str):
        cached = self.cache.get_shares_cache(stock_code)
        if cached:
            return cached
        return self.shares_data.get(stock_code)

# ============================
# 4. 메인 분석 로직
# ============================
def get_kospi_kosdaq_list():
    url_kospi = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt"
    url_kosdaq = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt"
    
    try:
        kospi_r = requests.get(url_kospi, timeout=30)
        kosdaq_r = requests.get(url_kosdaq, timeout=30)
        
        kospi = pd.read_html(kospi_r.content, header=0, encoding='euc-kr')[0]
        kosdaq = pd.read_html(kosdaq_r.content, header=0, encoding='euc-kr')[0]
        
        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)
        
        filtered = []
        for _, row in all_stocks.iterrows():
            name, code = row['회사명'], row['종목코드']
            if any(k in name for k in ['우', 'ETN', 'SPAC', '스팩', '리츠', '인프라']):
                continue
            if not code.isdigit():
                continue
            filtered.append([name, code])
        
        logging.info(f"필터링: {len(all_stocks)} → {len(filtered)}개")
        return filtered
    except Exception as e:
        logging.error(f"종목 리스트 로드 실패: {e}")
        return []

# v4.2.7 FIX: chart_data 추가
def analyze_stock_worker(args):
    """워커 프로세스 (10초 타임아웃)"""
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError()
    
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)
    
    try:
        name, code, dart_key = args
        
        # yfinance 데이터 로드
        ticker = yf.Ticker(f"{code}.KS" if code.startswith('0') else f"{code}.KQ")
        df = ticker.history(period='3mo')
        
        if df.empty or len(df) < 20:
            return None
        
        # v4.2.7 FIX: 차트 데이터 여기서 추출
        chart_data = [
            {'date': d.strftime('%Y-%m-%d'), 'close': float(r['Close'])} 
            for d, r in df.iterrows()
        ]
        
        current_price = df['Close'].iloc[-1]
        volume_avg = df['Volume'].iloc[-20:-1].mean()
        current_volume = df['Volume'].iloc[-1]
        
        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        rsi_score = 30 if current_rsi < 30 else 20 if current_rsi < 40 else 10 if current_rsi < 50 else 0
        
        # 이격도
        ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100
        disparity_score = 20 if disparity < 95 else 15 if disparity < 98 else 10 if disparity < 100 else 0
        
        # 거래량
        volume_ratio = current_volume / volume_avg if volume_avg > 0 else 0
        volume_score = 15 if volume_ratio >= 1.5 else 10 if volume_ratio >= 1.2 else 5 if volume_ratio >= 1.0 else 0
        
        # v4.2.6 FIX: DART → yfinance fallback
        cache = CacheManager()
        dart = DARTFinancials(dart_key, cache)
        krx = KRXData(cache)
        
        # ① DART 시도
        equity, net_income = dart.get_financials(code)
        
        # ② DART 실패 시 yfinance fallback
        if not equity or not net_income:
            try:
                balance_sheet = ticker.balance_sheet
                financials = ticker.financials
                
                if not balance_sheet.empty:
                    # Total Stockholder Equity
                    if 'Total Stockholder Equity' in balance_sheet.index:
                        equity = balance_sheet.loc['Total Stockholder Equity'].iloc[0]
                    elif 'Stockholders Equity' in balance_sheet.index:
                        equity = balance_sheet.loc['Stockholders Equity'].iloc[0]
                
                if not financials.empty:
                    # Net Income
                    if 'Net Income' in financials.index:
                        net_income = financials.loc['Net Income'].iloc[0]
            except:
                pass
        
        # ③ KRX → yfinance sharesOutstanding fallback
        shares = krx.get_shares(code)
        if not shares:
            try:
                info = ticker.info
                shares = info.get('sharesOutstanding') or info.get('floatShares')
            except:
                pass
        
        pbr_score = 0
        pbr_value = None
        per_value = None
        roe_value = None
        bps_value = None
        eps_value = None
        
        if equity and shares and shares > 0:
            bps_value = equity / shares
            pbr_value = current_price / bps_value if bps_value > 0 else None
            
            if pbr_value:
                pbr_score = 15 if pbr_value < 1.0 else 10 if pbr_value < 1.5 else 5 if pbr_value < 2.0 else 0
        
        if net_income and shares and shares > 0:
            eps_value = net_income / shares
            per_value = current_price / eps_value if eps_value > 0 else None
        
        if net_income and equity and equity > 0:
            roe_value = (net_income / equity) * 100
        
        # 5일 수익률
        returns_5d = ((df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100) if len(df) >= 6 else 0
        returns_score = 10 if -5 <= returns_5d <= 0 else 5 if -10 <= returns_5d < -5 else 0
        
        # 반등 강도
        low_20d = df['Low'].iloc[-20:].min()
        rebound_strength = ((current_price - low_20d) / low_20d * 100) if low_20d > 0 else 0
        rebound_score = 10 if rebound_strength >= 5 else 5 if rebound_strength >= 3 else 0
        
        total_score = rsi_score + disparity_score + volume_score + pbr_score + returns_score + rebound_score
        trading_value = current_price * current_volume
        
        if total_score >= 30 and trading_value >= 100_000_000:
            return {
                'name': name, 'code': code, 'score': total_score, 'price': current_price,
                'rsi': current_rsi, 'disparity': disparity, 'volume_ratio': volume_ratio,
                'pbr': pbr_value, 'per': per_value, 'roe': roe_value, 'bps': bps_value,
                'eps': eps_value, 'rebound_strength': rebound_strength,
                'chart_data': chart_data  # v4.2.7 FIX: 차트 데이터 포함
            }
        return None
    except:
        return None
    finally:
        signal.alarm(0)

def get_market_data():
    """v4.2.10 FIX: pykrx 우선 사용하여 한국 시장 데이터 안정적으로 가져오기"""
    result = {'kospi': None, 'kospi_change': None, 'kosdaq': None, 'kosdaq_change': None, 'usd': None, 'eur': None, 'jpy': None}
    
    # KOSPI/KOSDAQ: pykrx 사용
    try:
        from pykrx import stock
        from datetime import timedelta
        
        today = datetime.now()
        # 최근 7일간 시도 (주말/공휴일 대응)
        for days_back in range(7):
            try:
                end_date = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                start_date = (today - timedelta(days=days_back+5)).strftime('%Y%m%d')
                
                # KOSPI (1001)
                kospi_df = stock.get_index_ohlcv(start_date, end_date, "1001")
                if len(kospi_df) >= 2:
                    result['kospi'] = kospi_df['종가'].iloc[-1]
                    result['kospi_change'] = ((kospi_df['종가'].iloc[-1] - kospi_df['종가'].iloc[-2]) / kospi_df['종가'].iloc[-2] * 100)
                    break
                elif len(kospi_df) == 1:
                    result['kospi'] = kospi_df['종가'].iloc[-1]
                    break
            except:
                continue
        
        # KOSDAQ (2001)
        for days_back in range(7):
            try:
                end_date = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                start_date = (today - timedelta(days=days_back+5)).strftime('%Y%m%d')
                
                kosdaq_df = stock.get_index_ohlcv(start_date, end_date, "2001")
                if len(kosdaq_df) >= 2:
                    result['kosdaq'] = kosdaq_df['종가'].iloc[-1]
                    result['kosdaq_change'] = ((kosdaq_df['종가'].iloc[-1] - kosdaq_df['종가'].iloc[-2]) / kosdaq_df['종가'].iloc[-2] * 100)
                    break
                elif len(kosdaq_df) == 1:
                    result['kosdaq'] = kosdaq_df['종가'].iloc[-1]
                    break
            except:
                continue
                
        logging.info(f"pykrx 시장 데이터: KOSPI={result['kospi']}, KOSDAQ={result['kosdaq']}")
        
    except Exception as e:
        logging.warning(f"pykrx 실패: {e}")
    
    # 환율: yfinance 사용
    try:
        usd = yf.Ticker("KRW=X").history(period='5d')
        result['usd'] = usd['Close'].iloc[-1] if not usd.empty else None
        
        eur = yf.Ticker("EURKRW=X").history(period='5d')
        result['eur'] = eur['Close'].iloc[-1] if not eur.empty else None
        
        jpy = yf.Ticker("JPYKRW=X").history(period='5d')
        result['jpy'] = jpy['Close'].iloc[-1] if not jpy.empty else None
    except Exception as e:
        logging.warning(f"환율 실패: {e}")
    
    return result


def get_gemini_analysis(top_stocks):
    try:
        api_key = userdata.get('swingTrading')
        genai.configure(api_key=api_key)
        
        # v4.2.9 FIX: gemini-2.5-flash 사용 (1.5는 지원 종료, 2.5가 현재 최신 안정 버전)
        model = genai.GenerativeModel('gemini-2.5-flash')

        data = []
        for s in top_stocks[:6]:
            data.append({
                '종목명': s['name'], '현재가': f"{s['price']:,.0f}원", '총점': f"{s['score']}점",
                'RSI': f"{s['rsi']:.1f}", '이격도': f"{s['disparity']:.1f}%",
                '거래량비율': f"{s['volume_ratio']:.2f}배",
                'PBR': f"{s['pbr']:.2f}" if s['pbr'] else 'N/A',
                'PER': f"{s['per']:.1f}" if s['per'] else 'N/A',
                'ROE': f"{s['roe']:.1f}%" if s['roe'] else 'N/A'
            })

        prompt = f"""20년 경력 애널리스트로 TOP 6 종목 분석:
{json.dumps(data, ensure_ascii=False, indent=2)}

1. 공통점 2. 주목 종목 3. 진입 타이밍 4. 리스크
200자 이내, 종목명 언급"""

        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.warning(f"Gemini 오류: {e}")
        return "<div style='text-align:center; padding:20px; color:#888;'>⚠️ 데이터가 부족하여 AI 분석을 생략합니다</div>"

def safe_format(value, fmt, default='N/A'):
    if value is None:
        return default
    try:
        return format(value, fmt)
    except:
        return default

def generate_html(top_stocks, market_data, ai_analysis, timestamp):
    """HTML 보고서 생성 (v4.2.4와 동일한 디자인 유지)"""
    # TOP 6 카드
    top6_cards = ""
    for i, s in enumerate(top_stocks[:6], 1):
        # v4.2.7 FIX: chart_data를 worker에서 받음 (yfinance 재호출 제거)
        chart_data = s.get('chart_data', [])
        chart_json = json.dumps(chart_data)
        
        per_str = safe_format(s['per'], '.1f')
        pbr_str = safe_format(s['pbr'], '.2f')
        roe_str = safe_format(s['roe'], '.1f') + '%' if s['roe'] else 'N/A'
        bps_str = safe_format(s['bps'], ',.0f') + '원' if s['bps'] else 'N/A'
        
        top6_cards += f"""
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;'>
                <div><h3 style='margin:0;color:#2c3e50;'>{i}. {s['name']} <a href='https://search.naver.com/search.naver?where=news&query={s['name']}' target='_blank' style='text-decoration:none;font-size:18px;' title='뉴스 검색'>📰</a></h3><p style='margin:5px 0;color:#7f8c8d;font-size:14px;'>{s['code']}</p></div>
                <div style='text-align:right;'><div style='font-size:24px;font-weight:bold;color:#e74c3c;'>{s['score']}점</div><div style='font-size:18px;color:#2c3e50;'>{s['price']:,.0f}원</div></div>
            </div>
            <canvas id='chart{i}' width='400' height='200'></canvas>
            <div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;font-size:13px;'>
                <div><strong>PER:</strong> {per_str}</div><div><strong>PBR:</strong> {pbr_str}</div>
                <div><strong>ROE:</strong> {roe_str}</div><div><strong>BPS:</strong> {bps_str}</div>
            </div>
            <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px;'>
                <div style='background:#ecf0f1;padding:10px;border-radius:5px;text-align:center;'><div style='font-size:12px;color:#7f8c8d;'>RSI</div><div style='font-size:16px;font-weight:bold;color:#e74c3c;'>{s['rsi']:.1f}</div></div>
                <div style='background:#ecf0f1;padding:10px;border-radius:5px;text-align:center;'><div style='font-size:12px;color:#7f8c8d;'>이격도</div><div style='font-size:16px;font-weight:bold;color:#e67e22;'>{s['disparity']:.1f}%</div></div>
                <div style='background:#ecf0f1;padding:10px;border-radius:5px;text-align:center;'><div style='font-size:12px;color:#7f8c8d;'>거래량</div><div style='font-size:16px;font-weight:bold;color:#27ae60;'>{s['volume_ratio']:.2f}배</div></div>
            </div>
        </div>
        <script>
        (function(){{var ctx=document.getElementById('chart{i}').getContext('2d');var data={chart_json};if(data.length===0)return;var prices=data.map(d=>d.close);var minPrice=Math.min(...prices);var maxPrice=Math.max(...prices);var range=maxPrice-minPrice;var padding=range*0.1;var canvas=ctx.canvas;var width=canvas.width;var height=canvas.height;ctx.strokeStyle='#3498db';ctx.lineWidth=2;ctx.beginPath();prices.forEach((price,i)=>{{var x=(i/(prices.length-1))*width;var y=height-((price-minPrice+padding)/(range+2*padding))*height;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});ctx.stroke();}})();
        </script>
        """
    
    # TOP 7-30 테이블
    table_rows = ""
    for i, s in enumerate(top_stocks[6:30], 7):
        pbr_display = safe_format(s.get('pbr'), '.2f')
        table_rows += f"<tr><td style='padding:12px;border-bottom:1px solid #ecf0f1;'>{i}</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;font-weight:bold;'>{s['name']} <a href='https://search.naver.com/search.naver?where=news&query={s['name']}' target='_blank' style='text-decoration:none;font-size:14px;' title='뉴스 검색'>📰</a></td><td style='padding:12px;border-bottom:1px solid #ecf0f1;'>{s['code']}</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:right;'>{s['price']:,.0f}원</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;font-weight:bold;color:#e74c3c;'>{s['score']}점</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['rsi']:.1f}</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['disparity']:.1f}%</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['volume_ratio']:.2f}배</td><td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{pbr_display}</td></tr>"
    
    # 지표별 분석
    top_rsi = sorted([s for s in top_stocks if s.get('rsi')], key=lambda x: x['rsi'])[:5]
    top_disparity = sorted([s for s in top_stocks if s.get('disparity')], key=lambda x: x['disparity'])[:5]
    top_volume = sorted([s for s in top_stocks if s.get('volume_ratio')], key=lambda x: x['volume_ratio'], reverse=True)[:5]
    top_rebound = sorted([s for s in top_stocks if s.get('rebound_strength')], key=lambda x: x['rebound_strength'], reverse=True)[:5]
    top_pbr = sorted([s for s in top_stocks if s.get('pbr')], key=lambda x: x['pbr'])[:5]
    
    # v4.2.7 FIX: safe_format 사용 + 단위 추가
    def make_card(title, stocks, key, fmt, unit, color):
        items = "".join([
            f"<div style='padding:10px;border-bottom:1px solid #ecf0f1;'>"
            f"<div style='display:flex;justify-content:space-between;'>"
            f"<span style='font-weight:bold;'>{i}. {s['name']}</span>"
            f"<span style='color:{color};font-weight:bold;'>{safe_format(s.get(key), fmt)}{unit}</span>"
            f"</div>"
            f"<div style='font-size:12px;color:#7f8c8d;margin-top:5px;'>현재가: {s['price']:,.0f}원 | 총점: {s['score']}점</div>"
            f"</div>"
            for i, s in enumerate(stocks, 1)
        ])
        return f"<div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='margin:0 0 10px 0;color:#2c3e50;border-bottom:3px solid {color};padding-bottom:10px;'>{title}</h3>{items}</div>"
    
    indicator_cards = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:30px;'>"
        f"{make_card('📉 RSI 과매도 TOP 5', top_rsi, 'rsi', '.1f', '', '#e74c3c')}"
        f"{make_card('📊 이격도 하락 TOP 5', top_disparity, 'disparity', '.1f', '%', '#e67e22')}"
        f"{make_card('📈 거래량 급증 TOP 5', top_volume, 'volume_ratio', '.2f', '배', '#27ae60')}"
        f"{make_card('💪 반등 강도 TOP 5', top_rebound, 'rebound_strength', '.1f', '%', '#9b59b6')}"
        f"{make_card('💎 저PBR 가치주 TOP 5', top_pbr, 'pbr', '.2f', '', '#3498db')}"
        "</div>"
    )
    
    # 투자자 유형
    aggressive = sorted([s for s in top_stocks if s.get('volume_ratio') and s.get('rebound_strength')], key=lambda x: x['volume_ratio']+x['rebound_strength'], reverse=True)[:5]
    balanced = top_stocks[:5]
    conservative = sorted([s for s in top_stocks if s.get('pbr') and s.get('disparity')], key=lambda x: (x['pbr'] or 999)+(100-x['disparity']))[:5]
    
    def make_investor(title, stocks, icon, color):
        items = "".join([f"<div style='padding:10px;border-bottom:1px solid #ecf0f1;'><div style='display:flex;justify-content:space-between;'><span style='font-weight:bold;'>{i}. {s['name']}</span><span style='color:{color};font-weight:bold;'>{s['score']}점</span></div><div style='font-size:12px;color:#7f8c8d;margin-top:5px;'>현재가: {s['price']:,.0f}원 | RSI: {s['rsi']:.1f} | 이격도: {s['disparity']:.1f}%</div></div>" for i, s in enumerate(stocks, 1)])
        return f"<div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='margin:0 0 10px 0;color:#2c3e50;border-bottom:3px solid {color};padding-bottom:10px;'>{icon} {title}</h3>{items}</div>"
    
    investor_cards = f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:30px;'>{make_investor('공격적 투자자', aggressive, '🔥', '#e74c3c')}{make_investor('균형잡힌 투자자', balanced, '⚖️', '#f39c12')}{make_investor('보수적 투자자', conservative, '🛡️', '#27ae60')}</div>"
    
    # v4.2.8 FIX: market_data None 처리 (TypeError 방지)
    kospi_str = f"{market_data['kospi']:.2f}" if market_data['kospi'] else 'N/A'
    kospi_change_str = f"{market_data['kospi_change']:+.2f}%" if market_data['kospi_change'] is not None else 'N/A'
    kosdaq_str = f"{market_data['kosdaq']:.2f}" if market_data['kosdaq'] else 'N/A'
    kosdaq_change_str = f"{market_data['kosdaq_change']:+.2f}%" if market_data['kosdaq_change'] is not None else 'N/A'
    usd_str = f"{market_data['usd']:.2f}" if market_data['usd'] else 'N/A'
    eur_str = f"{market_data['eur']:.2f}" if market_data['eur'] else 'N/A'
    jpy_str = f"{market_data['jpy']:.2f}" if market_data['jpy'] else 'N/A'
    
    kospi_color = "#27ae60" if (market_data.get('kospi_change') or 0) >= 0 else "#e74c3c"
    kosdaq_color = "#27ae60" if (market_data.get('kosdaq_change') or 0) >= 0 else "#e74c3c"
    
    html = f"""<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'><title>스윙 트레이딩 v4.2.10 FINAL - {timestamp}</title><style>body{{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;}}.container{{max-width:1400px;margin:0 auto;background:#f8f9fa;padding:30px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,0.3);}}h1{{color:#2c3e50;text-align:center;margin-bottom:10px;font-size:32px;}}.timestamp{{text-align:center;color:#7f8c8d;margin-bottom:30px;font-size:14px;}}.market-overview{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin-bottom:30px;}}.market-card{{background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);text-align:center;}}.ai-analysis{{background:white;padding:25px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;border-left:5px solid #3498db;}}.top-stocks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-bottom:30px;}}table{{width:100%;background:white;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;}}th{{background:#34495e;color:white;padding:15px;text-align:left;}}</style></head><body><div class='container'><h1>📊 스윙 트레이딩 종목 추천 v4.2.8 FINAL</h1><div class='timestamp'>생성 시간: {timestamp}</div><div class='market-overview'><div class='market-card'><h3 style='margin:0;color:#e74c3c;'>KOSPI</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{kospi_str}</div><div style='color:{kospi_color};'>{kospi_change_str}</div></div><div class='market-card'><h3 style='margin:0;color:#3498db;'>KOSDAQ</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{kosdaq_str}</div><div style='color:{kosdaq_color};'>{kosdaq_change_str}</div></div><div class='market-card'><h3 style='margin:0;color:#95a5a6;'>USD/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{usd_str}</div></div><div class='market-card'><h3 style='margin:0;color:#95a5a6;'>EUR/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{eur_str}</div></div><div class='market-card'><h3 style='margin:0;color:#95a5a6;'>JPY/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{jpy_str}</div></div></div><div class='ai-analysis'><h2 style='margin:0 0 15px 0;color:#2c3e50;'>🤖 AI 종합 분석</h2>{ai_analysis}</div><h2 style='color:#2c3e50;margin:30px 0 20px;'>🏆 추천 종목 TOP 30</h2><div class='top-stocks'>{top6_cards}</div><table><thead><tr><th>순위</th><th>종목명</th><th>코드</th><th>현재가</th><th>총점</th><th>RSI</th><th>이격도</th><th>거래량비율</th><th>PBR</th></tr></thead><tbody>{table_rows}</tbody></table><h2 style='color:#2c3e50;margin:30px 0 20px;'>📈 지표별 TOP 5</h2>{indicator_cards}<h2 style='color:#2c3e50;margin:30px 0 20px;'>👥 투자자 유형별 추천</h2>{investor_cards}</div></body></html>"""
    return html

def main():
    logging.info("=== v4.2.10 FINAL 시작 ===")
    
    dart_key = userdata.get('DART_API')
    cache = CacheManager()
    krx = KRXData(cache)
    
    krx.load_all_shares()
    stock_list = get_kospi_kosdaq_list()
    logging.info(f"분석 대상: {len(stock_list)}개")
    
    # 병렬 처리
    args = [(name, code, dart_key) for name, code in stock_list]
    
    logging.info("병렬 분석 시작 (4 프로세스)...")
    with Pool(processes=4) as pool:
        results = []
        for i, result in enumerate(pool.imap_unordered(analyze_stock_worker, args), 1):
            if i % 100 == 0:
                logging.info(f"진행: {i}/{len(stock_list)} ({i/len(stock_list)*100:.1f}%)")
            if result:
                results.append(result)
    
    results.sort(key=lambda x: x['score'], reverse=True)
    top_stocks = results[:30]
    
    logging.info(f"✅ {len(results)}개 추출")
    
    market_data = get_market_data()
    ai_analysis = get_gemini_analysis(top_stocks)
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html = generate_html(top_stocks, market_data, ai_analysis, timestamp)
    
    filename = f"stock_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)
    
    logging.info(f"=== 완료: {filename} ===")
    print(f"\n✅ {filename}")
    
    # v4.2.7 FIX: 콘솔 출력 TypeError 수정
    for i, s in enumerate(top_stocks[:10], 1):
        print(f"  {i}. {s['name']} ({s['code']}) - {s['score']}점")
        per_str = "N/A" if not s.get('per') else f"{s['per']:.1f}"
        pbr_str = "N/A" if not s.get('pbr') else f"{s['pbr']:.2f}"
        roe_str = "N/A" if not s.get('roe') else f"{s['roe']:.1f}%"
        print(f"      PER: {per_str} | PBR: {pbr_str} | ROE: {roe_str}")


if __name__ == "__main__":
    main()
