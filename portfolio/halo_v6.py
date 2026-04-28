#!/usr/bin/env python3
"""
HALO v6.0 — VAA Korean Edition
Vigilant Asset Allocation (Keller & Keuning 2017) 한국 ETF 구현

핵심 규칙:
  공격 자산(4종) 중 하나라도 모멘텀 음수  →  방어 1위 ETF 100%
  공격 자산(4종) 모두 모멘텀 양수         →  공격 상위 3종 균등 배분 (33%)

모멘텀 점수 = (12M×1 + 6M×2 + 3M×4 + 1M×8) / 15  (VAA 표준)

ETF 유니버스 (티커 앱에서 반드시 확인):
  [공격] 379800 KODEX 미국S&P500
         251350 KODEX 선진국MSCI
         195980 PLUS 신흥국MSCI(합성H)
         305080 TIGER 미국채10년선물    ← 앱에서 티커 확인 필요
  [방어] 214980 KODEX 단기채권PLUS
         411060 ACE KRX금현물
         329750 TIGER 미국달러단기채권액티브  ← 앱에서 티커 확인 필요

⚠ IRP 계좌의 경우 주식형 ETF 70% 상한 준수 필요
  → IRP_MODE = True 설정 시 자동 처리
"""

import os, json, logging, smtplib, sys
import numpy as np
import pandas as pd
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("HALO-v6")

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
HOLDINGS_PATH = os.path.join(DATA_DIR, "holdings_v6.csv")
STATE_PATH    = os.path.join(DATA_DIR, "state_v6.json")
HISTORY_PATH  = os.path.join(DATA_DIR, "history_v6.csv")

# ─────────────────────────────────────────────
# 설정 (GitHub Secrets → 환경변수)
# ─────────────────────────────────────────────
MONTHLY_CASH = int(os.environ.get("MONTHLY_BASE_CASH",  "2000000"))
IRP_MODE     = os.environ.get("IRP_MODE", "true").lower() == "true"  # IRP 70% 주식 상한
EQUITY_CAP   = 0.70 if IRP_MODE else 1.00

EMAIL_CFG = {
    "smtp_host": "smtp.gmail.com", "smtp_port": 465,
    "sender":    os.environ.get("EMAIL_SENDER",       ""),
    "app_pw":    os.environ.get("GMAIL_APP_PASSWORD",  ""),
    "recipient": os.environ.get("EMAIL_RECIPIENT",    ""),
}

# ─────────────────────────────────────────────
# ETF 유니버스
# ─────────────────────────────────────────────
OFFENSIVE = [
    "379800.KS",   # KODEX 미국S&P500
    "251350.KS",   # KODEX 선진국MSCI
    "195980.KS",   # PLUS 신흥국MSCI(합성H)
    "305080.KS",   # TIGER 미국채10년선물  ← 티커 확인 필요
]
DEFENSIVE = [
    "214980.KS",   # KODEX 단기채권PLUS    (방어 1순위)
    "411060.KS",   # ACE KRX금현물         (방어 2순위)
    "329750.KS",   # TIGER 미국달러단기채권 (방어 3순위) ← 티커 확인 필요
]
ALL_TICKERS = OFFENSIVE + DEFENSIVE

FULL_NAME = {
    "379800.KS": "KODEX 미국S&P500",
    "251350.KS": "KODEX 선진국MSCI",
    "195980.KS": "PLUS 신흥국MSCI(H)",
    "305080.KS": "TIGER 미국채10년선물",
    "214980.KS": "KODEX 단기채권PLUS",
    "411060.KS": "ACE KRX금현물",
    "329750.KS": "TIGER 달러단기채권",
}
# IRP 위험자산 분류 (주식형 ETF만 위험자산)
IS_EQUITY = {
    "379800.KS": True,
    "251350.KS": True,
    "195980.KS": True,
    "305080.KS": False,   # 채권 ETF → 안전자산
    "214980.KS": False,
    "411060.KS": False,   # 금 ETF: 증권사마다 다름, 보수적으로 False
    "329750.KS": False,
}

