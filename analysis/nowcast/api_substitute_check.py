"""입력 대체 가능성 점검(같은 해 2025): 기관이 집계한 주차대수(연계 데이터 15164822, 시간 단위)로 S_rel을 만들어
동결 모델에 넣었을 때, 입출차 기록으로 재구성한 S_rel과 비교해 오차가 얼마나 늘어나는가.
한계: 연계 데이터는 2025-06-14~ 시간 단위(시작+30분은 앞뒤 정시 선형보간), 재구성 쪽은 학습에 쓴 날이라 유리(표본 내).
실행: python api_substitute_check.py → out/api_substitute_check.txt"""
from pathlib import Path
import json, numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
c = pd.read_csv(ROOT / "03_데이터" / "processed" / "kcisa_cpm009.csv", encoding="utf-8-sig", dtype=str)
c["t"] = pd.to_datetime(c.ISSUED_DATE); c["p"] = pd.to_numeric(c["desc_주차대수"], errors="coerce"); h = c.groupby("t").p.first().dropna()
D = pd.read_csv(HERE / "out" / "nowcast_dataset.csv", encoding="utf-8-sig"); M = json.loads((HERE / "model_frozen.json").read_text(encoding="utf-8"))
rows = []
for x in D.drop_duplicates("date").itertuples():
    t0 = pd.Timestamp(x.date) + pd.Timedelta(x.start + ":00") + pd.Timedelta(minutes=30)
    a, b, v3 = h.get(t0.floor("h")), h.get(t0.ceil("h")), h.get(pd.Timestamp(x.date) + pd.Timedelta(hours=3))
    if None in (a, b, v3): continue
    rows.append((x.date, a + (b - a) * (t0 - t0.floor("h")).seconds / 3600 - v3))
X = D.merge(pd.DataFrame(rows, columns=["date", "c9_rel"]), on="date")
mu = lambda s: M["b"] * np.log(s) + np.array([M["a"][g] for g in X.gate])
e_rec = np.abs(X.N - (np.exp(mu(X.S_rel)) - 1)).mean(); e_c9 = np.abs(X.N - (np.exp(mu(X.c9_rel)) - 1)).mean()
q1, q9 = M["resid_q10_q90"]; cov = ((X.N >= np.exp(mu(X.c9_rel) + q1) - 1) & (X.N <= np.exp(mu(X.c9_rel) + q9) - 1)).mean()
d = X.drop_duplicates("date")
txt = (f"공연일 {d.shape[0]}일·{len(X)}곡선 ({X.date.min()}~{X.date.max()})\n"
       f"증가분 상관(기관 vs 재구성) {np.corrcoef(d.c9_rel, d.S_rel)[0, 1]:.3f}, 비 중앙 {(d.c9_rel / d.S_rel).median():.3f}\n"
       f"동결 모델 MAE: 재구성 입력 {e_rec:.1f}대 / 기관 주차대수 입력 {e_c9:.1f}대, 기관 입력 80% 포함 {cov:.0%}\n")
print(txt); (HERE / "out" / "api_substitute_check.txt").write_text(txt, encoding="utf-8")
