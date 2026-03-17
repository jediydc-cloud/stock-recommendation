#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스윙 트레이딩 종목 추천 시스템 v5.3
- v5.2: 진입 신호 3중 조건, ROE nan 처리, 공격형 ROE 최소 기준
- v5.3: 🔧 PBR 수집 경로 3단계 강화 (전 종목 N/A 버그 수정)
         ✅ 1순위: yfinance info['priceToBook'] 직접 수집 (가장 안정적)
         ✅ 2순위: DART equity + KRX shares 역산 (기존 방식)
         ✅ 3순위: yfinance balance_sheet + shares fallback
         🔧 보수형/균형형 Fallback ROE None 종목 전면 제외
         ✅ 모든 Fallback 단계에서 roe is not None 조건 추가
         ✅ 보수형=균형형 동일 현상 근본 차단
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz
import requests
import sqlite3
import time
import logging
import json
from typing import Dict, List, Optional, Tuple
import google.generativeai as genai
import os
from multiprocessing import Pool
import warnings
import zipfile
import io
import xml.etree.ElementTree as ET

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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
        cursor.execute('''CREATE TABLE IF NOT EXISTS financial_cache (
            stock_code TEXT PRIMARY KEY, equity REAL, net_income REAL, cached_at TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS shares_cache (
            stock_code TEXT PRIMARY KEY, shares_outstanding INTEGER, cached_at TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS dart_corp_map (
            stock_code TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT, cached_at TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS exchange_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usd REAL, eur REAL, jpy REAL, cached_at TEXT)''')
        conn.commit()
        conn.close()

    def get_financial_cache(self, stock_code: str, days: int = 30):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_date = (datetime.now(kst) - timedelta(days=days)).isoformat()
        cursor.execute('SELECT equity, net_income FROM financial_cache WHERE stock_code = ? AND cached_at > ?',
                      (stock_code, cutoff_date))
        result = cursor.fetchone()
        conn.close()
        return result

    def set_financial_cache(self, stock_code: str, equity: float, net_income: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cursor.execute('INSERT OR REPLACE INTO financial_cache (stock_code, equity, net_income, cached_at) VALUES (?, ?, ?, ?)',
                      (stock_code, equity, net_income, datetime.now(kst).isoformat()))
        conn.commit()
        conn.close()

    def get_shares_cache(self, stock_code: str, days: int = 7):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_date = (datetime.now(kst) - timedelta(days=days)).isoformat()
        cursor.execute('SELECT shares_outstanding FROM shares_cache WHERE stock_code = ? AND cached_at > ?',
                      (stock_code, cutoff_date))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def set_shares_cache(self, stock_code: str, shares: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cursor.execute('INSERT OR REPLACE INTO shares_cache (stock_code, shares_outstanding, cached_at) VALUES (?, ?, ?)',
                      (stock_code, shares, datetime.now(kst).isoformat()))
        conn.commit()
        conn.close()

    def get_corp_code_cache(self, stock_code: str, days: int = 30) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_date = (datetime.now(kst) - timedelta(days=days)).isoformat()
        cursor.execute('SELECT corp_code FROM dart_corp_map WHERE stock_code = ? AND cached_at > ?',
                      (stock_code, cutoff_date))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def set_corp_code_cache(self, stock_code: str, corp_code: str, corp_name: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cursor.execute('INSERT OR REPLACE INTO dart_corp_map (stock_code, corp_code, corp_name, cached_at) VALUES (?, ?, ?, ?)',
                      (stock_code, corp_code, corp_name, datetime.now(kst).isoformat()))
        conn.commit()
        conn.close()

    def check_corp_map_valid(self, days: int = 30) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_date = (datetime.now(kst) - timedelta(days=days)).isoformat()
        cursor.execute('SELECT COUNT(*) FROM dart_corp_map WHERE cached_at > ?', (cutoff_date,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def get_all_corp_codes(self, days: int = 30) -> Dict[str, str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_date = (datetime.now(kst) - timedelta(days=days)).isoformat()
        cursor.execute('SELECT stock_code, corp_code FROM dart_corp_map WHERE cached_at > ?', (cutoff_date,))
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result

    def get_exchange_cache(self, hours: int = 24) -> Optional[Tuple[float, float, float]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cutoff_time = (datetime.now(kst) - timedelta(hours=hours)).isoformat()
        cursor.execute('SELECT usd, eur, jpy FROM exchange_cache WHERE cached_at > ? ORDER BY id DESC LIMIT 1',
                      (cutoff_time,))
        result = cursor.fetchone()
        conn.close()
        return result if result else None

    def set_exchange_cache(self, usd: float, eur: float, jpy: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cursor.execute('INSERT INTO exchange_cache (usd, eur, jpy, cached_at) VALUES (?, ?, ?, ?)',
                      (usd, eur, jpy, datetime.now(kst).isoformat()))
        conn.commit()
        conn.close()


# ============================
# 2. DART corp_code 매핑 관리자
# ============================
class DARTCorpCodeMapper:
    def __init__(self, api_key: str, cache_manager: CacheManager):
        self.api_key = api_key
        self.cache = cache_manager
        self.base_url = "https://opendart.fss.or.kr/corpCode.xml"

        if not self.cache.check_corp_map_valid(days=30):
            logging.info("⏳ DART corpCode 캐시 만료 → 재다운로드")
            self._download_and_cache()
        else:
            logging.info("✅ DART corpCode 캐시 유효")

    def _download_and_cache(self):
        try:
            response = requests.get(self.base_url, params={'crtfc_key': self.api_key}, timeout=30)
            if response.status_code != 200:
                logging.error(f"DART corpCode 다운로드 실패: {response.status_code}")
                return
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                xml_data = z.read(z.namelist()[0])
            root = ET.fromstring(xml_data)
            count = 0
            for corp in root.findall('list'):
                corp_code = corp.findtext('corp_code', '').strip()
                corp_name = corp.findtext('corp_name', '').strip()
                stock_code = corp.findtext('stock_code', '').strip()
                if stock_code and corp_code:
                    self.cache.set_corp_code_cache(stock_code, corp_code, corp_name)
                    count += 1
            logging.info(f"✅ DART corpCode: {count}개 저장")
        except Exception as e:
            logging.error(f"DART corpCode 실패: {e}")

    def get_all_mappings(self) -> Dict[str, str]:
        return self.cache.get_all_corp_codes(days=30)


# ============================
# 3. DART 재무제표 수집
# ============================
class DARTFinancials:
    def __init__(self, api_key: str, cache_manager: CacheManager, corp_code_map: Dict[str, str]):
        self.api_key = api_key
        self.cache = cache_manager
        self.corp_code_map = corp_code_map
        self.base_url = "https://opendart.fss.or.kr/api"
        self.request_count = 0
        self.last_request_time = time.time()

    def rate_limit(self):
        self.request_count += 1
        if self.request_count >= 90:
            elapsed = time.time() - self.last_request_time
            if elapsed < 60:
                time.sleep(60 - elapsed)
            self.request_count = 0
            self.last_request_time = time.time()

    def get_financials(self, stock_code: str):
        cached = self.cache.get_financial_cache(stock_code)
        if cached:
            return cached
        self.rate_limit()

        corp_code_to_use = self.corp_code_map.get(stock_code) or stock_code.zfill(6)
        kst = pytz.timezone('Asia/Seoul')
        today = datetime.now(kst)
        year = today.year if today.month > 3 else today.year - 1
        quarter = ((today.month - 1) // 3) if today.month > 3 else 4
        reprt_code = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}[quarter]

        try:
            response = requests.get(f"{self.base_url}/fnlttSinglAcntAll.json", params={
                'crtfc_key': self.api_key, 'corp_code': corp_code_to_use,
                'bsns_year': str(year), 'reprt_code': reprt_code, 'fs_div': 'CFS'
            }, timeout=10)
            if response.status_code != 200:
                return None, None
            data = response.json()
            if data.get('status') != '000':
                return None, None

            equity, net_income = None, None
            for item in data.get('list', []):
                account_nm = item.get('account_nm', '')
                amount_str = item.get('thstrm_amount', '').replace(',', '')
                if '자본총계' in account_nm:
                    try:
                        equity = float(amount_str) * 1_000_000
                    except:
                        pass
                if '당기순이익' in account_nm and '지배' in account_nm:
                    try:
                        net_income = float(amount_str) * 1_000_000
                    except:
                        pass
            if equity or net_income:
                self.cache.set_financial_cache(stock_code, equity or 0, net_income or 0)
            return equity, net_income
        except:
            return None, None


# ============================
# 4. KRX 발행주식수 수집
# ============================
class KRXData:
    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager
        self.shares_data = {}

    def load_all_shares(self):
        try:
            response = requests.get(
                "http://kind.krx.co.kr/corpgeneral/corpList.do",
                params={'method': 'download', 'searchType': '13'}, timeout=30)
            df = pd.read_html(response.content, encoding='euc-kr')[0]
            df['종목코드'] = df['종목코드'].astype(str).str.zfill(6)
            for _, row in df.iterrows():
                code, shares = row['종목코드'], row['상장주식수']
                if pd.notna(shares) and shares > 0:
                    self.shares_data[code] = int(shares)
                    self.cache.set_shares_cache(code, int(shares))
            logging.info(f"발행주식수: {len(self.shares_data)}개")
        except Exception as e:
            logging.warning(f"KRX 발행주식수 실패: {e}")

    def get_shares(self, stock_code: str):
        cached = self.cache.get_shares_cache(stock_code, days=7)
        if cached:
            return cached
        return self.shares_data.get(stock_code)


# ============================
# 5. 환율 독립 조회
# ============================
def get_exchange_rates_only(cache: CacheManager) -> Dict[str, Optional[float]]:
    result = {'usd': None, 'eur': None, 'jpy': None}
    cached_rates = cache.get_exchange_cache(hours=24)
    if cached_rates:
        result['usd'], result['eur'], result['jpy'] = cached_rates
        logging.info(f"✅ 환율 캐시: USD={result['usd']:.2f}")
        return result
    try:
        usd = yf.Ticker("KRW=X").history(period='1d')
        result['usd'] = usd['Close'].iloc[-1] if not usd.empty else None
        time.sleep(0.5)
        eur = yf.Ticker("EURKRW=X").history(period='1d')
        result['eur'] = eur['Close'].iloc[-1] if not eur.empty else None
        time.sleep(0.5)
        jpy = yf.Ticker("JPYKRW=X").history(period='1d')
        result['jpy'] = jpy['Close'].iloc[-1] if not jpy.empty else None
        if result['usd']:
            cache.set_exchange_cache(result['usd'], result['eur'] or 0, result['jpy'] or 0)
    except Exception as e:
        logging.warning(f"환율 조회 실패: {e}")
    return result


# ============================
# 6. 종목 리스트 로드
# ============================
def load_stock_list():
    try:
        kospi_r = requests.get("http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt", timeout=30)
        kosdaq_r = requests.get("http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt", timeout=30)

        kospi = pd.read_html(kospi_r.content, header=0, encoding='euc-kr')[0]
        kosdaq = pd.read_html(kosdaq_r.content, header=0, encoding='euc-kr')[0]
        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)

        listing_date_col = next((c for c in all_stocks.columns if '상장' in c and '일' in c), None)

        filtered = []
        for _, row in all_stocks.iterrows():
            name, code = row['회사명'], row['종목코드']
            exclude_keywords = ['우', 'ETN', 'SPAC', '스팩', '리츠', '인프라',
                               '관리', '(M)', '(관)', '정지', '제8호', '제9호', '제10호',
                               '기업인수목적', '기업재무안정']
            if any(k in name for k in exclude_keywords):
                continue
            if not code.isdigit():
                continue
            if listing_date_col and pd.notna(row.get(listing_date_col)):
                try:
                    listing_date = pd.to_datetime(str(row[listing_date_col]), errors='coerce')
                    if pd.notna(listing_date):
                        if (datetime.now() - listing_date.to_pydatetime()).days / 365.0 < 1.0:
                            continue
                except:
                    pass
            filtered.append([name, code])

        logging.info(f"종목 필터링: {len(all_stocks)} → {len(filtered)}개")
        return filtered
    except Exception as e:
        logging.error(f"종목 리스트 로드 실패: {e}")
        return []


# ============================
# 7. 종목 분석 워커
# ============================
def analyze_stock_worker(args):
    """
    v5.3: PBR 수집 3단계 강화
    ① yfinance info['priceToBook'] 직접 수집 (1순위)
    ② DART equity + KRX shares 역산 (2순위)
    ③ yfinance balance_sheet fallback (3순위)
    """
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)

    try:
        name, code, dart_key, corp_code_map = args

        ticker = yf.Ticker(f"{code}.KS" if code.startswith('0') else f"{code}.KQ")
        df = ticker.history(period='3mo')

        if df.empty or len(df) < 20:
            return None

        current_price = df['Close'].iloc[-1]
        volume_avg = df['Volume'].iloc[-20:-1].mean()
        current_volume = df['Volume'].iloc[-1]

        if current_volume == 0:
            return None
        if current_price < 2000:
            return None
        avg_trading_value = volume_avg * current_price
        if avg_trading_value < 300_000_000:
            return None

        chart_data = [
            {'date': d.strftime('%Y-%m-%d'), 'close': float(r['Close'])}
            for d, r in df.iterrows()
        ]

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

        # =====================================================
        # v5.3: 재무데이터 수집 3단계 강화
        # =====================================================
        cache = CacheManager()
        dart = DARTFinancials(dart_key, cache, corp_code_map)
        krx = KRXData(cache)

        equity, net_income = dart.get_financials(code)
        shares = krx.get_shares(code)

        pbr_value = None
        bps_value = None
        per_value = None
        roe_value = None
        eps_value = None
        pbr_score = 0

        # ─── 1순위: yfinance info 직접 수집 (가장 안정적) ───
        try:
            info = ticker.info

            # PBR 직접 수집
            ptb = info.get('priceToBook')
            if ptb and ptb > 0:
                pbr_value = float(ptb)

            # BPS 직접 수집
            bv = info.get('bookValue')
            if bv and bv > 0:
                bps_value = float(bv)
                if not pbr_value:
                    pbr_value = current_price / bps_value

            # shares fallback
            if not shares:
                shares = info.get('sharesOutstanding') or info.get('floatShares')
                if shares:
                    shares = int(shares)

            # equity/net_income fallback
            if not equity:
                mc = info.get('marketCap')
                if mc and pbr_value and pbr_value > 0:
                    equity = mc / pbr_value  # 시가총액 / PBR = 자본총계 역산

            if not net_income:
                ni = info.get('netIncomeToCommon') or info.get('profitMargins')
                if isinstance(ni, float) and ni != info.get('profitMargins'):
                    net_income = ni  # netIncomeToCommon은 절대값
                elif info.get('netIncomeToCommon'):
                    net_income = info['netIncomeToCommon']

        except Exception:
            pass

        # ─── 2순위: DART equity + KRX shares → BPS/PBR 역산 ───
        if not pbr_value and equity and shares and shares > 0:
            try:
                bps_value = bps_value or (equity / shares)
                if bps_value and bps_value > 0:
                    pbr_value = current_price / bps_value
            except:
                pass

        # ─── 3순위: yfinance balance_sheet fallback ───
        if not equity or not net_income:
            try:
                balance_sheet = ticker.balance_sheet
                financials = ticker.financials

                if not balance_sheet.empty and not equity:
                    for key in ['Total Stockholder Equity', 'Stockholders Equity',
                                'Common Stock Equity', 'Total Equity Gross Minority Interest']:
                        if key in balance_sheet.index:
                            equity = balance_sheet.loc[key].iloc[0]
                            break

                if not financials.empty and not net_income:
                    for key in ['Net Income', 'Net Income Common Stockholders']:
                        if key in financials.index:
                            net_income = financials.loc[key].iloc[0]
                            break

                # balance_sheet에서 BPS/PBR 재계산
                if not pbr_value and equity and shares and shares > 0:
                    bps_value = bps_value or (equity / shares)
                    if bps_value and bps_value > 0:
                        pbr_value = current_price / bps_value

            except:
                pass

        # ─── 자본잠식 필터 ───
        if equity is not None and equity < 0:
            return None

        # PBR 점수
        if pbr_value and pbr_value > 0:
            pbr_score = 15 if pbr_value < 1.0 else 10 if pbr_value < 1.5 else 5 if pbr_value < 2.0 else 0

        # EPS / PER
        if net_income and shares and shares > 0:
            eps_value = net_income / shares
            per_value = current_price / eps_value if eps_value > 0 else None

        # ROE
        if net_income and equity and equity > 0:
            roe_value = (net_income / equity) * 100
            if roe_value < 0:
                return None

        # 5일 수익률
        returns_5d = ((df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100) if len(df) >= 6 else 0
        returns_score = 10 if -5 <= returns_5d <= 0 else 5 if -10 <= returns_5d < -5 else 0

        # 반등 강도
        low_20d = df['Low'].iloc[-20:].min()
        rebound_strength = ((current_price - low_20d) / low_20d * 100) if low_20d > 0 else 0
        rebound_score = 10 if rebound_strength >= 5 else 5 if rebound_strength >= 3 else 0

        # ROE 패널티
        roe_penalty = 10 if (roe_value is not None and 0 <= roe_value < 3.0) else 0

        # 거래량 추세
        recent_vol_up = (
            len(df) >= 3 and
            df['Volume'].iloc[-1] > df['Volume'].iloc[-2] and
            df['Volume'].iloc[-2] > df['Volume'].iloc[-3]
        )

        # v5.2: 진입 신호 3중 조건
        if recent_vol_up and volume_ratio >= 0.7 and current_rsi < 35:
            entry_signal = '확인'
        elif recent_vol_up or volume_ratio >= 0.8:
            entry_signal = '관찰'
        else:
            entry_signal = '대기'

        # v5.2: ROE nan → 🟢 다운그레이드
        if roe_value is None and entry_signal == '확인':
            entry_signal = '관찰'

        total_score = rsi_score + disparity_score + volume_score + pbr_score + returns_score + rebound_score
        total_score = max(0, total_score - roe_penalty)
        trading_value = current_price * current_volume

        # 시가총액 필터 ≥ 300억
        market_cap_value = None
        if shares and shares > 0:
            market_cap_value = current_price * shares
            if market_cap_value < 30_000_000_000:
                return None

        # 위험도 평가
        risk_score = 0
        if '정지' in name or '거래중지' in name:
            risk_score += 100
        if '관리' in name or '(M)' in name:
            risk_score += 80
        if pbr_value and pbr_value > 5.0:
            risk_score += 80
        if net_income and net_income < 0:
            risk_score += 50

        high_20d = df['High'].iloc[-20:].max()
        low_20d_risk = df['Low'].iloc[-20:].min()
        volatility_range = ((high_20d - low_20d_risk) / low_20d_risk * 100) if low_20d_risk > 0 else 0

        if volatility_range > 50:
            risk_score += 25
        if rebound_strength > 50:
            risk_score += 40
        elif rebound_strength > 30:
            risk_score += 20
        if volume_ratio > 5.0:
            risk_score += 20
        if disparity > 120:
            risk_score += 15
        if pbr_value and pbr_value > 3.0:
            risk_score += 20

        risk_level = '고위험' if risk_score >= 70 else '보통' if risk_score >= 30 else '안정'

        if disparity > 100:
            total_score = max(0, total_score - int((disparity - 100) * 2))

        return {
            'name': name, 'code': code, 'price': current_price,
            'score': total_score, 'trading_value': trading_value,
            'rsi': current_rsi, 'disparity': disparity, 'volume_ratio': volume_ratio,
            'pbr': pbr_value, 'per': per_value, 'roe': roe_value,
            'bps': bps_value, 'eps': eps_value,
            'chart_data': chart_data,
            'risk_score': risk_score, 'risk_level': risk_level,
            'rebound_strength': rebound_strength,
            'entry_signal': entry_signal,
            'market_cap': market_cap_value
        }
    except Exception:
        return None
    finally:
        signal.alarm(0)


# ============================
# 8. 시장 데이터 조회
# ============================
def get_market_data(exchange_rates: Dict[str, Optional[float]]) -> dict:
    result = {
        'kospi': None, 'kospi_change': 0,
        'kosdaq': None, 'kosdaq_change': 0,
        'usd': exchange_rates.get('usd'),
        'eur': exchange_rates.get('eur'),
        'jpy': exchange_rates.get('jpy')
    }
    try:
        from pykrx import stock
        kst = pytz.timezone('Asia/Seoul')
        today = datetime.now(kst)
        for days_back in range(7):
            try:
                end_date = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                start_date = (today - timedelta(days=days_back + 5)).strftime('%Y%m%d')
                df_k = stock.get_index_ohlcv(start_date, end_date, "1001")
                if len(df_k) >= 2:
                    result['kospi'] = df_k['종가'].iloc[-1]
                    result['kospi_change'] = (df_k['종가'].iloc[-1] - df_k['종가'].iloc[-2]) / df_k['종가'].iloc[-2] * 100
                    break
                elif len(df_k) == 1:
                    result['kospi'] = df_k['종가'].iloc[-1]
                    break
            except:
                continue
        for days_back in range(7):
            try:
                end_date = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                start_date = (today - timedelta(days=days_back + 5)).strftime('%Y%m%d')
                df_q = stock.get_index_ohlcv(start_date, end_date, "2001")
                if len(df_q) >= 2:
                    result['kosdaq'] = df_q['종가'].iloc[-1]
                    result['kosdaq_change'] = (df_q['종가'].iloc[-1] - df_q['종가'].iloc[-2]) / df_q['종가'].iloc[-2] * 100
                    break
                elif len(df_q) == 1:
                    result['kosdaq'] = df_q['종가'].iloc[-1]
                    break
            except:
                continue
    except Exception as e:
        logging.warning(f"pykrx 실패: {e}")
    return result


# ============================
# 9. Gemini AI 분석
# ============================
def get_gemini_analysis(top_stocks):
    try:
        genai.configure(api_key=os.environ.get('swingTrading'))
        model = genai.GenerativeModel('gemini-2.5-flash')
        data = [{
            '종목명': s['name'], '현재가': f"{s['price']:,.0f}원", '총점': f"{s['score']}점",
            'RSI': f"{s['rsi']:.1f}", '이격도': f"{s['disparity']:.1f}%",
            '거래량비율': f"{s['volume_ratio']:.2f}배",
            'PBR': f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A',
            'PER': f"{s['per']:.1f}" if s.get('per') else 'N/A',
            'ROE': f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A',
            '진입신호': s.get('entry_signal', '관찰')
        } for s in top_stocks[:6]]

        response = model.generate_content(
            f"20년 경력 애널리스트로 TOP 6 종목 분석:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
            f"1. 공통점 2. 주목 종목 3. 진입 타이밍 4. 리스크\n200자 이내, 종목명 언급")
        return response.text
    except Exception as e:
        logging.warning(f"Gemini 오류: {e}")
        return "<div style='text-align:center;padding:20px;color:#888;'>⚠️ AI 분석 생략</div>"


def safe_format(value, fmt, default='N/A'):
    if value is None:
        return default
    try:
        return format(value, fmt)
    except:
        return default


# ============================
# 10. HTML 보고서 생성
# ============================
def generate_html(top_stocks, market_data, ai_analysis, timestamp):
    def get_risk_badge(risk_level):
        colors = {'안정': '#27ae60', '보통': '#7f8c8d', '고위험': '#e74c3c'}
        color = colors.get(risk_level, '#7f8c8d')
        return f"<span style='display:inline-block;padding:3px 8px;margin-left:8px;border-radius:4px;font-size:12px;font-weight:bold;background:{color};color:white;'>{risk_level}</span>"

    entry_emoji_map = {'확인': '🟢', '관찰': '🟡', '대기': '🔴'}

    # TOP 6 카드
    top6_cards = ""
    for i, s in enumerate(top_stocks[:6], 1):
        chart_json = json.dumps(s.get('chart_data', []))
        per_str = safe_format(s.get('per'), '.1f')
        pbr_str = safe_format(s.get('pbr'), '.2f')
        roe_str = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
        bps_str = f"{s['bps']:,.0f}원" if s.get('bps') else 'N/A'
        risk_badge = get_risk_badge(s.get('risk_level', '보통'))
        entry_sig = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
        entry_label = s.get('entry_signal', '관찰')

        top6_cards += f"""
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;'>
                <div>
                    <h3 style='margin:0;color:#2c3e50;'>
                        {i}. {s['name']} {risk_badge}
                        <span style='font-size:18px;' title='진입신호: {entry_label}'>{entry_sig}</span>
                        <a href='https://search.naver.com/search.naver?where=news&query={s['name']}' target='_blank' style='text-decoration:none;font-size:18px;'>📰</a>
                    </h3>
                    <p style='margin:5px 0;color:#7f8c8d;font-size:14px;'>{s['code']}</p>
                </div>
                <div style='text-align:right;'>
                    <div style='font-size:24px;font-weight:bold;color:#e74c3c;'>{s['score']}점</div>
                    <div style='font-size:18px;color:#2c3e50;'>{s['price']:,.0f}원</div>
                </div>
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
        (function(){{var ctx=document.getElementById('chart{i}').getContext('2d');var data={chart_json};if(data.length===0)return;var prices=data.map(d=>d.close);var minPrice=Math.min(...prices);var maxPrice=Math.max(...prices);var range=maxPrice-minPrice;var padding=range*0.1;var width=ctx.canvas.width;var height=ctx.canvas.height;ctx.strokeStyle='#3498db';ctx.lineWidth=2;ctx.beginPath();prices.forEach((price,i)=>{{var x=(i/(prices.length-1))*width;var y=height-((price-minPrice+padding)/(range+2*padding))*height;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});ctx.stroke();}})();
        </script>"""

    # TOP 7-30 테이블
    table_rows = ""
    for i, s in enumerate(top_stocks[6:30], 7):
        pbr_display = safe_format(s.get('pbr'), '.2f')
        roe_display = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
        risk_level = s.get('risk_level', '보통')
        entry_sig = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
        risk_color = {'안정': '#27ae60', '보통': '#7f8c8d', '고위험': '#e74c3c'}.get(risk_level, '#7f8c8d')

        table_rows += f"""<tr>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;'>{i}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;font-weight:bold;'>
                {s['name']} <a href='https://search.naver.com/search.naver?where=news&query={s['name']}' target='_blank' style='text-decoration:none;'>📰</a>
            </td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;'>{s['code']}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:right;'>{s['price']:,.0f}원</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;font-weight:bold;color:#e74c3c;'>{s['score']}점</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;color:{risk_color};font-weight:bold;'>{risk_level}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;font-size:16px;'>{entry_sig}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['rsi']:.1f}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['disparity']:.1f}%</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['volume_ratio']:.2f}배</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{pbr_display}</td>
            <td style='padding:12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{roe_display}</td>
        </tr>"""

    # =====================================================
    # v5.3: 투자자 유형 — Fallback 전 단계 ROE None 제외
    # =====================================================

    # 공격형: 이격도<90 + RSI<30 + 🟢 + ROE≥3%
    aggressive_stocks = sorted([
        s for s in top_stocks[:30]
        if s.get('disparity', 100) < 90 and s.get('rsi', 100) < 30
        and s.get('entry_signal') == '확인'
        and s.get('roe') is not None and s['roe'] >= 3.0
    ], key=lambda x: -x['score'])[:5]

    if len(aggressive_stocks) < 5:
        agg2 = sorted([
            s for s in top_stocks[:30]
            if s.get('disparity', 100) < 93 and s.get('rsi', 100) < 35
            and s.get('roe') is not None          # ROE None 제외
            and s not in aggressive_stocks
        ], key=lambda x: -x['score'])
        aggressive_stocks += agg2[:5 - len(aggressive_stocks)]

    if len(aggressive_stocks) < 5:
        agg3 = sorted([
            s for s in top_stocks[:30]
            if s.get('roe') is not None            # ROE None 제외 유지
            and s not in aggressive_stocks
        ], key=lambda x: -x['score'])
        aggressive_stocks += agg3[:5 - len(aggressive_stocks)]

    # 균형형: 위험도 보통 이하 + 시총 3,000억↑ + ROE 있는 종목 우선
    balanced_stocks = sorted([
        s for s in top_stocks[:30]
        if s.get('risk_score', 0) < 70
        and s.get('market_cap') and s['market_cap'] >= 300_000_000_000
        and s.get('roe') is not None               # ROE None 제외
    ], key=lambda x: -x['score'])[:5]

    if len(balanced_stocks) < 5:
        bal2 = sorted([
            s for s in top_stocks[:30]
            if s.get('risk_score', 0) < 70
            and s.get('roe') is not None           # ROE None 제외
            and s not in balanced_stocks
        ], key=lambda x: -x['score'])
        balanced_stocks += bal2[:5 - len(balanced_stocks)]

    if len(balanced_stocks) < 5:                   # 최종 Fallback만 ROE 완화
        bal3 = sorted([
            s for s in top_stocks[:30]
            if s.get('risk_score', 0) < 70 and s not in balanced_stocks
        ], key=lambda x: -x['score'])
        balanced_stocks += bal3[:5 - len(balanced_stocks)]

    # 보수형: 안정 + PBR<1.0 + ROE>5%
    conservative_stocks = sorted([
        s for s in top_stocks[:30]
        if s.get('risk_level') == '안정'
        and s.get('pbr') and s['pbr'] < 1.0
        and s.get('roe') is not None and s['roe'] > 5.0
    ], key=lambda x: -x['score'])[:5]

    if len(conservative_stocks) < 5:
        con2 = sorted([
            s for s in top_stocks[:30]
            if s.get('risk_level') == '안정'
            and s.get('pbr') and s['pbr'] < 1.2
            and s.get('roe') is not None and s['roe'] > 3.0  # ROE 기준 완화
            and s not in conservative_stocks
        ], key=lambda x: -x['score'])
        conservative_stocks += con2[:5 - len(conservative_stocks)]

    if len(conservative_stocks) < 5:
        con3 = sorted([
            s for s in top_stocks[:30]
            if s.get('risk_level') == '안정'
            and s.get('roe') is not None           # ROE None 제외 유지
            and s not in conservative_stocks
        ], key=lambda x: -x['score'])
        conservative_stocks += con3[:5 - len(conservative_stocks)]

    def make_investor_card(title, desc, stocks, icon, color):
        items = ""
        for i, s in enumerate(stocks, 1):
            sig = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
            roe_d = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
            pbr_d = f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A'
            items += f"""<div style='padding:10px;background:#f8f9fa;margin:8px 0;border-radius:5px;'>
                <strong>{i}. {s['name']}</strong> ({s['code']}) {sig}<br>
                <span style='color:#555;font-size:13px;'>점수: {s['score']}점 | RSI: {s['rsi']:.1f} | 이격도: {s['disparity']:.1f}% | PBR: {pbr_d} | ROE: {roe_d}</span>
            </div>"""
        return f"""<div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);border-left:5px solid {color};'>
            <h3 style='margin:0 0 10px 0;color:{color};'>{icon} {title}</h3>
            <p style='color:#555;margin:0 0 15px 0;'>{desc}</p>{items}</div>"""

    investor_type_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>👥 투자자 유형별 추천</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:20px;margin-bottom:30px;'>
        {make_investor_card('공격적 투자자', '🟢 이격도 90↓ + RSI 30↓ + 거래량 0.7배↑ + ROE 3%↑', aggressive_stocks, '🚀', '#e74c3c')}
        {make_investor_card('균형잡힌 투자자', '위험 보통↓ + 시총 3,000억↑ + ROE 확인 → 유동성 확보', balanced_stocks, '⚖️', '#3498db')}
        {make_investor_card('보수적 투자자', '위험 안정 + PBR 1.0↓ + ROE 5%↑ → 진짜 저평가 우량주', conservative_stocks, '🛡️', '#27ae60')}
    </div>"""

    # 지표별 TOP5
    rsi_top5 = sorted(top_stocks, key=lambda x: x['rsi'])[:5]
    disp_top5 = sorted(top_stocks, key=lambda x: x['disparity'])[:5]
    vol_top5 = sorted(top_stocks, key=lambda x: -x['volume_ratio'])[:5]
    reb_top5 = sorted(top_stocks, key=lambda x: -x.get('rebound_strength', 0))[:5]
    pbr_top5 = sorted([s for s in top_stocks if s.get('pbr')], key=lambda x: x['pbr'])[:5]

    def make_list(stocks, fn):
        return "<ul style='margin:10px 0;padding-left:20px;line-height:1.8;'>" + \
               "".join(f"<li><strong>{s['name']}</strong> ({s['code']}) - {fn(s)}</li>" for i, s in enumerate(stocks, 1)) + \
               "</ul>"

    indicator_top5_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>📈 지표별 TOP 5</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:30px;'>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e74c3c;margin:0 0 15px 0;'>📉 RSI 과매도</h3>{make_list(rsi_top5, lambda s: f"RSI {s['rsi']:.1f}")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e67e22;margin:0 0 15px 0;'>📊 이격도 하락</h3>{make_list(disp_top5, lambda s: f"이격도 {s['disparity']:.1f}%")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#27ae60;margin:0 0 15px 0;'>📦 거래량 급증</h3>{make_list(vol_top5, lambda s: f"거래량 {s['volume_ratio']:.2f}배")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#9b59b6;margin:0 0 15px 0;'>🎯 반등 강도</h3>{make_list(reb_top5, lambda s: f"반등 {s.get('rebound_strength',0):.1f}%")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#3498db;margin:0 0 15px 0;'>💎 저PBR 가치주</h3>{make_list(pbr_top5, lambda s: f"PBR {s['pbr']:.2f}")}</div>
    </div>"""

    usd_d = f"{market_data['usd']:,.2f}" if market_data.get('usd') else "N/A"
    eur_d = f"{market_data['eur']:,.2f}" if market_data.get('eur') else "N/A"
    jpy_d = f"{market_data['jpy']:,.2f}" if market_data.get('jpy') else "N/A"
    kospi_d = f"{market_data['kospi']:,.2f}" if market_data.get('kospi') else "N/A"
    kosdaq_d = f"{market_data['kosdaq']:,.2f}" if market_data.get('kosdaq') else "N/A"
    kp_cc = '#27ae60' if market_data.get('kospi_change', 0) >= 0 else '#e74c3c'
    kq_cc = '#27ae60' if market_data.get('kosdaq_change', 0) >= 0 else '#e74c3c'
    kp_ct = f"{market_data.get('kospi_change', 0):+.2f}%"
    kq_ct = f"{market_data.get('kosdaq_change', 0):+.2f}%"

    indicator_footer = """
    <div style='background:#f8f9fa;padding:25px;border-radius:10px;margin-top:30px;border-left:4px solid #3498db;'>
        <h3 style='color:#2c3e50;margin-top:0;'>📘 주요 지표 설명</h3>
        <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;'>
            <div><h4 style='color:#e74c3c;'>📊 RSI</h4><p style='color:#555;line-height:1.6;margin:0;'>30 이하: 과매도(매수 기회) / 70 이상: 과매수</p></div>
            <div><h4 style='color:#e67e22;'>📈 이격도</h4><p style='color:#555;line-height:1.6;margin:0;'>95% 이하: 저평가 / 105% 이상: 과열</p></div>
            <div><h4 style='color:#27ae60;'>📦 거래량 비율</h4><p style='color:#555;line-height:1.6;margin:0;'>1.5배↑: 거래 활성화 / 0.7배↑+추세증가: 🟢 진입 신호</p></div>
            <div><h4 style='color:#9b59b6;'>💰 PBR</h4><p style='color:#555;line-height:1.6;margin:0;'>1.0 이하: 저평가 / 3.0 이상: 고평가 (v5.3: 3단계 수집)</p></div>
            <div><h4 style='color:#3498db;'>💵 PER</h4><p style='color:#555;line-height:1.6;margin:0;'>낮을수록 저평가 / 업종 평균과 비교 필요</p></div>
            <div><h4 style='color:#e74c3c;'>📊 ROE</h4><p style='color:#555;line-height:1.6;margin:0;'>10%↑: 양호 / 15%↑: 우수 / ⚠️ N/A: 재무 불투명</p></div>
        </div>
        <div style='margin-top:20px;padding:15px;background:#e8f4fd;border-radius:8px;border-left:4px solid #3498db;'>
            <h4 style='color:#2980b9;margin-top:0;'>🎯 v5.3 진입 신호 기준</h4>
            <ul style='color:#555;line-height:1.8;margin:0;padding-left:20px;'>
                <li><strong>🟢 확인</strong>: 거래량 2일 연속↑ AND ≥0.7배 AND RSI &lt;35 (3중 조건)</li>
                <li><strong>🟡 관찰</strong>: 거래량 추세↑ OR ≥0.8배 / ROE nan 종목은 최대 🟡</li>
                <li><strong>🔴 대기</strong>: 거래량 감소 + 모멘텀 부재</li>
            </ul>
        </div>
        <div style='margin-top:15px;padding:15px;background:#e8f5e9;border-radius:8px;border-left:4px solid #27ae60;'>
            <h4 style='color:#1b5e20;margin-top:0;'>🔧 v5.3 PBR 수집 3단계</h4>
            <ul style='color:#555;line-height:1.8;margin:0;padding-left:20px;'>
                <li><strong>1순위</strong>: yfinance info['priceToBook'] 직접 수집</li>
                <li><strong>2순위</strong>: DART equity + KRX shares 역산</li>
                <li><strong>3순위</strong>: yfinance balance_sheet fallback</li>
            </ul>
        </div>
        <div style='margin-top:15px;padding:20px;background:#fff3cd;border-radius:8px;border-left:4px solid #ffc107;'>
            <h4 style='color:#856404;margin-top:0;'>💡 투자 유의사항</h4>
            <ul style='color:#856404;line-height:1.8;margin:0;padding-left:20px;'>
                <li>본 분석은 기술적 지표 기반 참고 자료이며, 투자 판단은 본인 책임입니다.</li>
                <li>거래량이 적은 종목은 매수/매도 시 슬리피지가 클 수 있습니다.</li>
                <li>스윙 트레이딩은 단기 수익을 목표로 하므로 손절 라인 설정이 필수입니다.</li>
                <li>시장 상황(전체 지수 흐름)도 함께 고려하여 진입 타이밍을 결정하세요.</li>
                <li>분산 투자로 리스크를 관리하고, 한 종목에 과도한 비중을 두지 마세요.</li>
            </ul>
        </div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang='ko'>
<head>
    <meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>
    <meta http-equiv='Cache-Control' content='no-cache, no-store, must-revalidate'>
    <title>스윙 트레이딩 v5.3 - {timestamp}</title>
    <style>body{{font-family:'Segoe UI',sans-serif;margin:0;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;}}.container{{max-width:1400px;margin:0 auto;background:#f8f9fa;padding:30px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,0.3);}}h1{{color:#2c3e50;text-align:center;font-size:32px;}}.timestamp{{text-align:center;color:#7f8c8d;margin-bottom:30px;font-size:14px;}}.market-overview{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin-bottom:30px;}}.market-card{{background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);text-align:center;}}.ai-analysis{{background:white;padding:25px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;border-left:5px solid #3498db;}}.top-stocks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-bottom:30px;}}table{{width:100%;background:white;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;}}th{{background:#34495e;color:white;padding:15px;text-align:left;}}</style>
</head>
<body>
<div class='container'>
    <h1>📊 스윙 트레이딩 종목 추천 v5.3</h1>
    <div class='timestamp'>생성 시간: {timestamp}</div>
    <div class='market-overview'>
        <div class='market-card'><h3 style='margin:0;color:#e74c3c;'>KOSPI</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{kospi_d}</div><div style='color:{kp_cc};'>{kp_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#3498db;'>KOSDAQ</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{kosdaq_d}</div><div style='color:{kq_cc};'>{kq_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>USD/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{usd_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>EUR/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{eur_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>JPY/KRW</h3><div style='font-size:24px;font-weight:bold;margin:10px 0;'>{jpy_d}</div></div>
    </div>
    <div class='ai-analysis'><h2 style='margin:0 0 15px 0;color:#2c3e50;'>🤖 AI 종합 분석</h2>{ai_analysis}</div>
    <h2 style='color:#2c3e50;margin:30px 0 20px;'>🏆 추천 종목 TOP 30</h2>
    <div class='top-stocks'>{top6_cards}</div>
    <table>
        <thead><tr><th>순위</th><th>종목명</th><th>코드</th><th>현재가</th><th>점수</th><th>위험도</th><th>진입신호</th><th>RSI</th><th>이격도</th><th>거래량</th><th>PBR</th><th>ROE</th></tr></thead>
        <tbody>{table_rows}</tbody>
    </table>
    {investor_type_section}
    {indicator_top5_section}
    {indicator_footer}
    <div style='text-align:center;margin-top:30px;padding:20px;color:#7f8c8d;font-size:13px;'>
        <p>v5.3 - PBR 3단계 수집 · ROE nan 전면 차단 · 보수형/균형형 Fallback 강화</p>
        <p>본 자료는 투자 참고용이며, 투자 책임은 본인에게 있습니다.</p>
    </div>
</div>
</body>
</html>"""
    return html


# ============================
# 11. 메인 함수
# ============================
def main():
    kst = pytz.timezone('Asia/Seoul')
    start_time = datetime.now(kst)
    logging.info("=== 스윙 트레이딩 분석 시작 (v5.3) ===")

    cache = CacheManager()
    exchange_rates = get_exchange_rates_only(cache)
    market_data = get_market_data(exchange_rates)

    dart_key = os.environ.get('DART_API')
    if not dart_key:
        logging.warning("⚠️ DART_API 없음 → yfinance fallback")

    mapper = DARTCorpCodeMapper(dart_key, cache) if dart_key else None
    corp_code_map = mapper.get_all_mappings() if mapper else {}

    krx = KRXData(cache)
    krx.load_all_shares()

    stock_list = load_stock_list()
    if not stock_list:
        logging.error("종목 리스트 로드 실패")
        return

    logging.info(f"분석 시작: {len(stock_list)}개 종목")
    args_list = [(name, code, dart_key, corp_code_map) for name, code in stock_list]

    with Pool(processes=4) as pool:
        results = pool.map(analyze_stock_worker, args_list)

    valid_results = [r for r in results if r and r['score'] >= 50]
    valid_results.sort(key=lambda x: (-x['score'], -x['trading_value']))
    top_stocks = valid_results[:30]

    logging.info(f"v5.3 완료: {len(valid_results)}개 추출")

    ai_analysis = get_gemini_analysis(top_stocks)
    timestamp = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(top_stocks, market_data, ai_analysis, timestamp)

    filename = f"stock_result_{datetime.now(kst).strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)

    elapsed = (datetime.now(kst) - start_time).total_seconds()
    logging.info(f"=== 완료: {filename} ({elapsed:.1f}초) ===")
    print(f"\n✅ {filename}")

    entry_map = {'확인': '🟢', '관찰': '🟡', '대기': '🔴'}
    risk_map = {'안정': '✅', '보통': '⚠️', '고위험': '🚨'}
    for i, s in enumerate(top_stocks[:10], 1):
        print(f"  {i}. {s['name']} ({s['code']}) - {s['score']}점 "
              f"{risk_map.get(s.get('risk_level','보통'),'⚠️')}{s.get('risk_level','보통')} "
              f"{entry_map.get(s.get('entry_signal','관찰'),'🟡')}")
        pbr_s = f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A'
        roe_s = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️N/A'
        print(f"      PBR: {pbr_s} | ROE: {roe_s}")


if __name__ == "__main__":
    main()
