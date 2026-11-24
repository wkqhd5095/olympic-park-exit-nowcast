"""실시간 주차현황 API(로거) ↔ 2025 재구성 잔류 차량 대조, 그리고 2026 공연일 사후 점검.

왜: 나우캐스트는 2025 입출차 기록으로 다시 계산한 '잔류 차량'으로 학습했다. 운영 때는 기관 실시간 API(parking_sum)를 쓴다.
    두 값의 눈금이 같은지 확인하고, 다르면 어떻게 맞출지 정한다. 그다음 로거가 기록한 2026 공연일에 예측을 돌려 본다.
무엇을
  1) 눈금 대조: 공연 없는 평일 03:00·10:55·19:30의 2025 재구성값 vs 2026 API값(연도가 달라 '같은 시각대 분포' 비교)
  2) 2026 공연일 예측: S_rel = API(시작+30분) − API(03:00) → 문별 공연 차량 N (2025 전체로 적합, 80% 구간)
  3) 사후 점검: API로 관측 가능한 건 3문 합계뿐 → '종료 후 주차장 감소량'(종료 −30~0분 최대 − 종료 0~240분 최소)을
     2025에서 학습한 관계(감소량 ~ 3문 공연 차량 합)로 예측해 실제 API 감소량과 비교. 문별 배분은 검증 불가.
입력: 로거 CSV 폴더(LOGGER_DIR, 기본 05_개발/kspo-parking-logger/data/parking — parking-data 브랜치의 data/parking),
      input/공연일_2026-11.csv, out/nowcast_dataset.csv(nowcast.py 결과), 05_개발/sim/cache/cpm008_clean.pkl
출력: out/api_check.txt, out/api_2026_pred.csv
실행: python api_check.py
"""
import os, glob
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SIM = ROOT / "05_개발" / "sim"
LOGGER_DIR = Path(os.environ.get("LOGGER_DIR", ROOT / "05_개발" / "kspo-parking-logger" / "data" / "parking"))
OUT = HERE / "out"
GATES = ["남2문", "동2문", "북2문"]
LEAD = 30

# ── 데이터 ──
api = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(LOGGER_DIR / "*.csv")))], ignore_index=True)
api["t"] = pd.to_datetime(api.fetched_at_kst)
api = api[api.parking_sum.notna() & api.error.isna()].sort_values("t").set_index("t").parking_sum.astype(float)
D = pd.read_csv(OUT / "nowcast_dataset.csv", encoding="utf-8-sig")
shows = pd.read_csv(HERE / "input" / "공연일_2026-11.csv", encoding="utf-8")
df = pd.read_pickle(SIM / "cache" / "cpm008_clean.pkl")
df = df[~df.bad_order & df.in_gate.astype(str).isin(GATES)]
ti, to = df.t_in.values, df.t_out.values


def S_rec(T):
    T = np.datetime64(pd.Timestamp(T)); return int(((ti <= T) & (to > T)).sum())


def S_api(T, tol_min=10):
    T = pd.Timestamp(T); i = api.index.get_indexer([T], method="nearest")[0]
    return float(api.iloc[i]) if abs((api.index[i] - T).total_seconds()) <= tol_min * 60 else np.nan


lines = [f"# 실시간 API ↔ 재구성 대조 ({pd.Timestamp.now(tz='Asia/Seoul'):%Y-%m-%d %H:%M} KST 실행)",
         f"로거: {api.index.min():%m/%d %H:%M} ~ {api.index.max():%m/%d %H:%M}, {len(api):,}행, "
         f"호출 간격 중앙 {api.index.to_series().diff().dt.total_seconds().median() / 60:.1f}분"]

# ── 1) 눈금 대조 ──
show25 = set(D.date)
cnt = df.t_out.dt.normalize().value_counts()
rec = []
for day in pd.date_range("2025-10-01", "2025-12-31"):
    if day.dayofweek > 3 or day.strftime("%Y-%m-%d") in show25 or cnt.get(day, 0) < 3000 or cnt.get(day - pd.Timedelta(days=1), 0) < 3000:
        continue
    rec.append({h: S_rec(day + pd.Timedelta(h + ":00")) for h in ["03:00", "10:55", "19:30"]})
