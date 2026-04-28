#!/usr/bin/env python3
"""
HALO v5.0 — VAA 캐너리 + 리스크 패리티 통합

v4.0 대비 개선:
  1. 멀티타임프레임 모멘텀  (1M×8 + 3M×4 + 6M×2 + 12M×1) / 15
  2. VAA 캐너리 신호        공격/방어 전환을 후행(SPY DD) → 선행으로 교체
  3. 리스크 패리티           주식 슬리브 내에서 1/vol 비중
  4. 채권 구성 개선          국고채30년 비중 축소, 단기채 비중 확대
"""

import os, json, logging, smtplib, sys
import numpy as np
import pandas as pd
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("HALO-v5")

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
# 설정
# ─────────────────────────────────────────────
BASE_MONTHLY  = int(os.environ.get("MONTHLY_BASE_CASH",  "2000000"))
EXTRA_MONTHLY = int(os.environ.get("MONTHLY_EXTRA_CASH", "0"))

EMAIL_CFG = {
    "smtp_host": "smtp.gmail.com", "smtp_port": 465,
    "sender":    os.environ.get("EMAIL_SENDER",      ""),
    "app_pw":    os.environ.get("GMAIL_APP_PASSWORD", ""),
    "recipient": os.environ.get("EMAIL_RECIPIENT",   ""),
}

# ─────────────────────────────────────────────
# ETF UNIVERSE
# ─────────────────────────────────────────────
ETF = {
    "US"  : "379800.KS",   # KODEX 미국S&P500
    "DM"  : "251350.KS",   # KODEX MSCI선진국
    "EM"  : "195980.KS",   # PLUS 신흥국MSCI(합성 H)
    "BOND": "439870.KS",   # KODEX 국고채30년 (비중 축소)
    "CASH": "214980.KS",   # KODEX 단기채권PLUS  (캐너리 + 방어 자산)
    "GOLD": "411060.KS",   # ACE KRX금현물
}
TICKERS = list(ETF.values())
FULL_NAME = {
    "379800.KS": "KODEX 미국S&P500",
    "251350.KS": "KODEX MSCI선진국",
    "195980.KS": "PLUS 신흥국MSCI(H)",
    "439870.KS": "KODEX 국고채30년",
    "214980.KS": "KODEX 단기채권PLUS",
    "411060.KS": "ACE KRX금현물",
}
ASSET_TYPE = {
    ETF["US"]: "EQUITY", ETF["DM"]: "EQUITY", ETF["EM"]: "EQUITY",
    ETF["BOND"]: "BOND", ETF["CASH"]: "CASH", ETF["GOLD"]: "GOLD",
}

# ─────────────────────────────────────────────
# 포트폴리오 파라미터 (v5.0 변경)
# ─────────────────────────────────────────────
REBALANCE_THRESHOLD = 0.07

# 자산 유형별 한도
CAP = {ETF["CASH"]: 0.60, ETF["GOLD"]: 0.20, ETF["BOND"]: 0.15}  # BOND 상한 추가

# 최소 비중 (방어 모드 아닐 때)
MIN_W = {ETF["CASH"]: 0.08, ETF["GOLD"]: 0.02, ETF["BOND"]: 0.03}

# VAA 캐너리: 단기채 모멘텀이 음수면 방어 모드
CANARY_TICKER   = ETF["CASH"]     # 단기채권을 캐너리로 활용
CANARY_LOOKBACK = 63              # 3개월 모멘텀

# 보호 레벨
VIX_WARN = 25; VIX_HIGH = 32; VIX_EXTREME = 45

# 멀티타임프레임 모멘텀 가중치 (VAA 방식)
MOM_WEIGHTS = {252: 1, 126: 2, 63: 4, 21: 8}   # 합계 15

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"remaining_extra": 0, "last_run": None, "peak_nav": 0.0}

def save_state(s):
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2)

# ─────────────────────────────────────────────
# 데이터
# ─────────────────────────────────────────────
def _extract_close(raw, tickers):
    if isinstance(raw.columns, pd.MultiIndex):
        lvl = raw.columns.get_level_values(0)
        f   = "Adj Close" if "Adj Close" in lvl else "Close"
        px  = raw[f].copy()
    else:
        f  = "Adj Close" if "Adj Close" in raw.columns else "Close"
        px = raw[[f]].copy()
        if len(tickers) == 1: px.columns = tickers
    for t in tickers:
        if t not in px.columns: px[t] = np.nan
    return px[tickers].sort_index().ffill()

