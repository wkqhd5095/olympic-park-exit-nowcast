"""줄 판정 검증(김성주, 11/23)의 핵심 수치를 '재수집 데이터'(2,303,876건)로 다시 계산한다.

왜: 11/23 검증은 재수집 전 원본(2,024,751건)으로 show_exits를 재구성해 THIN=0.885 보정을 썼다.
    지금 팀 파이프라인(05_개발/sim/out/show_exits.csv, cache/cpm008_clean.pkl)은 이미 재수집본이므로
    보정 없이(THIN=1) 같은 함수(queue_tests.py)로 다시 돌려 β·분할회귀·god 차로 간격을 갱신한다.
입력: 05_개발/sim/out/{show_exits,fit_results}.csv, 05_개발/sim/cache/cpm008_clean.pkl
출력: out/t4_recheck.txt, out/t4_metrics.csv, out/top_headway.csv
실행: python t4_recheck.py   (약 1~2분)
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SIM = ROOT / "05_개발" / "sim"
sys.path.insert(0, str(ROOT / "04_분석" / "줄판정검증" / "줄판정검증"))
import queue_tests as qt                      # noqa: E402  (김성주 검정 함수 그대로 사용)
from run_synth_rules import flat_slope        # noqa: E402

OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
se = pd.read_csv(SIM / "out" / "show_exits.csv")
fit = pd.read_csv(SIM / "out" / "fit_results.csv", encoding="utf-8-sig")
cal = pd.read_csv(SIM / "out" / "calendar_used.csv", encoding="utf-8-sig")
# 11/23 검증과 같은 표본: 수집 온전 공연일(fit_results) 중 시작 시각 있는 날 (GMF 10/18~19 제외 → 94일)
keep = set(fit.date) & set(cal.loc[cal.last_start.notna(), "date"])
fit = fit[fit.date.isin(keep)]; se = se[se.date.isin(keep)]
show_id = fit.drop_duplicates("date").set_index("date")["show_name"]

rows = []
for (dt, g), c in se.groupby(["date", "gate"]):
    c = c.sort_values("rel_min")
    tot10 = pd.Series(c.exits.values.astype(float)).rolling(10, center=True).mean().max()
    ma15 = pd.Series(c.show_exits.values.astype(float)).rolling(15, center=True).mean(); p15 = ma15.max()
    flat75 = (ma15 >= .75 * p15).sum() / max((ma15 >= .5 * p15).sum(), 1)
    rows.append({"date": dt, "gate": g, "show_id": show_id[dt], "N": int(c.show_exits.sum()), "tot_peak10": tot10,
                 "flat75": flat75, **qt.curve_metrics(c.show_exits.values)})
M = pd.DataFrame(rows).merge(fit[["date", "gate", "queue", "mu", "N", "end_est"]].rename(columns={"N": "N_team"}),
                             on=["date", "gate"])
M = M[M.N > 0].reset_index(drop=True)

b = qt.slope_beta(M, B=2000); h = qt.hinge_fit(M, B=1000); f = flat_slope(M, B=1000)
bt = qt.slope_beta(M, y="T_high", B=1000)
q = M[M.queue].copy(); q["obs_over_mu"] = q.tot_peak10 / q.mu

# 최대 공연 상위 8곡선: 고부하 구간 차로 간격(초) — 차단기 포화 의심 재확인
df = pd.read_pickle(SIM / "cache" / "cpm008_clean.pkl")
df = df[~df.bad_order & df.t_out.notna()]
top = M.sort_values("N_team", ascending=False).head(8)
hw = []
for _, r in top.iterrows():
    c = se[(se.date == r.date) & (se.gate == r.gate)].sort_values("rel_min")
    ma = c.exits.rolling(10, center=True).mean().values
    win = c.rel_min.values[ma >= .8 * np.nanmax(ma)]
    en = pd.Timestamp(r.date) + pd.Timedelta(r.end_est + ":00")
    lo, hi = en + pd.Timedelta(minutes=int(win.min())), en + pd.Timedelta(minutes=int(win.max()))
    cc = df[(df.out_gate.astype(str) == r.gate) & (df.t_out >= lo) & (df.t_out < hi)]
    g = {}
    for L in ["1", "2"]:
        t = np.sort(cc.t_out[cc.out_lane.astype(str) == L].values.astype("datetime64[s]").astype(np.int64))
        d = np.diff(t); d = d[d > 0]
        g[L] = float(np.median(d)) if len(d) else np.nan
    flat_min = int((pd.Series(c.exits.values.astype(float)).rolling(15, center=True).mean() >= .9 * np.nanmax(ma)).sum())
    hw.append({"date": r.date, "gate": r.gate, "show": r.show_id[:18], "N_team": int(r.N_team),
               "고부하구간(분)": f"{win.min()}~{win.max()}", "문피크10분(대/분)": round(r.tot_peak10, 1),
               "피크90%유지(분,15분평균)": flat_min, "차로1간격중앙(초)": g["1"], "차로2간격중앙(초)": g["2"],
               "flat75": round(r.flat75, 2)})
HW = pd.DataFrame(hw)

M["N분위"] = pd.qcut(M.N, 5, labels=["1(하)", "2", "3", "4", "5(상)"])
quint = M.groupby("N분위", observed=True).agg(N중앙=("N", "median"), 피크중앙=("r_peak", "median"),
                                             고부하분=("T_high", "median")).round(2)
lines = [
    "# T4 재계산 — 재수집 데이터(2,303,876건), THIN=1.0",
    f"곡선 {len(M)} (N>0) · 공연 클러스터 {M.show_id.nunique()} · 줄 판정 {int(M.queue.sum())}",
    f"β 피크~N        = {b['beta']:.2f} [{b['lo']:.2f}, {b['hi']:.2f}]   (11/23 재수집 전: 0.77 [0.65, 0.89])",
    f"β 고부하시간~N  = {bt['beta']:.2f} [{bt['lo']:.2f}, {bt['hi']:.2f}]   (11/23: 0.38 [0.22, 0.52])",
    f"분할회귀: 꺾임 N {h['kink_N']:.0f} [{h['kinkN_lo']:.0f}, {h['kinkN_hi']:.0f}], b2 {h['b2']:.2f} [{h['b2_lo']:.2f}, {h['b2_hi']:.2f}], SSE비 {h['sse_ratio']:.3f}   (11/23: SSE비 0.99)",
    f"  → 용량 제한 규칙(b2 95% 상한<0.3 그리고 SSE비<0.8): {'충족' if (h['b2_hi'] < .3 and h['sse_ratio'] < .8) else '불충족 = 천장 없음'}",
    f"평탄도 기울기   = {f[0]:.3f} [{f[1]:.3f}, {f[2]:.3f}]",
    f"T0 용량 일관성: 줄 판정 {len(q)}곡선 중 관측 10분 피크 > 1.1μ 비율 {(q.obs_over_mu > 1.1).mean():.2f}, 중앙 {q.obs_over_mu.median():.2f}배",
    "", "## N 5분위", quint.to_string(), "", "## 최대 공연 상위 8곡선 차로 간격", HW.to_string(index=False),
]
(OUT / "t4_recheck.txt").write_text("\n".join(lines), encoding="utf-8")
M.drop(columns="N분위").to_csv(OUT / "t4_metrics.csv", index=False, encoding="utf-8-sig")
HW.to_csv(OUT / "top_headway.csv", index=False, encoding="utf-8-sig")
print("\n".join(lines))
