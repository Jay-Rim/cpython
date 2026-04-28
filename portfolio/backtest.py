#!/usr/bin/env python3
"""
HALO v4.0 백테스트 분석
Google Colab 또는 로컬에서 실행 (인터넷 필요)

측정 지표: CAGR, MDD, Sharpe, Sortino, Calmar, 승률, 연도별 수익률
벤치마크: KODEX S&P500 단순 적립식
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import yfinance as yf

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
INITIAL_CAPITAL  = 10_000_000    # 초기 자본 (원)
MONTHLY_INVEST   = 2_000_000     # 월 적립 (원)
TRANSACTION_COST = 0.00015       # 편도 거래비용 0.015%
RISK_FREE_ANNUAL = 0.035         # 무위험 이자율 (국고채 단기)
BACKTEST_PERIOD  = "5y"

ETF = {
    "US"  : "379800.KS",
    "DM"  : "251350.KS",
    "EM"  : "195980.KS",
    "BOND": "439870.KS",
    "CASH": "214980.KS",
    "GOLD": "411060.KS",
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
    "379800.KS": "EQUITY", "251350.KS": "EQUITY", "195980.KS": "EQUITY",
    "439870.KS": "BOND",   "214980.KS": "CASH",   "411060.KS": "GOLD",
}
MIN_WEIGHT = {"BOND": 0.05, "CASH": 0.03, "GOLD": 0.02}
CAP_CASH = 0.50; CAP_GOLD = 0.20
VIX_WARN = 25; VIX_HIGH = 32; VIX_EXTREME = 45

# ─────────────────────────────────────────────
# 데이터
# ─────────────────────────────────────────────
def get_all_data(period=BACKTEST_PERIOD):
    print(f"데이터 취득 중 (기간: {period})...")
    symbols = TICKERS + ["^VIX", "SPY"]
    raw     = yf.download(symbols, period=period, progress=False, auto_adjust=False)

    if isinstance(raw.columns, pd.MultiIndex):
        f  = "Adj Close" if "Adj Close" in raw.columns.get_level_values(0) else "Close"
        px = raw[f].ffill()
    else:
        f  = "Adj Close" if "Adj Close" in raw.columns else "Close"
        px = raw[[f]].ffill()

    etf_px = px[[t for t in TICKERS if t in px.columns]].copy()
    vix_s  = px["^VIX"].dropna() if "^VIX" in px.columns else pd.Series(dtype=float)
    spy_s  = px["SPY"].dropna()  if "SPY"  in px.columns else pd.Series(dtype=float)

    start, end = etf_px.index[0].date(), etf_px.index[-1].date()
    print(f"ETF 기간: {start} ~ {end}  ({len(etf_px)}거래일)")

    missing = [FULL_NAME.get(t, t) for t in TICKERS if t not in etf_px.columns]
    if missing:
        print(f"⚠  데이터 없는 티커: {missing}")

    return etf_px, vix_s, spy_s

# ─────────────────────────────────────────────
# 전략 함수 (halo_v4.py 동일)
# ─────────────────────────────────────────────
def _norm(w):
    w = w.fillna(0).clip(lower=0)
    s = w.sum()
    return w / s if s > 1e-9 else pd.Series(1 / len(w), index=w.index)


def calc_weights(px_slice):
    available = [t for t in TICKERS if t in px_slice.columns
                 and px_slice[t].dropna().shape[0] >= 252]
    if len(available) < 2:
        avail2 = [t for t in TICKERS if t in px_slice.columns]
        return _norm(pd.Series(1.0, index=avail2)).reindex(TICKERS).fillna(0)

    px       = px_slice[available].dropna()
    if len(px) < 252:
        return _norm(pd.Series(1.0, index=available)).reindex(TICKERS).fillna(0)

    mom_12m  = px.iloc[-1] / px.iloc[-252] - 1
    mom_3m   = px.iloc[-1] / px.iloc[-63]  - 1
    vol_63   = px.pct_change().rolling(63).std().iloc[-1]
    mom      = 0.7 * mom_12m + 0.3 * mom_3m
    score    = (mom / vol_63).replace([np.inf, -np.inf], np.nan).fillna(0)
    score[mom_12m < 0] = 0.0

    total = score.sum()
    if total > 1e-9:
        for key, mw in MIN_WEIGHT.items():
            t = ETF[key]
            if t in score.index:
                score[t] = max(score[t], mw / (1 - mw) * total)

    return _norm(score).reindex(TICKERS).fillna(0)


def calc_eq_target(spy_dd, vix, pdd):
    eq_spy = 0.20 if spy_dd <= -0.30 else 0.35 if spy_dd <= -0.20 else 0.50 if spy_dd <= -0.10 else 0.70
    if   np.isnan(vix):      eq_vix = 0.70
    elif vix >= VIX_EXTREME: eq_vix = 0.20
    elif vix >= VIX_HIGH:    eq_vix = 0.40
    elif vix >= VIX_WARN:    eq_vix = 0.55
    else:                    eq_vix = 0.70
    eq_pdd = 0.35 if pdd <= -0.15 else 0.55 if pdd <= -0.08 else 0.70
    return min(eq_spy, eq_vix, eq_pdd)


def apply_protection(w, spy_dd, vix, pdd):
    w   = _norm(w)
    eqt = calc_eq_target(spy_dd, vix, pdd)
    eq_t   = [t for t in w.index if ASSET_TYPE.get(t) == "EQUITY" and w[t] > 0]
    safe_t = [t for t in w.index if ASSET_TYPE.get(t) != "EQUITY" and w[t] > 0]
    if not eq_t or not safe_t:
        return w
    w2          = w.copy()
    w2[eq_t]   = w2[eq_t]   / w2[eq_t].sum()   * eqt
    w2[safe_t] = w2[safe_t] / w2[safe_t].sum() * (1 - eqt)
    return _norm(w2)


def apply_caps(w):
    caps = {ETF["CASH"]: CAP_CASH, ETF["GOLD"]: CAP_GOLD}
    w    = _norm(w)
    for _ in range(20):
        excess = sum(max(0.0, w.get(t, 0) - c) for t, c in caps.items() if t in w.index)
        if excess < 1e-9:
            break
        for t, cap in caps.items():
            if t in w.index:
                w[t] = min(w[t], cap)
        free = [t for t in w.index if t not in caps]
        fs   = w[free].sum()
        if fs > 1e-9:
            w[free] += excess * w[free] / fs
        w = _norm(w)
    return _norm(w)

# ─────────────────────────────────────────────
# 성과 지표
# ─────────────────────────────────────────────
def calc_metrics(nav: pd.Series, label="") -> dict:
    nav    = nav.dropna()
    if len(nav) < 6:
        return {}

    rets   = nav.pct_change().dropna()
    n_yr   = len(nav) / 12

    cagr   = (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    vol    = rets.std() * np.sqrt(12)
    total  = nav.iloc[-1] / nav.iloc[0] - 1

    # MDD
    roll_max = nav.cummax()
    dd       = nav / roll_max - 1
    mdd      = dd.min()

    # MDD 최장 회복 기간 (개월)
    max_rec = 0
    i       = 0
    vals    = dd.values
    while i < len(vals):
        if vals[i] < 0:
            j = i
            while j < len(vals) and vals[j] < 0:
                j += 1
            max_rec = max(max_rec, j - i)
            i = j
        else:
            i += 1

    rf_mo  = (1 + RISK_FREE_ANNUAL) ** (1 / 12) - 1
    ex     = rets - rf_mo
    sharpe = ex.mean() / ex.std() * np.sqrt(12) if ex.std() > 0 else 0

    dn     = rets[rets < rf_mo]
    sortino = ex.mean() / dn.std() * np.sqrt(12) if len(dn) > 0 and dn.std() > 0 else 0

    calmar = cagr / abs(mdd) if mdd != 0 else 0
    win    = (rets > 0).mean()

    return {
        "label":         label,
        "CAGR":          cagr,
        "연간 변동성":    vol,
        "MDD":           mdd,
        "최장 MDD 회복":  max_rec,
        "Sharpe":        sharpe,
        "Sortino":       sortino,
        "Calmar":        calmar,
        "월 승률":        win,
        "최고 월":        rets.max(),
        "최저 월":        rets.min(),
        "총 수익률":      total,
    }

# ─────────────────────────────────────────────
# 백테스트 엔진
# ─────────────────────────────────────────────
def run_backtest(etf_px, vix_s, spy_s):
    monthly = etf_px.resample("ME").last()
    shares  = {t: 0 for t in TICKERS}
    cash    = float(INITIAL_CAPITAL)
    peak_nav = 0.0

    nav_hist = {}; dd_hist = {}; eqt_hist = {}; w_hist = {}

    for i, (date, prices) in enumerate(monthly.iterrows()):
        valid = {t: float(prices[t]) for t in TICKERS
                 if t in prices.index and not np.isnan(prices.get(t, np.nan)) and prices.get(t, 0) > 0}
        if len(valid) < 2:
            continue

        if i > 0:
            cash += MONTHLY_INVEST

        hold_val = sum(shares.get(t, 0) * p for t, p in valid.items())
        nav      = hold_val + cash
        if nav > peak_nav:
            peak_nav = nav
        pdd = nav / peak_nav - 1 if peak_nav > 0 else 0.0

        # 시장 지표
        hist = etf_px.loc[:date].iloc[-504:]  # 최대 2년
        vix_now = float(vix_s.loc[:date].iloc[-1]) if len(vix_s.loc[:date]) > 0 else np.nan
        spy_sub = spy_s.loc[:date].iloc[-252:]
        if len(spy_sub) >= 20:
            pk     = float(spy_sub.max())
            spy_dd = float(spy_sub.iloc[-1] / pk - 1) if pk > 0 else 0.0
        else:
            spy_dd = 0.0

        # 비중
        base_w = calc_weights(hist)
        base_w = base_w.reindex(list(valid.keys())).fillna(0)
        base_w = _norm(base_w)
        w      = apply_protection(base_w, spy_dd, vix_now, pdd)
        w      = apply_caps(w)
        w      = _norm(w.reindex(list(valid.keys())).fillna(0))

        # 리밸런싱 + 거래비용
        tx_cost = 0.0
        for t, p in valid.items():
            tgt    = int(float(w.get(t, 0)) * nav / p)
            delta  = tgt - shares.get(t, 0)
            tx_cost += abs(delta) * p * TRANSACTION_COST
            shares[t] = tgt

        hold_val = sum(shares.get(t, 0) * p for t, p in valid.items())
        cash     = max(nav - hold_val - tx_cost, 0)
        final_nav = hold_val + cash

        nav_hist[date] = final_nav
        dd_hist[date]  = pdd
        eqt_hist[date] = calc_eq_target(spy_dd, vix_now, pdd)
        w_hist[date]   = {t: float(w.get(t, 0)) for t in TICKERS}

    return (pd.Series(nav_hist), pd.Series(dd_hist),
            pd.Series(eqt_hist), pd.DataFrame(w_hist).T)


def run_buyhold(etf_px, ticker):
    monthly = etf_px.resample("ME").last()
    if ticker not in monthly.columns:
        return pd.Series(dtype=float)
    px     = monthly[ticker].dropna()
    shares = 0; cash = float(INITIAL_CAPITAL)
    nav_h  = {}
    for i, (date, p) in enumerate(px.items()):
        if i > 0:
            cash += MONTHLY_INVEST
        buy     = int(cash / p)
        shares += buy
        cash   -= buy * p
        nav_h[date] = shares * p + cash
    return pd.Series(nav_h)

# ─────────────────────────────────────────────
# 시각화
# ─────────────────────────────────────────────
def plot_all(nav_h, nav_b, dd_h, eqt_h, w_df):
    fig = plt.figure(figsize=(16, 22))
    fig.suptitle("HALO v4.0 — 백테스트 분석 리포트", fontsize=16, fontweight="bold", y=0.99)
    gs  = gridspec.GridSpec(4, 2, figure=fig, hspace=0.50, wspace=0.35)

    # ① 자산 성장
    ax1 = fig.add_subplot(gs[0, :])
    base1 = nav_h.iloc[0]
    (nav_h / base1 * 100).plot(ax=ax1, label="HALO v4.0", color="#2c3e50", lw=2.2)
    if len(nav_b) > 3:
        base2 = nav_b.iloc[0]
        aligned = (nav_b / base2 * 100).reindex(nav_h.index).ffill()
        aligned.plot(ax=ax1, label="KODEX S&P500 적립식", color="#e74c3c", lw=1.6, ls="--")
    ax1.set_title("누적 자산 성장 (시작 = 100)", fontweight="bold")
    ax1.legend(); ax1.grid(alpha=0.3); ax1.set_ylabel("성장 지수")

    # ② 드로우다운
    ax2 = fig.add_subplot(gs[1, :])
    (dd_h * 100).plot(ax=ax2, color="#c0392b", lw=1.5)
    ax2.fill_between(dd_h.index, dd_h * 100, 0, alpha=0.25, color="#c0392b")
    ax2.axhline(-8,  color="#e67e22", ls=":", lw=1.2, label="-8% 경계")
    ax2.axhline(-15, color="#c0392b", ls=":", lw=1.2, label="-15% 위험")
    mdd_val = dd_h.min() * 100
    mdd_date = dd_h.idxmin()
    ax2.annotate(f"MDD {mdd_val:.1f}%\n({mdd_date.strftime('%Y-%m')})",
                 xy=(mdd_date, mdd_val),
                 xytext=(mdd_date, mdd_val - 3),
                 fontsize=9, color="#c0392b",
                 arrowprops=dict(arrowstyle="->", color="#c0392b"))
    ax2.set_title("포트폴리오 드로우다운 (%)", fontweight="bold")
    ax2.legend(); ax2.grid(alpha=0.3); ax2.set_ylabel("DD (%)")

    # ③ 목표 주식 비중
    ax3 = fig.add_subplot(gs[2, 0])
    (eqt_h * 100).plot(ax=ax3, color="#2980b9", lw=1.5)
    ax3.fill_between(eqt_h.index, eqt_h * 100, 70, where=(eqt_h * 100 < 70),
                     alpha=0.2, color="#e74c3c", label="방어 구간")
    ax3.axhline(70, color="#27ae60", ls="--", lw=1, label="정상 70%")
    ax3.axhline(50, color="#e67e22", ls="--", lw=1, label="경계 50%")
    ax3.set_ylim(0, 80)
    ax3.set_title("목표 주식 비중 (%)", fontweight="bold")
    ax3.legend(fontsize=8); ax3.grid(alpha=0.3)

    # ④ 월별 수익률 분포
    ax4 = fig.add_subplot(gs[2, 1])
    mr  = nav_h.pct_change().dropna() * 100
    ax4.hist(mr, bins=30, color="#2c3e50", edgecolor="white", alpha=0.85)
    ax4.axvline(0,         color="red",    lw=1.5, label="0%")
    ax4.axvline(mr.mean(), color="#f39c12", lw=1.5, ls="--", label=f"평균 {mr.mean():.2f}%")
    ax4.set_title("월별 수익률 분포", fontweight="bold")
    ax4.set_xlabel("월수익률 (%)")
    ax4.legend(); ax4.grid(alpha=0.3)

    # ⑤ 평균 자산 배분 파이
    ax5 = fig.add_subplot(gs[3, 0])
    mw  = w_df.mean().reindex(TICKERS).fillna(0)
    mw  = mw[mw > 0]
    lbs = [FULL_NAME.get(t, t) for t in mw.index]
    clrs = ["#3498db", "#2ecc71", "#e67e22", "#e74c3c", "#95a5a6", "#f1c40f"][:len(mw)]
    ax5.pie(mw.values, labels=lbs, colors=clrs, autopct="%1.1f%%", startangle=90,
            textprops={"fontsize": 8})
    ax5.set_title("전략 평균 자산 배분", fontweight="bold")

    # ⑥ 연도별 수익률
    ax6 = fig.add_subplot(gs[3, 1])
    yr  = nav_h.resample("YE").last().pct_change().dropna() * 100
    clr = ["#27ae60" if v >= 0 else "#c0392b" for v in yr.values]
    bars = ax6.bar([str(d.year) for d in yr.index], yr.values, color=clr, edgecolor="white", width=0.6)
    for bar, v in zip(bars, yr.values):
        ypos = bar.get_height() + 0.4 if v >= 0 else bar.get_height() - 1.5
        va   = "bottom" if v >= 0 else "top"
        ax6.text(bar.get_x() + bar.get_width() / 2, ypos, f"{v:+.1f}%",
                 ha="center", va=va, fontsize=9, fontweight="bold")
    ax6.axhline(0, color="black", lw=0.8)
    ax6.set_title("연도별 수익률 (%)", fontweight="bold")
    ax6.grid(alpha=0.3, axis="y")

    plt.savefig("halo_backtest_report.png", dpi=150, bbox_inches="tight")
    plt.show()
    print("\n차트 저장 완료: halo_backtest_report.png")

# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def print_metrics_table(m_halo, m_bench):
    items = [
        ("CAGR",          "{:>+.2%}"),
        ("연간 변동성",    "{:>.2%}"),
        ("MDD",           "{:>+.2%}"),
        ("최장 MDD 회복",  "{:>.0f}개월"),
        ("Sharpe",        "{:>.2f}"),
        ("Sortino",       "{:>.2f}"),
        ("Calmar",        "{:>.2f}"),
        ("월 승률",        "{:>.1%}"),
        ("최고 월",        "{:>+.2%}"),
        ("최저 월",        "{:>+.2%}"),
        ("총 수익률",      "{:>+.2%}"),
    ]
    print("\n" + "═" * 62)
    print("  HALO v4.0  백테스트 성과 요약")
    print("═" * 62)
    print(f"  {'지표':<20} {'HALO v4.0':>16} {'S&P500 적립':>16}")
    print("─" * 62)
    for name, fmt in items:
        h = fmt.format(m_halo.get(name, 0))  if name in m_halo  else "N/A"
        b = fmt.format(m_bench.get(name, 0)) if name in m_bench else "N/A"
        print(f"  {name:<20} {h:>16} {b:>16}")
    print("═" * 62)


def main():
    etf_px, vix_s, spy_s = get_all_data()

    nav_halo, dd_halo, eqt_halo, w_df = run_backtest(etf_px, vix_s, spy_s)
    nav_bench = run_buyhold(etf_px, ETF["US"])

    if len(nav_halo) < 6:
        print("❌ 백테스트 기간 부족 — ETF 데이터를 확인하세요.")
        return

    m_halo  = calc_metrics(nav_halo,  "HALO v4.0")
    m_bench = calc_metrics(nav_bench, "S&P500 적립")

    print_metrics_table(m_halo, m_bench)

    # 연도별 수익률 텍스트
    print("\n  [ 연도별 HALO 수익률 ]")
    yr = nav_halo.resample("YE").last().pct_change().dropna()
    for d, r in yr.items():
        sign = "+" if r >= 0 else ""
        bar  = "█" * min(int(abs(r) * 120), 25)
        print(f"  {d.year}년  {sign}{r:.2%}  {bar}")

    # 드로우다운 구간 상위 3
    print("\n  [ 주요 드로우다운 구간 ]")
    in_dd = False; cnt = 0; events = []
    start_dd = None; peak_dd = 0
    for date, val in dd_halo.items():
        if val < 0 and not in_dd:
            in_dd = True; start_dd = date; peak_dd = val
        elif val < 0 and in_dd:
            if val < peak_dd:
                peak_dd = val
        elif val >= 0 and in_dd:
            events.append((peak_dd, start_dd, date))
            in_dd = False
    if in_dd:
        events.append((peak_dd, start_dd, dd_halo.index[-1]))
    events.sort()
    for peak, s, e in events[:3]:
        dur = (e - s).days // 30
        print(f"  {s.strftime('%Y-%m')} ~ {e.strftime('%Y-%m')}  "
              f"MDD {peak:.1%}  ({dur}개월)")

    # 차트
    try:
        plot_all(nav_halo, nav_bench, dd_halo, eqt_halo, w_df)
    except Exception as e:
        print(f"\n차트 생성 실패 (인터페이스 없음): {e}")


if __name__ == "__main__":
    main()