rec = pd.DataFrame(rec)
show26 = set(shows.date)
ap = []
for day in pd.date_range(api.index.min().normalize() + pd.Timedelta(days=1), api.index.max().normalize() - pd.Timedelta(days=1)):
    if day.dayofweek > 3 or day.strftime("%Y-%m-%d") in show26:
        continue
    ap.append({h: S_api(day + pd.Timedelta(h + ":00")) for h in ["03:00", "10:55", "19:30"]})
ap = pd.DataFrame(ap)
cmp_ = pd.DataFrame({"2025 재구성(10~12월 평일, 중앙)": rec.median(), "2026 API(11월 평일, 중앙)": ap.median()})
cmp_["차이(API−재구성)"] = cmp_.iloc[:, 1] - cmp_.iloc[:, 0]
cmp_["비(API/재구성)"] = (cmp_.iloc[:, 1] / cmp_.iloc[:, 0]).round(2)
lines += ["", f"## 1) 눈금 대조 — 공연 없는 평일 (재구성 {len(rec)}일, API {len(ap)}일)", cmp_.round(0).to_string(),
          "→ 비율은 시각마다 크게 다르고(곱셈 보정 불가) 차이는 비슷하다(덧셈 보정). API에는 입출차 기록에 없는 상시 차량이 얹혀 있다.",
          "  그래서 수준이 아니라 같은 날 03:00 대비 증가분(S_rel)을 입력으로 쓴다. 이렇게 하면 상시분이 빠지고 연도별 기준선 이동도 흡수된다."]

# ── 2) 2025 모델 적합(전체 94일) ──
def fit(d, x, y):
    X = np.column_stack([np.log(d[x])] + [(d.gate == g).astype(float) for g in GATES])
    b, *_ = np.linalg.lstsq(X, np.log(d[y] + 1), rcond=None)
    return b, np.quantile(np.log(d[y] + 1) - X @ b, [.1, .9])


bN, qN = fit(D, "S_rel", "N")
# 감소량 ~ 3문 공연 차량 합 (2025)
drop25 = []
for r in D.drop_duplicates("date").itertuples():
    day = pd.Timestamp(r.date); en = day + pd.Timedelta(r.end + ":00")
    pre = max(S_rec(en + pd.Timedelta(minutes=m)) for m in range(-30, 1, 10))
    post = min(S_rec(en + pd.Timedelta(minutes=m)) for m in range(0, 241, 10))
    drop25.append({"date": r.date, "drop": pre - post})
T25 = D.groupby("date").agg(N3=("N", "sum"), S_rel=("S_rel", "first")).reset_index().merge(pd.DataFrame(drop25), on="date")
T25 = T25[T25["drop"] > 0]
A = np.column_stack([np.ones(len(T25)), np.log(T25.N3)]); bd, *_ = np.linalg.lstsq(A, np.log(T25["drop"]), rcond=None)
qd = np.quantile(np.log(T25["drop"]) - A @ bd, [.1, .9])
# 직접: 감소량 ~ S_rel (비교용)
A2 = np.column_stack([np.ones(len(T25)), np.log(T25.S_rel)]); bs, *_ = np.linalg.lstsq(A2, np.log(T25["drop"]), rcond=None)
qs = np.quantile(np.log(T25["drop"]) - A2 @ bs, [.1, .9])
lines += ["", "## 2) 2025 관계 (전체 적합)",
          f"log(N_문+1) = a_문 + {bN[0]:.2f}·log S_rel, 80% 잔차 [{qN[0]:.2f}, {qN[1]:.2f}]",
          f"log 감소량 = {bd[0]:.2f} + {bd[1]:.2f}·log(3문 N 합), 상관 r={np.corrcoef(np.log(T25.N3), np.log(T25['drop']))[0, 1]:.2f}, n={len(T25)}일",
          f"감소량/3문 N 합 중앙 {(T25['drop'] / T25.N3).median():.2f} — 감소량에는 같은 시간대 공원·시설 이용 차량 퇴장도 섞인다"]