# VAA 모멘텀 가중치 (합계 15)
MOM_W = {252: 1, 126: 2, 63: 4, 21: 8}

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {
        "last_run":    None,
        "peak_nav":    0.0,
        "last_mode":   None,          # "OFFENSIVE" | "DEFENSIVE"
        "last_picks":  [],            # 지난달 선택 티커 목록
    }

def save_state(s: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────
# 데이터 취득
# ─────────────────────────────────────────────
def _extract_close(raw: pd.DataFrame, tickers: list) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        lvl = raw.columns.get_level_values(0)
        f   = "Adj Close" if "Adj Close" in lvl else "Close"
        px  = raw[f].copy()
    else:
        f  = "Adj Close" if "Adj Close" in raw.columns else "Close"
        px = raw[[f]].copy()
        if len(tickers) == 1:
            px.columns = tickers
    for t in tickers:
        if t not in px.columns:
            px[t] = np.nan
    return px[tickers].sort_index().ffill()


def get_prices() -> pd.DataFrame:
    log.info("ETF 가격 취득 중...")
    raw = yf.download(ALL_TICKERS, period="2y", progress=False, auto_adjust=False)
    if raw.empty:
        raise ValueError("가격 데이터 로드 실패")
    px = _extract_close(raw, ALL_TICKERS)
    # 데이터 없는 티커 경고
    for t in ALL_TICKERS:
        valid = px[t].dropna()
        if valid.empty:
            log.warning(f"⚠  {FULL_NAME.get(t,t)} ({t}) 가격 없음 — 티커 확인 필요")
        else:
            log.info(f"  {FULL_NAME.get(t,t):20s} {len(valid):4d}일  최종가 {valid.iloc[-1]:,.0f}원")
    return px


def get_spy_vix() -> tuple[float, float]:
    """SPY drawdown, VIX — 리포트용 참고값"""
    spy_dd, vix = 0.0, float("nan")
    try:
        raw  = yf.download(["SPY", "^VIX"], period="1y", progress=False, auto_adjust=False)
        px   = _extract_close(raw, ["SPY", "^VIX"])
        spy_s = px["SPY"].dropna()
        if len(spy_s) >= 20:
            peak   = float(spy_s.rolling(min(252, len(spy_s))).max().iloc[-1])
            spy_dd = float(spy_s.iloc[-1] / peak - 1) if peak > 0 else 0.0
        vix_s = px["^VIX"].dropna()
        if len(vix_s) > 0:
            vix = float(vix_s.iloc[-1])
    except Exception as e:
        log.warning(f"SPY/VIX 취득 실패: {e}")
    return spy_dd, vix

# ─────────────────────────────────────────────
# HOLDINGS
# ─────────────────────────────────────────────
def load_holdings() -> pd.DataFrame:
    if os.path.exists(HOLDINGS_PATH):
        h = pd.read_csv(HOLDINGS_PATH)
        if list(h.columns[:2]) != ["ticker", "shares"]:
            h.columns = ["ticker", "shares"] + list(h.columns[2:])
    else:
        h = pd.DataFrame({"ticker": ALL_TICKERS, "shares": [0] * len(ALL_TICKERS)})
    base = pd.DataFrame({"ticker": ALL_TICKERS})
    h    = base.merge(h[["ticker", "shares"]], on="ticker", how="left")
    h["shares"] = pd.to_numeric(h["shares"], errors="coerce").fillna(0).astype(int)
    return h

# ─────────────────────────────────────────────
# VAA 핵심: 모멘텀 점수 계산
# ─────────────────────────────────────────────
def calc_vaa_score(px: pd.DataFrame) -> pd.Series:
    """
    VAA 표준 모멘텀 점수
    score = (12M×1 + 6M×2 + 3M×4 + 1M×8) / 15
    양수 → 상승 추세, 음수 → 하락 추세
    """
    scores = pd.Series(0.0, index=px.columns)
    total_w = sum(MOM_W.values())   # 15

    for lb, w in MOM_W.items():
        if len(px) < lb + 2:
            continue
        ret     = px.iloc[-1] / px.iloc[-lb] - 1
        scores += ret * w

    scores /= total_w
    return scores.reindex(ALL_TICKERS).fillna(float("-inf"))


# ─────────────────────────────────────────────
# VAA 신호: 공격 / 방어 결정
# ─────────────────────────────────────────────
def get_vaa_signal(scores: pd.Series) -> tuple[str, list[str]]:
    """
    Returns:
        mode  : "OFFENSIVE" | "DEFENSIVE"
        picks : 투자할 티커 목록 (우선순위 순)
    """
    off_scores = scores[OFFENSIVE].copy()
    def_scores = scores[DEFENSIVE].copy()

    any_negative = (off_scores < 0).any()

    if any_negative:
        # 방어 모드: 방어 자산 중 점수 1위에 100%
        negative_tickers = off_scores[off_scores < 0].index.tolist()
        log.info(f"방어 신호 발동 — 음수 모멘텀: {[FULL_NAME.get(t,t) for t in negative_tickers]}")
        best_def = def_scores.idxmax()
        return "DEFENSIVE", [best_def]
    else:
        # 공격 모드: 공격 자산 상위 3종 균등 배분
        top3 = off_scores.nlargest(3).index.tolist()
        return "OFFENSIVE", top3


def calc_target_weights(mode: str, picks: list[str]) -> pd.Series:
    """
    picks → 목표 비중 Series
    IRP_MODE=True 이면 주식형 ETF 70% 초과 시 조정
    """
    w = pd.Series(0.0, index=ALL_TICKERS)

    if mode == "DEFENSIVE":
        w[picks[0]] = 1.0

    else:  # OFFENSIVE
        base = 1.0 / len(picks)
        for t in picks:
            w[t] = base

        # IRP 모드: 주식 비중 70% 초과 시 초과분을 방어 1위로 이동
        if IRP_MODE:
            eq_w = sum(w[t] for t in picks if IS_EQUITY.get(t, False))
            if eq_w > EQUITY_CAP + 1e-6:
                excess = eq_w - EQUITY_CAP
                # 주식 비중 균등 축소
                eq_tickers = [t for t in picks if IS_EQUITY.get(t, False)]
                for t in eq_tickers:
                    w[t] -= excess / len(eq_tickers)
                # 초과분 → 방어 1위
                best_def = DEFENSIVE[0]
                w[best_def] += excess
                log.info(f"IRP 70% 조정: 주식 {eq_w:.0%} → {EQUITY_CAP:.0%}, "
                         f"차액 {excess:.0%} → {FULL_NAME.get(best_def, best_def)}")

    return w / w.sum() if w.sum() > 0 else w

# ─────────────────────────────────────────────
# 주문 생성
# ─────────────────────────────────────────────
def need_rebalance(state: dict, mode: str, picks: list[str]) -> bool:
    """신호 또는 종목 바뀌면 전체 리밸런싱, 아니면 매수 전용"""
    if state.get("last_mode") != mode:
        log.info(f"모드 변경: {state.get('last_mode')} → {mode} → 전체 리밸런싱")
        return True
    if sorted(state.get("last_picks", [])) != sorted(picks):
        log.info(f"종목 변경: {state.get('last_picks')} → {picks} → 전체 리밸런싱")
        return True
    return False


def generate_orders_buy_only(
    h: pd.DataFrame, prices: pd.Series, w: pd.Series, cash: float
) -> tuple[pd.DataFrame, float, float]:
    """매수 전용: 현금만 picks에 배분 (기존 포지션 유지)"""
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

    # shortfall 기반 잔여 현금 배분
    while remaining >= df.loc[df["buy_shares"] >= 0, "price"].min():
        aff = df[df["price"] <= remaining].copy()
        if aff.empty:
            break
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


def generate_orders_full_rebalance(
    h: pd.DataFrame, prices: pd.Series, w: pd.Series, cash: float
) -> tuple[pd.DataFrame, float, float]:
    """전체 리밸런싱: 목표 비중에 맞게 매도→매수"""
    df                  = h.copy()
    df["price"]         = df["ticker"].map(prices)
    df["value"]         = df["shares"] * df["price"]
    nav                 = float(df["value"].sum())
    new_nav             = nav + cash
    df["target_w"]      = df["ticker"].map(w).fillna(0)
    df["target_shares"] = np.floor(df["target_w"] * new_nav / df["price"]).astype(int)
    df["delta"]         = df["target_shares"] - df["shares"]

    rows = []
    for _, r in df[df["delta"] < 0].iterrows():
        rows.append([r["ticker"], "SELL", int(-r["delta"]), float(r["price"])])
    for _, r in df[df["delta"] > 0].iterrows():
        rows.append([r["ticker"], "BUY",  int(r["delta"]),  float(r["price"])])

    if not rows:
        return pd.DataFrame(columns=["ticker", "side", "shares", "price"]), nav, 0.0

    orders   = pd.DataFrame(rows, columns=["ticker", "side", "shares", "price"])
    buy_cost = float((orders.loc[orders["side"]=="BUY",  "shares"]
                      * orders.loc[orders["side"]=="BUY",  "price"]).sum())
    sell_val = float((orders.loc[orders["side"]=="SELL", "shares"]
                      * orders.loc[orders["side"]=="SELL", "price"]).sum())
    return orders, nav, buy_cost - sell_val

# ─────────────────────────────────────────────
# 히스토리
# ─────────────────────────────────────────────
def append_history(row: dict):
    df   = pd.DataFrame([row])
    mode = "a" if os.path.exists(HISTORY_PATH) else "w"
    df.to_csv(HISTORY_PATH, mode=mode, header=(mode == "w"), index=False)

# ─────────────────────────────────────────────
# HTML 이메일
# ─────────────────────────────────────────────
def build_email(d: dict) -> str:
    mode_badge = ("🟢 공격 모드 (Offensive)"
                  if d["mode"] == "OFFENSIVE" else "🔴 방어 모드 (Defensive)")

    picks_html = "".join(
        f"<li><b>{FULL_NAME.get(t, t)}</b> ({t})</li>"
        for t in d["picks"]
    )

    def order_rows(orders):
        if orders is None or len(orders) == 0:
            return "<tr><td colspan='5' style='text-align:center;color:#888'>주문 없음</td></tr>"
        rows = ""
        for _, r in orders.iterrows():
            sc  = "#27ae60" if r["side"] == "BUY" else "#e74c3c"
            amt = int(r["shares"]) * float(r["price"])
            rows += (
                f"<tr><td>{FULL_NAME.get(r['ticker'], r['ticker'])}</td>"
                f"<td style='color:{sc};font-weight:bold;text-align:center'>{r['side']}</td>"
                f"<td style='text-align:right'>{int(r['shares']):,}주</td>"
                f"<td style='text-align:right'>{float(r['price']):,.0f}원</td>"
                f"<td style='text-align:right'>{amt:,.0f}원</td></tr>"
            )
        return rows

    score_rows = "".join(
        f"<tr><td>{FULL_NAME.get(t,t)}</td>"
        f"<td>{'공격' if t in OFFENSIVE else '방어'}</td>"
        f"<td style='text-align:right;color:{'#27ae60' if s>=0 else '#e74c3c'}'>"
        f"{'▲' if s>=0 else '▼'} {s*100:.2f}%</td></tr>"
        for t, s in sorted(d["scores"].items(), key=lambda x: -x[1])
    )

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>
  body{{font-family:'Malgun Gothic',Arial,sans-serif;max-width:680px;margin:auto;padding:20px;color:#333}}
  h1{{background:#1a252f;color:#fff;padding:14px 20px;border-radius:8px;font-size:18px}}
  h2{{color:#1a252f;border-left:4px solid #3498db;padding-left:10px;margin-top:24px}}
  .badge{{font-size:17px;font-weight:bold;padding:12px;background:#ecf0f1;border-radius:8px;text-align:center;margin:12px 0}}
  .metrics{{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}}
  .m{{flex:1;min-width:110px;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center}}
  .mv{{font-size:20px;font-weight:bold}}.ml{{font-size:11px;color:#888;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
  th{{background:#2c3e50;color:#fff;padding:8px 12px;text-align:left}}
  td{{padding:7px 12px;border-bottom:1px solid #eee}}
  ul{{margin:8px 0;padding-left:20px}}li{{margin:4px 0}}
</style></head><body>
<h1>📊 HALO v6.0 — VAA 월간 리포트</h1>
<p style='color:#888;margin:4px 0 16px'>{d['date']} | {'전체 리밸런싱' if d['rebalanced'] else '매수 전용'}</p>

<h2>이달 신호</h2>
<div class='badge'>{mode_badge}</div>
<ul>{picks_html}</ul>

<h2>시장 지표</h2>
<div class='metrics'>
  <div class='m'><div class='mv' style='color:{"#e74c3c" if d["vix"]>=32 else "#27ae60"}'>{d["vix"]:.1f}</div><div class='ml'>VIX</div></div>
  <div class='m'><div class='mv' style='color:{"#e74c3c" if d["spy_dd"]<=-0.10 else "#27ae60"}'>{d["spy_dd"]*100:.1f}%</div><div class='ml'>SPY 낙폭</div></div>
  <div class='m'><div class='mv' style='color:{"#e74c3c" if d["port_dd"]<=-0.08 else "#27ae60"}'>{d["port_dd"]*100:.1f}%</div><div class='ml'>포트폴리오 DD</div></div>
  <div class='m'><div class='mv'>{d["nav"]:,.0f}</div><div class='ml'>NAV (원)</div></div>
</div>

<h2>VAA 모멘텀 점수</h2>
<table><tr><th>ETF</th><th>구분</th><th>점수</th></tr>{score_rows}</table>

<h2>주문 내역</h2>
<table><tr><th>ETF</th><th>구분</th><th>수량</th><th>현재가</th><th>금액</th></tr>
{order_rows(d["orders"])}</table>
<p style='font-size:12px;color:#555'>투입: {d["cash"]:,.0f}원 | 집행: {d["used"]:,.0f}원 | 미집행: {d["cash"]-d["used"]:,.0f}원</p>

<hr style='border:none;border-top:1px solid #eee;margin-top:24px'>
<p style='font-size:11px;color:#aaa'>HALO v6.0 VAA Korean Edition | 투자 결과는 본인 책임입니다.</p>
</body></html>"""


def send_email(subject: str, html: str) -> bool:
    cfg = EMAIL_CFG
    if not all([cfg["sender"], cfg["app_pw"], cfg["recipient"]]):
        log.warning("이메일 설정 불완전 → 건너뜀")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["sender"]
        msg["To"]      = cfg["recipient"]
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as s:
            s.login(cfg["sender"], cfg["app_pw"])
            s.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        log.info(f"이메일 발송 완료 → {cfg['recipient']}")
        return True
    except Exception as e:
        log.error(f"이메일 실패: {e}")
        return False

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run():
    state = load_state()
    h     = load_holdings()

    # 가격 취득
    px   = get_prices()
    last = px.iloc[-1]

    missing = [t for t in ALL_TICKERS
               if t not in last.index or pd.isna(last.get(t))]
    if missing:
        log.error(f"가격 없는 티커 {missing} — 티커를 앱에서 확인하세요")
        sys.exit(1)

    spy_dd, vix = get_spy_vix()

    # NAV / DD
    h["price"] = h["ticker"].map(last)
    h["value"] = h["shares"] * h["price"]
    nav        = float(h["value"].sum())
    if nav > state.get("peak_nav", 0):
        state["peak_nav"] = nav
    port_dd = float(nav / state["peak_nav"] - 1) if state["peak_nav"] > 0 else 0.0

    # ── VAA 핵심 ──────────────────────────────
    scores = calc_vaa_score(px)
    mode, picks = get_vaa_signal(scores)
    w = calc_target_weights(mode, picks)
    # ─────────────────────────────────────────

    # 리밸런싱 여부
    rebalance = need_rebalance(state, mode, picks)

    if rebalance:
        orders, _, used = generate_orders_full_rebalance(
            h[["ticker", "shares"]], last, w, MONTHLY_CASH)
    else:
        orders, _, used = generate_orders_buy_only(
            h[["ticker", "shares"]], last, w, MONTHLY_CASH)

    # 히스토리 & 상태 저장
    append_history({
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "nav":        nav, "port_dd": port_dd,
        "spy_dd":     spy_dd, "vix": vix if not np.isnan(vix) else None,
        "mode":       mode, "picks": ",".join(picks),
        "cash":       MONTHLY_CASH, "used": used,
        "rebalanced": rebalance,
    })
    state.update({
        "last_run":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_mode":  mode,
        "last_picks": picks,
    })
    save_state(state)

    # 콘솔 출력
    log.info("=" * 55)
    log.info(f"HALO v6.0 | {state['last_run']}")
    log.info(f"모드: {mode} | 선택: {[FULL_NAME.get(t,t) for t in picks]}")
    log.info(f"NAV: {nav:,.0f}원 | Port DD: {port_dd:.2%}")
    log.info(f"VIX: {vix:.1f} | SPY DD: {spy_dd:.2%}")
    log.info(f"{'전체 리밸런싱' if rebalance else '매수 전용'} | 투입: {MONTHLY_CASH:,.0f}원 | 집행: {used:,.0f}원")
    log.info("")
    log.info("[ VAA 모멘텀 점수 ]")
    for t in sorted(ALL_TICKERS, key=lambda x: -scores.get(x, float("-inf"))):
        sign = "▲" if scores.get(t, -1) >= 0 else "▼"
        cat  = "공격" if t in OFFENSIVE else "방어"
        log.info(f"  {FULL_NAME.get(t,t):22s} [{cat}] {sign} {scores.get(t,0)*100:+.2f}%")
    log.info("")
    log.info("[ 목표 비중 ]")
    for t in ALL_TICKERS:
        if w.get(t, 0) > 0:
            log.info(f"  {FULL_NAME.get(t,t):22s} {w[t]*100:.1f}%")
    log.info("")
    if len(orders) > 0:
        log.info("[ 주문 ]")
        print(orders.to_string(index=False))
    else:
        log.info("[ 주문 없음 ]")

    # 이메일
    report_data = {
        "date":       datetime.now().strftime("%Y년 %m월 %d일"),
        "mode":       mode,
        "picks":      picks,
        "scores":     scores.to_dict(),
        "nav":        nav,
        "port_dd":    port_dd,
        "spy_dd":     spy_dd,
        "vix":        vix if not np.isnan(vix) else 0,
        "cash":       MONTHLY_CASH,
        "used":       used,
        "orders":     orders,
        "rebalanced": rebalance,
    }
    html    = build_email(report_data)
    subject = f"[HALO v6] {datetime.now().strftime('%Y년 %m월')} VAA 리포트 — {mode}"
    send_email(subject, html)

    return orders, w, mode, picks


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log.error(f"실행 실패: {e}")
        sys.exit(1)
