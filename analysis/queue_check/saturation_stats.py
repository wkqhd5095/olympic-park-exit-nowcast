"""포화 사례 통계: 공연일 × 문마다 '차단기 근접 포화 신호'가 있었는지 판정하고 공연 차량 수 구간별로 집계.
신호 정의(모델 무관, 두 가지):
  S1 속도: 문 전체 10분 평균 출차율(÷THIN 보정)이 10대/분 이상
  S2 차로 간격: 고부하 구간(10분 평균이 그날 최대의 80% 이상, 15분 이상 지속) 동안
      주 차로 1·2번 모두 차간 간격 중앙값 ≤ 11초 (차로당 ≥ 약 5.5대/분). 초 단위 신뢰 달만(2~12월; 재수집본 기준, 1월만 제외)
  포화 신호 = S1 그리고 (S2 또는 S2 판정 불가)  → S2 불가인 날은 '속도만' 표시
근거: EDA 차로 최소 처리 간격(하위 25%) 6~8초 → 포화 시 중앙 8~11초 예상."""
import os
from pathlib import Path
import numpy as np, pandas as pd
# 11/24: 기본 입력을 팀 재수집본(2,303,876건)으로 변경 → 보정 불필요(THIN=1.0).
#   재수집 전 재구성본으로 되돌리려면: SHOW_EXITS=out/show_exits_rebuilt.csv CARS=out/cars_window.parquet THIN=0.885
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]; SIM = ROOT / "05_개발" / "sim"
SHOW_EXITS = os.environ.get("SHOW_EXITS", str(SIM / "out" / "show_exits.csv"))
CARS = os.environ.get("CARS", str(SIM / "cache" / "cpm008_clean.pkl"))
THIN = float(os.environ.get("THIN", "1.0"))
os.chdir(HERE)
se = pd.read_csv(SHOW_EXITS)
fit = pd.read_csv(SIM / "out" / "fit_results.csv", encoding="utf-8-sig")
cal = pd.read_csv(SIM / "out" / "calendar_used.csv", encoding="utf-8-sig")
# 표본: 수집 온전 공연일(fit_results) 중 시작 시각 있는 날 (t4_recheck.py와 동일, GMF 제외 94일)
keep = set(fit.date) & set(cal.loc[cal.last_start.notna(), "date"])
fit = fit[fit.date.isin(keep)]; se = se[se.date.isin(keep)]
if CARS.endswith(".pkl"):   # 원자료에서 cars_window와 같은 형태(date, gate, lane, rel_s) 생성. 기준 = 팀 end_est
    d = pd.read_pickle(CARS); d = d[~d.bad_order & d.t_out.notna()]
    d = d.assign(gate=d.out_gate.astype(str), lane=d.out_lane.astype(str))
    d = d[d.gate.isin(["남2문", "동2문", "북2문"]) & d.lane.isin(["1", "2"])]
    parts = []
    for dt, e in fit.drop_duplicates("date")[["date", "end_est"]].itertuples(index=False):
        en = pd.Timestamp(dt) + pd.Timedelta(e + ":00")
        x = d[(d.t_out >= en - pd.Timedelta(minutes=60)) & (d.t_out < en + pd.Timedelta(minutes=240))]
        parts.append(pd.DataFrame({"date": dt, "gate": x.gate.values, "lane": x.lane.values,
                                   "rel_s": (x.t_out - en).dt.total_seconds().values}))
    cars = pd.concat(parts, ignore_index=True)
else:
    cars = pd.read_parquet(CARS)