def get_prices():
    raw = yf.download(TICKERS, period="2y", progress=False, auto_adjust=False)
    if raw.empty: raise ValueError("ETF 가격 로드 실패")
    px = _extract_close(raw, TICKERS)
    log.info(f"ETF 가격: {len(px)}일, {px.index[-1].date()} 기준")
    return px

def get_market_indicators():
    vix, spy_dd = np.nan, 0.0
    try:
        raw   = yf.download(["^VIX", "SPY"], period="2y", progress=False, auto_adjust=False)
        px    = _extract_close(raw, ["^VIX", "SPY"])
        vix_s = px["^VIX"].dropna()
        if len(vix_s) > 0: vix = float(vix_s.iloc[-1])
        spy_s = px["SPY"].dropna()
        if len(spy_s) >= 20:
            pk     = float(spy_s.rolling(min(252, len(spy_s))).max().iloc[-1])
            spy_dd = float(spy_s.iloc[-1] / pk - 1) if pk > 0 else 0.0
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
# 핵심 개선 ①: 멀티타임프레임 모멘텀 (VAA 방식)
# ─────────────────────────────────────────────
def _norm(w):
    w = w.fillna(0).clip(lower=0); s = w.sum()
    return w / s if s > 1e-9 else pd.Series(1 / len(w), index=w.index)

def calc_vaa_momentum(px):
    """
    VAA 모멘텀 점수 = (12M×1 + 6M×2 + 3M×4 + 1M×8) / 15
    단일 12M보다 최신 추세 변화에 빠르게 반응
    """
    score = pd.Series(0.0, index=px.columns)
    total_w = sum(MOM_WEIGHTS.values())

    for lookback, weight in MOM_WEIGHTS.items():
        if len(px) < lookback + 5:
            continue
        mom     = px.iloc[-1] / px.iloc[-lookback] - 1
        score  += mom * weight

    score /= total_w
    return score

# ─────────────────────────────────────────────
# 핵심 개선 ②: VAA 캐너리 신호
# ─────────────────────────────────────────────
def check_canary(px):
    """
    캐너리 = 단기채권(CASH) 3개월 모멘텀
    음수 → 위험 환경 신호 (채권시장 스트레스)
    양수 → 정상 환경
    """
    if CANARY_TICKER not in px.columns or len(px) < CANARY_LOOKBACK + 5:
        return True  # 데이터 부족 → 정상 가정

    cash_s = px[CANARY_TICKER].dropna()
    if len(cash_s) < CANARY_LOOKBACK:
        return True

    mom_canary = float(cash_s.iloc[-1] / cash_s.iloc[-CANARY_LOOKBACK] - 1)
    log.info(f"캐너리 모멘텀(단기채 3M): {mom_canary:.2%} {'⚠ 방어 신호' if mom_canary < 0 else '✅ 정상'}")
    return mom_canary >= 0  # True = 정상, False = 위험

