"""판정 규칙 후보의 오판율을 가상 데이터로 측정 (세계 4종 × 시드 반복)."""
import numpy as np, pandas as pd, sys
import synth_cars as sc, queue_tests as qt

WORLDS = {"분산": dict(world="disperse"), "분산+규모비례퍼짐": dict(world="disperse_scale"),
          "용량 μ=5": dict(world="capacity", mu=5.0), "혼합 μ=6": dict(world="mixed", mu=6.0)}
TRUTH = {"분산": "반박", "분산+규모비례퍼짐": "반박", "용량 μ=5": "확인", "혼합 μ=6": "확인"}
SEEDS = int(sys.argv[1]) if (__name__ == "__main__" and len(sys.argv) > 1) else 12

def metrics(rows):
    return pd.DataFrame([{"show_id": r["show_id"], "gate": r["gate"], "N": r["N"],
                          **qt.curve_metrics(r["show_exits"]), **qt.drop_metric(r["exits"])} for r in rows])

def flat_slope(df, B=200, seed=0):
    d = df.dropna(subset=["flat"]).reset_index(drop=True)
    X = qt.design(d, np.log(d["N"].values)); b0 = qt._ols(d["flat"].values, X)[1]
    rng = np.random.default_rng(seed); ids = d.show_id.unique(); g = {k: np.flatnonzero(d.show_id.values == k) for k in ids}
    bs = []
    for _ in range(B):
        dd = d.iloc[np.concatenate([g[k] for k in rng.choice(ids, len(ids))])]
        if dd.gate.nunique() < 3: continue
        bs.append(qt._ols(dd["flat"].values, qt.design(dd, np.log(dd["N"].values)))[1])
    return b0, *np.percentile(bs, [2.5, 97.5])

def main():
    global R
    rows_out = []
    for name, kw in WORLDS.items():
        for s in range(SEEDS):
            df = metrics(sc.dataset(n_show=40, seed=100 + s, scale_rng=(15, 25), **kw))
            b = qt.slope_beta(df, B=200, seed=s)
            h = qt.hinge_fit(df, B=150, seed=s)
            f = flat_slope(df, seed=s)
            r_old = "확인" if (b["lo"] <= 0 <= b["hi"]) else ("반박" if b["lo"] <= 1 <= b["hi"] else "불확실")
            r_hinge = "확인" if (h["b2_hi"] < 0.3 and h["sse_ratio"] < 0.8) else "반박"
            r_flat = "확인" if f[1] > 0 else "반박"
            r_both = "확인" if (r_hinge == "확인" and r_flat == "확인") else ("반박" if (r_hinge == "반박" and r_flat == "반박") else "불확실")
            rows_out.append({"세계": name, "seed": s, "β": b["beta"], "b1": h["b1"], "b2": h["b2"], "b2_hi": h["b2_hi"],
                             "kinkN": h["kink_N"], "SSE비": h["sse_ratio"], "평탄도기울기": f[0], "평탄도_lo": f[1],
                             "규칙_가이드초안": r_old, "규칙_분할": r_hinge, "규칙_평탄도": r_flat, "규칙_분할+평탄도": r_both,
                             "정답": TRUTH[name]})
    R = pd.DataFrame(rows_out); R.to_csv("out/synth_rule_eval.csv", index=False, encoding="utf-8-sig")
    summ = R.groupby("세계", sort=False).agg(β=("β", "median"), b2=("b2", "median"), SSE비=("SSE비", "median"),
                                              꺾임N=("kinkN", "median"), 평탄도기울기=("평탄도기울기", "median")).round(2)
    for rule in ["규칙_가이드초안", "규칙_분할", "규칙_평탄도", "규칙_분할+평탄도"]:
        summ[rule + " 정답률"] = R.assign(ok=R[rule] == R["정답"]).groupby("세계", sort=False).ok.mean().round(2)
    print(summ.T.to_string())


if __name__ == "__main__":
    main()
