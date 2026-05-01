#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
다이나믹 트레이딩 종목 추천 시스템 v1.1
─────────────────────────────────────────
기반: v1.0 (시장 국면 감지 · 모멘텀 · 섹터 로테이션)

v1.1 신규 — 퀀트 투자 원칙 반영:
  ✅ 재무 추세 분석: 매출·영업이익·순이익 3분기 방향(▲/→/▼)
     → DART 추가 호출 없이 yfinance quarterly_financials 활용
  ✅ Value Trap 탐지:
     - 저PBR + 실적 악화 → ⛔ 밸류트랩 위험 (최대 -20점 패널티)
     - 저PBR + 실적 개선 → ✅ 진짜 저평가 (신뢰도 상승)
  ✅ 점수 항목별 분해 표시 (블랙박스 점수 금지 원칙)
     반등점수 / 모멘텀점수 / 재무추세점수 / 트랩패널티 → 총점
  ✅ 부채비율 추가 수집 (재무 건전성)
  ✅ HTML: 재무 트렌드 화살표·밸류트랩 배지·점수 분해 바 추가
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
        cursor.execute('INSERT OR REPLACE INTO financial_cache VALUES (?, ?, ?, ?)',
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
        cursor.execute('INSERT OR REPLACE INTO shares_cache VALUES (?, ?, ?)',
                       (stock_code, shares, datetime.now(kst).isoformat()))
        conn.commit()
        conn.close()

    def set_corp_code_cache(self, stock_code: str, corp_code: str, corp_name: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        kst = pytz.timezone('Asia/Seoul')
        cursor.execute('INSERT OR REPLACE INTO dart_corp_map VALUES (?, ?, ?, ?)',
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
                corp_code  = corp.findtext('corp_code', '').strip()
                corp_name  = corp.findtext('corp_name', '').strip()
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
        self.api_key       = api_key
        self.cache         = cache_manager
        self.corp_code_map = corp_code_map
        self.base_url      = "https://opendart.fss.or.kr/api"
        self.request_count     = 0
        self.last_request_time = time.time()

    def rate_limit(self):
        self.request_count += 1
        if self.request_count >= 90:
            elapsed = time.time() - self.last_request_time
            if elapsed < 60:
                time.sleep(60 - elapsed)
            self.request_count     = 0
            self.last_request_time = time.time()

    def get_financials(self, stock_code: str):
        cached = self.cache.get_financial_cache(stock_code)
        if cached:
            return cached
        self.rate_limit()
        corp_code_to_use = self.corp_code_map.get(stock_code) or stock_code.zfill(6)
        kst   = pytz.timezone('Asia/Seoul')
        today = datetime.now(kst)
        year  = today.year if today.month > 3 else today.year - 1
        quarter    = ((today.month - 1) // 3) if today.month > 3 else 4
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
        self.cache       = cache_manager
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
# [v1.1 신규] 재무 추세 분석
# ============================
def get_financial_trend(ticker_obj) -> dict:
    """
    yfinance quarterly_financials에서 3분기 재무 추세 추출
    - 매출(Revenue) / 영업이익(Operating Income) / 순이익(Net Income)
    - DART 추가 호출 없이 처리 (속도 영향 최소화)
    - 반환: 각 항목별 방향(▲/→/▼), 변화율, 점수(-17 ~ +17), 부채비율
    """
    result = {
        'revenue_trend': '?', 'revenue_change': None, 'revenue_score': 0,
        'op_trend':      '?', 'op_change': None,      'op_score': 0,
        'ni_trend':      '?', 'ni_change': None,       'ni_score': 0,
        'debt_ratio':    None,
        'total_score':   0,
        'data_available': False
    }

    try:
        qf = ticker_obj.quarterly_financials
        if qf is None or qf.empty or len(qf.columns) < 2:
            return result

        def calc_trend(series_name_list, score_weight):
            for key in series_name_list:
                if key in qf.index:
                    vals = qf.loc[key].dropna()
                    if len(vals) >= 2:
                        # 컬럼은 최신순 정렬 (yfinance 기본)
                        v_new = float(vals.iloc[0])
                        v_old = float(vals.iloc[1])
                        if v_old == 0:
                            return '→', 0.0, 0
                        change = (v_new - v_old) / abs(v_old) * 100
                        if change >= 5:
                            return '▲', change, score_weight
                        elif change <= -5:
                            return '▼', change, -score_weight
                        else:
                            return '→', change, 0
            return '?', None, 0

        r_trend, r_change, r_score = calc_trend(
            ['Total Revenue', 'Revenue', 'Net Revenue'], 5)
        o_trend, o_change, o_score = calc_trend(
            ['Operating Income', 'EBIT', 'Operating Revenue'], 7)
        n_trend, n_change, n_score = calc_trend(
            ['Net Income', 'Net Income Common Stockholders', 'Net Income Applicable To Common Shares'], 5)

        # 부채비율: balance_sheet에서 수집
        debt_ratio = None
        try:
            bs = ticker_obj.quarterly_balance_sheet
            if bs is not None and not bs.empty:
                total_debt, total_equity = None, None
                for dk in ['Total Liabilities Net Minority Interest', 'Total Debt', 'Long Term Debt']:
                    if dk in bs.index:
                        total_debt = float(bs.loc[dk].iloc[0])
                        break
                for ek in ['Total Equity Gross Minority Interest', 'Stockholders Equity',
                           'Common Stock Equity', 'Total Stockholder Equity']:
                    if ek in bs.index:
                        total_equity = float(bs.loc[ek].iloc[0])
                        break
                if total_debt and total_equity and total_equity > 0:
                    debt_ratio = (total_debt / total_equity) * 100
        except:
            pass

        result.update({
            'revenue_trend':  r_trend, 'revenue_change': r_change, 'revenue_score': r_score,
            'op_trend':       o_trend, 'op_change':      o_change, 'op_score':      o_score,
            'ni_trend':       n_trend, 'ni_change':      n_change, 'ni_score':      n_score,
            'debt_ratio':     debt_ratio,
            'total_score':    r_score + o_score + n_score,
            'data_available': True
        })
    except Exception as e:
        pass

    return result


# ============================
# [v1.1 신규] Value Trap 탐지
# ============================
def detect_value_trap(pbr, roe, financial_trend: dict) -> dict:
    """
    저밸류에이션인데 실적이 지속 악화 중인 함정주 탐지
    ─────────────────────────────────────────────────
    ⛔ 위험: 저PBR + 매출·이익 동반 하락  → -20점 패널티
    ⚠️ 주의: 저PBR + 일부 지표 악화       → -10점 패널티
    ✅ 기회: 저PBR + 실적 개선             → 패널티 없음 + 라벨
    ➖ 중립: 해당 없음                     → 패널티 없음
    """
    if not financial_trend.get('data_available'):
        return {'level': 'unknown', 'penalty': 0, 'label': '', 'reason': '재무 추세 데이터 없음'}

    fin_score   = financial_trend.get('total_score', 0)
    rev_trend   = financial_trend.get('revenue_trend', '?')
    op_trend    = financial_trend.get('op_trend', '?')
    ni_trend    = financial_trend.get('ni_trend', '?')
    debt_ratio  = financial_trend.get('debt_ratio')

    low_pbr = pbr is not None and pbr < 1.2
    low_roe = roe is not None and roe < 5.0

    # ⛔ 강한 밸류트랩: 저PBR + 매출·영업이익 동반 하락
    if low_pbr and rev_trend == '▼' and op_trend == '▼':
        return {
            'level':   'danger',
            'penalty': 20,
            'label':   '⛔ 밸류트랩 위험',
            'reason':  f"저PBR({pbr:.2f})이지만 매출·영업이익 동반 하락 → 함정주 가능성"
        }

    # ⛔ 강한 밸류트랩: 저PBR + ROE 낮고 + 순이익 하락
    if low_pbr and low_roe and ni_trend == '▼':
        return {
            'level':   'danger',
            'penalty': 20,
            'label':   '⛔ 밸류트랩 위험',
            'reason':  f"저PBR({pbr:.2f}) + 낮은ROE({roe:.1f}%) + 순이익 하락 → 구조적 수익성 훼손 의심"
        }

    # ⚠️ 주의: 재무 종합 점수 마이너스 + 부채 과다
    if fin_score <= -5 and debt_ratio and debt_ratio > 200:
        return {
            'level':   'caution',
            'penalty': 10,
            'label':   '⚠️ 밸류트랩 주의',
            'reason':  f"실적 일부 하락 + 부채비율 {debt_ratio:.0f}% 과다 → 재무 건전성 점검 필요"
        }

    # ⚠️ 주의: 저PBR + 재무 점수 마이너스
    if low_pbr and fin_score <= -5:
        return {
            'level':   'caution',
            'penalty': 10,
            'label':   '⚠️ 밸류트랩 주의',
            'reason':  f"밸류에이션 낮지만 실적 일부 하락 — 추가 확인 필요"
        }

    # ✅ 진짜 저평가: 저PBR + 실적 개선
    if low_pbr and fin_score >= 7:
        return {
            'level':   'opportunity',
            'penalty': 0,
            'label':   '✅ 진짜 저평가',
            'reason':  f"저PBR({pbr:.2f}) + 매출·이익 개선 → 실질 저평가 가능성 높음"
        }

    # ✅ 좋은 신호: ROE 높고 실적 개선
    if roe and roe >= 10 and fin_score >= 10:
        return {
            'level':   'opportunity',
            'penalty': 0,
            'label':   '✅ 실적 개선주',
            'reason':  f"ROE {roe:.1f}% + 전 항목 실적 개선 → 펀더멘털 강화 중"
        }

    return {'level': 'neutral', 'penalty': 0, 'label': '', 'reason': ''}


# ============================
# [v1.0] 시장 국면 감지
# ============================
def detect_market_regime() -> dict:
    """KOSPI MA20/MA60 기반 시장 국면 자동 감지"""
    try:
        from pykrx import stock
        kst        = pytz.timezone('Asia/Seoul')
        today      = datetime.now(kst)
        end_date   = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=200)).strftime('%Y%m%d')

        df = stock.get_index_ohlcv(start_date, end_date, "1001")
        if len(df) < 60:
            return {'regime': '횡보장', 'emoji': '⚖️', 'color': '#e67e22',
                    'strategy_hint': '데이터 부족 — 횡보장으로 처리', 'momentum_20d': 0}

        df['MA20'] = df['종가'].rolling(20).mean()
        df['MA60'] = df['종가'].rolling(60).mean()
        last  = df.iloc[-1]
        price = float(last['종가'])
        ma20  = float(last['MA20'])
        ma60  = float(last['MA60'])

        momentum_20d = 0.0
        if len(df) >= 20:
            momentum_20d = (price - float(df['종가'].iloc[-20])) / float(df['종가'].iloc[-20]) * 100

        if price > ma20 and ma20 > ma60:
            regime = '상승장'; emoji, color = '🚀', '#27ae60'
            strategy_hint = '모멘텀 전략 강화 · 신고가 근접 종목 중심'
        elif price < ma20 and ma20 < ma60:
            regime = '하락장'; emoji, color = '⚠️', '#e74c3c'
            strategy_hint = '반등 전략 강화 · 저평가 종목 중심 (비중 축소 권장)'
        else:
            regime = '횡보장'; emoji, color = '⚖️', '#e67e22'
            strategy_hint = '반등 + 모멘텀 균형 병행 전략'

        logging.info(f"📊 시장 국면: {regime} | KOSPI {price:,.0f} / MA20 {ma20:,.0f} / MA60 {ma60:,.0f}")
        return {'regime': regime, 'emoji': emoji, 'color': color,
                'strategy_hint': strategy_hint,
                'price': price, 'ma20': ma20, 'ma60': ma60, 'momentum_20d': momentum_20d}
    except Exception as e:
        logging.warning(f"시장 국면 감지 실패: {e}")
        return {'regime': '횡보장', 'emoji': '⚖️', 'color': '#e67e22',
                'strategy_hint': '횡보장 (조회 실패)', 'momentum_20d': 0}


# ============================
# [v1.0] 섹터 분류 키워드
# ============================
SECTOR_KEYWORDS = {
    'IT/반도체':    ['반도체', '하이닉스', '웨이퍼', '팹', '파운드리', '칩', '실리콘'],
    'AI/소프트웨어': ['소프트', '네이버', '카카오', '게임', '클라우드', '인터넷', 'AI', '크래프톤'],
    '바이오/제약':   ['바이오', '제약', '의약', '헬스케어', '셀', '진단', '의료기기'],
    '2차전지':      ['배터리', '이차전지', '전기차', '양극재', '음극', '전해질', '에너지솔'],
    '방산/우주':    ['방산', '항공', '우주', '방위', '레이더', '함정', '미사일', '한화시스템', '한화에어로'],
    '금융/증권':    ['금융', '은행', '증권', '보험', '카드', '자산운용', '저축', '지주'],
    '에너지/화학':  ['에너지', '화학', '정유', '석유', '가스', '전력', 'LG화학'],
    '소비재/유통':  ['식품', '유통', '소매', '의류', '패션', '뷰티', '화장품', '리테일'],
    '통신':         ['통신', 'KT', 'SKT', '유플러스', '텔레콤', 'LGU'],
    '건설/부동산':  ['건설', '부동산', '개발', '아파트', '주택', '현대건설'],
    '조선/해운':    ['조선', '해운', '중공업', 'HMM', '선박', '물류'],
    '철강/소재':    ['철강', '포스코', '현대제철', '소재', '금속', '알루미늄'],
}

def get_sector_for_stock(name: str) -> str:
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return sector
    return '기타'


# ============================
# [v1.0] 섹터 모멘텀 분석
# ============================
def get_sector_momentum() -> dict:
    SECTOR_INDEX = {
        'IT/반도체':   '1028', '바이오/제약': '1021', '금융/증권':   '1032',
        '에너지/화학': '1006', '건설/부동산': '1016', '철강/소재':   '1007',
        '조선/해운':   '1010', '통신':        '1026', '소비재/유통': '1020',
    }
    try:
        from pykrx import stock
        kst        = pytz.timezone('Asia/Seoul')
        today      = datetime.now(kst)
        end_date   = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=35)).strftime('%Y%m%d')

        sector_returns = {}
        for sector_name, idx_code in SECTOR_INDEX.items():
            try:
                df = stock.get_index_ohlcv(start_date, end_date, idx_code)
                if len(df) >= 2:
                    ret = (df['종가'].iloc[-1] - df['종가'].iloc[0]) / df['종가'].iloc[0] * 100
                    sector_returns[sector_name] = round(ret, 2)
                time.sleep(0.2)
            except:
                continue

        if sector_returns:
            sorted_returns = dict(sorted(sector_returns.items(), key=lambda x: -x[1]))
            top_sectors    = list(sorted_returns.keys())[:3]
            logging.info(f"📈 주도 섹터 Top3: {top_sectors}")
            return {'returns': sorted_returns, 'top_sectors': top_sectors}
    except Exception as e:
        logging.warning(f"섹터 모멘텀 실패: {e}")
    return {'returns': {}, 'top_sectors': []}


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
        kospi_r  = requests.get("http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=stockMkt",  timeout=30)
        kosdaq_r = requests.get("http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=kosdaqMkt", timeout=30)
        kospi    = pd.read_html(kospi_r.content,  header=0, encoding='euc-kr')[0]
        kosdaq   = pd.read_html(kosdaq_r.content, header=0, encoding='euc-kr')[0]
        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)
        listing_date_col = next((c for c in all_stocks.columns if '상장' in c and '일' in c), None)
        filtered = []
        for _, row in all_stocks.iterrows():
            name, code = row['회사명'], row['종목코드']
            exclude = ['우', 'ETN', 'SPAC', '스팩', '리츠', '인프라', '관리',
                       '(M)', '(관)', '정지', '제8호', '제9호', '제10호',
                       '기업인수목적', '기업재무안정']
            if any(k in name for k in exclude): continue
            if not code.isdigit(): continue
            if listing_date_col and pd.notna(row.get(listing_date_col)):
                try:
                    ld = pd.to_datetime(str(row[listing_date_col]), errors='coerce')
                    if pd.notna(ld) and (datetime.now() - ld.to_pydatetime()).days / 365.0 < 1.0:
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
# 7. 종목 분석 워커 (v1.1)
# ============================
def analyze_stock_worker(args):
    """
    v1.1: 재무 추세 분석 + Value Trap 탐지 통합
    점수 구성: 반등점수 + 모멘텀점수 + 재무추세점수 - 트랩패널티 + 섹터보너스
    """
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(15)  # v1.1: quarterly_financials 수집 시간 고려해 15초로 확대

    try:
        name, code, dart_key, corp_code_map, market_regime, top_sectors = args

        ticker = yf.Ticker(f"{code}.KS" if code.startswith('0') else f"{code}.KQ")
        df     = ticker.history(period='3mo')

        if df.empty or len(df) < 20:
            return None

        current_price  = df['Close'].iloc[-1]
        volume_avg     = df['Volume'].iloc[-20:-1].mean()
        current_volume = df['Volume'].iloc[-1]

        if current_volume == 0 or current_price < 2000:
            return None
        if volume_avg * current_price < 300_000_000:
            return None

        chart_data = [
            {'date': d.strftime('%Y-%m-%d'), 'close': float(r['Close'])}
            for d, r in df.iterrows()
        ]

        # ── 기존 반등 지표 ────────────────────────────
        delta = df['Close'].diff()
        gain  = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        rsi_score   = 30 if current_rsi < 30 else 20 if current_rsi < 40 else 10 if current_rsi < 50 else 0

        ma20      = df['Close'].rolling(20).mean().iloc[-1]
        disparity = (current_price / ma20) * 100
        disparity_score = 20 if disparity < 95 else 15 if disparity < 98 else 10 if disparity < 100 else 0

        volume_ratio = current_volume / volume_avg if volume_avg > 0 else 0
        volume_score = 15 if volume_ratio >= 1.5 else 10 if volume_ratio >= 1.2 else 5 if volume_ratio >= 1.0 else 0

        # ── 재무 데이터 수집 (PBR 3단계 — v5.3 유지) ──
        cache = CacheManager()
        dart  = DARTFinancials(dart_key, cache, corp_code_map)
        krx   = KRXData(cache)

        equity, net_income = dart.get_financials(code)
        shares = krx.get_shares(code)

        pbr_value = bps_value = per_value = roe_value = eps_value = None
        pbr_score = 0

        try:
            info = ticker.info
            ptb  = info.get('priceToBook')
            if ptb and ptb > 0:
                pbr_value = float(ptb)
            bv = info.get('bookValue')
            if bv and bv > 0:
                bps_value = float(bv)
                if not pbr_value:
                    pbr_value = current_price / bps_value
            if not shares:
                shares = info.get('sharesOutstanding') or info.get('floatShares')
                if shares: shares = int(shares)
            if not equity:
                mc = info.get('marketCap')
                if mc and pbr_value and pbr_value > 0:
                    equity = mc / pbr_value
            if not net_income and info.get('netIncomeToCommon'):
                net_income = info['netIncomeToCommon']
        except:
            pass

        if not pbr_value and equity and shares and shares > 0:
            try:
                bps_value = bps_value or (equity / shares)
                if bps_value and bps_value > 0:
                    pbr_value = current_price / bps_value
            except:
                pass

        if not equity or not net_income:
            try:
                bs = ticker.balance_sheet
                fi = ticker.financials
                if not bs.empty and not equity:
                    for k in ['Total Stockholder Equity', 'Stockholders Equity',
                              'Common Stock Equity', 'Total Equity Gross Minority Interest']:
                        if k in bs.index:
                            equity = bs.loc[k].iloc[0]; break
                if not fi.empty and not net_income:
                    for k in ['Net Income', 'Net Income Common Stockholders']:
                        if k in fi.index:
                            net_income = fi.loc[k].iloc[0]; break
                if not pbr_value and equity and shares and shares > 0:
                    bps_value = bps_value or (equity / shares)
                    if bps_value and bps_value > 0:
                        pbr_value = current_price / bps_value
            except:
                pass

        if equity is not None and equity < 0:
            return None

        if pbr_value and pbr_value > 0:
            pbr_score = 15 if pbr_value < 1.0 else 10 if pbr_value < 1.5 else 5 if pbr_value < 2.0 else 0

        if net_income and shares and shares > 0:
            eps_value = net_income / shares
            per_value = current_price / eps_value if eps_value > 0 else None

        if net_income and equity and equity > 0:
            roe_value = (net_income / equity) * 100
            if roe_value < 0: return None

        returns_5d    = ((df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100) if len(df) >= 6 else 0
        returns_score = 10 if -5 <= returns_5d <= 0 else 5 if -10 <= returns_5d < -5 else 0

        low_20d          = df['Low'].iloc[-20:].min()
        rebound_strength = ((current_price - low_20d) / low_20d * 100) if low_20d > 0 else 0
        rebound_score    = 10 if rebound_strength >= 5 else 5 if rebound_strength >= 3 else 0

        roe_penalty  = 10 if (roe_value is not None and 0 <= roe_value < 3.0) else 0

        recent_vol_up = (
            len(df) >= 3 and
            df['Volume'].iloc[-1] > df['Volume'].iloc[-2] and
            df['Volume'].iloc[-2] > df['Volume'].iloc[-3]
        )

        if recent_vol_up and volume_ratio >= 0.7 and current_rsi < 35:
            entry_signal = '확인'
        elif recent_vol_up or volume_ratio >= 0.8:
            entry_signal = '관찰'
        else:
            entry_signal = '대기'

        if roe_value is None and entry_signal == '확인':
            entry_signal = '관찰'

        # ── [v1.1 신규] 재무 추세 분석 ───────────────
        financial_trend = get_financial_trend(ticker)
        fin_trend_score = financial_trend.get('total_score', 0)  # -17 ~ +17

        # ── [v1.1 신규] Value Trap 탐지 ──────────────
        trap_info     = detect_value_trap(pbr_value, roe_value, financial_trend)
        trap_penalty  = trap_info.get('penalty', 0)

        # ── [v1.0] 모멘텀 지표 ───────────────────────
        high_3m           = df['High'].max()
        proximity_to_high = (current_price / high_3m) * 100 if high_3m > 0 else 50
        return_1m         = ((current_price - df['Close'].iloc[-21]) / df['Close'].iloc[-21] * 100) if len(df) >= 21 else 0

        momentum_score_raw = 0
        if proximity_to_high >= 97:   momentum_score_raw += 20
        elif proximity_to_high >= 90: momentum_score_raw += 12
        elif proximity_to_high >= 80: momentum_score_raw += 6
        if return_1m >= 15:   momentum_score_raw += 15
        elif return_1m >= 8:  momentum_score_raw += 10
        elif return_1m >= 3:  momentum_score_raw += 5

        # ── [v1.0] 섹터 분류 및 보너스 ───────────────
        sector       = get_sector_for_stock(name)
        sector_bonus = 5 if sector in top_sectors else 0

        # ── [v1.1] 국면별 가중치 + 재무추세 + 트랩패널티 ──
        base_rebound = rsi_score + disparity_score + volume_score + pbr_score + returns_score + rebound_score
        base_rebound = max(0, base_rebound - roe_penalty)

        if market_regime == '상승장':
            weighted = int(base_rebound * 0.6 + momentum_score_raw * 1.5)
        elif market_regime == '하락장':
            weighted = int(base_rebound * 1.0 + momentum_score_raw * 0.3)
        else:
            weighted = int(base_rebound * 0.8 + momentum_score_raw * 0.8)

        total_score = weighted + fin_trend_score + sector_bonus - trap_penalty

        if disparity > 100:
            total_score = max(0, total_score - int((disparity - 100) * 2))

        trading_value    = current_price * current_volume
        market_cap_value = None
        if shares and shares > 0:
            market_cap_value = current_price * shares
            if market_cap_value < 30_000_000_000:
                return None

        # 위험도 평가
        risk_score = 0
        if '정지' in name or '거래중지' in name: risk_score += 100
        if '관리' in name or '(M)' in name:      risk_score += 80
        if pbr_value and pbr_value > 5.0:         risk_score += 80
        if net_income and net_income < 0:          risk_score += 50
        high_20d         = df['High'].iloc[-20:].max()
        low_20d_risk     = df['Low'].iloc[-20:].min()
        volatility_range = ((high_20d - low_20d_risk) / low_20d_risk * 100) if low_20d_risk > 0 else 0
        if volatility_range > 50:             risk_score += 25
        if rebound_strength > 50:             risk_score += 40
        elif rebound_strength > 30:           risk_score += 20
        if volume_ratio > 5.0:               risk_score += 20
        if disparity > 120:                  risk_score += 15
        if pbr_value and pbr_value > 3.0:    risk_score += 20
        if trap_info['level'] == 'danger':   risk_score += 30  # [v1.1] 트랩 시 위험도 가중

        risk_level = '고위험' if risk_score >= 70 else '보통' if risk_score >= 30 else '안정'

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
            'market_cap': market_cap_value,
            # v1.0
            'momentum_score': momentum_score_raw,
            'proximity_to_high': proximity_to_high,
            'return_1m': return_1m,
            'sector': sector,
            'sector_bonus': sector_bonus,
            # [v1.1 신규]
            'financial_trend': financial_trend,
            'fin_trend_score': fin_trend_score,
            'trap_info': trap_info,
            'trap_penalty': trap_penalty,
            'score_breakdown': {
                'base_rebound':    base_rebound,
                'weighted':        weighted,
                'fin_trend_score': fin_trend_score,
                'sector_bonus':    sector_bonus,
                'trap_penalty':    trap_penalty,
            }
        }
    except Exception:
        return None
    finally:
        signal.alarm(0)