# ─────────────────────────────────────────────
# 핵심 개선 ③: 리스크 패리티 (주식 슬리브 내)
# ─────────────────────────────────────────────
def calc_weights_v5(px, canary_ok):
    """
    캐너리 정상:  VAA 모멘텀 + 리스크 패리티 (주식 내)
    캐너리 위험:  주식 비중 최소화, CASH/BOND 최대화
    """
    if len(px) < 63:
        log.warning("데이터 부족 → 균등 비중")
        return pd.Series(1 / len(TICKERS), index=TICKERS)

    score = calc_vaa_momentum(px)

    if not canary_ok:
        # 캐너리 발동 → 방어 모드: 주식 0, CASH/BOND/GOLD만
        log.info("캐너리 발동 → 방어 포트폴리오")
        score = score.copy()
        for t in TICKERS:
            if ASSET_TYPE[t] == "EQUITY":
                score[t] = 0.0
        score[ETF["CASH"]] = abs(score[ETF["CASH"]]) + 0.1   # CASH 강제 선호
        score[ETF["BOND"]] = max(score.get(ETF["BOND"], 0), 0)
        score[ETF["GOLD"]] = max(score.get(ETF["GOLD"], 0), 0)

    else:
        # 정상 → 음수 모멘텀 자산 제외 (절대 모멘텀 필터)
        score[score < 0] = 0.0

    score = score.reindex(TICKERS).fillna(0)

    # 리스크 패리티: 주식 슬리브 내 1/vol 적용
    eq_tickers = [t for t in TICKERS if ASSET_TYPE[t] == "EQUITY" and score[t] > 0]
    if len(eq_tickers) > 1 and len(px) >= 63:
        vol = px[eq_tickers].pct_change().rolling(63).std().iloc[-1]
        vol = vol.replace(0, np.nan).fillna(vol.mean())
        inv_vol = 1 / vol
        # 주식 총 스코어를 1/vol로 재배분
        eq_total = score[eq_tickers].sum()
        if eq_total > 1e-9 and inv_vol.sum() > 1e-9:
            score[eq_tickers] = inv_vol / inv_vol.sum() * eq_total

    # 최소 비중 보장
    total = score.sum()
    if total > 1e-9:
        for t, min_w in MIN_W.items():
            if t in score.index:
                score[t] = max(score[t], min_w * total)

    return _norm(score)

# ─────────────────────────────────────────────
# 통합 보호 오버레이 (v4.0 동일 + canary 통합)
# ─────────────────────────────────────────────
def _calc_eq_target(spy_dd, vix, portfolio_dd, canary_ok):
    if not canary_ok:
        return 0.10   # 캐너리 발동 → 주식 최소화

    if   spy_dd <= -0.30: eq_spy = 0.20
    elif spy_dd <= -0.20: eq_spy = 0.35
    elif spy_dd <= -0.10: eq_spy = 0.50
    else:                 eq_spy = 0.70

    if   np.isnan(vix):      eq_vix = 0.70
    elif vix >= VIX_EXTREME: eq_vix = 0.20
    elif vix >= VIX_HIGH:    eq_vix = 0.40
    elif vix >= VIX_WARN:    eq_vix = 0.55
    else:                    eq_vix = 0.70

    if   portfolio_dd <= -0.15: eq_pdd = 0.35
    elif portfolio_dd <= -0.08: eq_pdd = 0.55
    else:                       eq_pdd = 0.70

    return min(eq_spy, eq_vix, eq_pdd)

def apply_protection_v5(w, spy_dd, vix, portfolio_dd, canary_ok):
    w   = _norm(w)
    eqt = _calc_eq_target(spy_dd, vix, portfolio_dd, canary_ok)
    eq_t   = [t for t in w.index if ASSET_TYPE[t] == "EQUITY" and w[t] > 0]
    safe_t = [t for t in w.index if ASSET_TYPE[t] != "EQUITY" and w[t] > 0]
    if not eq_t or not safe_t: return w
    w2 = w.copy()
    w2[eq_t]   = w2[eq_t]   / w2[eq_t].sum()   * eqt
    w2[safe_t] = w2[safe_t] / w2[safe_t].sum() * (1 - eqt)
    return _norm(w2)

# ─────────────────────────────────────────────
# 캡 적용 (BOND 상한 추가)
# ─────────────────────────────────────────────
def apply_caps_v5(w):
    w = _norm(w)
    for _ in range(30):
        excess = sum(max(0.0, w.get(t, 0) - c) for t, c in CAP.items() if t in w.index)
        if excess < 1e-9: break
        for t, cap in CAP.items():
            if t in w.index: w[t] = min(w[t], cap)
        free = [t for t in w.index if t not in CAP]
        fs   = w[free].sum()
        if fs > 1e-9: w[free] += excess * w[free] / fs
        w = _norm(w)
    return _norm(w)

# ─────────────────────────────────────────────
# 드리프트
# ─────────────────────────────────────────────
def calc_max_drift(h, prices, w):
    h = h.copy()
    h["price"] = h["ticker"].map(prices)
    h["value"] = h["shares"] * h["price"]
    nav = h["value"].sum()
    if nav <= 0: return 1.0
    h["cur_w"]    = h["value"] / nav
    h["target_w"] = h["ticker"].map(w).fillna(0)
    return float((h["cur_w"] - h["target_w"]).abs().max())

