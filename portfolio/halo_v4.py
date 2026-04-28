#!/usr/bin/env python3
"""
HALO v4.0 - Quantitative Portfolio System
GitHub Actions standalone version

ETF Universe:
  379800.KS  KODEX 미국S&P500
  251350.KS  KODEX MSCI선진국
  195980.KS  PLUS 신흥국MSCI(합성 H)
  439870.KS  KODEX 국고채30년액티브
  214980.KS  KODEX 단기채권PLUS
  411060.KS  ACE KRX금현물
"""

import os
import json
import logging
import smtplib
import sys
import numpy as np
import pandas as pd
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("HALO")

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

HOLDINGS_PATH = os.path.join(DATA_DIR, "holdings.csv")
STATE_PATH    = os.path.join(DATA_DIR, "state.json")
HISTORY_PATH  = os.path.join(DATA_DIR, "history.csv")

# ─────────────────────────────────────────────
# SETTINGS (GitHub Secrets → 환경 변수)
# ─────────────────────────────────────────────
BASE_MONTHLY  = int(os.environ.get("MONTHLY_BASE_CASH",  "2000000"))
EXTRA_MONTHLY = int(os.environ.get("MONTHLY_EXTRA_CASH", "2000000"))

EMAIL_CFG = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "sender":    os.environ.get("EMAIL_SENDER",      ""),
    "app_pw":    os.environ.get("GMAIL_APP_PASSWORD", ""),
    "recipient": os.environ.get("EMAIL_RECIPIENT",   ""),
}

# ─────────────────────────────────────────────
# PORTFOLIO PARAMETERS
# ─────────────────────────────────────────────
REBALANCE_THRESHOLD = 0.07   # 드리프트 7% 초과 시만 매도 리밸런싱
CAP_CASH  = 0.50
CAP_GOLD  = 0.20
MIN_WEIGHT = {"BOND": 0.05, "CASH": 0.03, "GOLD": 0.02}

VIX_WARN    = 25.0
VIX_HIGH    = 32.0
VIX_EXTREME = 45.0

# ─────────────────────────────────────────────
# ETF UNIVERSE
# ─────────────────────────────────────────────
ETF = {
    "US"  : "379800.KS",
    "DM"  : "251350.KS",
    "EM"  : "195980.KS",
    "BOND": "439870.KS",
    "CASH": "214980.KS",
    "GOLD": "411060.KS",
}
TICKERS = list(ETF.values())

NAME_MAP = {v: k for k, v in ETF.items()}
FULL_NAME = {
    "379800.KS": "KODEX 미국S&P500",
    "251350.KS": "KODEX MSCI선진국",
    "195980.KS": "PLUS 신흥국MSCI(H)",
    "439870.KS": "KODEX 국고채30년",
    "214980.KS": "KODEX 단기채권PLUS",
    "411060.KS": "ACE KRX금현물",
}
ASSET_TYPE = {
    ETF["US"]:   "EQUITY",
    ETF["DM"]:   "EQUITY",
    ETF["EM"]:   "EQUITY",
    ETF["BOND"]: "BOND",
    ETF["CASH"]: "CASH",
    ETF["GOLD"]: "GOLD",
}

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"remaining_extra": 12, "last_run": None, "peak_nav": 0.0}

def save_state(s):
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2)

# ─────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────
def _extract_close(raw, tickers):
    if isinstance(raw.columns, pd.MultiIndex):
        lvl   = raw.columns.get_level_values(0)
        field = "Adj Close" if "Adj Close" in lvl else "Close"
        px    = raw[field].copy()
    else:
        field = "Adj Close" if "Adj Close" in raw.columns else "Close"
        px    = raw[[field]].copy()
        if len(tickers) == 1:
            px.columns = tickers

    for t in tickers:
        if t not in px.columns:
            px[t] = np.nan
    return px[tickers].sort_index().ffill()


def get_prices():
    raw = yf.download(TICKERS, period="2y", progress=False, auto_adjust=False)
    if raw.empty:
        raise ValueError("ETF 가격 데이터 로드 실패")
    px = _extract_close(raw, TICKERS)
    if px.dropna(how="all").empty:
        raise ValueError("유효한 가격 데이터 없음")
    log.info(f"ETF 가격 로드: {len(px)}일, 최종일={px.index[-1].date()}")
    return px