# ============================
# 8. 시장 데이터 조회
# ============================
def get_market_data(exchange_rates: Dict[str, Optional[float]]) -> dict:
    result = {'kospi': None, 'kospi_change': 0, 'kosdaq': None, 'kosdaq_change': 0,
              'usd': exchange_rates.get('usd'), 'eur': exchange_rates.get('eur'), 'jpy': exchange_rates.get('jpy')}
    try:
        from pykrx import stock
        kst   = pytz.timezone('Asia/Seoul')
        today = datetime.now(kst)
        for days_back in range(7):
            try:
                ed = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                sd = (today - timedelta(days=days_back + 5)).strftime('%Y%m%d')
                df_k = stock.get_index_ohlcv(sd, ed, "1001")
                if len(df_k) >= 2:
                    result['kospi']        = df_k['종가'].iloc[-1]
                    result['kospi_change'] = (df_k['종가'].iloc[-1] - df_k['종가'].iloc[-2]) / df_k['종가'].iloc[-2] * 100
                    break
                elif len(df_k) == 1:
                    result['kospi'] = df_k['종가'].iloc[-1]; break
            except: continue
        for days_back in range(7):
            try:
                ed = (today - timedelta(days=days_back)).strftime('%Y%m%d')
                sd = (today - timedelta(days=days_back + 5)).strftime('%Y%m%d')
                df_q = stock.get_index_ohlcv(sd, ed, "2001")
                if len(df_q) >= 2:
                    result['kosdaq']        = df_q['종가'].iloc[-1]
                    result['kosdaq_change'] = (df_q['종가'].iloc[-1] - df_q['종가'].iloc[-2]) / df_q['종가'].iloc[-2] * 100
                    break
                elif len(df_q) == 1:
                    result['kosdaq'] = df_q['종가'].iloc[-1]; break
            except: continue
    except Exception as e:
        logging.warning(f"pykrx 실패: {e}")
    return result