# ─────────────────────────────────────────────
# 주문 생성
# ─────────────────────────────────────────────
def generate_orders_buy_only(h, prices, w, cash):
    df          = h.copy()
    df["price"] = df["ticker"].map(prices)
    df["value"] = df["shares"] * df["price"]
    nav         = float(df["value"].sum())
    df["target_w"]   = df["ticker"].map(w).fillna(0)
    gap              = ((nav + cash) * df["target_w"] - df["value"]).clip(lower=0)
    df["gap"]        = gap if gap.sum() > 1e-9 else df["target_w"]
    alloc            = df["gap"] / df["gap"].sum()
    df["buy_shares"] = np.floor(alloc * cash / df["price"]).astype(int)
    remaining        = float(cash - (df["buy_shares"] * df["price"]).sum())

    while remaining >= df["price"].min():
        aff = df[df["price"] <= remaining].copy()
        if aff.empty: break
        tv  = float(((df["shares"] + df["buy_shares"]) * df["price"]).sum()) + remaining
        cur = (df.loc[aff.index, "shares"] + df.loc[aff.index, "buy_shares"]) \
              * df.loc[aff.index, "price"] / tv
        sf  = df.loc[aff.index, "target_w"] - cur
        best = sf.idxmax()
        df.loc[best, "buy_shares"] += 1
        remaining -= float(df.loc[best, "price"])

    orders = df[df["buy_shares"] > 0][["ticker", "buy_shares", "price"]].copy()
    orders["side"] = "BUY"
    orders.rename(columns={"buy_shares": "shares"}, inplace=True)
    used = float((orders["shares"] * orders["price"]).sum()) if len(orders) > 0 else 0.0
    return orders[["ticker", "side", "shares", "price"]], nav, used

def generate_orders_full_rebalance(h, prices, w, cash):
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
    buy_cost = float((orders.loc[orders["side"]=="BUY",  "shares"] * orders.loc[orders["side"]=="BUY",  "price"]).sum())
    sell_val = float((orders.loc[orders["side"]=="SELL", "shares"] * orders.loc[orders["side"]=="SELL", "price"]).sum())
    return orders, nav, buy_cost - sell_val

# ─────────────────────────────────────────────
# HISTORY / EMAIL (v4.0 동일)
# ─────────────────────────────────────────────
def append_history(row):
    df   = pd.DataFrame([row])
    mode = "a" if os.path.exists(HISTORY_PATH) else "w"
    df.to_csv(HISTORY_PATH, mode=mode, header=(mode == "w"), index=False)

def send_report(subject, body):
    cfg = EMAIL_CFG
    if not all([cfg["sender"], cfg["app_pw"], cfg["recipient"]]):
        log.warning("이메일 설정 불완전 → 건너뜀")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = cfg["sender"]; msg["To"] = cfg["recipient"]
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as srv:
            srv.login(cfg["sender"], cfg["app_pw"])
            srv.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        log.info(f"이메일 발송 → {cfg['recipient']}")
        return True
    except Exception as e:
        log.error(f"이메일 실패: {e}"); return False