def get_market_indicators():
    """VIX + SPY drawdown 동시 취득 (API 호출 1회)"""
    vix, spy_dd = np.nan, 0.0
    try:
        raw   = yf.download(["^VIX", "SPY"], period="2y", progress=False, auto_adjust=False)
        px    = _extract_close(raw, ["^VIX", "SPY"])

        vix_s = px["^VIX"].dropna()
        if len(vix_s) > 0:
            vix = float(vix_s.iloc[-1])

        spy_s = px["SPY"].dropna()
        if len(spy_s) >= 20:
            lookback = min(252, len(spy_s))
            peak     = float(spy_s.rolling(lookback).max().iloc[-1])
            last     = float(spy_s.iloc[-1])
            if peak > 0:
                spy_dd = float(last / peak - 1)
    except Exception as e:
        log.warning(f"시장 지표 취득 실패: {e}")
    return vix, spy_dd

# ─────────────────────────────────────────────
# HOLDINGS
# ─────────────────────────────────────────────
def load_holdings():
    if os.path.exists(HOLDINGS_PATH):
        h = pd.read_csv(HOLDINGS_PATH)
        if list(h.columns[:2]) != ["ticker", "shares"]:
            h.columns = ["ticker", "shares"] + list(h.columns[2:])
    else:
        h = pd.DataFrame({"ticker": [], "shares": []})

    base = pd.DataFrame({"ticker": TICKERS})
    h    = base.merge(h[["ticker", "shares"]], on="ticker", how="left")
    h["shares"] = pd.to_numeric(h["shares"], errors="coerce").fillna(0).astype(int)
    return h

# ─────────────────────────────────────────────
# WEIGHT CALCULATION (듀얼 모멘텀 + 변동성 조정)
# ─────────────────────────────────────────────
def _normalize(w):
    w     = w.fillna(0).clip(lower=0)
    total = w.sum()
    return w / total if total > 1e-9 else pd.Series(1 / len(w), index=w.index)


def calc_weights(px):
    if len(px) < 252:
        log.warning("데이터 부족 (<252일) → 균등 비중 사용")
        return pd.Series(1 / len(TICKERS), index=TICKERS)

    mom_12m   = px.iloc[-1] / px.iloc[-252] - 1
    mom_3m    = px.iloc[-1] / px.iloc[-63]  - 1
    vol_63    = px.pct_change().rolling(63).std().iloc[-1]
    mom_combo = 0.7 * mom_12m + 0.3 * mom_3m
    score     = (mom_combo / vol_63).replace([np.inf, -np.inf], np.nan).fillna(0)

    # 절대 모멘텀 음수 자산 제외
    score[mom_12m < 0] = 0.0
    score = score.reindex(TICKERS).fillna(0)

    # 자산 유형별 최소 비중 보장
    total = score.sum()
    if total > 1e-9:
        for key, min_w in MIN_WEIGHT.items():
            t         = ETF[key]
            score[t]  = max(score[t], min_w / (1 - min_w) * total)

    return _normalize(score)

# ─────────────────────────────────────────────
# 통합 보호 오버레이 (SPY DD + VIX + 포트폴리오 DD)
# ─────────────────────────────────────────────
def _calc_eq_target(spy_dd, vix, portfolio_dd):
    # SPY 낙폭 기반 (후행)
    if   spy_dd <= -0.30: eq_spy = 0.20
    elif spy_dd <= -0.20: eq_spy = 0.35
    elif spy_dd <= -0.10: eq_spy = 0.50
    else:                 eq_spy = 0.70

    # VIX 기반 (선행)
    if   np.isnan(vix):      eq_vix = 0.70
    elif vix >= VIX_EXTREME: eq_vix = 0.20
    elif vix >= VIX_HIGH:    eq_vix = 0.40
    elif vix >= VIX_WARN:    eq_vix = 0.55
    else:                    eq_vix = 0.70

    # 포트폴리오 자체 DD (보완)
    if   portfolio_dd <= -0.15: eq_pdd = 0.35
    elif portfolio_dd <= -0.08: eq_pdd = 0.55
    else:                       eq_pdd = 0.70

    # 셋 중 최소 → 가장 방어적인 값 채택 (이중 감소 방지)
    return min(eq_spy, eq_vix, eq_pdd)