# ============================
# 9. Gemini AI 분석
# ============================
def get_gemini_analysis(top_stocks, market_regime: str = '횡보장'):
    try:
        genai.configure(api_key=os.environ.get('swingTrading'))
        model = genai.GenerativeModel('gemini-2.5-flash')
        data  = [{
            '종목명':   s['name'],
            '현재가':   f"{s['price']:,.0f}원",
            '총점':     f"{s['score']}점",
            'RSI':      f"{s['rsi']:.1f}",
            '이격도':   f"{s['disparity']:.1f}%",
            '거래량':   f"{s['volume_ratio']:.2f}배",
            'PBR':      f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A',
            'ROE':      f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A',
            '진입신호': s.get('entry_signal', '관찰'),
            '섹터':     s.get('sector', '기타'),
            '1M수익률': f"{s.get('return_1m', 0):+.1f}%",
            '재무추세': f"매출{s.get('financial_trend', {}).get('revenue_trend','?')} 영익{s.get('financial_trend', {}).get('op_trend','?')} 순익{s.get('financial_trend', {}).get('ni_trend','?')}",
            '밸류트랩': s.get('trap_info', {}).get('label', ''),
        } for s in top_stocks[:6]]
        response = model.generate_content(
            f"20년 경력 퀀트 애널리스트로 현재 시장 국면({market_regime}) 기준 TOP 6 종목 분석:\n"
            f"{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
            f"1. 공통점 2. 주목 종목(재무추세 고려) 3. 진입 타이밍 4. 밸류트랩 주의사항\n"
            f"200자 이내, 숫자 근거 포함")
        return response.text
    except Exception as e:
        logging.warning(f"Gemini 오류: {e}")
        return "<div style='text-align:center;padding:20px;color:#888;'>⚠️ AI 분석 생략</div>"