def build_simple_report(d):
    rows = ""
    if d["orders"] is not None and len(d["orders"]) > 0:
        for _, r in d["orders"].iterrows():
            sc = "#27ae60" if r["side"] == "BUY" else "#e74c3c"
            rows += f"<tr><td>{FULL_NAME.get(r['ticker'], r['ticker'])}</td>" \
                    f"<td style='color:{sc};text-align:center'>{r['side']}</td>" \
                    f"<td style='text-align:right'>{int(r['shares']):,}주</td>" \
                    f"<td style='text-align:right'>{float(r['price']):,.0f}원</td>" \
                    f"<td style='text-align:right'>{int(r['shares'])*float(r['price']):,.0f}원</td></tr>"
    else:
        rows = "<tr><td colspan='5' style='text-align:center'>주문 없음</td></tr>"

    canary_badge = "🟢 정상" if d["canary_ok"] else "🔴 캐너리 발동 (방어 모드)"
    return f"""<html><body style='font-family:Arial;max-width:680px;margin:auto;padding:20px'>
<h2>📊 HALO v5.0 월간 리포트 — {d['date']}</h2>
<p>캐너리: <b>{canary_badge}</b> | VIX: {d['vix']:.1f} | SPY DD: {d['spy_dd']:.1%} | 포트폴리오 DD: {d['portfolio_dd']:.1%}</p>
<p>NAV: <b>{d['nav']:,.0f}원</b> | 투입: {d['cash']:,.0f}원 | 집행: {d['used_cash']:,.0f}원</p>
<table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse;width:100%'>
<tr style='background:#2c3e50;color:white'><th>종목</th><th>구분</th><th>수량</th><th>현재가</th><th>금액</th></tr>
{rows}
</table>
<p style='color:#888;font-size:11px'>HALO v5.0 자동 생성 | 투자 결과에 대한 책임은 본인에게 있습니다.</p>
</body></html>"""

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
        raise ValueError(f"가격 누락: {missing}")

    vix, spy_dd = get_market_indicators()

    # NAV / DD
    h["price"] = h["ticker"].map(last)
    h["value"] = h["shares"] * h["price"]
    nav        = float(h["value"].sum())
    if nav > state.get("peak_nav", 0): state["peak_nav"] = nav
    peak_nav = state["peak_nav"]
    dd       = float(nav / peak_nav - 1) if peak_nav > 0 else 0.0

    # v5.0 핵심: 캐너리 체크 → 비중 계산
    canary_ok = check_canary(px)
    base_w    = calc_weights_v5(px, canary_ok)
    w         = apply_protection_v5(base_w, spy_dd, vix, dd, canary_ok)
    w         = apply_caps_v5(w)

    # 운용 모드
    cash     = BASE_MONTHLY + (EXTRA_MONTHLY if state["remaining_extra"] > 0 else 0)
    buy_only = state["remaining_extra"] > 0

    if not buy_only:
        drift = calc_max_drift(h, last, w)
        log.info(f"드리프트: {drift:.2%} (임계값 {REBALANCE_THRESHOLD:.0%})")
        if drift < REBALANCE_THRESHOLD:
            log.info("드리프트 임계값 미달 → 매수 전용")
            buy_only = True

    if buy_only:
        orders, _, used = generate_orders_buy_only(h[["ticker","shares"]], last, w, cash)
    else:
        orders, _, used = generate_orders_full_rebalance(h[["ticker","shares"]], last, w, cash)

    eq_w = float(w[[t for t in w.index if ASSET_TYPE[t]=="EQUITY"]].sum())
    append_history({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "nav": nav, "drawdown": dd, "spy_dd": spy_dd,
        "vix": vix if not np.isnan(vix) else None,
        "canary_ok": canary_ok, "eq_weight": eq_w,
        "cash_input": cash, "used_cash": used, "buy_only": buy_only,
    })

    if state["remaining_extra"] > 0: state["remaining_extra"] -= 1
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    # 콘솔
    h["weight"] = h["value"] / nav if nav > 0 else 0.0
    eqt = _calc_eq_target(spy_dd, vix, dd, canary_ok)
    log.info(f"캐너리: {'OK' if canary_ok else '⚠ 방어 발동'} | VIX: {vix:.1f} | SPY DD: {spy_dd:.2%}")
    log.info(f"NAV: {nav:,.0f}원 | DD: {dd:.2%} | 목표주식비중: {eqt:.0%}")
    log.info(f"모드: {'매수전용' if buy_only else '리밸런싱'} | 투입: {cash:,.0f}원 | 집행: {used:,.0f}원")
    print("\n[목표 비중]\n" + (w * 100).round(1).astype(str).add("%").to_string())
    print("\n[주문]\n" + (orders.to_string(index=False) if len(orders) > 0 else "없음"))

    report = build_simple_report({
        "date": datetime.now().strftime("%Y년 %m월 %d일"),
        "nav": nav, "portfolio_dd": dd, "spy_dd": spy_dd, "vix": vix if not np.isnan(vix) else 0,
        "cash": cash, "used_cash": used, "orders": orders, "canary_ok": canary_ok,
    })
    send_report(f"[HALO v5] {datetime.now().strftime('%Y년 %m월')} 리포트", report)
    return orders, w

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log.error(f"실행 실패: {e}"); sys.exit(1)
