"""기획서 2-2 '공연 차량 정의' 민감도: 입차 구간(시작 전 W분)을 180·240(기본)·300분으로 바꿔 핵심 결과가 유지되는지.
정의(prep.py A안과 동일): 입차 ∈ [시작−W, 시작+30], 출차 ≥ 예정종료−30, 출차 창 종료 −60~+239분, 주요 3문.
보는 결과: ① 공연 차량 수 N 변화 ② 나우캐스트(S_rel) vs 문별 중앙값 B0 MAE 감소율(시간순 1~6→7~12월)
            ③ 피크~N 로그 기울기 β (줄 판정, queue_tests와 같은 함수) ④ 80% 구간 포함률
검산: W=240에서 N이 sim/out/show_exits.csv 합과 일치해야 함.  실행: python sensitivity_window.py (약 1분)"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]; SIM = ROOT / "05_개발" / "sim"
sys.path.insert(0, str(ROOT / "04_분석" / "줄판정검증" / "줄판정검증")); import queue_tests as qt  # noqa: E402
G = ["남2문", "동2문", "북2문"]
df = pd.read_pickle(SIM / "cache" / "cpm008_clean.pkl"); df = df[~df.bad_order & df.t_out.notna()]
df = df[df.out_gate.astype(str).isin(G)].assign(gate=lambda d: d.out_gate.astype(str))
fit = pd.read_csv(SIM / "out" / "fit_results.csv", encoding="utf-8-sig"); cal = pd.read_csv(SIM / "out" / "calendar_used.csv", encoding="utf-8-sig")
D = pd.read_csv(HERE / "out" / "nowcast_dataset.csv", encoding="utf-8-sig")
days = fit.drop_duplicates("date").merge(cal[["date", "last_start"]], on="date")
days = days[days.date.isin(set(D.date))]
team = pd.read_csv(SIM / "out" / "show_exits.csv").groupby(["date", "gate"]).show_exits.sum()

def curves(W):
    rows = []
    for r in days.itertuples():
        d0 = pd.Timestamp(r.date); en = d0 + pd.Timedelta(r.end_est + ":00"); st = d0 + pd.Timedelta(r.last_start + ":00")
        w = df[(df.t_out >= en - pd.Timedelta(minutes=60)) & (df.t_out < en + pd.Timedelta(minutes=240))]
        w = w[(w.t_in >= st - pd.Timedelta(minutes=W)) & (w.t_in <= st + pd.Timedelta(minutes=30)) & (w.t_out >= en - pd.Timedelta(minutes=30))]
        rel = ((w.t_out - en).dt.total_seconds() // 60).astype(int) + 60
        for g in G:
            x = np.bincount(rel[w.gate == g], minlength=300)[:300]
            m = qt.curve_metrics(x); rows.append({"date": r.date, "gate": g, "show_id": r.show_name, "N": int(x.sum()), **m})
    return pd.DataFrame(rows)

def nowcast(C):
    X = D[["date", "gate", "month", "S_rel"]].merge(C[["date", "gate", "N"]], on=["date", "gate"])
    tr, te = X[X.month <= 6], X[X.month > 6]
    A = lambda d: np.column_stack([np.log(d.S_rel)] + [(d.gate == g).astype(float) for g in G])
    b, *_ = np.linalg.lstsq(A(tr), np.log(tr.N + 1), rcond=None); q1, q9 = np.quantile(np.log(tr.N + 1) - A(tr) @ b, [.1, .9])
    mu = A(te) @ b; p = np.exp(mu) - 1
    e0 = np.abs(te.N - te.gate.map(tr.groupby("gate").N.median())).mean(); eM = np.abs(te.N - p).mean()
    cov = ((te.N >= np.exp(mu + q1) - 1) & (te.N <= np.exp(mu + q9) - 1)).mean()
    return e0, eM, cov

out = []; base = None
for W in (180, 240, 300):
    C = curves(W); N = C.set_index(["date", "gate"]).N
    if W == 240:
        chk = (N - team.reindex(N.index)).abs().max(); print(f"검산 W=240 vs show_exits.csv: 최대 차이 {chk}대")
        base = N
    e0, eM, cov = nowcast(C); b = qt.slope_beta(C[C.N > 0], B=1000)
    out.append({"입차구간(시작 전 분)": W, "N 중앙": N.median(), "N 합": int(N.sum()),
                "B0 MAE": round(e0, 1), "M MAE": round(eM, 1), "감소율": f"{1 - eM / e0:.0%}", "80%포함": f"{cov:.0%}",
                "β 피크~N": f"{b['beta']:.2f} [{b['lo']:.2f}, {b['hi']:.2f}]"})
R = pd.DataFrame(out); R["N 합/기본"] = (R["N 합"] / R.loc[R["입차구간(시작 전 분)"] == 240, "N 합"].iloc[0]).round(3)
print(R.to_string(index=False)); R.to_csv(HERE / "out" / "sensitivity_window.csv", index=False, encoding="utf-8-sig")