def apply_protection(w, spy_dd, vix, portfolio_dd):
    w         = _normalize(w)
    eq_target = _calc_eq_target(spy_dd, vix, portfolio_dd)
    eq_t      = [t for t in w.index if ASSET_TYPE[t] == "EQUITY"]
    safe_t    = [t for t in w.index if ASSET_TYPE[t] != "EQUITY"]
    w2        = w.copy()

    eq_sum   = w2[eq_t].sum()
    safe_sum = w2[safe_t].sum()

    if eq_sum > 1e-9:
        w2[eq_t]   = w2[eq_t]   / eq_sum   * eq_target
    else:
        w2[eq_t]   = eq_target / len(eq_t)

    if safe_sum > 1e-9:
        w2[safe_t] = w2[safe_t] / safe_sum * (1 - eq_target)
    else:
        w2[safe_t] = (1 - eq_target) / len(safe_t)

    return _normalize(w2)

# ─────────────────────────────────────────────
# CAPS (해석적 처리)
# ─────────────────────────────────────────────
def apply_caps(w):
    caps = {ETF["CASH"]: CAP_CASH, ETF["GOLD"]: CAP_GOLD}
    w    = _normalize(w)

    for _ in range(20):
        excess = sum(max(0.0, w[t] - c) for t, c in caps.items())
        if excess < 1e-9:
            break
        for t, cap in caps.items():
            w[t] = min(w[t], cap)
        free     = [t for t in w.index if t not in caps]
        free_sum = w[free].sum()
        if free_sum > 1e-9:
            w[free] += excess * w[free] / free_sum
        w = _normalize(w)

    return _normalize(w)

# ─────────────────────────────────────────────
# DRIFT (리밸런싱 필요 여부)
# ─────────────────────────────────────────────
def calc_max_drift(h, prices, w):
    h         = h.copy()
    h["price"] = h["ticker"].map(prices)
    h["value"] = h["shares"] * h["price"]
    nav        = h["value"].sum()
    if nav <= 0:
        return 1.0
    h["cur_w"]    = h["value"] / nav
    h["target_w"] = h["ticker"].map(w).fillna(0)
    return float((h["cur_w"] - h["target_w"]).abs().max())

# ─────────────────────────────────────────────
# ORDER GENERATION
# ─────────────────────────────────────────────
def generate_orders_buy_only(h, prices, w, cash):
    """매수 전용: shortfall 최대 자산 우선 배분"""
    df         = h.copy()
    df["price"] = df["ticker"].map(prices)
    df["value"] = df["shares"] * df["price"]
    nav         = float(df["value"].sum())

    df["target_w"] = df["ticker"].map(w).fillna(0)
    gap            = ((nav + cash) * df["target_w"] - df["value"]).clip(lower=0)
    df["gap"]      = gap if gap.sum() > 1e-9 else df["target_w"]

    alloc            = df["gap"] / df["gap"].sum()
    df["buy_shares"] = np.floor(alloc * cash / df["price"]).astype(int)
    remaining        = float(cash - (df["buy_shares"] * df["price"]).sum())

    # shortfall 기반 그리디 (비중 편차 최소화)
    while remaining >= df["price"].min():
        affordable = df[df["price"] <= remaining].copy()
        if affordable.empty:
            break
        total_v    = float(((df["shares"] + df["buy_shares"]) * df["price"]).sum()) + remaining
        cur_w      = (
            (df.loc[affordable.index, "shares"] + df.loc[affordable.index, "buy_shares"])
            * df.loc[affordable.index, "price"]
        ) / total_v
        shortfall  = df.loc[affordable.index, "target_w"] - cur_w
        best       = shortfall.idxmax()
        df.loc[best, "buy_shares"] += 1
        remaining  -= float(df.loc[best, "price"])

    orders       = df[df["buy_shares"] > 0][["ticker", "buy_shares", "price"]].copy()
    orders["side"] = "BUY"
    orders.rename(columns={"buy_shares": "shares"}, inplace=True)
    used = float((orders["shares"] * orders["price"]).sum()) if len(orders) > 0 else 0.0
    return orders[["ticker", "side", "shares", "price"]], nav, used


