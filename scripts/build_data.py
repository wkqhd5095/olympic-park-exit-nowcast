"""웹 화면용 데이터(JSON) 생성. 팀 분석 폴더의 산출물을 읽어 docs/data/ 에 쓴다.
입력(팀 폴더 기준 상대경로):
  04_분석/출차나우캐스트/model_frozen.json        동결 모델(계수·80% 잔차 분위)
  04_분석/출차나우캐스트/out/nowcast_dataset.csv  2025 공연일×문 (N, S_rel, 피크 시각)
  04_분석/출차나우캐스트/out/nowcast_pred_test.csv 2025 7~12월 시간순 검증 예측
  04_분석/출차나우캐스트/input/공연일_2026-11.csv  2026 공연 일정(시작·러닝타임)
  04_분석/줄판정검증/줄판정검증/out/포화사례_곡선별.csv  공연 규모별 포화 신호
실행: python scripts/build_data.py [팀폴더 경로]   (기본: 이 저장소의 두 단계 위)
갱신: 해마다 입출차 데이터로 분석을 다시 돌린 뒤 이 스크립트만 다시 실행하면 화면 기준이 바뀐다."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parents[1]
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1]
NC = ROOT / "04_분석" / "출차나우캐스트"; SAT = ROOT / "04_분석" / "줄판정검증" / "줄판정검증" / "out"
OUT = HERE / "docs" / "data"; OUT.mkdir(parents=True, exist_ok=True)
G = ["남2문", "동2문", "북2문"]
M = json.loads((NC / "model_frozen.json").read_text(encoding="utf-8"))
D = pd.read_csv(NC / "out" / "nowcast_dataset.csv", encoding="utf-8-sig")
S = pd.read_csv(SAT / "포화사례_곡선별.csv", encoding="utf-8-sig")
bins = [0, 200, 300, 400, 500, 10**6]; labels = ["200대 미만", "200~299대", "300~399대", "400~499대", "500대 이상"]
S["bin"] = pd.cut(S.N, bins, labels=labels, right=False)
sat = [{"label": l, "lo": bins[i], "hi": bins[i + 1], "curves": int((S.bin == l).sum()),
        "signal": int(((S.bin == l) & (S.포화신호 != "없음")).sum())} for i, l in enumerate(labels)]
gates = {}
for g in G:
    d = D[D.gate == g]
    gates[g] = {"N_median": float(d.N.median()), "N_p90": float(d.N.quantile(.9)), "N_max": int(d.N.max()),
                "peak_min_q": [float(v) for v in d.t_peak.quantile([.25, .5, .75])], "curves": int(len(d))}
model = {k: M[k] for k in ["frozen_at_kst", "b", "a", "resid_q10_q90", "lead_min", "sha256_of_params", "data"]}
model["S_rel_train_range"] = [int(D.S_rel.min()), int(D.S_rel.max())]
model["metrics"] = {"time_split": {"B0": 98.1, "M": 62.6, "reduction": 0.36, "ci95": [0.15, 0.48], "cover80": 0.85, "shows": 10, "curves": 81},
                    "loso": {"B0": 95.4, "M": 70.3, "reduction": 0.26, "ci95": [0.14, 0.37], "cover80": 0.78, "shows": 41, "curves": 282}}
T = pd.read_csv(NC / "out" / "nowcast_pred_test.csv", encoding="utf-8-sig")
B0 = D[D.month <= 6].groupby("gate").N.median()
back = [{"date": r.date, "show": r.show, "gate": r.gate, "S_rel": int(r.S_rel), "N": int(r.N), "pred": int(r.pred_M),
         "lo": int(r.lo80), "hi": int(r.hi80), "b0": float(B0[r.gate])} for r in T.itertuples()]
sh = pd.read_csv(NC / "input" / "공연일_2026-11.csv", encoding="utf-8")
shows = [{"date": r.date, "show": r.show, "venue": r.venue, "start": r.start, "runtime_min": int(r.runtime_min),
          "note": r.note if isinstance(r.note, str) else ""} for r in sh.itertuples()]
for name, obj in [("model", model), ("gates", gates), ("saturation", sat), ("backtest", back), ("shows", shows)]:
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
# 검산: 백테스트 MAE가 검증표와 같아야 함
e = np.mean([abs(r["N"] - r["pred"]) for r in back]); e0 = np.mean([abs(r["N"] - r["b0"]) for r in back])
print(f"백테스트 {len(back)}곡선 MAE M {e:.1f} (검증표 62.6, 반올림 예측이라 ±0.5) / B0 {e0:.1f} (98.1)")
print({g: (v["N_p90"], v["peak_min_q"]) for g, v in gates.items()}); print(sat)