# ── 3) 2026 공연일 ──
rows = []
for r in shows.itertuples():
    day = pd.Timestamp(r.date); st = day + pd.Timedelta(r.start + ":00"); en = st + pd.Timedelta(minutes=int(r.runtime_min))
    s0, s03 = S_api(st + pd.Timedelta(minutes=LEAD)), S_api(day + pd.Timedelta(hours=3), tol_min=60)
    if np.isnan(s03):   # 로거 시작 전(11/13) → 다음날 03:00로 대체
        s03 = S_api(day + pd.Timedelta(days=1, hours=3), tol_min=60); s03_note = "다음날 03시 대체"
    else:
        s03_note = ""
    row = {"date": r.date, "show": r.show[:28], "start": r.start, "end_est": f"{en:%H:%M}", "API_t0": s0, "API_03": s03,
           "S_rel": s0 - s03, "비고": (s03_note + " " + (r.note if isinstance(r.note, str) else "")).strip()}
    if not np.isnan(row["S_rel"]):
        x = np.log(row["S_rel"]); N3 = 0; lo3 = hi3 = 0
        for k, g in enumerate(GATES):
            mu = bN[0] * x + bN[1 + k]
            row[f"N_{g}"] = round(np.exp(mu) - 1); row[f"N_{g}_80"] = f"{np.exp(mu + qN[0]) - 1:.0f}~{np.exp(mu + qN[1]) - 1:.0f}"
            N3 += np.exp(mu) - 1
        row["N3_pred"] = round(N3)
        md = bd[0] + bd[1] * np.log(N3)
        row["감소량_예측"] = round(np.exp(md)); row["감소량_80"] = f"{np.exp(md + qd[0]):.0f}~{np.exp(md + qd[1]):.0f}"
        ms = bs[0] + bs[1] * x
        row["감소량_직접예측"] = round(np.exp(ms))
        if en + pd.Timedelta(minutes=240) <= api.index.max():
            pre = np.nanmax([S_api(en + pd.Timedelta(minutes=m)) for m in range(-30, 1, 5)])
            post = np.nanmin([S_api(en + pd.Timedelta(minutes=m)) for m in range(0, 241, 5)])
            row["감소량_관측"] = pre - post
            row["포함(80%)"] = bool(np.exp(md + qd[0]) <= pre - post <= np.exp(md + qd[1]))
    rows.append(row)
P = pd.DataFrame(rows)
P.to_csv(OUT / "api_2026_pred.csv", index=False, encoding="utf-8-sig")
done = P[P.get("감소량_관측").notna()] if "감소량_관측" in P else P.iloc[0:0]
lines += ["", "## 3) 2026 공연일 — 문별 예측과 API 사후 점검",
          P[["date", "show", "start", "end_est", "API_t0", "API_03", "S_rel"] + [f"N_{g}" for g in GATES] + ["N3_pred"]].to_string(index=False), "",
          P[["date", "감소량_예측", "감소량_80", "감소량_직접예측"] + [c for c in ["감소량_관측", "포함(80%)"] if c in P]].to_string(index=False)]
if len(done):
    e = (done["감소량_관측"] - done["감소량_예측"]); rel = (e / done["감소량_관측"]).abs()
    lines += [f"사후 점검 {len(done)}일: 80% 구간 포함 {int(done['포함(80%)'].astype(bool).sum())}/{len(done)}, "
              f"감소량 상대오차 중앙 {rel.median():.0%}, 평균 편향 {e.mean():+.0f}대(관측−예측)",
              f"학습 범위 S_rel {D.S_rel.min()}~{D.S_rel.max()} — 2026 값 {P.S_rel.min():.0f}~{P.S_rel.max():.0f}"]
lines += ["", "## 한계",
          "- API는 3문 합계뿐이라 문별 배분(남2·동2·북2)은 2026에서 검증할 수 없다. 2025 시간순 검증이 유일한 근거.",
          "- 종료 시각은 러닝타임 미기재 시 2025 중앙값 120분으로 추정. 감소량 창(종료 −30~+240분)이 넓어 영향은 작다.",
          "- 2026 공연일 수가 적다(현재 5일). 11/27~29 조용필 공연이 마감 전 '미리 낸 예측'의 첫 사례가 된다."]
(OUT / "api_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