def safe_format(value, fmt, default='N/A'):
    if value is None: return default
    try: return format(value, fmt)
    except: return default


# ============================
# 10. HTML 보고서 생성
# ============================
def generate_html(top_stocks, market_data, ai_analysis, timestamp,
                  regime_info=None, sector_data=None):

    def get_risk_badge(risk_level):
        colors = {'안정': '#27ae60', '보통': '#7f8c8d', '고위험': '#e74c3c'}
        color  = colors.get(risk_level, '#7f8c8d')
        return f"<span style='display:inline-block;padding:3px 8px;margin-left:8px;border-radius:4px;font-size:12px;font-weight:bold;background:{color};color:white;'>{risk_level}</span>"

    entry_emoji_map = {'확인': '🟢', '관찰': '🟡', '대기': '🔴'}

    def trend_badge(trend):
        cfg = {'▲': ('#27ae60', '▲ 개선'), '▼': ('#e74c3c', '▼ 하락'), '→': ('#7f8c8d', '→ 보합'), '?': ('#bdc3c7', '? 미확인')}
        color, label = cfg.get(trend, ('#bdc3c7', trend))
        return f"<span style='background:{color};color:white;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:bold;'>{label}</span>"

    def trap_badge(trap_info):
        label = trap_info.get('label', '')
        if not label: return ''
        level  = trap_info.get('level', 'neutral')
        bg     = {'danger': '#e74c3c', 'caution': '#f39c12', 'opportunity': '#27ae60'}.get(level, '#95a5a6')
        return f"<span style='background:{bg};color:white;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-left:6px;'>{label}</span>"

    def score_breakdown_bar(sb):
        """점수 분해 시각화"""
        weighted     = sb.get('weighted', 0)
        fin_score    = sb.get('fin_trend_score', 0)
        s_bonus      = sb.get('sector_bonus', 0)
        trap_pen     = sb.get('trap_penalty', 0)
        fin_color    = '#27ae60' if fin_score >= 0 else '#e74c3c'
        trap_color   = '#e74c3c' if trap_pen > 0 else '#95a5a6'
        return f"""
        <div style='font-size:12px;color:#555;margin-top:10px;background:#f8f9fa;padding:10px;border-radius:6px;'>
            <div style='font-weight:bold;margin-bottom:6px;color:#2c3e50;'>📊 점수 구성</div>
            <div style='display:flex;gap:6px;flex-wrap:wrap;'>
                <span style='background:#3498db;color:white;padding:2px 8px;border-radius:3px;'>시세·밸류: {weighted}점</span>
                <span style='background:{fin_color};color:white;padding:2px 8px;border-radius:3px;'>재무추세: {fin_score:+d}점</span>
                <span style='background:#f39c12;color:white;padding:2px 8px;border-radius:3px;'>섹터보너스: +{s_bonus}점</span>
                <span style='background:{trap_color};color:white;padding:2px 8px;border-radius:3px;'>트랩패널티: -{trap_pen}점</span>
            </div>
        </div>"""

    # ── 시장 국면 배너 ────────────────────────────────
    regime  = regime_info or {'regime': '횡보장', 'emoji': '⚖️', 'color': '#e67e22',
                               'strategy_hint': '-', 'momentum_20d': 0}
    r_color = regime['color']; r_emoji = regime['emoji']; r_name = regime['regime']
    r_hint  = regime.get('strategy_hint', ''); r_mom = regime.get('momentum_20d', 0)
    r_ma20  = f"{regime['ma20']:,.0f}"  if regime.get('ma20')  else 'N/A'
    r_ma60  = f"{regime['ma60']:,.0f}"  if regime.get('ma60')  else 'N/A'
    r_price = f"{regime['price']:,.0f}" if regime.get('price') else 'N/A'

    regime_banner = f"""
    <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);
                margin-bottom:20px;border-left:6px solid {r_color};'>
        <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;'>
            <div>
                <h2 style='margin:0;color:{r_color};font-size:20px;'>{r_emoji} 현재 시장 국면: {r_name}</h2>
                <p style='margin:6px 0 0;color:#555;font-size:14px;'>💡 전략 힌트: {r_hint}</p>
            </div>
            <div style='display:flex;gap:12px;flex-wrap:wrap;'>
                <div style='background:#f8f9fa;padding:10px 16px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>KOSPI</div><div style='font-weight:bold;'>{r_price}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 16px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>MA20</div><div style='font-weight:bold;'>{r_ma20}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 16px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>MA60</div><div style='font-weight:bold;'>{r_ma60}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 16px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>20일 모멘텀</div>
                    <div style='font-weight:bold;color:{"#27ae60" if r_mom>=0 else "#e74c3c"};'>{r_mom:+.1f}%</div>
                </div>
            </div>
        </div>
        <div style='margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;font-size:13px;'>
            <span style='padding:4px 12px;border-radius:20px;background:{"#d5f5e3" if r_name=="상승장" else "#f0f0f0"};color:{"#1e8449" if r_name=="상승장" else "#aaa"};font-weight:bold;'>🚀 상승장: 모멘텀 1.5x</span>
            <span style='padding:4px 12px;border-radius:20px;background:{"#fdebd0" if r_name=="횡보장" else "#f0f0f0"};color:{"#935116" if r_name=="횡보장" else "#aaa"};font-weight:bold;'>⚖️ 횡보장: 균형 0.8x</span>
            <span style='padding:4px 12px;border-radius:20px;background:{"#fadbd8" if r_name=="하락장" else "#f0f0f0"};color:{"#922b21" if r_name=="하락장" else "#aaa"};font-weight:bold;'>⚠️ 하락장: 반등 강화</span>
        </div>
    </div>"""

    # ── 섹터 로테이션 섹션 ────────────────────────────
    s_data    = sector_data or {'returns': {}, 'top_sectors': []}
    top3      = s_data.get('top_sectors', [])
    s_returns = s_data.get('returns', {})
    sector_rows = ""
    for sec, ret in s_returns.items():
        is_top  = sec in top3
        bc      = '#27ae60' if ret > 0 else '#e74c3c'
        bw      = min(abs(ret) * 4, 100)
        badge   = "<span style='background:#f39c12;color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:6px;'>주도</span>" if is_top else ""
        sector_rows += f"""<tr style='background:{"#f0fff4" if is_top else "white"}'>
            <td style='padding:10px 15px;font-weight:{"bold" if is_top else "normal"};border-bottom:1px solid #ecf0f1;'>{sec}{badge}</td>
            <td style='padding:10px 15px;text-align:right;color:{bc};font-weight:bold;border-bottom:1px solid #ecf0f1;'>{ret:+.1f}%</td>
            <td style='padding:10px 15px;border-bottom:1px solid #ecf0f1;'>
                <div style='background:#ecf0f1;border-radius:4px;height:10px;'>
                    <div style='background:{bc};border-radius:4px;height:10px;width:{bw:.0f}%;'></div>
                </div>
            </td></tr>"""

    sector_bonus_note = f"현재 보너스 적용 섹터: {', '.join(top3)}" if top3 else "섹터 데이터 없음"
    sector_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>🔄 섹터 로테이션 분석 (최근 1개월)</h2>
    <div style='background:white;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;overflow:hidden;'>
        <div style='padding:15px 20px;background:#34495e;color:white;font-size:13px;'>
            📊 {sector_bonus_note} · 상위 3개 섹터 종목에 +5점 보너스 적용
        </div>
        <table style='width:100%;border-collapse:collapse;'>
            <thead><tr style='background:#ecf0f1;'>
                <th style='padding:10px 15px;text-align:left;'>섹터</th>
                <th style='padding:10px 15px;text-align:right;'>1개월 수익률</th>
                <th style='padding:10px 15px;'>추세</th>
            </tr></thead>
            <tbody>{sector_rows if sector_rows else "<tr><td colspan='3' style='padding:20px;text-align:center;color:#aaa;'>데이터 없음</td></tr>"}</tbody>
        </table>
    </div>"""

    # ── TOP 6 카드 ────────────────────────────────────
    top6_cards = ""
    for i, s in enumerate(top_stocks[:6], 1):
        chart_json  = json.dumps(s.get('chart_data', []))
        per_str     = safe_format(s.get('per'), '.1f')
        pbr_str     = safe_format(s.get('pbr'), '.2f')
        roe_str     = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
        bps_str     = f"{s['bps']:,.0f}원" if s.get('bps') else 'N/A'
        risk_badge  = get_risk_badge(s.get('risk_level', '보통'))
        entry_sig   = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
        entry_label = s.get('entry_signal', '관찰')
        sector_tag  = s.get('sector', '기타')
        is_top_sec  = sector_tag in top3
        sec_badge   = f"<span style='display:inline-block;padding:2px 7px;margin-left:6px;border-radius:4px;font-size:11px;background:{'#f39c12' if is_top_sec else '#95a5a6'};color:white;'>{sector_tag}</span>"
        ret1m_color = '#27ae60' if s.get('return_1m', 0) >= 0 else '#e74c3c'
        ft          = s.get('financial_trend', {})
        trap        = s.get('trap_info', {})
        sb          = s.get('score_breakdown', {})
        debt_str    = f"{ft.get('debt_ratio'):.0f}%" if ft.get('debt_ratio') else 'N/A'

        top6_cards += f"""
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;'>
                <div>
                    <h3 style='margin:0;color:#2c3e50;'>
                        {i}. {s['name']} {risk_badge}
                        <span style='font-size:18px;' title='진입신호: {entry_label}'>{entry_sig}</span>
                        <a href='https://search.naver.com/search.naver?where=news&query={s["name"]}' target='_blank' style='text-decoration:none;font-size:18px;'>📰</a>
                    </h3>
                    <p style='margin:4px 0 0;color:#7f8c8d;font-size:14px;'>{s['code']}{sec_badge}{trap_badge(trap)}</p>
                </div>
                <div style='text-align:right;'>
                    <div style='font-size:24px;font-weight:bold;color:#e74c3c;'>{s['score']}점</div>
                    <div style='font-size:17px;color:#2c3e50;'>{s['price']:,.0f}원</div>
                    <div style='font-size:12px;color:{ret1m_color};'>1M: {s.get("return_1m", 0):+.1f}%</div>
                </div>
            </div>
            <canvas id='chart{i}' width='400' height='180'></canvas>

            <div style='margin-top:12px;padding:10px;background:#f8f9fa;border-radius:6px;'>
                <div style='font-size:12px;font-weight:bold;color:#2c3e50;margin-bottom:6px;'>📈 재무 추세 (최근 분기)</div>
                <div style='display:flex;gap:8px;flex-wrap:wrap;'>
                    <span style='font-size:12px;'>매출: {trend_badge(ft.get("revenue_trend","?"))}</span>
                    <span style='font-size:12px;'>영업익: {trend_badge(ft.get("op_trend","?"))}</span>
                    <span style='font-size:12px;'>순익: {trend_badge(ft.get("ni_trend","?"))}</span>
                    <span style='font-size:12px;color:#7f8c8d;'>부채비율: {debt_str}</span>
                </div>
                {"<div style='margin-top:5px;font-size:11px;color:#e74c3c;'>⚠️ " + trap.get('reason','') + "</div>" if trap.get('reason') and trap.get('level') in ['danger','caution'] else ""}
                {"<div style='margin-top:5px;font-size:11px;color:#27ae60;'>✅ " + trap.get('reason','') + "</div>" if trap.get('level') == 'opportunity' else ""}
            </div>

            <div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;font-size:13px;'>
                <div><strong>PER:</strong> {per_str}</div><div><strong>PBR:</strong> {pbr_str}</div>
                <div><strong>ROE:</strong> {roe_str}</div><div><strong>BPS:</strong> {bps_str}</div>
            </div>
            <div style='display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px;'>
                <div style='background:#ecf0f1;padding:8px;border-radius:5px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>RSI</div>
                    <div style='font-size:15px;font-weight:bold;color:#e74c3c;'>{s['rsi']:.1f}</div>
                </div>
                <div style='background:#ecf0f1;padding:8px;border-radius:5px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>이격도</div>
                    <div style='font-size:15px;font-weight:bold;color:#e67e22;'>{s['disparity']:.1f}%</div>
                </div>
                <div style='background:#ecf0f1;padding:8px;border-radius:5px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>거래량</div>
                    <div style='font-size:15px;font-weight:bold;color:#27ae60;'>{s['volume_ratio']:.2f}배</div>
                </div>
                <div style='background:#ecf0f1;padding:8px;border-radius:5px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>모멘텀</div>
                    <div style='font-size:15px;font-weight:bold;color:#9b59b6;'>{s.get("momentum_score",0)}점</div>
                </div>
            </div>
            {score_breakdown_bar(sb)}
        </div>
        <script>
        (function(){{var ctx=document.getElementById('chart{i}').getContext('2d');var data={chart_json};if(data.length===0)return;var prices=data.map(d=>d.close);var minPrice=Math.min(...prices);var maxPrice=Math.max(...prices);var range=maxPrice-minPrice;var padding=range*0.1;var width=ctx.canvas.width;var height=ctx.canvas.height;ctx.strokeStyle='#3498db';ctx.lineWidth=2;ctx.beginPath();prices.forEach((price,i)=>{{var x=(i/(prices.length-1))*width;var y=height-((price-minPrice+padding)/(range+2*padding))*height;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});ctx.stroke();}})();
        </script>"""

    # ── TOP 7-30 테이블 ───────────────────────────────
    table_rows = ""
    for i, s in enumerate(top_stocks[6:30], 7):
        pbr_d       = safe_format(s.get('pbr'), '.2f')
        roe_d       = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
        risk_level  = s.get('risk_level', '보통')
        entry_sig   = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
        risk_color  = {'안정': '#27ae60', '보통': '#7f8c8d', '고위험': '#e74c3c'}.get(risk_level, '#7f8c8d')
        sec         = s.get('sector', '기타')
        sec_color   = '#f39c12' if sec in top3 else '#95a5a6'
        ft          = s.get('financial_trend', {})
        trap        = s.get('trap_info', {})
        trap_lv     = trap.get('level', 'neutral')
        trap_cell   = trap.get('label', '') if trap_lv in ['danger', 'caution', 'opportunity'] else '—'
        trap_tc     = {'danger': '#e74c3c', 'caution': '#f39c12', 'opportunity': '#27ae60'}.get(trap_lv, '#7f8c8d')

        table_rows += f"""<tr>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;'>{i}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;font-weight:bold;'>
                {s['name']} <a href='https://search.naver.com/search.naver?where=news&query={s["name"]}' target='_blank' style='text-decoration:none;'>📰</a>
            </td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;'>{s['code']}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>
                <span style='background:{sec_color};color:white;padding:2px 6px;border-radius:3px;font-size:11px;'>{sec}</span>
            </td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:right;'>{s['price']:,.0f}원</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;font-weight:bold;color:#e74c3c;'>{s['score']}점</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;color:{risk_color};font-weight:bold;'>{risk_level}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;font-size:16px;'>{entry_sig}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{ft.get("revenue_trend","?")} {ft.get("op_trend","?")} {ft.get("ni_trend","?")}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;font-size:11px;color:{trap_tc};font-weight:bold;'>{trap_cell}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['rsi']:.1f}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s['disparity']:.1f}%</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s.get("return_1m", 0):+.1f}%</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{pbr_d}</td>
            <td style='padding:10px;border-bottom:1px solid #ecf0f1;text-align:center;'>{roe_d}</td>
        </tr>"""

    # ── 투자자 유형별 추천 ────────────────────────────
    # 밸류트랩 danger 종목은 모든 추천에서 제외
    safe_stocks = [s for s in top_stocks[:30] if s.get('trap_info', {}).get('level') != 'danger']

    aggressive_stocks = sorted([
        s for s in safe_stocks
        if s.get('disparity', 100) < 90 and s.get('rsi', 100) < 30
        and s.get('entry_signal') == '확인'
        and s.get('roe') is not None and s['roe'] >= 3.0
    ], key=lambda x: -x['score'])[:5]
    if len(aggressive_stocks) < 5:
        agg2 = sorted([s for s in safe_stocks if s.get('disparity', 100) < 93
                       and s.get('rsi', 100) < 35 and s.get('roe') is not None
                       and s not in aggressive_stocks], key=lambda x: -x['score'])
        aggressive_stocks += agg2[:5 - len(aggressive_stocks)]
    if len(aggressive_stocks) < 5:
        agg3 = sorted([s for s in safe_stocks if s.get('roe') is not None
                       and s not in aggressive_stocks], key=lambda x: -x['score'])
        aggressive_stocks += agg3[:5 - len(aggressive_stocks)]

    balanced_stocks = sorted([
        s for s in safe_stocks
        if s.get('risk_score', 0) < 70
        and s.get('market_cap') and s['market_cap'] >= 300_000_000_000
        and s.get('roe') is not None
    ], key=lambda x: -x['score'])[:5]
    if len(balanced_stocks) < 5:
        bal2 = sorted([s for s in safe_stocks if s.get('risk_score', 0) < 70
                       and s.get('roe') is not None and s not in balanced_stocks], key=lambda x: -x['score'])
        balanced_stocks += bal2[:5 - len(balanced_stocks)]
    if len(balanced_stocks) < 5:
        bal3 = sorted([s for s in safe_stocks if s.get('risk_score', 0) < 70
                       and s not in balanced_stocks], key=lambda x: -x['score'])
        balanced_stocks += bal3[:5 - len(balanced_stocks)]

    conservative_stocks = sorted([
        s for s in safe_stocks
        if s.get('risk_level') == '안정'
        and s.get('pbr') and s['pbr'] < 1.0
        and s.get('roe') is not None and s['roe'] > 5.0
        and s.get('fin_trend_score', 0) >= 0  # [v1.1] 재무 추세 악화 종목 제외
    ], key=lambda x: -x['score'])[:5]
    if len(conservative_stocks) < 5:
        con2 = sorted([s for s in safe_stocks if s.get('risk_level') == '안정'
                       and s.get('pbr') and s['pbr'] < 1.2
                       and s.get('roe') is not None and s['roe'] > 3.0
                       and s not in conservative_stocks], key=lambda x: -x['score'])
        conservative_stocks += con2[:5 - len(conservative_stocks)]
    if len(conservative_stocks) < 5:
        con3 = sorted([s for s in safe_stocks if s.get('risk_level') == '안정'
                       and s.get('roe') is not None and s not in conservative_stocks], key=lambda x: -x['score'])
        conservative_stocks += con3[:5 - len(conservative_stocks)]

    # [v1.0] 모멘텀 추천 (트랩 종목 제외)
    momentum_stocks = sorted([
        s for s in safe_stocks if s.get('momentum_score', 0) >= 10
    ], key=lambda x: (-x.get('momentum_score', 0), -x['score']))[:5]

    # [v1.1 신규] 진짜 저평가 추천 (opportunity)
    genuine_value_stocks = sorted([
        s for s in top_stocks[:30]
        if s.get('trap_info', {}).get('level') == 'opportunity'
    ], key=lambda x: -x['score'])[:5]

    def make_investor_card(title, desc, stocks, icon, color):
        items = ""
        for idx, s in enumerate(stocks, 1):
            sig   = entry_emoji_map.get(s.get('entry_signal', '관찰'), '🟡')
            roe_d = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
            pbr_d = f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A'
            ft    = s.get('financial_trend', {})
            sec_d = s.get('sector', '기타')
            trap  = s.get('trap_info', {})
            tb    = trap_badge(trap)
            items += f"""<div style='padding:10px;background:#f8f9fa;margin:8px 0;border-radius:5px;'>
                <strong>{idx}. {s['name']}</strong> ({s['code']}) {sig}
                <span style='font-size:11px;background:#95a5a6;color:white;padding:1px 5px;border-radius:3px;margin-left:3px;'>{sec_d}</span>{tb}<br>
                <span style='color:#555;font-size:12px;'>점수: {s['score']}점 | PBR: {pbr_d} | ROE: {roe_d} | 1M: {s.get("return_1m",0):+.1f}%</span><br>
                <span style='font-size:11px;color:#7f8c8d;'>재무: 매출{ft.get("revenue_trend","?")} 영익{ft.get("op_trend","?")} 순익{ft.get("ni_trend","?")}</span>
            </div>"""
        if not items:
            items = "<div style='color:#aaa;padding:10px;'>해당 조건 종목 없음</div>"
        return f"""<div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);border-left:5px solid {color};'>
            <h3 style='margin:0 0 8px 0;color:{color};'>{icon} {title}</h3>
            <p style='color:#555;margin:0 0 12px 0;font-size:12px;'>{desc}</p>{items}</div>"""

    investor_type_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>👥 투자자 유형별 추천</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;margin-bottom:30px;'>
        {make_investor_card('공격적 투자자','이격도 90↓ + RSI 30↓ + 진입확인 + 밸류트랩 제외', aggressive_stocks,'🚀','#e74c3c')}
        {make_investor_card('균형잡힌 투자자','시총 3,000억↑ + ROE 확인 + 밸류트랩 제외', balanced_stocks,'⚖️','#3498db')}
        {make_investor_card('보수적 투자자','안정 + PBR 1.0↓ + ROE 5%↑ + 재무추세 개선', conservative_stocks,'🛡️','#27ae60')}
        {make_investor_card('모멘텀 투자자','신고가 근접 + 1개월 수익률 우수 + 밸류트랩 제외', momentum_stocks,'🔥','#9b59b6')}
        {make_investor_card('퀀트 가치주','저PBR + 실적 개선 확인 = 진짜 저평가 신호', genuine_value_stocks,'💎','#1abc9c')}
    </div>"""

    # ── 지표별 TOP5 ───────────────────────────────────
    rsi_top5      = sorted(top_stocks, key=lambda x: x['rsi'])[:5]
    disp_top5     = sorted(top_stocks, key=lambda x: x['disparity'])[:5]
    vol_top5      = sorted(top_stocks, key=lambda x: -x['volume_ratio'])[:5]
    reb_top5      = sorted(top_stocks, key=lambda x: -x.get('rebound_strength', 0))[:5]
    pbr_top5      = sorted([s for s in top_stocks if s.get('pbr')], key=lambda x: x['pbr'])[:5]
    mom_top5      = sorted([s for s in top_stocks if s.get('return_1m') is not None],
                           key=lambda x: -x.get('momentum_score', 0))[:5]
    fin_top5      = sorted([s for s in top_stocks if s.get('fin_trend_score', 0) > 0],
                           key=lambda x: -x.get('fin_trend_score', 0))[:5]

    def make_list(stocks, fn):
        if not stocks:
            return "<p style='color:#aaa;padding:10px;'>해당 없음</p>"
        return "<ul style='margin:10px 0;padding-left:20px;line-height:1.8;'>" + \
               "".join(f"<li><strong>{s['name']}</strong> ({s['code']}) - {fn(s)}</li>" for s in stocks) + \
               "</ul>"

    indicator_top5_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>📈 지표별 TOP 5</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin-bottom:30px;'>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e74c3c;margin:0 0 10px 0;'>📉 RSI 과매도</h3>{make_list(rsi_top5, lambda s: f"RSI {s['rsi']:.1f}")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e67e22;margin:0 0 10px 0;'>📊 이격도 하락</h3>{make_list(disp_top5, lambda s: f"이격도 {s['disparity']:.1f}%")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#27ae60;margin:0 0 10px 0;'>📦 거래량 급증</h3>{make_list(vol_top5, lambda s: f"거래량 {s['volume_ratio']:.2f}배")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#9b59b6;margin:0 0 10px 0;'>🎯 반등 강도</h3>{make_list(reb_top5, lambda s: f"반등 {s.get('rebound_strength',0):.1f}%")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#3498db;margin:0 0 10px 0;'>💎 저PBR 가치주</h3>{make_list(pbr_top5, lambda s: f"PBR {s['pbr']:.2f}")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#f39c12;margin:0 0 10px 0;'>🔥 모멘텀 강도</h3>{make_list(mom_top5, lambda s: f"1M: {s.get('return_1m',0):+.1f}% / 고점 {s.get('proximity_to_high',0):.0f}%")}</div>
        <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#1abc9c;margin:0 0 10px 0;'>📋 재무 개선주</h3>{make_list(fin_top5, lambda s: f"재무점수 {s.get('fin_trend_score',0):+d}점 | 매출{s.get('financial_trend',{{}}).get('revenue_trend','?')} 영익{s.get('financial_trend',{{}}).get('op_trend','?')}")}</div>
    </div>"""

    # 시장 데이터
    usd_d    = f"{market_data['usd']:,.2f}"    if market_data.get('usd')    else "N/A"
    eur_d    = f"{market_data['eur']:,.2f}"    if market_data.get('eur')    else "N/A"
    jpy_d    = f"{market_data['jpy']:,.2f}"    if market_data.get('jpy')    else "N/A"
    kospi_d  = f"{market_data['kospi']:,.2f}"  if market_data.get('kospi')  else "N/A"
    kosdaq_d = f"{market_data['kosdaq']:,.2f}" if market_data.get('kosdaq') else "N/A"
    kp_cc    = '#27ae60' if market_data.get('kospi_change', 0) >= 0 else '#e74c3c'
    kq_cc    = '#27ae60' if market_data.get('kosdaq_change', 0) >= 0 else '#e74c3c'
    kp_ct    = f"{market_data.get('kospi_change', 0):+.2f}%"
    kq_ct    = f"{market_data.get('kosdaq_change', 0):+.2f}%"

    indicator_footer = """
    <div style='background:#f8f9fa;padding:25px;border-radius:10px;margin-top:30px;border-left:4px solid #3498db;'>
        <h3 style='color:#2c3e50;margin-top:0;'>📘 주요 지표 설명</h3>
        <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:15px;'>
            <div><h4 style='color:#e74c3c;'>📊 RSI</h4><p style='color:#555;line-height:1.6;margin:0;'>30 이하: 과매도(매수 기회) / 70 이상: 과매수</p></div>
            <div><h4 style='color:#e67e22;'>📈 이격도</h4><p style='color:#555;line-height:1.6;margin:0;'>95% 이하: 저평가 / 105% 이상: 과열</p></div>
            <div><h4 style='color:#27ae60;'>📦 거래량 비율</h4><p style='color:#555;line-height:1.6;margin:0;'>1.5배↑: 거래 활성화 / 0.7배↑+연속↑: 🟢 진입신호</p></div>
            <div><h4 style='color:#9b59b6;'>💰 PBR</h4><p style='color:#555;line-height:1.6;margin:0;'>1.0 이하: 저평가 / 3.0 이상: 고평가 (3단계 수집)</p></div>
            <div><h4 style='color:#3498db;'>💵 PER</h4><p style='color:#555;line-height:1.6;margin:0;'>낮을수록 저평가 / 업종 평균과 비교 필요</p></div>
            <div><h4 style='color:#e74c3c;'>📊 ROE</h4><p style='color:#555;line-height:1.6;margin:0;'>10%↑: 양호 / 15%↑: 우수 / N/A: 재무 불투명</p></div>
            <div><h4 style='color:#1abc9c;'>📋 재무 추세</h4><p style='color:#555;line-height:1.6;margin:0;'>▲: 전분기 대비 5%↑ / ▼: 5%↓ / →: 보합 / ?: 데이터 없음</p></div>
            <div><h4 style='color:#e74c3c;'>⛔ 밸류트랩</h4><p style='color:#555;line-height:1.6;margin:0;'>저PBR이지만 실적 동반 하락 → 함정주 가능성 (자동 패널티)</p></div>
            <div><h4 style='color:#f39c12;'>🔥 모멘텀</h4><p style='color:#555;line-height:1.6;margin:0;'>신고가 근접도 + 1개월 수익률 / 상승장 1.5배 가중</p></div>
            <div><h4 style='color:#f39c12;'>🔄 섹터보너스</h4><p style='color:#555;line-height:1.6;margin:0;'>주도 섹터 Top3 소속 종목 +5점 추가</p></div>
        </div>
        <div style='margin-top:15px;padding:15px;background:#e8f5e9;border-radius:8px;border-left:4px solid #27ae60;'>
            <h4 style='color:#1b5e20;margin-top:0;'>⚖️ 국면별 전략 가중치 (v1.1)</h4>
            <ul style='color:#555;line-height:1.8;margin:0;padding-left:20px;'>
                <li><strong>🚀 상승장</strong>: (반등×0.6 + 모멘텀×1.5) + 재무추세 + 섹터보너스 - 트랩패널티</li>
                <li><strong>⚖️ 횡보장</strong>: (반등×0.8 + 모멘텀×0.8) + 재무추세 + 섹터보너스 - 트랩패널티</li>
                <li><strong>⚠️ 하락장</strong>: (반등×1.0 + 모멘텀×0.3) + 재무추세 + 섹터보너스 - 트랩패널티</li>
            </ul>
        </div>
        <div style='margin-top:15px;padding:20px;background:#fff3cd;border-radius:8px;border-left:4px solid #ffc107;'>
            <h4 style='color:#856404;margin-top:0;'>💡 투자 유의사항</h4>
            <ul style='color:#856404;line-height:1.8;margin:0;padding-left:20px;'>
                <li>본 분석은 기술적·재무적 지표 기반 참고 자료이며, 투자 판단은 본인 책임입니다.</li>
                <li>재무 추세는 yfinance 분기 데이터 기준이며 발표 시점 시차가 있을 수 있습니다.</li>
                <li>밸류트랩 탐지는 보조 신호이며, 실제 재무제표 원문 확인을 권장합니다.</li>
                <li>시장 국면 판단은 MA20/MA60 후행 지표 기반으로 전환 시점에 시차가 있습니다.</li>
                <li>분산 투자로 리스크를 관리하고, 한 종목에 과도한 비중을 두지 마세요.</li>
            </ul>
        </div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang='ko'>
<head>
    <meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>
    <meta http-equiv='Cache-Control' content='no-cache, no-store, must-revalidate'>
    <title>다이나믹 트레이딩 v1.1 - {timestamp}</title>
    <style>
        body{{font-family:'Segoe UI',sans-serif;margin:0;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;}}
        .container{{max-width:1440px;margin:0 auto;background:#f8f9fa;padding:30px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,0.3);}}
        h1{{color:#2c3e50;text-align:center;font-size:30px;}}
        .timestamp{{text-align:center;color:#7f8c8d;margin-bottom:30px;font-size:14px;}}
        .market-overview{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:15px;margin-bottom:30px;}}
        .market-card{{background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);text-align:center;}}
        .ai-analysis{{background:white;padding:25px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;border-left:5px solid #3498db;}}
        .top-stocks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-bottom:30px;}}
        table{{width:100%;background:white;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;border-collapse:collapse;}}
        th{{background:#34495e;color:white;padding:10px 8px;text-align:left;font-size:12px;}}
    </style>
</head>
<body>
<div class='container'>
    <h1>📊 다이나믹 트레이딩 종목 추천 v1.1</h1>
    <div class='timestamp'>생성 시간: {timestamp}</div>
    {regime_banner}
    <div class='market-overview'>
        <div class='market-card'><h3 style='margin:0;color:#e74c3c;'>KOSPI</h3><div style='font-size:22px;font-weight:bold;margin:10px 0;'>{kospi_d}</div><div style='color:{kp_cc};'>{kp_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#3498db;'>KOSDAQ</h3><div style='font-size:22px;font-weight:bold;margin:10px 0;'>{kosdaq_d}</div><div style='color:{kq_cc};'>{kq_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>USD/KRW</h3><div style='font-size:22px;font-weight:bold;margin:10px 0;'>{usd_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>EUR/KRW</h3><div style='font-size:22px;font-weight:bold;margin:10px 0;'>{eur_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>JPY/KRW</h3><div style='font-size:22px;font-weight:bold;margin:10px 0;'>{jpy_d}</div></div>
    </div>
    <div class='ai-analysis'><h2 style='margin:0 0 15px 0;color:#2c3e50;'>🤖 AI 종합 분석</h2>{ai_analysis}</div>
    <h2 style='color:#2c3e50;margin:30px 0 20px;'>🏆 추천 종목 TOP 30</h2>
    <div class='top-stocks'>{top6_cards}</div>
    <table>
        <thead><tr>
            <th>순위</th><th>종목명</th><th>코드</th><th>섹터</th><th>현재가</th>
            <th>점수</th><th>위험도</th><th>진입</th><th>재무추세</th><th>밸류트랩</th>
            <th>RSI</th><th>이격도</th><th>1M수익</th><th>PBR</th><th>ROE</th>
        </tr></thead>
        <tbody>{table_rows}</tbody>
    </table>
    {sector_section}
    {investor_type_section}
    {indicator_top5_section}
    {indicator_footer}
    <div style='text-align:center;margin-top:30px;padding:20px;color:#7f8c8d;font-size:13px;'>
        <p>다이나믹 트레이딩 v1.1 — 시장 국면 감지 · 모멘텀 · 섹터 로테이션 · 재무 추세 · Value Trap 탐지</p>
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
    kst        = pytz.timezone('Asia/Seoul')
    start_time = datetime.now(kst)
    logging.info("=== 다이나믹 트레이딩 분석 시작 (v1.1) ===")

    cache          = CacheManager()
    exchange_rates = get_exchange_rates_only(cache)
    market_data    = get_market_data(exchange_rates)

    dart_key = os.environ.get('DART_API')
    if not dart_key:
        logging.warning("⚠️ DART_API 없음 → yfinance fallback")

    mapper        = DARTCorpCodeMapper(dart_key, cache) if dart_key else None
    corp_code_map = mapper.get_all_mappings() if mapper else {}

    krx = KRXData(cache)
    krx.load_all_shares()

    logging.info("📊 시장 국면 감지 중...")
    regime_info   = detect_market_regime()
    market_regime = regime_info['regime']
    logging.info(f"→ 국면: {market_regime} / {regime_info.get('strategy_hint', '')}")

    logging.info("📈 섹터 로테이션 분석 중...")
    sector_data = get_sector_momentum()
    top_sectors = sector_data.get('top_sectors', [])
    logging.info(f"→ 주도 섹터: {top_sectors}")

    stock_list = load_stock_list()
    if not stock_list:
        logging.error("종목 리스트 로드 실패")
        return

    logging.info(f"분석 시작: {len(stock_list)}개 종목")
    args_list = [
        (name, code, dart_key, corp_code_map, market_regime, top_sectors)
        for name, code in stock_list
    ]

    with Pool(processes=4) as pool:
        results = pool.map(analyze_stock_worker, args_list)

    valid_results = [r for r in results if r and r['score'] >= 40]
    valid_results.sort(key=lambda x: (-x['score'], -x['trading_value']))
    top_stocks = valid_results[:30]

    logging.info(f"v1.1 완료: {len(valid_results)}개 추출")

    # 밸류트랩 통계 출력
    danger_count  = sum(1 for r in valid_results if r.get('trap_info', {}).get('level') == 'danger')
    caution_count = sum(1 for r in valid_results if r.get('trap_info', {}).get('level') == 'caution')
    oppty_count   = sum(1 for r in valid_results if r.get('trap_info', {}).get('level') == 'opportunity')
    logging.info(f"밸류트랩 탐지 — ⛔위험:{danger_count} ⚠️주의:{caution_count} ✅기회:{oppty_count}")

    ai_analysis  = get_gemini_analysis(top_stocks, market_regime)
    timestamp    = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(top_stocks, market_data, ai_analysis, timestamp,
                                 regime_info, sector_data)

    filename = f"stock_result_{datetime.now(kst).strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)

    elapsed = (datetime.now(kst) - start_time).total_seconds()
    logging.info(f"=== 완료: {filename} ({elapsed:.1f}초) ===")
    print(f"\n✅ {filename}")
    print(f"   시장 국면: {regime_info['emoji']} {market_regime}")
    print(f"   주도 섹터: {', '.join(top_sectors) if top_sectors else '없음'}")
    print(f"   밸류트랩: ⛔{danger_count}건 ⚠️{caution_count}건 ✅{oppty_count}건")

    entry_map = {'확인': '🟢', '관찰': '🟡', '대기': '🔴'}
    risk_map  = {'안정': '✅', '보통': '⚠️', '고위험': '🚨'}
    for i, s in enumerate(top_stocks[:10], 1):
        ft     = s.get('financial_trend', {})
        trap   = s.get('trap_info', {})
        t_lbl  = trap.get('label', '')
        print(f"  {i:2}. {s['name']:<12} ({s['code']}) - {s['score']}점 "
              f"{risk_map.get(s.get('risk_level','보통'),'⚠️')}{s.get('risk_level','보통')} "
              f"{entry_map.get(s.get('entry_signal','관찰'),'🟡')} "
              f"[{s.get('sector','기타')}] {t_lbl}")
        pbr_s = f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A'
        roe_s = f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A'
        print(f"       PBR:{pbr_s} ROE:{roe_s} | "
              f"매출{ft.get('revenue_trend','?')} 영익{ft.get('op_trend','?')} 순익{ft.get('ni_trend','?')} | "
              f"재무:{s.get('fin_trend_score',0):+d}점 트랩:-{s.get('trap_penalty',0)}점")


if __name__ == "__main__":
    main()