def generate_orders_full_rebalance(h, prices, w, cash):
    """전체 리밸런싱: SELL → BUY"""
    df                  = h.copy()
    df["price"]         = df["ticker"].map(prices)
    df["value"]         = df["shares"] * df["price"]
    nav                 = float(df["value"].sum())
    df["target_w"]      = df["ticker"].map(w).fillna(0)
    df["target_shares"] = np.floor(df["target_w"] * (nav + cash) / df["price"]).astype(int)
    df["delta"]         = df["target_shares"] - df["shares"]

    rows = []
    for _, r in df[df["delta"] < 0].iterrows():
        rows.append([r["ticker"], "SELL", int(-r["delta"]), float(r["price"])])
    for _, r in df[df["delta"] > 0].iterrows():
        rows.append([r["ticker"], "BUY",  int(r["delta"]),  float(r["price"])])

    if not rows:
        return pd.DataFrame(columns=["ticker", "side", "shares", "price"]), nav, 0.0

    orders   = pd.DataFrame(rows, columns=["ticker", "side", "shares", "price"])
    buy_cost = float((orders.loc[orders["side"] == "BUY",  "shares"] * orders.loc[orders["side"] == "BUY",  "price"]).sum())
    sell_val = float((orders.loc[orders["side"] == "SELL", "shares"] * orders.loc[orders["side"] == "SELL", "price"]).sum())
    return orders, nav, buy_cost - sell_val

# ─────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────
def append_history(row):
    df   = pd.DataFrame([row])
    mode = "a" if os.path.exists(HISTORY_PATH) else "w"
    df.to_csv(HISTORY_PATH, mode=mode, header=(mode == "w"), index=False)

# ─────────────────────────────────────────────
# HTML EMAIL REPORT
# ─────────────────────────────────────────────
def _color(val, warn, bad, reverse=False):
    if reverse:
        return "#e74c3c" if val >= bad else ("#f39c12" if val >= warn else "#27ae60")
    return "#e74c3c" if val <= bad else ("#f39c12" if val <= warn else "#27ae60")


