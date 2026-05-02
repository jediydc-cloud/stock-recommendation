#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
다이나믹 트레이딩 종목 추천 시스템 v1.2.1
─────────────────────────────────────────
v1.0: 시장 국면 감지 · 모멘텀 · 섹터 로테이션
v1.1: 재무 추세 분석(▲/→/▼) · Value Trap 탐지 · 점수 분해 표시
v1.2: 상대강도(RS) · 하락 방어력 · 물타기 경고
v1.2.1: pykrx 차단 환경 대응 패치
  ✅ detect_market_regime: yfinance ^KS11 fallback 추가
  ✅ get_market_data: yfinance ^KS11/^KQ11 fallback 추가
  ✅ get_sector_momentum: 섹터 ETF (yfinance) fallback 추가
  ✅ 물타기 경고 문구 친절하게 개선
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
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS financial_cache
            (stock_code TEXT PRIMARY KEY, equity REAL, net_income REAL, cached_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS shares_cache
            (stock_code TEXT PRIMARY KEY, shares_outstanding INTEGER, cached_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS dart_corp_map
            (stock_code TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT, cached_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS exchange_cache
            (id INTEGER PRIMARY KEY AUTOINCREMENT, usd REAL, eur REAL, jpy REAL, cached_at TEXT)''')
        conn.commit(); conn.close()

    def _kst_now(self):
        return datetime.now(pytz.timezone('Asia/Seoul'))

    def _cutoff(self, days=0, hours=0):
        return (self._kst_now() - timedelta(days=days, hours=hours)).isoformat()

    def get_financial_cache(self, code: str, days: int = 30):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('SELECT equity, net_income FROM financial_cache WHERE stock_code=? AND cached_at>?',
                  (code, self._cutoff(days=days)))
        r = c.fetchone(); conn.close(); return r

    def set_financial_cache(self, code: str, equity: float, net_income: float):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO financial_cache VALUES (?,?,?,?)',
                  (code, equity, net_income, self._kst_now().isoformat()))
        conn.commit(); conn.close()

    def get_shares_cache(self, code: str, days: int = 7):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('SELECT shares_outstanding FROM shares_cache WHERE stock_code=? AND cached_at>?',
                  (code, self._cutoff(days=days)))
        r = c.fetchone(); conn.close(); return r[0] if r else None

    def set_shares_cache(self, code: str, shares: int):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO shares_cache VALUES (?,?,?)',
                  (code, shares, self._kst_now().isoformat()))
        conn.commit(); conn.close()

    def set_corp_code_cache(self, code: str, corp_code: str, corp_name: str):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO dart_corp_map VALUES (?,?,?,?)',
                  (code, corp_code, corp_name, self._kst_now().isoformat()))
        conn.commit(); conn.close()

    def check_corp_map_valid(self, days: int = 30) -> bool:
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM dart_corp_map WHERE cached_at>?', (self._cutoff(days=days),))
        r = c.fetchone()[0]; conn.close(); return r > 0

    def get_all_corp_codes(self, days: int = 30) -> Dict[str, str]:
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('SELECT stock_code, corp_code FROM dart_corp_map WHERE cached_at>?', (self._cutoff(days=days),))
        r = {row[0]: row[1] for row in c.fetchall()}; conn.close(); return r

    def get_exchange_cache(self, hours: int = 24) -> Optional[Tuple]:
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('SELECT usd,eur,jpy FROM exchange_cache WHERE cached_at>? ORDER BY id DESC LIMIT 1',
                  (self._cutoff(hours=hours),))
        r = c.fetchone(); conn.close(); return r

    def set_exchange_cache(self, usd: float, eur: float, jpy: float):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute('INSERT INTO exchange_cache (usd,eur,jpy,cached_at) VALUES (?,?,?,?)',
                  (usd, eur, jpy, self._kst_now().isoformat()))
        conn.commit(); conn.close()


# ============================
# 2. DART corp_code 매핑
# ============================
class DARTCorpCodeMapper:
    def __init__(self, api_key: str, cache: CacheManager):
        self.api_key = api_key; self.cache = cache
        self.base_url = "https://opendart.fss.or.kr/corpCode.xml"
        if not self.cache.check_corp_map_valid(30):
            logging.info("⏳ DART corpCode 캐시 만료 → 재다운로드")
            self._download()
        else:
            logging.info("✅ DART corpCode 캐시 유효")

    def _download(self):
        try:
            r = requests.get(self.base_url, params={'crtfc_key': self.api_key}, timeout=30)
            if r.status_code != 200: return
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                xml = z.read(z.namelist()[0])
            count = 0
            for corp in ET.fromstring(xml).findall('list'):
                sc = corp.findtext('stock_code','').strip()
                cc = corp.findtext('corp_code','').strip()
                cn = corp.findtext('corp_name','').strip()
                if sc and cc:
                    self.cache.set_corp_code_cache(sc, cc, cn); count += 1
            logging.info(f"✅ DART corpCode: {count}개 저장")
        except Exception as e:
            logging.error(f"DART corpCode 실패: {e}")

    def get_all_mappings(self) -> Dict[str, str]:
        return self.cache.get_all_corp_codes(30)


# ============================
# 3. DART 재무제표 수집
# ============================
class DARTFinancials:
    def __init__(self, api_key: str, cache: CacheManager, corp_map: Dict[str, str]):
        self.api_key = api_key; self.cache = cache; self.corp_map = corp_map
        self.base_url = "https://opendart.fss.or.kr/api"
        self.req_count = 0; self.last_req_time = time.time()

    def _rate_limit(self):
        self.req_count += 1
        if self.req_count >= 90:
            elapsed = time.time() - self.last_req_time
            if elapsed < 60: time.sleep(60 - elapsed)
            self.req_count = 0; self.last_req_time = time.time()

    def get_financials(self, code: str):
        cached = self.cache.get_financial_cache(code)
        if cached: return cached
        self._rate_limit()
        corp = self.corp_map.get(code) or code.zfill(6)
        kst = pytz.timezone('Asia/Seoul'); today = datetime.now(kst)
        year = today.year if today.month > 3 else today.year - 1
        q = ((today.month - 1) // 3) if today.month > 3 else 4
        rc = {1:'11013', 2:'11012', 3:'11014', 4:'11011'}[q]
        try:
            r = requests.get(f"{self.base_url}/fnlttSinglAcntAll.json",
                params={'crtfc_key': self.api_key, 'corp_code': corp,
                        'bsns_year': str(year), 'reprt_code': rc, 'fs_div': 'CFS'}, timeout=10)
            if r.status_code != 200: return None, None
            data = r.json()
            if data.get('status') != '000': return None, None
            equity = net_income = None
            for item in data.get('list', []):
                nm = item.get('account_nm', '')
                amt = item.get('thstrm_amount', '').replace(',', '')
                if '자본총계' in nm:
                    try: equity = float(amt) * 1_000_000
                    except: pass
                if '당기순이익' in nm and '지배' in nm:
                    try: net_income = float(amt) * 1_000_000
                    except: pass
            if equity or net_income:
                self.cache.set_financial_cache(code, equity or 0, net_income or 0)
            return equity, net_income
        except: return None, None


# ============================
# 4. KRX 발행주식수
# ============================
class KRXData:
    def __init__(self, cache: CacheManager):
        self.cache = cache; self.shares_data = {}

    def load_all_shares(self):
        try:
            r = requests.get("http://kind.krx.co.kr/corpgeneral/corpList.do",
                params={'method':'download','searchType':'13'}, timeout=30)
            df = pd.read_html(r.content, encoding='euc-kr')[0]
            df['종목코드'] = df['종목코드'].astype(str).str.zfill(6)
            for _, row in df.iterrows():
                code, shares = row['종목코드'], row['상장주식수']
                if pd.notna(shares) and shares > 0:
                    self.shares_data[code] = int(shares)
                    self.cache.set_shares_cache(code, int(shares))
            logging.info(f"발행주식수: {len(self.shares_data)}개")
        except Exception as e:
            logging.warning(f"KRX 발행주식수 실패: {e}")

    def get_shares(self, code: str):
        c = self.cache.get_shares_cache(code, days=7)
        return c if c else self.shares_data.get(code)


# ============================
# [v1.1] 재무 추세 분석
# ============================
def get_financial_trend(ticker_obj) -> dict:
    """
    yfinance quarterly_financials → 매출·영업이익·순이익 3분기 추세
    """
    result = {
        'revenue_trend':'?', 'revenue_change':None, 'revenue_score':0,
        'op_trend':'?',      'op_change':None,      'op_score':0,
        'ni_trend':'?',      'ni_change':None,       'ni_score':0,
        'debt_ratio':None,   'total_score':0,        'data_available':False
    }
    try:
        qf = ticker_obj.quarterly_financials
        if qf is None or qf.empty or len(qf.columns) < 2:
            return result

        def _trend(keys, weight):
            for k in keys:
                if k in qf.index:
                    vals = qf.loc[k].dropna()
                    if len(vals) >= 2:
                        v_new, v_old = float(vals.iloc[0]), float(vals.iloc[1])
                        if v_old == 0: return '→', 0.0, 0
                        chg = (v_new - v_old) / abs(v_old) * 100
                        if chg >= 5:   return '▲', chg,  weight
                        if chg <= -5:  return '▼', chg, -weight
                        return '→', chg, 0
            return '?', None, 0

        r_t, r_c, r_s = _trend(['Total Revenue','Revenue','Net Revenue'], 5)
        o_t, o_c, o_s = _trend(['Operating Income','EBIT','Operating Revenue'], 7)
        n_t, n_c, n_s = _trend(['Net Income','Net Income Common Stockholders',
                                 'Net Income Applicable To Common Shares'], 5)
        debt_ratio = None
        try:
            bs = ticker_obj.quarterly_balance_sheet
            if bs is not None and not bs.empty:
                td = eq = None
                for k in ['Total Liabilities Net Minority Interest','Total Debt','Long Term Debt']:
                    if k in bs.index: td = float(bs.loc[k].iloc[0]); break
                for k in ['Total Equity Gross Minority Interest','Stockholders Equity',
                          'Common Stock Equity','Total Stockholder Equity']:
                    if k in bs.index: eq = float(bs.loc[k].iloc[0]); break
                if td and eq and eq > 0: debt_ratio = (td / eq) * 100
        except: pass

        result.update({
            'revenue_trend':r_t, 'revenue_change':r_c, 'revenue_score':r_s,
            'op_trend':o_t,      'op_change':o_c,      'op_score':o_s,
            'ni_trend':n_t,      'ni_change':n_c,       'ni_score':n_s,
            'debt_ratio':debt_ratio,
            'total_score': r_s + o_s + n_s,
            'data_available': True
        })
    except: pass
    return result


# ============================
# [v1.1] Value Trap 탐지
# ============================
def detect_value_trap(pbr, roe, ft: dict) -> dict:
    if not ft.get('data_available'):
        return {'level':'unknown','penalty':0,'label':'','reason':'재무 추세 데이터 없음'}
    fs    = ft.get('total_score', 0)
    r_t   = ft.get('revenue_trend','?')
    o_t   = ft.get('op_trend','?')
    dr    = ft.get('debt_ratio')
    lp    = pbr is not None and pbr < 1.2
    lr    = roe is not None and roe < 5.0

    if lp and r_t == '▼' and o_t == '▼':
        return {'level':'danger','penalty':20,'label':'⛔ 밸류트랩 위험',
                'reason':f"저PBR({pbr:.2f}) + 매출·영업이익 동반 하락 → 함정주 가능성"}
    if lp and lr and ft.get('ni_trend','?') == '▼':
        return {'level':'danger','penalty':20,'label':'⛔ 밸류트랩 위험',
                'reason':f"저PBR({pbr:.2f}) + ROE({roe:.1f}%) + 순이익 하락 → 구조적 수익성 훼손"}
    if fs <= -5 and dr and dr > 200:
        return {'level':'caution','penalty':10,'label':'⚠️ 밸류트랩 주의',
                'reason':f"실적 하락 + 부채비율 {dr:.0f}% 과다 → 재무 건전성 점검 필요"}
    if lp and fs <= -5:
        return {'level':'caution','penalty':10,'label':'⚠️ 밸류트랩 주의',
                'reason':"밸류에이션 낮지만 실적 일부 하락 — 추가 확인 필요"}
    if lp and fs >= 7:
        return {'level':'opportunity','penalty':0,'label':'✅ 진짜 저평가',
                'reason':f"저PBR({pbr:.2f}) + 매출·이익 개선 → 실질 저평가 가능성 높음"}
    if roe and roe >= 10 and fs >= 10:
        return {'level':'opportunity','penalty':0,'label':'✅ 실적 개선주',
                'reason':f"ROE {roe:.1f}% + 전 항목 실적 개선 → 펀더멘털 강화 중"}
    return {'level':'neutral','penalty':0,'label':'','reason':''}


# ============================
# [v1.2] KOSPI 기준 데이터
# ============================
def get_kospi_reference_data() -> dict:
    empty = {'data_available': False, 'return_20d': 0.0, 'return_50d': 0.0,
             'stress_dates': set(), 'daily_returns': {}}
    try:
        from pykrx import stock
        kst   = pytz.timezone('Asia/Seoul')
        today = datetime.now(kst)
        ed    = today.strftime('%Y%m%d')
        sd    = (today - timedelta(days=120)).strftime('%Y%m%d')
        df    = stock.get_index_ohlcv(sd, ed, "1001")
        if len(df) >= 20:
            df['ret'] = df['종가'].pct_change() * 100
            r20 = (df['종가'].iloc[-1] - df['종가'].iloc[-20]) / df['종가'].iloc[-20] * 100 if len(df) >= 20 else 0
            r50 = (df['종가'].iloc[-1] - df['종가'].iloc[-50]) / df['종가'].iloc[-50] * 100 if len(df) >= 50 else 0
            stress  = set(df[df['ret'] <= -1.0].index.strftime('%Y-%m-%d').tolist())
            daily_r = {d.strftime('%Y-%m-%d'): float(v)
                       for d, v in zip(df.index, df['ret']) if pd.notna(v)}
            logging.info(f"📊 KOSPI 기준: 20d {r20:+.1f}% / 50d {r50:+.1f}% / 스트레스{len(stress)}일 (pykrx)")
            return {'data_available': True, 'return_20d': r20, 'return_50d': r50,
                    'stress_dates': stress, 'daily_returns': daily_r}
    except Exception as e:
        logging.warning(f"pykrx KOSPI 실패: {e} → yfinance fallback")

    try:
        df = yf.Ticker("^KS11").history(period='6mo')
        if len(df) >= 20:
            df['ret'] = df['Close'].pct_change() * 100
            r20 = (df['Close'].iloc[-1] - df['Close'].iloc[-20]) / df['Close'].iloc[-20] * 100 if len(df) >= 20 else 0
            r50 = (df['Close'].iloc[-1] - df['Close'].iloc[-50]) / df['Close'].iloc[-50] * 100 if len(df) >= 50 else 0
            stress  = set(df[df['ret'] <= -1.0].index.strftime('%Y-%m-%d').tolist())
            daily_r = {d.strftime('%Y-%m-%d'): float(v)
                       for d, v in zip(df.index, df['ret']) if pd.notna(v)}
            logging.info(f"📊 KOSPI 기준: 20d {r20:+.1f}% / 50d {r50:+.1f}% / 스트레스{len(stress)}일 (yfinance)")
            return {'data_available': True, 'return_20d': r20, 'return_50d': r50,
                    'stress_dates': stress, 'daily_returns': daily_r}
    except Exception as e:
        logging.warning(f"yfinance KOSPI fallback 실패: {e}")

    logging.warning("⚠️ KOSPI 기준 데이터 수집 실패 → RS Score 비활성화")
    return empty


# ============================
# [v1.2.1 패치] 시장 국면 감지 - yfinance fallback 추가
# ============================
def detect_market_regime() -> dict:
    """KOSPI MA20/MA60 기반 시장 국면 자동 감지 (pykrx → yfinance fallback)"""
    df = None
    source = ""

    # 1차: pykrx 시도
    try:
        from pykrx import stock
        kst = pytz.timezone('Asia/Seoul'); today = datetime.now(kst)
        raw = stock.get_index_ohlcv(
            (today - timedelta(days=200)).strftime('%Y%m%d'),
            today.strftime('%Y%m%d'), "1001")
        if raw is not None and len(raw) >= 60:
            df = raw.rename(columns={'종가': 'Close'})
            source = "pykrx"
    except Exception as e:
        logging.warning(f"pykrx 시장 국면 실패: {e} → yfinance fallback 시도")

    # 2차: yfinance ^KS11 fallback
    if df is None or len(df) < 60:
        try:
            yf_df = yf.Ticker("^KS11").history(period='1y')
            if yf_df is not None and len(yf_df) >= 60:
                df = yf_df
                source = "yfinance"
        except Exception as e:
            logging.warning(f"yfinance KOSPI fallback 실패: {e}")

    if df is None or len(df) < 60:
        logging.warning("⚠️ 시장 국면 데이터 부족 → 횡보장 기본값")
        return {'regime': '횡보장', 'emoji': '⚖️', 'color': '#e67e22',
                'strategy_hint': '데이터 수집 실패 - 균형 전략 권장', 'momentum_20d': 0}

    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    last = df.iloc[-1]
    price = float(last['Close']); ma20 = float(last['MA20']); ma60 = float(last['MA60'])
    mom20 = (price - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20]) * 100

    if price > ma20 > ma60:
        r, e, c = '상승장', '🚀', '#27ae60'
        hint = '모멘텀 전략 강화 · 신고가 근접 종목 중심'
    elif price < ma20 < ma60:
        r, e, c = '하락장', '⚠️', '#e74c3c'
        hint = '반등 전략 강화 · 저평가 종목 중심 (비중 축소 권장)'
    else:
        r, e, c = '횡보장', '⚖️', '#e67e22'
        hint = '반등 + 모멘텀 균형 병행 전략'

    logging.info(f"📊 시장 국면: {r} | KOSPI {price:,.0f} / MA20 {ma20:,.0f} / MA60 {ma60:,.0f} ({source})")
    return {'regime': r, 'emoji': e, 'color': c, 'strategy_hint': hint,
            'price': price, 'ma20': ma20, 'ma60': ma60, 'momentum_20d': mom20}


# ============================
# [v1.0] 섹터 분류
# ============================
SECTOR_KEYWORDS = {
    'IT/반도체':    ['반도체','하이닉스','웨이퍼','팹','파운드리','칩','실리콘'],
    'AI/소프트웨어':['소프트','네이버','카카오','게임','클라우드','인터넷','AI','크래프톤'],
    '바이오/제약':  ['바이오','제약','의약','헬스케어','셀','진단','의료기기'],
    '2차전지':      ['배터리','이차전지','전기차','양극재','음극','전해질','에너지솔'],
    '방산/우주':    ['방산','항공','우주','방위','레이더','함정','미사일','한화시스템','한화에어로'],
    '금융/증권':    ['금융','은행','증권','보험','카드','자산운용','저축','지주'],
    '에너지/화학':  ['에너지','화학','정유','석유','가스','전력','LG화학'],
    '소비재/유통':  ['식품','유통','소매','의류','패션','뷰티','화장품','리테일'],
    '통신':         ['통신','KT','SKT','유플러스','텔레콤','LGU'],
    '건설/부동산':  ['건설','부동산','개발','아파트','주택','현대건설'],
    '조선/해운':    ['조선','해운','중공업','HMM','선박','물류'],
    '철강/소재':    ['철강','포스코','현대제철','소재','금속','알루미늄'],
}

def get_sector_for_stock(name: str) -> str:
    for sector, kws in SECTOR_KEYWORDS.items():
        if any(k in name for k in kws): return sector
    return '기타'


# ============================
# [v1.2.1 패치] 섹터 모멘텀 분석 - ETF fallback 추가
# ============================
def get_sector_momentum() -> dict:
    """
    1차: pykrx 섹터 인덱스 (KRX 직접 차단 시 실패 가능)
    2차: yfinance 섹터 ETF로 대체 (안정적)
    """
    SECTOR_INDEX = {
        'IT/반도체': '1028', '바이오/제약': '1021', '금융/증권': '1032',
        '에너지/화학': '1006', '건설/부동산': '1016', '철강/소재': '1007',
        '조선/해운': '1010', '통신': '1026', '소비재/유통': '1020',
    }

    # 섹터 ETF 매핑 (yfinance fallback용)
    SECTOR_ETF = {
        'IT/반도체':    '091160.KS',  # KODEX 반도체
        '바이오/제약':  '244580.KS',  # KODEX 바이오
        '금융/증권':    '091170.KS',  # KODEX 은행
        '에너지/화학':  '117460.KS',  # KODEX 에너지화학
        '건설/부동산':  '117700.KS',  # KODEX 건설
        '철강/소재':    '117680.KS',  # KODEX 철강
        '조선/해운':    '102960.KS',  # KODEX 기계장비
        '통신':         '098560.KS',  # TIGER 방송통신
        '소비재/유통':  '266370.KS',  # KODEX K-신소비
    }

    sr = {}

    # 1차: pykrx
    try:
        from pykrx import stock
        kst = pytz.timezone('Asia/Seoul'); today = datetime.now(kst)
        ed = today.strftime('%Y%m%d')
        sd = (today - timedelta(days=35)).strftime('%Y%m%d')
        for sn, ic in SECTOR_INDEX.items():
            try:
                df = stock.get_index_ohlcv(sd, ed, ic)
                if len(df) >= 2:
                    sr[sn] = round((df['종가'].iloc[-1] - df['종가'].iloc[0]) / df['종가'].iloc[0] * 100, 2)
                time.sleep(0.2)
            except: continue
    except Exception as e:
        logging.warning(f"pykrx 섹터 모멘텀 실패: {e}")

    # 2차: yfinance ETF fallback (pykrx 실패 또는 부분 실패 시)
    if len(sr) < 5:
        logging.info("⏳ 섹터 ETF fallback 시도 (yfinance)...")
        for sn, etf in SECTOR_ETF.items():
            if sn in sr: continue
            try:
                df = yf.Ticker(etf).history(period='1mo')
                if len(df) >= 2:
                    sr[sn] = round((df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0] * 100, 2)
                time.sleep(0.3)
            except: continue

    if sr:
        srt = dict(sorted(sr.items(), key=lambda x: -x[1]))
        top = list(srt.keys())[:3]
        logging.info(f"📈 주도 섹터 Top3: {top} ({len(sr)}개 섹터 수집)")
        return {'returns': srt, 'top_sectors': top}

    logging.warning("⚠️ 섹터 모멘텀 수집 완전 실패")
    return {'returns': {}, 'top_sectors': []}


# ============================
# 5. 환율 조회
# ============================
def get_exchange_rates_only(cache: CacheManager) -> Dict[str, Optional[float]]:
    result = {'usd': None, 'eur': None, 'jpy': None}
    cached = cache.get_exchange_cache(hours=24)
    if cached:
        result['usd'], result['eur'], result['jpy'] = cached
        logging.info(f"✅ 환율 캐시: USD={result['usd']:.2f}")
        return result
    try:
        for key, ticker in [('usd','KRW=X'),('eur','EURKRW=X'),('jpy','JPYKRW=X')]:
            h = yf.Ticker(ticker).history(period='1d')
            result[key] = h['Close'].iloc[-1] if not h.empty else None
            time.sleep(0.5)
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
        base = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType="
        all_stocks = pd.concat([
            pd.read_html(requests.get(base+'stockMkt',  timeout=30).content, header=0, encoding='euc-kr')[0],
            pd.read_html(requests.get(base+'kosdaqMkt', timeout=30).content, header=0, encoding='euc-kr')[0],
        ], ignore_index=True)
        all_stocks['종목코드'] = all_stocks['종목코드'].astype(str).str.zfill(6)
        ld_col = next((c for c in all_stocks.columns if '상장' in c and '일' in c), None)
        filtered = []
        for _, row in all_stocks.iterrows():
            name, code = row['회사명'], row['종목코드']
            if any(k in name for k in ['우','ETN','SPAC','스팩','리츠','인프라','관리',
                                        '(M)','(관)','정지','제8호','제9호','제10호',
                                        '기업인수목적','기업재무안정']): continue
            if not code.isdigit(): continue
            if ld_col and pd.notna(row.get(ld_col)):
                try:
                    ld = pd.to_datetime(str(row[ld_col]), errors='coerce')
                    if pd.notna(ld) and (datetime.now() - ld.to_pydatetime()).days / 365.0 < 1.0: continue
                except: pass
            filtered.append([name, code])
        logging.info(f"종목 필터링: {len(all_stocks)} → {len(filtered)}개")
        return filtered
    except Exception as e:
        logging.error(f"종목 리스트 로드 실패: {e}"); return []


# ============================
# 7. 종목 분석 워커 (v1.2)
# ============================
def analyze_stock_worker(args):
    import signal

    def _timeout(signum, frame): raise TimeoutError()
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(18)

    try:
        name, code, dart_key, corp_map, market_regime, top_sectors, kospi_ref = args

        suffix = ".KS" if code.startswith('0') else ".KQ"
        ticker = yf.Ticker(f"{code}{suffix}")
        df     = ticker.history(period='3mo')
        if df.empty or len(df) < 20: return None

        price  = df['Close'].iloc[-1]
        v_avg  = df['Volume'].iloc[-20:-1].mean()
        v_cur  = df['Volume'].iloc[-1]

        if v_cur == 0 or price < 2000: return None
        if v_avg * price < 300_000_000: return None

        chart_data = [{'date': d.strftime('%Y-%m-%d'), 'close': float(r['Close'])}
                      for d, r in df.iterrows()]

        # ── 기존 반등 지표 ────────────────────────────
        delta = df['Close'].diff()
        gain  = delta.where(delta > 0, 0).rolling(14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi   = 100 - (100 / (1 + gain / loss))
        cur_rsi     = rsi.iloc[-1]
        rsi_score   = 30 if cur_rsi < 30 else 20 if cur_rsi < 40 else 10 if cur_rsi < 50 else 0

        ma20      = df['Close'].rolling(20).mean().iloc[-1]
        disparity = (price / ma20) * 100
        disp_score = 20 if disparity < 95 else 15 if disparity < 98 else 10 if disparity < 100 else 0

        v_ratio  = v_cur / v_avg if v_avg > 0 else 0
        vol_score = 15 if v_ratio >= 1.5 else 10 if v_ratio >= 1.2 else 5 if v_ratio >= 1.0 else 0

        # ── 재무 데이터 수집 (PBR 3단계) ─────────────
        cache = CacheManager()
        dart  = DARTFinancials(dart_key, cache, corp_map)
        krx   = KRXData(cache)
        equity, net_income = dart.get_financials(code)
        shares = krx.get_shares(code)

        pbr = bps = per = roe = eps = None
        pbr_score = 0

        try:
            info = ticker.info
            ptb  = info.get('priceToBook')
            if ptb and ptb > 0: pbr = float(ptb)
            bv = info.get('bookValue')
            if bv and bv > 0:
                bps = float(bv)
                if not pbr: pbr = price / bps
            if not shares:
                s2 = info.get('sharesOutstanding') or info.get('floatShares')
                if s2: shares = int(s2)
            if not equity:
                mc = info.get('marketCap')
                if mc and pbr and pbr > 0: equity = mc / pbr
            if not net_income and info.get('netIncomeToCommon'):
                net_income = info['netIncomeToCommon']
        except: pass

        if not pbr and equity and shares and shares > 0:
            try:
                bps = bps or (equity / shares)
                if bps and bps > 0: pbr = price / bps
            except: pass

        if not equity or not net_income:
            try:
                bs = ticker.balance_sheet; fi = ticker.financials
                if not bs.empty and not equity:
                    for k in ['Total Stockholder Equity','Stockholders Equity',
                              'Common Stock Equity','Total Equity Gross Minority Interest']:
                        if k in bs.index: equity = bs.loc[k].iloc[0]; break
                if not fi.empty and not net_income:
                    for k in ['Net Income','Net Income Common Stockholders']:
                        if k in fi.index: net_income = fi.loc[k].iloc[0]; break
                if not pbr and equity and shares and shares > 0:
                    bps = bps or (equity / shares)
                    if bps and bps > 0: pbr = price / bps
            except: pass

        if equity is not None and equity < 0: return None
        if pbr and pbr > 0:
            pbr_score = 15 if pbr < 1.0 else 10 if pbr < 1.5 else 5 if pbr < 2.0 else 0
        if net_income and shares and shares > 0:
            eps = net_income / shares
            per = price / eps if eps > 0 else None
        if net_income and equity and equity > 0:
            roe = (net_income / equity) * 100
            if roe < 0: return None

        ret5d  = ((df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100) if len(df) >= 6 else 0
        ret_score = 10 if -5 <= ret5d <= 0 else 5 if -10 <= ret5d < -5 else 0

        low20d  = df['Low'].iloc[-20:].min()
        rebound = ((price - low20d) / low20d * 100) if low20d > 0 else 0
        reb_score = 10 if rebound >= 5 else 5 if rebound >= 3 else 0

        roe_penalty = 10 if (roe is not None and 0 <= roe < 3.0) else 0

        vol_up = (len(df) >= 3 and
                  df['Volume'].iloc[-1] > df['Volume'].iloc[-2] and
                  df['Volume'].iloc[-2] > df['Volume'].iloc[-3])

        if vol_up and v_ratio >= 0.7 and cur_rsi < 35: entry = '확인'
        elif vol_up or v_ratio >= 0.8:                  entry = '관찰'
        else:                                            entry = '대기'
        if roe is None and entry == '확인': entry = '관찰'

        # ── [v1.1] 재무 추세 + Value Trap ───────────
        ft           = get_financial_trend(ticker)
        fin_score    = ft.get('total_score', 0)
        trap         = detect_value_trap(pbr, roe, ft)
        trap_penalty = trap.get('penalty', 0)

        # ── [v1.0] 모멘텀 지표 ───────────────────────
        high3m  = df['High'].max()
        prox_hi = (price / high3m) * 100 if high3m > 0 else 50
        ret1m   = ((price - df['Close'].iloc[-21]) / df['Close'].iloc[-21] * 100) if len(df) >= 21 else 0

        mom_score = 0
        if prox_hi >= 97:   mom_score += 20
        elif prox_hi >= 90: mom_score += 12
        elif prox_hi >= 80: mom_score += 6
        if ret1m >= 15:   mom_score += 15
        elif ret1m >= 8:  mom_score += 10
        elif ret1m >= 3:  mom_score += 5

        # ── [v1.0] 섹터 ───────────────────────────────
        sector       = get_sector_for_stock(name)
        sector_bonus = 5 if sector in top_sectors else 0

        # ── [v1.2] 상대강도(RS) 계산 ───────────
        rs_20d = rs_50d = 0.0
        rs_score = defensive_score = 0
        averaging_warning = False

        if kospi_ref.get('data_available'):
            s20 = ((price - df['Close'].iloc[-20]) / df['Close'].iloc[-20] * 100) if len(df) >= 20 else 0
            rs_20d = s20 - kospi_ref['return_20d']

            if len(df) >= 50:
                s50    = (price - df['Close'].iloc[-50]) / df['Close'].iloc[-50] * 100
                rs_50d = s50 - kospi_ref['return_50d']
                rs_50_pts = (5  if rs_50d >= 5  else 2  if rs_50d >= 0 else
                            -2  if rs_50d >= -5 else -5)
            else:
                rs_50d    = 0.0
                rs_50_pts = 0

            rs_20_pts = (15 if rs_20d >= 10 else 10 if rs_20d >= 5 else
                         5  if rs_20d >= 0  else -5 if rs_20d >= -5 else -10)
            rs_score = rs_20_pts + rs_50_pts

            stress_dates = kospi_ref.get('stress_dates', set())
            df_tmp = df.copy()
            df_tmp['ds']  = [d.strftime('%Y-%m-%d') for d in df_tmp.index]
            df_tmp['ret'] = df_tmp['Close'].pct_change() * 100
            common = stress_dates & set(df_tmp['ds'].tolist())

            if len(common) >= 3:
                s_rets = df_tmp[df_tmp['ds'].isin(common)]['ret'].dropna()
                k_rets = [kospi_ref['daily_returns'].get(d, 0) for d in common]
                if len(s_rets) > 0:
                    avg_s = s_rets.mean()
                    avg_k = sum(k_rets) / len(k_rets) if k_rets else 0
                    diff  = avg_s - avg_k
                    defensive_score = (15 if diff >= 2.0 else 10 if diff >= 0 else
                                       5  if diff >= -1.0 else 0)

            if rs_20d < -5 and fin_score < 0:              averaging_warning = True
            elif rs_20d < -10:                             averaging_warning = True
            elif rs_20d < -5 and trap.get('level') == 'danger': averaging_warning = True

        # ── 국면별 가중치 적용 ────────────────────────
        base = rsi_score + disp_score + vol_score + pbr_score + ret_score + reb_score
        base = max(0, base - roe_penalty)

        if market_regime == '상승장':
            weighted = int(base * 0.6 + mom_score * 1.5)
        elif market_regime == '하락장':
            weighted = int(base * 1.0 + mom_score * 0.3)
        else:
            weighted = int(base * 0.8 + mom_score * 0.8)

        total_score = weighted + fin_score + rs_score + defensive_score + sector_bonus - trap_penalty
        if disparity > 100:
            total_score = max(0, total_score - int((disparity - 100) * 2))

        tv = price * v_cur
        mc = (price * shares) if shares and shares > 0 else None
        if mc and mc < 30_000_000_000: return None

        # 위험도
        risk = 0
        if '정지' in name or '거래중지' in name: risk += 100
        if '관리' in name or '(M)' in name:      risk += 80
        if pbr and pbr > 5.0:                    risk += 80
        if net_income and net_income < 0:         risk += 50
        hi20 = df['High'].iloc[-20:].max(); lo20 = df['Low'].iloc[-20:].min()
        vola = ((hi20 - lo20) / lo20 * 100) if lo20 > 0 else 0
        if vola > 50:             risk += 25
        if rebound > 50:          risk += 40
        elif rebound > 30:        risk += 20
        if v_ratio > 5.0:        risk += 20
        if disparity > 120:      risk += 15
        if pbr and pbr > 3.0:    risk += 20
        if trap.get('level') == 'danger':    risk += 30
        if averaging_warning:                risk += 15
        risk_level = '고위험' if risk >= 70 else '보통' if risk >= 30 else '안정'

        return {
            'name':name, 'code':code, 'price':price,
            'score':total_score, 'trading_value':tv,
            'rsi':cur_rsi, 'disparity':disparity, 'volume_ratio':v_ratio,
            'pbr':pbr, 'per':per, 'roe':roe, 'bps':bps, 'eps':eps,
            'chart_data':chart_data,
            'risk_score':risk, 'risk_level':risk_level,
            'rebound_strength':rebound,
            'entry_signal':entry,
            'market_cap':mc,
            'momentum_score':mom_score, 'proximity_to_high':prox_hi,
            'return_1m':ret1m, 'sector':sector, 'sector_bonus':sector_bonus,
            'financial_trend':ft, 'fin_trend_score':fin_score,
            'trap_info':trap, 'trap_penalty':trap_penalty,
            'rs_20d':rs_20d, 'rs_50d':rs_50d,
            'rs_score':rs_score, 'defensive_score':defensive_score,
            'averaging_warning':averaging_warning,
            'score_breakdown':{
                'weighted':weighted,       'fin_trend_score':fin_score,
                'rs_score':rs_score,       'defensive_score':defensive_score,
                'sector_bonus':sector_bonus, 'trap_penalty':trap_penalty,
            }
        }
    except Exception: return None
    finally: signal.alarm(0)


# ============================
# [v1.2.1 패치] 시장 데이터 조회 - yfinance fallback 추가
# ============================
def get_market_data(exchange_rates: Dict[str, Optional[float]]) -> dict:
    result = {'kospi': None, 'kospi_change': 0, 'kosdaq': None, 'kosdaq_change': 0,
              'usd': exchange_rates.get('usd'), 'eur': exchange_rates.get('eur'),
              'jpy': exchange_rates.get('jpy')}

    # 1차: pykrx
    try:
        from pykrx import stock
        kst = pytz.timezone('Asia/Seoul'); today = datetime.now(kst)
        for idx_code, key in [("1001", "kospi"), ("2001", "kosdaq")]:
            for d in range(7):
                try:
                    ed = (today - timedelta(days=d)).strftime('%Y%m%d')
                    sd = (today - timedelta(days=d + 5)).strftime('%Y%m%d')
                    df = stock.get_index_ohlcv(sd, ed, idx_code)
                    if len(df) >= 2:
                        result[key] = df['종가'].iloc[-1]
                        result[f'{key}_change'] = (df['종가'].iloc[-1] - df['종가'].iloc[-2]) / df['종가'].iloc[-2] * 100
                        break
                    elif len(df) == 1:
                        result[key] = df['종가'].iloc[-1]; break
                except: continue
    except Exception as e:
        logging.warning(f"pykrx 시장 데이터 실패: {e} → yfinance fallback")

    # 2차: yfinance fallback
    if not result['kospi']:
        try:
            df = yf.Ticker("^KS11").history(period='5d')
            if len(df) >= 2:
                result['kospi'] = float(df['Close'].iloc[-1])
                result['kospi_change'] = (df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100
                logging.info(f"✅ KOSPI yfinance fallback: {result['kospi']:,.2f}")
        except Exception as e:
            logging.warning(f"yfinance KOSPI 실패: {e}")

    if not result['kosdaq']:
        try:
            df = yf.Ticker("^KQ11").history(period='5d')
            if len(df) >= 2:
                result['kosdaq'] = float(df['Close'].iloc[-1])
                result['kosdaq_change'] = (df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100
                logging.info(f"✅ KOSDAQ yfinance fallback: {result['kosdaq']:,.2f}")
        except Exception as e:
            logging.warning(f"yfinance KOSDAQ 실패: {e}")

    return result


# ============================
# 9. Gemini AI 분석
# ============================
def get_gemini_analysis(top_stocks, market_regime: str = '횡보장'):
    try:
        genai.configure(api_key=os.environ.get('swingTrading'))
        model = genai.GenerativeModel('gemini-2.5-flash')
        data  = [{
            '종목명':   s['name'], '총점': f"{s['score']}점",
            'RSI':      f"{s['rsi']:.1f}", '이격도': f"{s['disparity']:.1f}%",
            'PBR':      f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A',
            'ROE':      f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A',
            '진입신호': s.get('entry_signal','관찰'),
            '섹터':     s.get('sector','기타'),
            '1M수익률': f"{s.get('return_1m',0):+.1f}%",
            '재무추세': f"매출{s.get('financial_trend',{}).get('revenue_trend','?')} 영익{s.get('financial_trend',{}).get('op_trend','?')}",
            '밸류트랩': s.get('trap_info',{}).get('label',''),
            'RS20d':    f"{s.get('rs_20d',0):+.1f}%p",
            '방어력점수': f"{s.get('defensive_score',0)}점",
            '물타기경고': '⚠️예' if s.get('averaging_warning') else '없음',
        } for s in top_stocks[:6]]
        rsp = model.generate_content(
            f"20년 경력 퀀트 애널리스트로 현재 시장 국면({market_regime}) 기준 TOP6 종목 분석:\n"
            f"{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
            f"1.공통점 2.주목종목(RS·재무추세 고려) 3.진입타이밍 4.밸류트랩·물타기 주의\n200자 이내, 숫자 근거 포함")
        return rsp.text
    except Exception as e:
        logging.warning(f"Gemini 오류: {e}")
        return "<div style='text-align:center;padding:20px;color:#888;'>⚠️ AI 분석 생략</div>"


# ============================
# 헬퍼 함수
# ============================
def safe_format(v, fmt, default='N/A'):
    if v is None: return default
    try: return format(v, fmt)
    except: return default

def format_fin_trend(s):
    ft = s.get('financial_trend') or {}
    return (f"재무{s.get('fin_trend_score',0):+d}점 | "
            f"매출{ft.get('revenue_trend','?')} "
            f"영익{ft.get('op_trend','?')}")

def format_rs(s):
    return (f"RS20d: {s.get('rs_20d',0):+.1f}%p | "
            f"방어력: {s.get('defensive_score',0)}점")


# ============================
# 10. HTML 보고서 생성
# ============================
def generate_html(top_stocks, market_data, ai_analysis, timestamp,
                  regime_info=None, sector_data=None):

    def risk_badge(rl):
        c = {'안정':'#27ae60','보통':'#7f8c8d','고위험':'#e74c3c'}.get(rl,'#7f8c8d')
        return f"<span style='display:inline-block;padding:3px 8px;margin-left:6px;border-radius:4px;font-size:12px;font-weight:bold;background:{c};color:white;'>{rl}</span>"

    def trend_badge(t):
        cfg = {'▲':('#27ae60','▲ 개선'),'▼':('#e74c3c','▼ 하락'),'→':('#7f8c8d','→ 보합'),'?':('#bdc3c7','? 미확인')}
        c, lb = cfg.get(t,('#bdc3c7',t))
        return f"<span style='background:{c};color:white;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:bold;'>{lb}</span>"

    def trap_badge(trap):
        lb = trap.get('label','')
        if not lb: return ''
        c = {'danger':'#e74c3c','caution':'#f39c12','opportunity':'#27ae60'}.get(trap.get('level',''),'#95a5a6')
        return f"<span style='background:{c};color:white;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-left:4px;'>{lb}</span>"

    def rs_badge(rs_20d):
        if rs_20d >= 5:   c, lb = '#27ae60', f'RS {rs_20d:+.1f}%p 🔝'
        elif rs_20d >= 0: c, lb = '#58d68d', f'RS {rs_20d:+.1f}%p'
        elif rs_20d >= -5:c, lb = '#e67e22', f'RS {rs_20d:+.1f}%p'
        else:             c, lb = '#e74c3c', f'RS {rs_20d:+.1f}%p ⚠️'
        return f"<span style='background:{c};color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;'>{lb}</span>"

    def def_bar(score):
        w = min(score / 15 * 100, 100)
        c = '#27ae60' if score >= 10 else '#e67e22' if score >= 5 else '#bdc3c7'
        return (f"<div style='display:flex;align-items:center;gap:6px;'>"
                f"<span style='font-size:11px;color:#7f8c8d;width:36px;'>방어력</span>"
                f"<div style='flex:1;background:#ecf0f1;border-radius:3px;height:8px;'>"
                f"<div style='width:{w:.0f}%;background:{c};height:8px;border-radius:3px;'></div></div>"
                f"<span style='font-size:11px;font-weight:bold;color:{c};'>{score}점</span></div>")

    def score_bar(sb):
        w = sb.get('weighted',0); fs = sb.get('fin_trend_score',0)
        rs = sb.get('rs_score',0); ds = sb.get('defensive_score',0)
        se = sb.get('sector_bonus',0); tp = sb.get('trap_penalty',0)
        fc = '#27ae60' if fs >= 0 else '#e74c3c'
        rc = '#27ae60' if rs >= 0 else '#e74c3c'
        return (f"<div style='font-size:11px;background:#f8f9fa;padding:8px;border-radius:6px;margin-top:8px;'>"
                f"<div style='font-weight:bold;color:#2c3e50;margin-bottom:5px;'>📊 점수 구성</div>"
                f"<div style='display:flex;gap:4px;flex-wrap:wrap;'>"
                f"<span style='background:#3498db;color:white;padding:2px 6px;border-radius:3px;'>시세:{w}</span>"
                f"<span style='background:{fc};color:white;padding:2px 6px;border-radius:3px;'>재무:{fs:+d}</span>"
                f"<span style='background:{rc};color:white;padding:2px 6px;border-radius:3px;'>RS:{rs:+d}</span>"
                f"<span style='background:#1abc9c;color:white;padding:2px 6px;border-radius:3px;'>방어:{ds:+d}</span>"
                f"<span style='background:#f39c12;color:white;padding:2px 6px;border-radius:3px;'>섹터:+{se}</span>"
                f"<span style='background:#e74c3c;color:white;padding:2px 6px;border-radius:3px;'>트랩:-{tp}</span>"
                f"</div></div>")

    entry_map = {'확인':'🟢','관찰':'🟡','대기':'🔴'}
    top3      = (sector_data or {}).get('top_sectors', [])

    # ── 시장 국면 배너 ────────────────────────────────
    reg    = regime_info or {'regime':'횡보장','emoji':'⚖️','color':'#e67e22','strategy_hint':'-','momentum_20d':0}
    rc     = reg['color']; re = reg['emoji']; rn = reg['regime']
    rh     = reg.get('strategy_hint',''); rm = reg.get('momentum_20d',0)
    rma20  = f"{reg['ma20']:,.0f}"  if reg.get('ma20')  else 'N/A'
    rma60  = f"{reg['ma60']:,.0f}"  if reg.get('ma60')  else 'N/A'
    rpr    = f"{reg['price']:,.0f}" if reg.get('price') else 'N/A'

    regime_banner = f"""
    <div style='background:white;padding:20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);
                margin-bottom:20px;border-left:6px solid {rc};'>
        <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;'>
            <div>
                <h2 style='margin:0;color:{rc};font-size:20px;'>{re} 현재 시장 국면: {rn}</h2>
                <p style='margin:6px 0 0;color:#555;font-size:14px;'>💡 전략 힌트: {rh}</p>
            </div>
            <div style='display:flex;gap:10px;flex-wrap:wrap;'>
                <div style='background:#f8f9fa;padding:10px 14px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>KOSPI</div><div style='font-weight:bold;'>{rpr}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 14px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>MA20</div><div style='font-weight:bold;'>{rma20}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 14px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>MA60</div><div style='font-weight:bold;'>{rma60}</div>
                </div>
                <div style='background:#f8f9fa;padding:10px 14px;border-radius:8px;text-align:center;'>
                    <div style='font-size:11px;color:#7f8c8d;'>20일 모멘텀</div>
                    <div style='font-weight:bold;color:{"#27ae60" if rm>=0 else "#e74c3c"};'>{rm:+.1f}%</div>
                </div>
            </div>
        </div>
        <div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;font-size:13px;'>
            <span style='padding:4px 12px;border-radius:20px;background:{"#d5f5e3" if rn=="상승장" else "#f0f0f0"};color:{"#1e8449" if rn=="상승장" else "#aaa"};font-weight:bold;'>🚀 상승장: 모멘텀 1.5x</span>
            <span style='padding:4px 12px;border-radius:20px;background:{"#fdebd0" if rn=="횡보장" else "#f0f0f0"};color:{"#935116" if rn=="횡보장" else "#aaa"};font-weight:bold;'>⚖️ 횡보장: 균형 0.8x</span>
            <span style='padding:4px 12px;border-radius:20px;background:{"#fadbd8" if rn=="하락장" else "#f0f0f0"};color:{"#922b21" if rn=="하락장" else "#aaa"};font-weight:bold;'>⚠️ 하락장: 반등 강화</span>
        </div>
    </div>"""

    # ── 상대강도 분석 섹션 ──────────────
    rs_sorted = sorted(top_stocks, key=lambda x: -x.get('rs_20d', 0))
    rs_top5   = rs_sorted[:5]
    rs_bot5   = sorted(top_stocks, key=lambda x: x.get('rs_20d', 0))[:5]
    warn_list = [s for s in top_stocks if s.get('averaging_warning')]

    def rs_row(s, highlight=False):
        rs = s.get('rs_20d', 0); ds = s.get('defensive_score', 0)
        bg = '#f0fff4' if highlight else 'white'
        rc2 = '#27ae60' if rs >= 0 else '#e74c3c'
        return (f"<tr style='background:{bg};'>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #ecf0f1;font-weight:bold;'>{s['name']}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #ecf0f1;color:{rc2};font-weight:bold;text-align:right;'>{rs:+.1f}%p</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{ds}점</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #ecf0f1;text-align:center;'>{s.get('sector','기타')}</td>"
                f"</tr>")

    # ── [v1.2.1] 물타기 경고 문구 친절하게 개선 ──
    warn_html = ""
    if warn_list:
        items = "".join(
            f"<li><strong>{s['name']}</strong> ({s['code']}) — RS {s.get('rs_20d',0):+.1f}%p | "
            f"재무: {s.get('fin_trend_score',0):+d}점<br>"
            f"<span style='color:#922b21;font-size:12px;'>"
            f"📉 시장(KOSPI)보다 {abs(s.get('rs_20d',0)):.1f}%p 약하고, 매출·이익 추세도 하락 중. "
            f"<strong>추가매수(물타기) 시 약한 종목 비중만 커져 손실 확대 위험이 큽니다.</strong> "
            f"손절 검토 또는 재무 개선 신호 확인 후 재진입을 권장합니다."
            f"</span></li>"
            for s in warn_list
        )
        warn_html = (f"<div style='margin-top:20px;padding:15px;background:#fadbd8;border-radius:8px;border-left:4px solid #e74c3c;'>"
                     f"<h4 style='color:#922b21;margin:0 0 8px 0;'>⛔ 물타기 경고 종목 ({len(warn_list)}개)</h4>"
                     f"<ul style='margin:0;padding-left:20px;color:#922b21;line-height:1.8;font-size:13px;'>{items}</ul>"
                     f"</div>")

    rs_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>📡 시장 대비 상대강도 분석</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:20px;margin-bottom:20px;'>
        <div style='background:white;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;'>
            <div style='padding:12px 16px;background:#27ae60;color:white;font-weight:bold;font-size:14px;'>
                🔝 시장 대비 강세 TOP5 (RS 상위)
            </div>
            <table style='width:100%;border-collapse:collapse;'>
                <thead><tr style='background:#f8f9fa;'>
                    <th style='padding:8px 12px;text-align:left;font-size:12px;'>종목</th>
                    <th style='padding:8px 12px;text-align:right;font-size:12px;'>RS20d</th>
                    <th style='padding:8px 12px;text-align:center;font-size:12px;'>방어력</th>
                    <th style='padding:8px 12px;text-align:center;font-size:12px;'>섹터</th>
                </tr></thead>
                <tbody>{"".join(rs_row(s, True) for s in rs_top5)}</tbody>
            </table>
        </div>
        <div style='background:white;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;'>
            <div style='padding:12px 16px;background:#e74c3c;color:white;font-weight:bold;font-size:14px;'>
                ⚠️ 시장 대비 약세 경고 (RS 하위)
            </div>
            <table style='width:100%;border-collapse:collapse;'>
                <thead><tr style='background:#f8f9fa;'>
                    <th style='padding:8px 12px;text-align:left;font-size:12px;'>종목</th>
                    <th style='padding:8px 12px;text-align:right;font-size:12px;'>RS20d</th>
                    <th style='padding:8px 12px;text-align:center;font-size:12px;'>방어력</th>
                    <th style='padding:8px 12px;text-align:center;font-size:12px;'>섹터</th>
                </tr></thead>
                <tbody>{"".join(rs_row(s) for s in rs_bot5)}</tbody>
            </table>
        </div>
    </div>
    {warn_html}"""

    # ── 섹터 로테이션 섹션 ────────────────────────────
    s_data    = sector_data or {'returns': {}, 'top_sectors': []}
    s_returns = s_data.get('returns', {})
    sec_rows  = ""
    for sec, ret in s_returns.items():
        is_top = sec in top3
        bc     = '#27ae60' if ret > 0 else '#e74c3c'
        bw     = min(abs(ret) * 4, 100)
        badge  = "<span style='background:#f39c12;color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:6px;'>주도</span>" if is_top else ""
        bg_sec  = "#f0fff4" if is_top else "white"
        fw_sec  = "bold"   if is_top else "normal"
        sec_rows += (f"<tr style='background:{bg_sec};'>"
                     f"<td style='padding:9px 14px;border-bottom:1px solid #ecf0f1;font-weight:{fw_sec};'>{sec}{badge}</td>"
                     f"<td style='padding:9px 14px;border-bottom:1px solid #ecf0f1;text-align:right;color:{bc};font-weight:bold;'>{ret:+.1f}%</td>"
                     f"<td style='padding:9px 14px;border-bottom:1px solid #ecf0f1;'>"
                     f"<div style='background:#ecf0f1;border-radius:3px;height:9px;'>"
                     f"<div style='background:{bc};border-radius:3px;height:9px;width:{bw:.0f}%;'></div></div></td></tr>")

    sector_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>🔄 섹터 로테이션 분석 (최근 1개월)</h2>
    <div style='background:white;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;overflow:hidden;'>
        <div style='padding:12px 18px;background:#34495e;color:white;font-size:13px;'>
            📊 보너스 섹터: {", ".join(top3) if top3 else "없음"} · 상위 3개 섹터 +5점
        </div>
        <table style='width:100%;border-collapse:collapse;'>
            <thead><tr style='background:#f8f9fa;'>
                <th style='padding:9px 14px;text-align:left;'>섹터</th>
                <th style='padding:9px 14px;text-align:right;'>1개월 수익률</th>
                <th style='padding:9px 14px;'>추세</th>
            </tr></thead>
            <tbody>{sec_rows if sec_rows else "<tr><td colspan='3' style='padding:16px;text-align:center;color:#aaa;'>데이터 없음</td></tr>"}</tbody>
        </table>
    </div>"""

    # ── TOP 6 카드 ────────────────────────────────────
    top6_cards = ""
    for i, s in enumerate(top_stocks[:6], 1):
        cj   = json.dumps(s.get('chart_data', []))
        ft   = s.get('financial_trend') or {}
        trap = s.get('trap_info') or {}
        sb   = s.get('score_breakdown') or {}
        per_s = safe_format(s.get('per'),'.1f')
        pbr_s = safe_format(s.get('pbr'),'.2f')
        roe_s = f"{s['roe']:.1f}%" if s.get('roe') is not None else '⚠️ N/A'
        bps_s = f"{s['bps']:,.0f}원" if s.get('bps') else 'N/A'
        sec_t = s.get('sector','기타')
        is_ts = sec_t in top3
        sec_b = f"<span style='background:{'#f39c12' if is_ts else '#95a5a6'};color:white;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:4px;'>{sec_t}</span>"
        r1mc  = '#27ae60' if s.get('return_1m',0) >= 0 else '#e74c3c'
        rs20  = s.get('rs_20d', 0)
        ds    = s.get('defensive_score', 0)
        aw    = s.get('averaging_warning', False)

        # ── [v1.2.1] 물타기 경고 박스 친절하게 개선 ──
        avg_warn_box = ""
        if aw:
            avg_warn_box = ("<div style='background:#fadbd8;border:1px solid #e74c3c;border-radius:6px;"
                           "padding:10px 12px;margin-top:8px;font-size:12px;color:#922b21;line-height:1.6;'>"
                           "⛔ <strong>물타기 경고 (Averaging Down Warning)</strong><br>"
                           "이 종목은 시장(KOSPI) 평균보다 수익률이 뒤처지고 있고, 재무 추세도 하락 중입니다. "
                           "이런 상황에서 <strong>평균단가를 낮추려고 추가 매수(물타기)</strong>하면, "
                           "약한 종목에 자금만 더 묶여 손실이 확대될 수 있습니다. "
                           "<strong>손절 검토 또는 펀더멘털 개선 확인 후 재진입</strong>이 합리적입니다.</div>")

        top6_cards += f"""
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);{"border-top:3px solid #e74c3c;" if aw else ""}'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>
                <div>
                    <h3 style='margin:0;color:#2c3e50;font-size:16px;'>
                        {i}. {s['name']} {risk_badge(s.get('risk_level','보통'))}
                        <span title='진입신호: {s.get("entry_signal","관찰")}'>{entry_map.get(s.get("entry_signal","관찰"),"🟡")}</span>
                        <a href='https://search.naver.com/search.naver?where=news&query={s["name"]}' target='_blank' style='text-decoration:none;'>📰</a>
                    </h3>
                    <p style='margin:3px 0 0;color:#7f8c8d;font-size:13px;'>
                        {s['code']}{sec_b}{trap_badge(trap)}
                    </p>
                    <div style='margin-top:4px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;'>
                        {rs_badge(rs20)}
                    </div>
                </div>
                <div style='text-align:right;'>
                    <div style='font-size:22px;font-weight:bold;color:#e74c3c;'>{s['score']}점</div>
                    <div style='font-size:16px;color:#2c3e50;'>{s['price']:,.0f}원</div>
                    <div style='font-size:12px;color:{r1mc};'>1M: {s.get("return_1m",0):+.1f}%</div>
                </div>
            </div>
            <canvas id='chart{i}' width='400' height='170'></canvas>
            <div style='margin-top:10px;background:#f8f9fa;padding:8px;border-radius:6px;'>
                <div style='font-size:12px;font-weight:bold;color:#2c3e50;margin-bottom:5px;'>📈 재무 추세</div>
                <div style='display:flex;gap:8px;flex-wrap:wrap;align-items:center;'>
                    <span style='font-size:12px;'>매출: {trend_badge(ft.get("revenue_trend","?"))}</span>
                    <span style='font-size:12px;'>영업익: {trend_badge(ft.get("op_trend","?"))}</span>
                    <span style='font-size:12px;'>순익: {trend_badge(ft.get("ni_trend","?"))}</span>
                    <span style='font-size:12px;color:#7f8c8d;'>부채: {"%.0f%%" % ft["debt_ratio"] if ft.get("debt_ratio") else "N/A"}</span>
                </div>
                {"<div style='margin-top:4px;font-size:11px;color:#e74c3c;'>⚠️ " + trap.get('reason','') + "</div>" if trap.get('reason') and trap.get('level') in ['danger','caution'] else ""}
                {"<div style='margin-top:4px;font-size:11px;color:#27ae60;'>✅ " + trap.get('reason','') + "</div>" if trap.get('level') == 'opportunity' else ""}
            </div>
            {def_bar(ds)}
            <div style='display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:13px;'>
                <div><strong>PER:</strong> {per_s}</div><div><strong>PBR:</strong> {pbr_s}</div>
                <div><strong>ROE:</strong> {roe_s}</div><div><strong>BPS:</strong> {bps_s}</div>
            </div>
            <div style='display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px;'>
                <div style='background:#ecf0f1;padding:7px;border-radius:5px;text-align:center;'>
                    <div style='font-size:10px;color:#7f8c8d;'>RSI</div>
                    <div style='font-size:14px;font-weight:bold;color:#e74c3c;'>{s['rsi']:.1f}</div>
                </div>
                <div style='background:#ecf0f1;padding:7px;border-radius:5px;text-align:center;'>
                    <div style='font-size:10px;color:#7f8c8d;'>이격도</div>
                    <div style='font-size:14px;font-weight:bold;color:#e67e22;'>{s['disparity']:.1f}%</div>
                </div>
                <div style='background:#ecf0f1;padding:7px;border-radius:5px;text-align:center;'>
                    <div style='font-size:10px;color:#7f8c8d;'>거래량</div>
                    <div style='font-size:14px;font-weight:bold;color:#27ae60;'>{s['volume_ratio']:.2f}배</div>
                </div>
                <div style='background:#ecf0f1;padding:7px;border-radius:5px;text-align:center;'>
                    <div style='font-size:10px;color:#7f8c8d;'>모멘텀</div>
                    <div style='font-size:14px;font-weight:bold;color:#9b59b6;'>{s.get("momentum_score",0)}점</div>
                </div>
            </div>
            {score_bar(sb)}
            {avg_warn_box}
        </div>
        <script>
        (function(){{var ctx=document.getElementById('chart{i}').getContext('2d');
        var data={cj};if(!data.length)return;
        var prices=data.map(d=>d.close);
        var mn=Math.min(...prices),mx=Math.max(...prices),rng=mx-mn,pad=rng*0.1;
        var w=ctx.canvas.width,h=ctx.canvas.height;
        ctx.strokeStyle='#3498db';ctx.lineWidth=2;ctx.beginPath();
        prices.forEach((p,i)=>{{var x=(i/(prices.length-1))*w;
        var y=h-((p-mn+pad)/(rng+2*pad))*h;
        if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});ctx.stroke();}})();
        </script>"""

    # ── TOP 7-30 테이블 ───────────────────────────────
    tbl_rows = ""
    for i, s in enumerate(top_stocks[6:30], 7):
        ft   = s.get('financial_trend') or {}
        trap = s.get('trap_info') or {}
        tl   = trap.get('level','neutral')
        rl   = s.get('risk_level','보통')
        rc2  = {'안정':'#27ae60','보통':'#7f8c8d','고위험':'#e74c3c'}.get(rl,'#7f8c8d')
        tc   = {'danger':'#e74c3c','caution':'#f39c12','opportunity':'#27ae60'}.get(tl,'#7f8c8d')
        sec  = s.get('sector','기타')
        sc   = '#f39c12' if sec in top3 else '#95a5a6'
        rs20 = s.get('rs_20d',0)
        rsc  = '#27ae60' if rs20 >= 0 else '#e74c3c'
        aw   = '⛔' if s.get('averaging_warning') else ''

        tr_bg   = " style='background:#fff5f5;'" if s.get('averaging_warning') else ""
        news_url= f"https://search.naver.com/search.naver?where=news&query={s['name']}"
        ft_str  = f"{ft.get('revenue_trend','?')} {ft.get('op_trend','?')} {ft.get('ni_trend','?')}"
        trap_lb = trap.get('label','—') if tl in ['danger','caution','opportunity'] else '—'
        roe_str2= f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A'
        td = lambda v: f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;'>{v}</td>"
        tdc= lambda v,c,fw='normal': (f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;"
                                      f"text-align:center;color:{c};font-weight:{fw};'>{v}</td>")
        tdr= lambda v: f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;text-align:right;'>{v}</td>"
        tbl_rows += (
            f"<tr{tr_bg}>"
            + td(i)
            + f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;font-weight:bold;'>"
              f"{s['name']} {aw} <a href='{news_url}' target='_blank' style='text-decoration:none;'>📰</a></td>"
            + td(s['code'])
            + f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;text-align:center;'>"
              f"<span style='background:{sc};color:white;padding:2px 5px;border-radius:3px;font-size:11px;'>{sec}</span></td>"
            + tdr(f"{s['price']:,.0f}원")
            + tdc(f"{s['score']}점", '#e74c3c', 'bold')
            + tdc(rl, rc2, 'bold')
            + tdc(entry_map.get(s.get('entry_signal','관찰'),'🟡'), '#2c3e50')
            + tdc(f"{rs20:+.1f}%p", rsc, 'bold')
            + tdc(ft_str, '#2c3e50')
            + f"<td style='padding:9px 8px;border-bottom:1px solid #ecf0f1;text-align:center;"
              f"font-size:11px;color:{tc};font-weight:bold;'>{trap_lb}</td>"
            + tdc(f"{s['rsi']:.1f}", '#2c3e50')
            + tdc(f"{s['disparity']:.1f}%", '#2c3e50')
            + tdc(f"{s.get('return_1m',0):+.1f}%", '#2c3e50')
            + tdc(safe_format(s.get('pbr'),'.2f'), '#2c3e50')
            + tdc(roe_str2, '#2c3e50')
            + "</tr>"
        )

    # ── 투자자 유형별 추천 ────────────────────────────
    safe = [s for s in top_stocks[:30]
            if s.get('trap_info',{}).get('level') != 'danger'
            and not s.get('averaging_warning')]

    def fill_up(base_list, source, n=5):
        result = list(base_list)
        for s in source:
            if len(result) >= n: break
            if s not in result: result.append(s)
        return result[:n]

    agg = sorted([s for s in safe if s.get('disparity',100) < 90 and s.get('rsi',100) < 30
                  and s.get('entry_signal') == '확인' and s.get('roe') is not None and s['roe'] >= 3.0], key=lambda x: -x['score'])
    agg = fill_up(agg, sorted([s for s in safe if s.get('disparity',100) < 93 and s.get('rsi',100) < 35
                                and s.get('roe') is not None], key=lambda x: -x['score']))
    agg = fill_up(agg, sorted([s for s in safe if s.get('roe') is not None], key=lambda x: -x['score']))

    bal = sorted([s for s in safe if s.get('risk_score',0) < 70
                  and s.get('market_cap') and s['market_cap'] >= 300_000_000_000
                  and s.get('roe') is not None], key=lambda x: -x['score'])
    bal = fill_up(bal, sorted([s for s in safe if s.get('risk_score',0) < 70
                                and s.get('roe') is not None], key=lambda x: -x['score']))
    bal = fill_up(bal, sorted([s for s in safe if s.get('risk_score',0) < 70], key=lambda x: -x['score']))

    con = sorted([s for s in safe if s.get('risk_level') == '안정'
                  and s.get('pbr') and s['pbr'] < 1.0
                  and s.get('roe') is not None and s['roe'] > 5.0
                  and s.get('fin_trend_score',0) >= 0], key=lambda x: -x['score'])
    con = fill_up(con, sorted([s for s in safe if s.get('risk_level') == '안정'
                                and s.get('roe') is not None and s['roe'] > 3.0], key=lambda x: -x['score']))
    con = fill_up(con, sorted([s for s in safe if s.get('risk_level') == '안정'
                                and s.get('roe') is not None], key=lambda x: -x['score']))

    rs_strong = sorted([s for s in safe if s.get('rs_20d',0) >= 5 and s.get('defensive_score',0) >= 5],
                       key=lambda x: -(x.get('rs_20d',0) + x.get('defensive_score',0)))[:5]
    rs_strong = fill_up(rs_strong, sorted([s for s in safe if s.get('rs_20d',0) >= 0],
                                          key=lambda x: -x.get('rs_20d',0)))

    mom = sorted([s for s in safe if s.get('momentum_score',0) >= 10],
                 key=lambda x: (-x.get('momentum_score',0), -x['score']))[:5]

    gv = sorted([s for s in top_stocks[:30] if s.get('trap_info',{}).get('level') == 'opportunity'],
                key=lambda x: -x['score'])[:5]

    def investor_card(title, desc, stocks, icon, color):
        items = ""
        for idx, s in enumerate(stocks, 1):
            ft2  = s.get('financial_trend') or {}
            trap2= s.get('trap_info') or {}
            tb   = trap_badge(trap2)
            rs20_2= s.get('rs_20d',0)
            rsc2 = '#27ae60' if rs20_2 >= 0 else '#e74c3c'
            items += (f"<div style='padding:9px;background:#f8f9fa;margin:6px 0;border-radius:5px;'>"
                      f"<strong>{idx}. {s['name']}</strong> ({s['code']}) "
                      f"{entry_map.get(s.get('entry_signal','관찰'),'🟡')}"
                      f"<span style='background:#95a5a6;color:white;padding:1px 5px;border-radius:3px;font-size:11px;margin-left:3px;'>{s.get('sector','기타')}</span>"
                      f"{tb}<br>"
                      f"<span style='font-size:12px;color:#555;'>점수: {s['score']}점 | "
                      f"PBR: {safe_format(s.get('pbr'),'.2f')} | "
                      f"ROE: {'%.1f%%' % s['roe'] if s.get('roe') is not None else 'N/A'} | "
                      f"<span style='color:{rsc2};'>RS: {rs20_2:+.1f}%p</span></span><br>"
                      f"<span style='font-size:11px;color:#7f8c8d;'>"
                      f"재무: 매출{ft2.get('revenue_trend','?')} 영익{ft2.get('op_trend','?')} | "
                      f"방어력: {s.get('defensive_score',0)}점</span>"
                      f"</div>")
        if not items: items = "<div style='color:#aaa;padding:8px;'>해당 조건 종목 없음</div>"
        return (f"<div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);border-left:5px solid {color};'>"
                f"<h3 style='margin:0 0 6px 0;color:{color};'>{icon} {title}</h3>"
                f"<p style='color:#555;margin:0 0 10px 0;font-size:12px;'>{desc}</p>{items}</div>")

    investor_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>👥 투자자 유형별 추천</h2>
    <p style='color:#7f8c8d;font-size:13px;margin:-10px 0 15px;'>⛔ 물타기 경고 및 밸류트랩 위험 종목은 모든 유형에서 자동 제외됩니다.</p>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;margin-bottom:30px;'>
        {investor_card("공격적 투자자","이격도 90↓ + RSI 30↓ + 진입확인 + 물타기 제외",agg,"🚀","#e74c3c")}
        {investor_card("균형잡힌 투자자","시총 3,000억↑ + ROE 확인 + 물타기 제외",bal,"⚖️","#3498db")}
        {investor_card("보수적 투자자","안정 + PBR 1.0↓ + ROE 5%↑ + 재무 개선 + 물타기 제외",con,"🛡️","#27ae60")}
        {investor_card("RS 강세 추종","KOSPI 대비 RS +5%p 이상 + 방어력 5점 이상",rs_strong,"📡","#1abc9c")}
        {investor_card("모멘텀 투자자","신고가 근접 + 1개월 수익률 우수 + 물타기 제외",mom,"🔥","#9b59b6")}
        {investor_card("퀀트 가치주","저PBR + 실적 개선 동시 충족 = 진짜 저평가",gv,"💎","#f39c12")}
    </div>"""

    # ── 지표별 TOP5 ───────────────────────────────────
    def make_list(stocks, fn):
        if not stocks: return "<p style='color:#aaa;padding:8px;'>해당 없음</p>"
        return ("<ul style='margin:8px 0;padding-left:18px;line-height:1.9;'>"
                + "".join(f"<li><strong>{s['name']}</strong> ({s['code']}) — {fn(s)}</li>" for s in stocks)
                + "</ul>")

    rsi_top5  = sorted(top_stocks, key=lambda x: x['rsi'])[:5]
    disp_top5 = sorted(top_stocks, key=lambda x: x['disparity'])[:5]
    vol_top5  = sorted(top_stocks, key=lambda x: -x['volume_ratio'])[:5]
    reb_top5  = sorted(top_stocks, key=lambda x: -x.get('rebound_strength',0))[:5]
    pbr_top5  = sorted([s for s in top_stocks if s.get('pbr')], key=lambda x: x['pbr'])[:5]
    mom_top5  = sorted([s for s in top_stocks if s.get('return_1m') is not None],
                       key=lambda x: -x.get('momentum_score',0))[:5]
    fin_top5  = sorted([s for s in top_stocks if s.get('fin_trend_score',0) > 0],
                       key=lambda x: -x.get('fin_trend_score',0))[:5]
    def_top5  = sorted(top_stocks, key=lambda x: -x.get('defensive_score',0))[:5]

    indicator_section = f"""
    <h2 style='color:#2c3e50;margin:40px 0 20px;'>📈 지표별 TOP 5</h2>
    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px;margin-bottom:30px;'>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e74c3c;margin:0 0 8px;'>📉 RSI 과매도</h3>{make_list(rsi_top5, lambda s: f"RSI {s['rsi']:.1f}")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#e67e22;margin:0 0 8px;'>📊 이격도 하락</h3>{make_list(disp_top5, lambda s: f"이격도 {s['disparity']:.1f}%")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#27ae60;margin:0 0 8px;'>📦 거래량 급증</h3>{make_list(vol_top5, lambda s: f"거래량 {s['volume_ratio']:.2f}배")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#9b59b6;margin:0 0 8px;'>🎯 반등 강도</h3>{make_list(reb_top5, lambda s: f"반등 {s.get('rebound_strength',0):.1f}%")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#3498db;margin:0 0 8px;'>💎 저PBR 가치주</h3>{make_list(pbr_top5, lambda s: f"PBR {s['pbr']:.2f}")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#f39c12;margin:0 0 8px;'>🔥 모멘텀 강도</h3>{make_list(mom_top5, lambda s: f"1M: {s.get('return_1m',0):+.1f}% / 고점 {s.get('proximity_to_high',0):.0f}%")}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#1abc9c;margin:0 0 8px;'>📋 재무 개선주</h3>{make_list(fin_top5, format_fin_trend)}</div>
        <div style='background:white;padding:18px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'><h3 style='color:#27ae60;margin:0 0 8px;'>🛡️ 하락 방어력</h3>{make_list(def_top5, lambda s: f"방어력 {s.get('defensive_score',0)}점 / RS {s.get('rs_20d',0):+.1f}%p")}</div>
    </div>"""

    # ── 시장 데이터 ───────────────────────────────────
    usd_d   = f"{market_data['usd']:,.2f}"    if market_data.get('usd')    else "N/A"
    eur_d   = f"{market_data['eur']:,.2f}"    if market_data.get('eur')    else "N/A"
    jpy_d   = f"{market_data['jpy']:,.2f}"    if market_data.get('jpy')    else "N/A"
    kp_d    = f"{market_data['kospi']:,.2f}"  if market_data.get('kospi')  else "N/A"
    kq_d    = f"{market_data['kosdaq']:,.2f}" if market_data.get('kosdaq') else "N/A"
    kp_cc   = '#27ae60' if market_data.get('kospi_change',0) >= 0 else '#e74c3c'
    kq_cc   = '#27ae60' if market_data.get('kosdaq_change',0) >= 0 else '#e74c3c'
    kp_ct   = f"{market_data.get('kospi_change',0):+.2f}%"
    kq_ct   = f"{market_data.get('kosdaq_change',0):+.2f}%"

    footer = """
    <div style='background:#f8f9fa;padding:22px;border-radius:10px;margin-top:30px;border-left:4px solid #3498db;'>
        <h3 style='color:#2c3e50;margin-top:0;'>📘 주요 지표 설명</h3>
        <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;'>
            <div><h4 style='color:#e74c3c;'>📊 RSI</h4><p style='color:#555;line-height:1.6;margin:0;'>30 이하: 과매도 / 70 이상: 과매수</p></div>
            <div><h4 style='color:#e67e22;'>📈 이격도</h4><p style='color:#555;line-height:1.6;margin:0;'>95% 이하: 저평가 / 105% 이상: 과열</p></div>
            <div><h4 style='color:#27ae60;'>📦 거래량</h4><p style='color:#555;line-height:1.6;margin:0;'>1.5배↑: 거래 활성화 / 0.7배↑+연속↑: 🟢 진입신호</p></div>
            <div><h4 style='color:#9b59b6;'>💰 PBR</h4><p style='color:#555;line-height:1.6;margin:0;'>1.0 이하: 저평가 / 3.0 이상: 고평가</p></div>
            <div><h4 style='color:#1abc9c;'>📡 RS Score</h4><p style='color:#555;line-height:1.6;margin:0;'>종목수익률 - KOSPI수익률 (20일·50일) / 양수: 시장보다 강함</p></div>
            <div><h4 style='color:#27ae60;'>🛡️ 하락 방어력</h4><p style='color:#555;line-height:1.6;margin:0;'>KOSPI 스트레스일(-1%↓)에 종목이 얼마나 덜 빠졌는지</p></div>
            <div><h4 style='color:#1abc9c;'>📋 재무 추세</h4><p style='color:#555;line-height:1.6;margin:0;'>▲: 전분기 대비 5%↑ / ▼: 5%↓ / →: 보합</p></div>
            <div><h4 style='color:#e74c3c;'>⛔ 밸류트랩</h4><p style='color:#555;line-height:1.6;margin:0;'>저PBR + 실적 동반 하락 → 함정주 자동 감점</p></div>
            <div><h4 style='color:#e74c3c;'>⛔ 물타기 경고</h4><p style='color:#555;line-height:1.6;margin:0;'>RS -5%p 이하 + 재무 하락 → 추가매수 강경고 + 추천 제외</p></div>
            <div><h4 style='color:#f39c12;'>🔄 섹터보너스</h4><p style='color:#555;line-height:1.6;margin:0;'>주도 섹터 Top3 소속 종목 +5점</p></div>
        </div>
        <div style='margin-top:14px;padding:13px;background:#e8f5e9;border-radius:8px;border-left:4px solid #27ae60;'>
            <h4 style='color:#1b5e20;margin-top:0;'>⚖️ v1.2 점수 공식</h4>
            <ul style='color:#555;line-height:1.9;margin:0;padding-left:18px;'>
                <li><strong>🚀 상승장</strong>: (반등×0.6 + 모멘텀×1.5) + 재무추세 + RS점수 + 방어력 + 섹터보너스 - 트랩패널티</li>
                <li><strong>⚖️ 횡보장</strong>: (반등×0.8 + 모멘텀×0.8) + 재무추세 + RS점수 + 방어력 + 섹터보너스 - 트랩패널티</li>
                <li><strong>⚠️ 하락장</strong>: (반등×1.0 + 모멘텀×0.3) + 재무추세 + RS점수 + 방어력 + 섹터보너스 - 트랩패널티</li>
            </ul>
        </div>
        <div style='margin-top:12px;padding:16px;background:#fff3cd;border-radius:8px;border-left:4px solid #ffc107;'>
            <h4 style='color:#856404;margin-top:0;'>💡 투자 유의사항</h4>
            <ul style='color:#856404;line-height:1.8;margin:0;padding-left:18px;'>
                <li>본 분석은 기술적·재무적·상대강도 지표 기반 참고 자료이며, 투자 판단은 본인 책임입니다.</li>
                <li>재무 추세는 yfinance 분기 데이터 기준 — 발표 시점 시차가 있을 수 있습니다.</li>
                <li>물타기 경고는 자동화 신호이며, 개별 기업 공시 및 원문 재무제표 확인을 권장합니다.</li>
                <li>RS Score는 과거 수익률 기반 — 미래 성과를 보장하지 않습니다.</li>
                <li>분산 투자로 리스크를 관리하고, 한 종목에 과도한 비중을 두지 마세요.</li>
            </ul>
        </div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang='ko'>
<head>
    <meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>
    <meta http-equiv='Cache-Control' content='no-cache, no-store, must-revalidate'>
    <title>다이나믹 트레이딩 v1.2.1 — {timestamp}</title>
    <style>
        body{{font-family:'Segoe UI',sans-serif;margin:0;padding:20px;
              background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;}}
        .container{{max-width:1440px;margin:0 auto;background:#f8f9fa;padding:28px;
                    border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,0.3);}}
        h1{{color:#2c3e50;text-align:center;font-size:28px;margin-bottom:4px;}}
        .timestamp{{text-align:center;color:#7f8c8d;margin-bottom:24px;font-size:13px;}}
        .market-overview{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
                          gap:12px;margin-bottom:24px;}}
        .market-card{{background:white;padding:18px;border-radius:10px;
                       box-shadow:0 2px 8px rgba(0,0,0,0.1);text-align:center;}}
        .ai-analysis{{background:white;padding:22px;border-radius:10px;
                       box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:24px;
                       border-left:5px solid #3498db;}}
        .top-stocks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));
                     gap:18px;margin-bottom:28px;}}
        table{{width:100%;background:white;border-radius:10px;overflow:hidden;
                box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:28px;border-collapse:collapse;}}
        th{{background:#34495e;color:white;padding:9px 7px;text-align:left;font-size:11px;}}
    </style>
</head>
<body>
<div class='container'>
    <h1>📊 다이나믹 트레이딩 종목 추천 v1.2.1</h1>
    <div class='timestamp'>생성 시간: {timestamp}</div>
    {regime_banner}
    <div class='market-overview'>
        <div class='market-card'><h3 style='margin:0;color:#e74c3c;'>KOSPI</h3>
            <div style='font-size:21px;font-weight:bold;margin:8px 0;'>{kp_d}</div>
            <div style='color:{kp_cc};'>{kp_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#3498db;'>KOSDAQ</h3>
            <div style='font-size:21px;font-weight:bold;margin:8px 0;'>{kq_d}</div>
            <div style='color:{kq_cc};'>{kq_ct}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>USD/KRW</h3>
            <div style='font-size:21px;font-weight:bold;margin:8px 0;'>{usd_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>EUR/KRW</h3>
            <div style='font-size:21px;font-weight:bold;margin:8px 0;'>{eur_d}</div></div>
        <div class='market-card'><h3 style='margin:0;color:#95a5a6;'>JPY/KRW</h3>
            <div style='font-size:21px;font-weight:bold;margin:8px 0;'>{jpy_d}</div></div>
    </div>
    <div class='ai-analysis'>
        <h2 style='margin:0 0 12px 0;color:#2c3e50;'>🤖 AI 종합 분석</h2>{ai_analysis}
    </div>
    <h2 style='color:#2c3e50;margin:28px 0 18px;'>🏆 추천 종목 TOP 30</h2>
    <div class='top-stocks'>{top6_cards}</div>
    <table>
        <thead><tr>
            <th>순위</th><th>종목명</th><th>코드</th><th>섹터</th><th>현재가</th>
            <th>점수</th><th>위험도</th><th>진입</th>
            <th>RS20d</th><th>재무추세</th><th>밸류트랩</th>
            <th>RSI</th><th>이격도</th><th>1M수익</th><th>PBR</th><th>ROE</th>
        </tr></thead>
        <tbody>{tbl_rows}</tbody>
    </table>
    {rs_section}
    {sector_section}
    {investor_section}
    {indicator_section}
    {footer}
    <div style='text-align:center;margin-top:24px;padding:16px;color:#7f8c8d;font-size:12px;'>
        <p>다이나믹 트레이딩 v1.2.1 — 시장 국면 · 모멘텀 · 섹터 로테이션 · 재무 추세 · Value Trap · 상대강도 · 물타기 경고</p>
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
    logging.info("=== 다이나믹 트레이딩 분석 시작 (v1.2.1) ===")

    cache          = CacheManager()
    exchange_rates = get_exchange_rates_only(cache)
    market_data    = get_market_data(exchange_rates)

    dart_key = os.environ.get('DART_API')
    if not dart_key: logging.warning("⚠️ DART_API 없음 → yfinance fallback")
    mapper        = DARTCorpCodeMapper(dart_key, cache) if dart_key else None
    corp_map      = mapper.get_all_mappings() if mapper else {}

    krx = KRXData(cache); krx.load_all_shares()

    logging.info("📊 시장 국면 감지 중...")
    regime_info   = detect_market_regime()
    market_regime = regime_info['regime']
    logging.info(f"→ 국면: {market_regime} / {regime_info.get('strategy_hint','')}")

    logging.info("📈 섹터 로테이션 분석 중...")
    sector_data = get_sector_momentum()
    top_sectors = sector_data.get('top_sectors', [])
    logging.info(f"→ 주도 섹터: {top_sectors}")

    logging.info("📡 KOSPI 기준 데이터 수집 중 (RS Score용)...")
    kospi_ref = get_kospi_reference_data()

    stock_list = load_stock_list()
    if not stock_list: logging.error("종목 리스트 로드 실패"); return

    logging.info(f"분석 시작: {len(stock_list)}개 종목")
    args_list = [(name, code, dart_key, corp_map, market_regime, top_sectors, kospi_ref)
                 for name, code in stock_list]

    with Pool(processes=4) as pool:
        results = pool.map(analyze_stock_worker, args_list)

    valid = [r for r in results if r and r['score'] >= 40]
    valid.sort(key=lambda x: (-x['score'], -x['trading_value']))
    top_stocks = valid[:30]
    logging.info(f"v1.2.1 완료: {len(valid)}개 추출")

    danger_n  = sum(1 for r in valid if r.get('trap_info',{}).get('level') == 'danger')
    oppty_n   = sum(1 for r in valid if r.get('trap_info',{}).get('level') == 'opportunity')
    warn_n    = sum(1 for r in valid if r.get('averaging_warning'))
    rs_pos_n  = sum(1 for r in valid if r.get('rs_20d',0) > 0)
    logging.info(f"밸류트랩 ⛔{danger_n} ✅{oppty_n} | 물타기경고 {warn_n}건 | RS양수 {rs_pos_n}/{len(valid)}")

    ai_analysis  = get_gemini_analysis(top_stocks, market_regime)
    timestamp    = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S')
    html_content = generate_html(top_stocks, market_data, ai_analysis, timestamp, regime_info, sector_data)

    filename = f"stock_result_{datetime.now(kst).strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)

    elapsed = (datetime.now(kst) - start_time).total_seconds()
    logging.info(f"=== 완료: {filename} ({elapsed:.1f}초) ===")

    print(f"\n✅ {filename}")
    print(f"   시장 국면: {regime_info['emoji']} {market_regime}")
    print(f"   주도 섹터: {', '.join(top_sectors) if top_sectors else '없음'}")
    print(f"   KOSPI RS 기준: 20d {kospi_ref.get('return_20d',0):+.1f}% / "
          f"스트레스일 {len(kospi_ref.get('stress_dates',set()))}일")
    print(f"   밸류트랩 ⛔{danger_n} ✅{oppty_n} | 물타기경고 {warn_n}건")
    print()

    em = {'확인':'🟢','관찰':'🟡','대기':'🔴'}
    rm = {'안정':'✅','보통':'⚠️','고위험':'🚨'}
    for i, s in enumerate(top_stocks[:10], 1):
        ft   = s.get('financial_trend') or {}
        trap = s.get('trap_info') or {}
        aw   = ' ⛔물타기' if s.get('averaging_warning') else ''
        rs20 = s.get('rs_20d', 0)
        print(f"  {i:2}. {s['name']:<12} ({s['code']}) {s['score']}점 "
              f"{rm.get(s.get('risk_level','보통'),'⚠️')} "
              f"{em.get(s.get('entry_signal','관찰'),'🟡')} "
              f"[{s.get('sector','기타')}] "
              f"RS:{rs20:+.1f}%p 방어:{s.get('defensive_score',0)}점{aw}")
        pbr_s = f"{s['pbr']:.2f}" if s.get('pbr') else 'N/A'
        roe_s = f"{s['roe']:.1f}%" if s.get('roe') is not None else 'N/A'
        tl    = trap.get('label','')
        print(f"       PBR:{pbr_s} ROE:{roe_s} | "
              f"재무:{s.get('fin_trend_score',0):+d}점 트랩:{s.get('trap_penalty',0)} | "
              f"매출{ft.get('revenue_trend','?')} 영익{ft.get('op_trend','?')} "
              f"{tl}")


if __name__ == "__main__":
    main()
