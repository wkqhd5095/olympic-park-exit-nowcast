"""11/27~29 '미리 낸 예측'용 모델 고정. api_check.py와 같은 적합(2025 전체 94일)을 돌려 계수를 JSON으로 동결한다.
공연 전에 이 파일(해시 포함)을 팀 공유 문서에 남겨, 예측이 결과를 보고 바뀌지 않았음을 보인다.
출력: model_frozen.json   실행: python freeze_model.py (api_check.py를 한 번 실행함, 약 30초)"""
import hashlib, json, runpy
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
ns = runpy.run_path(str(HERE / "api_check.py"), run_name="freeze")
m = {"frozen_at_kst": f"{pd.Timestamp.now(tz='Asia/Seoul'):%Y-%m-%d %H:%M}", "data": "2025 입출차 재수집본 2,303,876건, 94공연일×3문",
     "gates": ns["GATES"], "lead_min": ns["LEAD"],
     "N_model": "log(N_g+1) = b*log(S_rel) + a_g ; S_rel = API(시작+30분) - API(당일 03:00)",
     "b": round(float(ns["bN"][0]), 6), "a": {g: round(float(ns["bN"][1 + k]), 6) for k, g in enumerate(ns["GATES"])},
     "resid_q10_q90": [round(float(v), 6) for v in ns["qN"]],
     "drop_model": "log(감소량) = c0 + c1*log(3문 N 합) ; 감소량 = 종료 −30~0분 최대 − 종료 0~240분 최소 (API 3문 합계)",
     "c": [round(float(v), 6) for v in ns["bd"]], "drop_resid_q10_q90": [round(float(v), 6) for v in ns["qd"]]}
s = json.dumps(m, ensure_ascii=False, indent=1)
m["sha256_of_params"] = hashlib.sha256(json.dumps({k: m[k] for k in ["b", "a", "resid_q10_q90", "c", "drop_resid_q10_q90"]}, sort_keys=True).encode()).hexdigest()[:16]
(HERE / "model_frozen.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(m, ensure_ascii=False, indent=1))