def build_html_report(d):
    eq_target = _calc_eq_target(d["spy_dd"], d["vix"], d["portfolio_dd"])
    if   eq_target <= 0.25: badge = "🔴 극단적 방어 모드"
    elif eq_target <= 0.40: badge = "🟠 강한 방어 모드"
    elif eq_target <= 0.55: badge = "🟡 부분 방어 모드"
    else:                   badge = "🟢 정상 모드"

    vix_v = d["vix"] if not np.isnan(d["vix"]) else 0.0

    h_rows = ""
    for _, r in d["holdings"].iterrows():
        fname   = FULL_NAME.get(r["ticker"], r["ticker"])
        h_rows += f"""
        <tr>
          <td><b>{fname}</b></td>
          <td style='text-align:right'>{int(r['shares']):,}주</td>
          <td style='text-align:right'>{float(r['price']):,.0f}원</td>
          <td style='text-align:right'>{float(r['value']):,.0f}원</td>
          <td style='text-align:right'>{float(r.get('weight', 0)) * 100:.1f}%</td>
        </tr>"""

    w_rows = ""
    for t, wv in d["weights"].items():
        fname   = FULL_NAME.get(t, t)
        atype   = ASSET_TYPE.get(t, "")
        w_rows += f"<tr><td>{fname}</td><td>{atype}</td><td style='text-align:right'><b>{wv*100:.1f}%</b></td></tr>"

    o_rows = ""
    if d["orders"] is not None and len(d["orders"]) > 0:
        for _, r in d["orders"].iterrows():
            sc      = "#27ae60" if r["side"] == "BUY" else "#e74c3c"
            fname   = FULL_NAME.get(r["ticker"], r["ticker"])
            amt     = int(r["shares"]) * float(r["price"])
            o_rows += f"""
            <tr>
              <td>{fname}</td>
              <td style='color:{sc};font-weight:bold;text-align:center'>{r['side']}</td>
              <td style='text-align:right'>{int(r['shares']):,}주</td>
              <td style='text-align:right'>{float(r['price']):,.0f}원</td>
              <td style='text-align:right'>{amt:,.0f}원</td>
            </tr>"""
    else:
        o_rows = "<tr><td colspan='5' style='text-align:center;color:#888'>주문 없음</td></tr>"

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
  body{{font-family:'Malgun Gothic',Arial,sans-serif;max-width:700px;margin:auto;padding:20px;color:#333}}
  h1{{background:#1a252f;color:#fff;padding:16px 22px;border-radius:8px}}
  h2{{color:#1a252f;border-left:4px solid #3498db;padding-left:10px;margin-top:28px}}
  .badge{{text-align:center;font-size:17px;font-weight:bold;padding:12px;background:#ecf0f1;border-radius:8px;margin:10px 0}}
  .metrics{{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}}
  .metric{{flex:1;min-width:120px;background:#f8f9fa;border-radius:8px;padding:14px;text-align:center}}
  .metric .val{{font-size:22px;font-weight:bold}}
  .metric .lbl{{font-size:11px;color:#888;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
  th{{background:#2c3e50;color:#fff;padding:9px 12px;text-align:left}}
  td{{padding:8px 12px;border-bottom:1px solid #eee}}
  tr:hover td{{background:#f5f5f5}}
</style></head><body>

<h1>📊 HALO v4.0 월간 포트폴리오 리포트</h1>
<p style='color:#888;margin:4px 0 16px'>{d['run_date']} | {"매수 전용" if d['buy_only'] else "전체 리밸런싱"} 모드 | 잔여 추가납입 {d.get('remaining_extra', 0)}개월</p>

<h2>시장 환경</h2>
<div class='badge'>{badge} → 목표 주식 비중 {eq_target*100:.0f}%</div>
<div class='metrics'>
  <div class='metric'>
    <div class='val' style='color:{_color(vix_v, VIX_WARN, VIX_HIGH, reverse=True)}'>{vix_v:.1f}</div>
    <div class='lbl'>VIX 공포지수</div>
  </div>
  <div class='metric'>
    <div class='val' style='color:{_color(d["spy_dd"], -0.05, -0.15)}'>{d['spy_dd']*100:.1f}%</div>
    <div class='lbl'>SPY 낙폭 (252일)</div>
  </div>
  <div class='metric'>
    <div class='val' style='color:{_color(d["portfolio_dd"], -0.04, -0.10)}'>{d['portfolio_dd']*100:.1f}%</div>
    <div class='lbl'>포트폴리오 낙폭</div>
  </div>
</div>

<h2>포트폴리오 현황</h2>
<div class='metrics'>
  <div class='metric'>
    <div class='val'>{d['nav']:,.0f}</div><div class='lbl'>총 평가금액 (원)</div>
  </div>
  <div class='metric'>
    <div class='val'>{d['cash']:,.0f}</div><div class='lbl'>이달 투입 현금 (원)</div>
  </div>
  <div class='metric'>
    <div class='val'>{d['used_cash']:,.0f}</div><div class='lbl'>집행 금액 (원)</div>
  </div>
</div>

<h2>보유 현황</h2>
<table>
  <tr><th>종목명</th><th>수량</th><th>현재가</th><th>평가금액</th><th>현재비중</th></tr>
  {h_rows}
</table>

<h2>이번 달 목표 비중</h2>
<table>
  <tr><th>종목명</th><th>유형</th><th>목표비중</th></tr>
  {w_rows}
</table>

<h2>이번 달 주문 내역</h2>
<table>
  <tr><th>종목명</th><th>구분</th><th>수량</th><th>현재가</th><th>금액</th></tr>
  {o_rows}
</table>
<p style='font-size:12px;color:#555'>미집행 현금: <b>{d['cash'] - d['used_cash']:,.0f}원</b></p>

<hr style='border:none;border-top:1px solid #eee;margin-top:28px'>
<p style='font-size:11px;color:#aaa'>HALO v4.0 자동 생성 | 본 리포트는 참고용이며 투자 결과에 대한 책임은 본인에게 있습니다.</p>
</body></html>"""


def send_report(subject, html):
    cfg = EMAIL_CFG
    if not all([cfg["sender"], cfg["app_pw"], cfg["recipient"]]):
        log.warning("이메일 설정 불완전 → 발송 건너뜀")
        return False
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["sender"]
        msg["To"]      = cfg["recipient"]
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as srv:
            srv.login(cfg["sender"], cfg["app_pw"])
            srv.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        log.info(f"이메일 발송 완료 → {cfg['recipient']}")
        return True
    except Exception as e:
        log.error(f"이메일 발송 실패: {e}")
        return False

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run():
    state = load_state()
    h     = load_holdings()

    px    = get_prices()
    last  = px.iloc[-1].copy()
    missing = [t for t in TICKERS if pd.isna(last.get(t, np.nan))]
    if missing:
        raise ValueError(f"최종 가격 누락: {missing}")

    vix, spy_dd = get_market_indicators()

    # NAV / DD 계산
    h["price"] = h["ticker"].map(last)
    h["value"] = h["shares"] * h["price"]
    nav        = float(h["value"].sum())

    if nav > state.get("peak_nav", 0):
        state["peak_nav"] = nav
    peak_nav = state["peak_nav"]
    dd       = float(nav / peak_nav - 1) if peak_nav > 0 else 0.0

    # 비중 계산 + 보호 오버레이
    base_w = calc_weights(px)
    w      = apply_protection(base_w, spy_dd, vix, dd)
    w      = apply_caps(w)

    # 운용 모드 결정
    cash     = BASE_MONTHLY + (EXTRA_MONTHLY if state["remaining_extra"] > 0 else 0)
    buy_only = state["remaining_extra"] > 0

    if not buy_only:
        drift = calc_max_drift(h, last, w)
        log.info(f"최대 드리프트: {drift:.2%} (임계값 {REBALANCE_THRESHOLD:.0%})")
        if drift < REBALANCE_THRESHOLD:
            log.info("드리프트 임계값 미달 → 매수 전용으로 전환")
            buy_only = True

    if buy_only:
        orders, _, used = generate_orders_buy_only(h[["ticker", "shares"]], last, w, cash)
    else:
        orders, _, used = generate_orders_full_rebalance(h[["ticker", "shares"]], last, w, cash)

    # 히스토리 저장
    eq_w = float(w[[t for t in w.index if ASSET_TYPE[t] == "EQUITY"]].sum())
    append_history({
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "nav":        nav,
        "drawdown":   dd,
        "spy_dd":     spy_dd,
        "vix":        vix if not np.isnan(vix) else None,
        "eq_weight":  eq_w,
        "cash_input": cash,
        "used_cash":  used,
        "buy_only":   buy_only,
    })

    # 상태 업데이트 및 저장
    if state["remaining_extra"] > 0:
        state["remaining_extra"] -= 1
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    # 콘솔 요약
    h["weight"] = h["value"] / nav if nav > 0 else 0.0
    log.info(f"NAV: {nav:,.0f}원 | DD: {dd:.2%} | VIX: {vix_v:.1f} | SPY DD: {spy_dd:.2%}".replace("vix_v", str(round(vix if not np.isnan(vix) else 0, 1))))
    log.info(f"모드: {'매수전용' if buy_only else '리밸런싱'} | 투입: {cash:,.0f}원 | 집행: {used:,.0f}원")
    log.info(f"목표 주식비중: {eq_w:.1%} (eq_target={_calc_eq_target(spy_dd, vix, dd):.0%})")
    print("\n[목표 비중]")
    print((w * 100).round(1).astype(str).add("%").to_string())
    print("\n[주문]")
    print(orders.to_string(index=False) if len(orders) > 0 else "없음")

    # 이메일 발송
    report_data = {
        "run_date":       datetime.now().strftime("%Y년 %m월 %d일"),
        "nav":            nav,
        "portfolio_dd":   dd,
        "spy_dd":         spy_dd,
        "vix":            vix,
        "cash":           cash,
        "buy_only":       buy_only,
        "used_cash":      used,
        "weights":        w.to_dict(),
        "orders":         orders,
        "holdings":       h[["ticker", "shares", "price", "value", "weight"]],
        "remaining_extra": state["remaining_extra"],
    }
    html    = build_html_report(report_data)
    subject = f"[HALO] {datetime.now().strftime('%Y년 %m월')} 포트폴리오 리포트"
    send_report(subject, html)

    return orders, w


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log.error(f"실행 실패: {e}")
        sys.exit(1)
