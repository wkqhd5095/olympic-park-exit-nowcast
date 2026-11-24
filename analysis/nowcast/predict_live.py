"""공연 당일 '미리 낸 예측' 기록 (11/27~29 조용필 등).
- 입력: GitHub 로거(parking-data 브랜치)의 당일 CSV를 직접 읽음 → 로컬 복사본이 오래돼도 됨
- 모델: model_frozen.json (11/24 동결, 결과 보기 전 고정) — 계수를 여기서 다시 적합하지 않는다
- predict: 시작+30분 값과 03:00 값으로 S_rel → 문별 공연 차량 N(80% 구간) + 3문 합 주차장 감소량(80% 구간)
- score : 종료+240분이 지난 뒤 관측 감소량을 채워 80% 구간 포함 여부를 기록 (문별 N은 API가 합계뿐이라 채점 불가)
출력: out/live_predictions.csv (한 공연일 1행, 기존 행 덮어쓰지 않음)
실행: python predict_live.py predict 2026-11-27   /   python predict_live.py score 2026-11-27"""
import io, json, sys, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; OUT = HERE / "out" / "live_predictions.csv"
RAW = "https://raw.githubusercontent.com/wkqhd5095/kspo-parking-logger/parking-data/data/parking/{}.csv"
M = json.loads((HERE / "model_frozen.json").read_text(encoding="utf-8"))
shows = pd.read_csv(HERE / "input" / "공연일_2026-11.csv", encoding="utf-8").set_index("date")

def api_day(d):
    with urllib.request.urlopen(RAW.format(d), timeout=30) as r: a = pd.read_csv(io.BytesIO(r.read()))
    a = a[a.parking_sum.notna() & a.error.isna()]; a["t"] = pd.to_datetime(a.fetched_at_kst)
    return a.sort_values("t").set_index("t").parking_sum.astype(float)

def at(api, T, tol):
    i = api.index.get_indexer([T], method="nearest")[0]; t = api.index[i]
    return (float(api.iloc[i]), t) if abs((t - T).total_seconds()) <= tol * 60 else (np.nan, None)

def predict(d):
    s = shows.loc[d]; day = pd.Timestamp(d); st = day + pd.Timedelta(s.start + ":00")
    en = st + pd.Timedelta(minutes=int(s.runtime_min)); api = api_day(d)
    v0, t0 = at(api, st + pd.Timedelta(minutes=M["lead_min"]), 10); v3, t3 = at(api, day + pd.Timedelta(hours=3), 60)
    if np.isnan(v0) or np.isnan(v3): sys.exit(f"입력 없음: +30분={t0}, 03시={t3} — 로거 커밋을 몇 분 뒤 다시 확인")
    x = np.log(v0 - v3); q1, q9 = M["resid_q10_q90"]; row = {"date": d, "show": s.show, "start": s.start, "end_est": f"{en:%H:%M}",
        "computed_at_kst": f"{pd.Timestamp.now(tz='Asia/Seoul'):%Y-%m-%d %H:%M:%S}", "model_sha": M["sha256_of_params"],
        "api_03_at": f"{t3:%H:%M:%S}", "api_03": v3, "api_t0_at": f"{t0:%H:%M:%S}", "api_t0": v0, "S_rel": v0 - v3}
    N3 = 0
    for g in M["gates"]:
        mu = M["b"] * x + M["a"][g]; row[f"N_{g}"] = round(np.exp(mu) - 1)
        row[f"N_{g}_80"] = f"{np.exp(mu + q1) - 1:.0f}~{np.exp(mu + q9) - 1:.0f}"; N3 += np.exp(mu) - 1
    md = M["c"][0] + M["c"][1] * np.log(N3); d1, d9 = M["drop_resid_q10_q90"]
    row.update(N3_pred=round(N3), drop_pred=round(np.exp(md)), drop_lo80=round(np.exp(md + d1)), drop_hi80=round(np.exp(md + d9)),
               drop_obs=np.nan, drop_in80="", scored_at_kst="")
    P = pd.read_csv(OUT, encoding="utf-8-sig") if OUT.exists() else pd.DataFrame()
    if len(P) and d in set(P.date.astype(str)): sys.exit(f"{d} 예측이 이미 기록돼 있음 — 덮어쓰지 않음")
    pd.concat([P, pd.DataFrame([row])], ignore_index=True).to_csv(OUT, index=False, encoding="utf-8-sig")
    print(pd.Series(row).to_string())

def score(d):
    P = pd.read_csv(OUT, encoding="utf-8-sig", dtype={"drop_in80": object, "scored_at_kst": object}); i = P.index[P.date == d][0]; r = P.loc[i]
    en = pd.Timestamp(d) + pd.Timedelta(r.end_est + ":00")
    api = pd.concat([api_day(d), api_day(f"{pd.Timestamp(d) + pd.Timedelta(days=1):%Y-%m-%d}")])
    if api.index.max() < en + pd.Timedelta(minutes=240): sys.exit("종료+240분 데이터가 아직 없음")
    pre = np.nanmax([at(api, en + pd.Timedelta(minutes=m), 5)[0] for m in range(-30, 1, 5)])
    post = np.nanmin([at(api, en + pd.Timedelta(minutes=m), 5)[0] for m in range(0, 241, 5)])
    P.loc[i, "drop_obs"] = pre - post; P.loc[i, "drop_in80"] = bool(r.drop_lo80 <= pre - post <= r.drop_hi80)
    P.loc[i, "scored_at_kst"] = f"{pd.Timestamp.now(tz='Asia/Seoul'):%Y-%m-%d %H:%M:%S}"
    P.to_csv(OUT, index=False, encoding="utf-8-sig"); print(P.loc[i].to_string())

{"predict": predict, "score": score}[sys.argv[1]](sys.argv[2])
