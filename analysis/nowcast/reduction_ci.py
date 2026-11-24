"""기획서 3-2 성능표용: 나우캐스트 M(S_rel)의 MAE 감소율과 공연 단위 부트스트랩 95% 구간.
왜: 연속 공연일·같은 날 3문은 독립이 아니므로 '공연'을 재표집 단위로 쓴다(기획서 3-2 서술과 일치).
입력: out/nowcast_dataset.csv (nowcast.py 산출) · 모델 정의는 nowcast.py와 동일(문 고정효과 로그선형, B0 = 학습기간 문별 중앙값)
출력: out/reduction_ci.txt   실행: python reduction_ci.py (수 초)"""
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; OUT = HERE / "out"
D = pd.read_csv(OUT / "nowcast_dataset.csv", encoding="utf-8-sig")
GATES = ["남2문", "동2문", "북2문"]; TRAIN = range(1, 7); B = 5000; rng = np.random.default_rng(0)

def fit(tr):
    X = np.column_stack([np.log(tr.S_rel)] + [(tr.gate == g).astype(float) for g in GATES])
    b, *_ = np.linalg.lstsq(X, np.log(tr.N + 1), rcond=None); return b
def pred(b, te):
    X = np.column_stack([np.log(te.S_rel)] + [(te.gate == g).astype(float) for g in GATES]); return np.exp(X @ b) - 1

def errors(tr, te):
    return np.abs(te.N.values - te.gate.map(tr.groupby("gate").N.median()).values), np.abs(te.N.values - pred(fit(tr), te))

# 1) 시간순: 1~6월 학습 → 7~12월 평가
tr, te = D[D.month.isin(TRAIN)], D[~D.month.isin(TRAIN)].reset_index(drop=True)
e0, eM = errors(tr, te)
# 2) 공연 LOSO
L0, LM = np.empty(len(D)), np.empty(len(D))
for s in D.show.unique():
    m = (D.show == s).values; a, b_ = errors(D[~m], D[m]); L0[m], LM[m] = a, b_

def boot(df, e0, eM):
    shows = df.show.unique(); idx = {s: np.flatnonzero(df.show.values == s) for s in shows}; r = []
    for _ in range(B):
        ii = np.concatenate([idx[s] for s in rng.choice(shows, len(shows))]); r.append(1 - eM[ii].mean() / e0[ii].mean())
    return np.percentile(r, [2.5, 97.5])

lines = []
for lab, df, a, b_ in [("시간순 1~6→7~12월", te, e0, eM), ("공연 LOSO", D, L0, LM)]:
    lo, hi = boot(df, a, b_)
    lines.append(f"{lab}: 곡선 {len(df)} · 공연 {df.show.nunique()} | B0 MAE {a.mean():.1f} · M MAE {b_.mean():.1f} | "
                 f"감소 {1 - b_.mean() / a.mean():.1%} [95% {lo:.0%}, {hi:.0%}]")
lines.append("검산: MAE 값이 nowcast_eval.csv(B0·M 잔류 증가분)와 일치해야 함")
print("\n".join(lines)); (OUT / "reduction_ci.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
