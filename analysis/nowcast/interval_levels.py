"""예측구간 수준별 포함률·폭 점검 — nowcast.py의 주 모델(M 잔류 증가분)과 같은 정의.

무엇을: 명목 수준 L ∈ {25, 35, 50, 80, 95}%마다
  구간 = 학습 잔차(log 스케일)의 (1−L)/2, (1+L)/2 분위 (nowcast.py의 80% 구간과 같은 방식, 대칭 분위)
  → 평가 표본에서 포함률, 포함률의 95% 불확실성(Wilson / 공연 단위 부트스트랩), 평균·중앙 구간 폭(대), 상대 폭(폭/예측).
비교: B0 구간 = 학습 기간 문별 N의 같은 분위 (공연 정보 없이 '문별 분포'만으로 만든 구간).
      같은 포함률을 더 좁은 폭으로 내야 모델이 정보를 준 것이다.
검증: 시간순 1~6월 → 7~12월, 공연 LOSO (nowcast.py와 같은 분할).
입력: out/nowcast_dataset.csv (nowcast.py가 만든다)
출력: out/interval_levels.csv, out/interval_levels.txt
실행: python interval_levels.py  (1초 미만, 필요: pandas·numpy)
"""
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
GATES = ["남2문", "동2문", "북2문"]
LEVELS = [0.25, 0.35, 0.50, 0.80, 0.95]
X = "S_rel"
D = pd.read_csv(OUT / "nowcast_dataset.csv", encoding="utf-8-sig")
rng = np.random.default_rng(20261124)


def design(d):
    return np.column_stack([np.log(d[X])] + [(d.gate == g).astype(float) for g in GATES])


def intervals(tr, te, L):
    """주 모델 M과 B0의 L 수준 구간 (lo, hi, 점예측)."""
    a, b = (1 - L) / 2, (1 + L) / 2
    yy = np.log(tr.N + 1)
    beta, *_ = np.linalg.lstsq(design(tr), yy, rcond=None)
    res = yy - design(tr) @ beta
    qa, qb = np.quantile(res, [a, b])
    mu = design(te) @ beta
    M = (np.exp(mu + qa) - 1, np.exp(mu + qb) - 1, np.exp(mu) - 1)
    lo = te.gate.map(tr.groupby("gate").N.quantile(a)).values
    hi = te.gate.map(tr.groupby("gate").N.quantile(b)).values
    B0 = (lo, hi, te.gate.map(tr.groupby("gate").N.median()).values)
    return M, B0


def wilson(k, n, z=1.96):
    p = k / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den; h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def boot_show(hit, show, B=4000):
    """공연 단위 부트스트랩 — 같은 공연의 날·문은 독립이 아니므로 공연을 통째로 재표집."""
    shows = np.unique(show); idx = {s: np.where(show == s)[0] for s in shows}
    v = [hit[np.concatenate([idx[s] for s in rng.choice(shows, len(shows))])].mean() for _ in range(B)]
    return np.quantile(v, [.025, .975])


def evaluate(split_name, pairs):
    """pairs: [(tr, te)] — 각 평가 행의 구간을 모은 뒤 수준별로 집계."""
    rows = []
    for L in LEVELS:
        acc = {"M": [], "B0": []}; N = []; show = []
        for tr, te in pairs:
            M, B0 = intervals(tr, te, L)
            acc["M"].append(M); acc["B0"].append(B0); N.append(te.N.values); show.append(te.show.values)
        N = np.concatenate(N); show = np.concatenate(show)
        for k in ["M", "B0"]:
            lo = np.concatenate([x[0] for x in acc[k]]); hi = np.concatenate([x[1] for x in acc[k]])
            p = np.concatenate([x[2] for x in acc[k]])
            hit = ((N >= lo) & (N <= hi)).astype(float); n = len(hit)
            w = hi - lo; wl, wh = wilson(hit.sum(), n); bl, bh = boot_show(hit, show)
            rows.append(dict(검증=split_name, 모델=k, 명목=L, n=n, 공연수=len(np.unique(show)),
                             포함률=hit.mean(), Wilson_lo=wl, Wilson_hi=wh, 공연부트_lo=bl, 공연부트_hi=bh,
                             평균폭=w.mean(), 중앙폭=np.median(w), 상대폭_중앙=np.median(w / np.maximum(p, 1)),
                             아래이탈=(N < lo).mean(), 위이탈=(N > hi).mean()))
    return rows


tr = D[D.month <= 6]; te = D[D.month > 6]
R = evaluate("시간순 1~6→7~12월", [(tr, te)])
R += evaluate("공연 LOSO", [(D[D.show != s], D[D.show == s]) for s in D.show.unique()])
R = pd.DataFrame(R)
R.to_csv(OUT / "interval_levels.csv", index=False, encoding="utf-8-sig")

# 검산: 80% 포함률이 nowcast.py 결과(시간순 85%, LOSO 78%)와 같아야 한다
chk = R[(R.모델 == "M") & (R.명목 == 0.80)].set_index("검증").포함률
# 검산: 수준이 높을수록 폭은 단조 증가해야 한다
mono = R.groupby(["검증", "모델"]).평균폭.apply(lambda s: bool(np.all(np.diff(s.values) > 0)))

f = R.copy()
for c in ["명목", "포함률", "아래이탈", "위이탈", "상대폭_중앙"]:
    f[c] = (f[c] * 100).round(0).astype(int).astype(str) + "%"
f["Wilson 95%"] = [f"{a:.0%}~{b:.0%}" for a, b in zip(R.Wilson_lo, R.Wilson_hi)]
f["공연부트 95%"] = [f"{a:.0%}~{b:.0%}" for a, b in zip(R.공연부트_lo, R.공연부트_hi)]
f[["평균폭", "중앙폭"]] = f[["평균폭", "중앙폭"]].round(0).astype(int)
cols = ["검증", "모델", "명목", "n", "공연수", "포함률", "Wilson 95%", "공연부트 95%", "평균폭", "중앙폭", "상대폭_중앙", "아래이탈", "위이탈"]
txt = ["# 예측구간 수준별 포함률·폭 (Y1 문별 공연 차량 출차, 주 모델 M = 잔류 증가분 S_rel)",
       f.loc[:, cols].to_string(index=False), "",
       f"검산1 80% 포함률(nowcast.py와 일치해야 함): " + ", ".join(f"{k} {v:.0%}" for k, v in chk.items()),
       f"검산2 수준↑ → 평균폭 단조 증가: " + ", ".join(f"{a}/{b} {v}" for (a, b), v in mono.items())]
(OUT / "interval_levels.txt").write_text("\n".join(txt), encoding="utf-8")
print("\n".join(txt))