print(f"입력: {SHOW_EXITS} | {CARS} | THIN={THIN} | 곡선 {se.groupby(['date','gate']).ngroups}")
rows = []
for (dt, g), c in se.groupby(["date", "gate"]):
    c = c.sort_values("rel_min"); ma = c.exits.rolling(10, center=True).mean().values
    pk = np.nanmax(ma); rate = pk / THIN
    hi = c.rel_min.values[ma >= .8 * pk]; dur = len(hi)
    month = int(dt[5:7]); sec_ok = month != 1   # 11/24: 9·10월도 판정 대상에 포함(팀 결정). 실제 초 해상도는 곡선별로 아래에서 검사
    minute_only = False
    med = {}
    if sec_ok and dur >= 15:
        cc = cars[(cars.date == dt) & (cars.gate == g)]
        for L in ["1", "2"]:
            t = np.sort(cc.rel_s[(cc.lane == L) & (cc.rel_s >= hi.min() * 60) & (cc.rel_s < (hi.max() + 1) * 60)].values)
            d = np.diff(t); d = d[d > 0]
            # 초 단위 실검사: 간격이 60초 배수 위주면 분 단위 기록(초는 고정 오프셋) → 판정 불가
            sec_real = len(d) >= 10 and np.mean(np.isclose(d % 60, 0)) < 0.5
            if len(d) >= 10 and not sec_real: minute_only = True
            med[L] = np.median(d) * THIN if sec_real else np.nan   # 누락 보정: 간격도 0.885배로 축소
    s1 = rate >= 10
    s2 = (dur >= 15 and len(med) == 2 and all(v <= 11 for v in med.values())) if sec_ok else None
    if sec_ok and minute_only: s2 = None   # 분 단위 기록 → 간격 판정 불가
    rows.append({"date": dt, "gate": g, "peak_rate": round(rate, 1), "high_min": dur,
                 "lane1_gap": round(med.get("1", np.nan), 1), "lane2_gap": round(med.get("2", np.nan), 1),
                 "S1_속도": s1, "S2_차로간격": s2})
S = pd.DataFrame(rows).merge(fit[["date", "gate", "N"]], on=["date", "gate"]).merge(cal[["date", "show_name", "venue_class"]], on="date")
S["포화신호"] = np.where(S.S1_속도 & (S.S2_차로간격 != False), np.where(S.S2_차로간격.isna(), "속도만(간격 판정 불가)", "확인"), "없음")
S["N구간"] = pd.cut(S.N, [0, 200, 300, 400, 500, 1000], labels=["<200", "200~299", "300~399", "400~499", "500+"], right=False)
S.to_csv("out/포화사례_곡선별.csv", index=False, encoding="utf-8-sig")
tab = S.groupby("N구간", observed=False).agg(곡선=("N", "size"), 공연일=("date", "nunique"),
        포화확인=("포화신호", lambda x: (x == "확인").sum()), 속도만=("포화신호", lambda x: (x == "속도만(간격 판정 불가)").sum()),
        피크중앙=("peak_rate", "median"))
tab["포화 비율"] = ((tab.포화확인 + tab.속도만) / tab.곡선).round(2)
print(tab.to_string()); tab.to_csv("out/포화사례_구간별.csv", encoding="utf-8-sig")
print("\n문별:", S.groupby("gate").포화신호.value_counts().unstack(fill_value=0).to_string())
print("\n포화 신호 곡선:\n", S[S.포화신호 != "없음"].sort_values("N", ascending=False)[["date", "gate", "show_name", "N", "peak_rate", "high_min", "lane1_gap", "lane2_gap", "포화신호"]].to_string(index=False))
# 문별 최대치: 10분 평균 최대 출차율과 그 공연, 최소 차로 간격
G = S.loc[S.groupby("gate").peak_rate.idxmax(), ["gate", "date", "show_name", "N", "peak_rate", "high_min", "lane1_gap", "lane2_gap", "포화신호"]]
G = G.rename(columns={"peak_rate": "최대_피크(대/분)", "date": "최대일", "show_name": "최대일_공연"}).set_index("gate")
G["피크_상위5%"] = S.groupby("gate").peak_rate.quantile(.95).round(1)
G["피크_중앙"] = S.groupby("gate").peak_rate.median().round(1)
G["최소_간격(1·2차로 큰 쪽)"] = S.assign(m=S[["lane1_gap", "lane2_gap"]].max(axis=1)).groupby("gate").m.min()
G["신호_곡선"] = S.groupby("gate").포화신호.apply(lambda x: (x != "없음").sum()); G["곡선"] = S.groupby("gate").size()
print("\n문별 최대치:\n", G.to_string()); G.to_csv("out/포화사례_문별.csv", encoding="utf-8-sig")
print("\n공연일 수(신호 1개 문 이상):", S[S.포화신호 != "없음"].date.nunique(), "/", S.date.nunique())
# 민감도: 속도 기준 9 / 11 대/분
for thr in (9, 11):
    print(f"민감도 S1≥{thr}: 신호 곡선", int(((S.peak_rate >= thr) & (S.S2_차로간격 != False)).sum()))
