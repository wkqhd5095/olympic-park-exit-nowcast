"""1단계: 입출차(cpm008) 정제 → 시간별 재차대수(cpm009로 보정) + 임시 공연 캘린더 + 공연일 게이트별 분당 출차.

실행:  python prep.py            (03_데이터/processed 의 kcisa_cpm008.csv, kcisa_cpm009.csv 사용)
환경변수 KSPO_DATA_DIR 로 데이터 폴더 변경 가능. 공연캘린더_2025.csv 가 03_데이터/raw 에 있으면 그것을 우선 사용.
출력(out/):
  hourly_occupancy.csv   시각별 재차대수(원본/통과차 제외/보정) + cpm009 주차대수
  calendar_used.csv      이번 실행에 쓴 공연일 라벨(출처 열 포함)
  show_exits.csv         공연일 × 게이트 × 종료기준 분(-60~+240) 분당 출차
  show_days.csv          공연일별 추정 종료시각·방법·관람객수·수집 온전 여부
  show_bg.csv            같은 시각 비공연일 출차 중앙값(배경) — 공연 차량 = 관측 − 배경
  prep_checks.txt        검산 결과
"""
import os
from pathlib import Path
import re
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("KSPO_DATA_DIR", ROOT / "03_데이터"))
OUT = Path(__file__).resolve().parent / "out"; OUT.mkdir(exist_ok=True)
CACHE = Path(__file__).resolve().parent / "cache"; CACHE.mkdir(exist_ok=True)
MAIN_GATES = ["남2문", "북2문", "동2문"]
WIN = (-60, 240)
# 공연 차량 정의(A안, 11/22 팀 결정): 입차가 [주 공연 시작 − ENTRY_BEFORE, 시작 + ENTRY_AFTER] 이고
#   출차가 예정 종료 − EXIT_MARGIN 이후인 차. 나머지 = 같은 날 배경. 민감도 분석 시 환경변수로 바꿔 재실행.
import os as _os
ENTRY_BEFORE = int(_os.environ.get("SHOW_ENTRY_BEFORE_MIN", "240"))
ENTRY_AFTER = int(_os.environ.get("SHOW_ENTRY_AFTER_MIN", "30"))
EXIT_MARGIN = int(_os.environ.get("SHOW_EXIT_MARGIN_MIN", "30"))
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)

# ── 1. 입출차 정제 ──────────────────────────────────────
def load_cpm008():
    src = DATA / "processed" / "kcisa_cpm008_slim.csv"          # 5개 열만 뽑은 판(재수집 반영, 11/22)
    if not src.exists():
        src = DATA / "processed" / "kcisa_cpm008.csv"
    cache = CACHE / "cpm008_clean.pkl"
    if cache.exists() and cache.stat().st_mtime > src.stat().st_mtime:
        return pd.read_pickle(cache)
    cols = ["LOCAL_ID", "desc_입구", "desc_입차시간", "desc_출구", "desc_출차시간"]
    raw = pd.read_csv(src, encoding="utf-8-sig", usecols=cols, dtype=str)
    d = raw.drop_duplicates("LOCAL_ID")
    df = pd.DataFrame({
        "t_in": pd.to_datetime(d["desc_입차시간"], errors="coerce"),
        "t_out": pd.to_datetime(d["desc_출차시간"], errors="coerce"),
        "in_gate": d["desc_입구"].str.extract(r"^(\S+?문|회관)")[0],
        "out_gate": d["desc_출구"].str.extract(r"^(\S+?문|사전정산기)")[0],
        "out_lane": np.where(d["desc_출구"].str.contains("CMB", na=False), "CMB",
                             d["desc_출구"].str.extract(r"#(\d+)")[0]),
    }).reset_index(drop=True)
    for c in ["in_gate", "out_gate", "out_lane"]:
        df[c] = df[c].astype("category")
    df["dwell"] = (df.t_out - df.t_in).dt.total_seconds() / 60
    df["bad_order"] = df.dwell < 0
    df["passthru"] = df.dwell.between(0, 5) & (df.in_gate.astype(str) != df.out_gate.astype(str))
    say(f"[정제] 원본 {len(raw):,} → 중복 제거 {len(df):,} (중복 {len(raw)-len(df):,})")
    df.to_pickle(cache)
    return df

df = load_cpm008()
ok = df[~df.bad_order]
say(f"[정제] 출차<입차 {int(df.bad_order.sum()):,}건 제외, 통과차량(5분 이하·다른 게이트) {int(ok.passthru.sum()):,}건 ({ok.passthru.mean():.1%})")

daily_rows = df.groupby(df.t_in.dt.normalize()).size()
full = daily_rows.reindex(pd.date_range("2025-01-01", "2025-12-31"), fill_value=0)
ref = full[full.index.month.isin([2, 3, 4, 5, 6, 7, 10])]
cover = full / full.index.dayofweek.map(ref.groupby(ref.index.dayofweek).median()).values
complete = cover >= 0.6
say(f"[수집] 수집 온전(요일 기준 60% 이상) {int(complete.sum())}일 / 365일")

# ── 2. 시간별 재차대수 + cpm009 보정 ───────────────────
hours = pd.date_range("2025-01-01", "2026-01-01", freq="h", inclusive="left")
def occ_at(sub, T):
    ti = np.sort(sub.t_in.values); to = np.sort(sub.t_out.values)
    return np.searchsorted(ti, T, "right") - np.searchsorted(to, T, "right")
H = pd.DataFrame({"hour": hours})
H["occ_raw"] = occ_at(ok, hours.values)
H["occ_nopass"] = occ_at(ok[~ok.passthru], hours.values)
H["entries"] = H.hour.map(ok.groupby(ok.t_in.dt.floor("h")).size()).fillna(0).astype(int)
H["exits"] = H.hour.map(ok.groupby(ok.t_out.dt.floor("h")).size()).fillna(0).astype(int)
H["complete_day"] = H.hour.dt.normalize().map(complete).astype(bool)

c9 = pd.read_csv(DATA / "processed" / "kcisa_cpm009.csv", encoding="utf-8-sig", dtype=str)
c9["hour"] = pd.to_datetime(c9.ISSUED_DATE, errors="coerce")
c9 = c9[c9["desc_주차대수"].notna()]
c9h = c9.groupby("hour").agg(cpm009_park=("desc_주차대수", lambda s: float(s.iloc[0])),
                             show_name=("desc_공연명", "first"),
                             attendance=("desc_공연관람객수", lambda s: pd.to_numeric(s, errors="coerce").max()))
H = H.merge(c9h, left_on="hour", right_index=True, how="left")

# 보정계수: 시(hour-of-day)별 중앙값 (cpm009 / 재구성), 수집 온전한 날만
cal = H[H.complete_day & H.cpm009_park.notna() & (H.occ_raw > 50)].copy()
cal["ratio"] = cal.cpm009_park / cal.occ_raw
f_hod = cal.groupby(cal.hour.dt.hour).ratio.median()
f_all = cal.ratio.median()
H["calib_factor"] = H.hour.dt.hour.map(f_hod).fillna(f_all)
H["occ_calibrated"] = (H.occ_raw * H.calib_factor).round(0)
# 검산: 보정 전/후 오차 (시간 순 분할 — 1~7월로 계수 추정, 9~12월로 평가)
tr, te = cal[cal.hour.dt.month <= 7], cal[cal.hour.dt.month >= 9]
f_tr = tr.groupby(tr.hour.dt.hour).ratio.median()
def mape(a, b): return float(np.median(np.abs(a - b) / a))
if len(te):
    say(f"[보정] 학습(1~7월) {len(tr)}시간 / 평가(9~12월) {len(te)}시간")
    say(f"[보정] 평가구간 상대오차 중앙값: 보정 전 {mape(te.cpm009_park, te.occ_raw):.1%} → 시간대별 계수 보정 후 "
        f"{mape(te.cpm009_park, te.occ_raw * te.hour.dt.hour.map(f_tr).fillna(tr.ratio.median())):.1%} "
        f"(단일계수 {mape(te.cpm009_park, te.occ_raw * tr.ratio.median()):.1%})")
say(f"[보정] 상관 r(재구성, cpm009) = {cal[['occ_raw','cpm009_park']].corr().iloc[0,1]:.3f}, 전체 계수 중앙값 {f_all:.2f}")
H.to_csv(OUT / "hourly_occupancy.csv", index=False, encoding="utf-8-sig")

# ── 3. 공연 캘린더 (정식 파일 있으면 우선, 없으면 임시) ──
official = DATA / "raw" / "공연캘린더_2025.csv"
if official.exists():
    calr = pd.read_csv(official, encoding="utf-8-sig")
    calr["date"] = pd.to_datetime(calr["date"]).dt.normalize()
    calr["source"] = calr.get("source", "공연캘린더_2025")
    say(f"[캘린더] 정식 캘린더 사용: {calr.date.nunique()}일" +
        (f" (대형 {int((calr.venue_class=='대형').sum())} · 중형 {int((calr.venue_class=='중형').sum())} · 소형만 {int((calr.venue_class=='소형').sum())})" if "venue_class" in calr else ""))
else:
    s9 = H[H.show_name.notna()].copy(); s9["date"] = s9.hour.dt.normalize()
    cal9 = s9.groupby("date").agg(show_name=("show_name", "first"), attendance=("attendance", "max")).reset_index()
    cal9["source"] = "cpm009"
    # 출차 급증일 (cpm009가 다루지 않는 날만 추가)
    m5 = ok.groupby(ok.t_out.dt.floor("5min")).size()
    m5 = m5.to_frame("n"); m5["d"] = m5.index.normalize(); m5["h"] = m5.index.hour
    day_mean = m5[m5.h.between(10, 16)].groupby("d").n.mean()
    eve_max = m5[m5.h >= 19].groupby("d").n.max()
    ratio = (eve_max / day_mean).dropna(); ratio = ratio[day_mean.reindex(ratio.index) > 3]
    thr = ratio.quantile(0.85)
    c9_days = set(H[H.cpm009_park.notna()].hour.dt.normalize())
    surge = [d for d in ratio[ratio >= thr].index if d not in c9_days]
    calr = pd.concat([cal9, pd.DataFrame({"date": surge, "show_name": "(출차 급증 감지)", "attendance": np.nan,
                                          "source": "surge"})], ignore_index=True)
    say(f"[캘린더] 임시 캘린더: cpm009 {len(cal9)}일 + 급증 감지 {len(surge)}일 (정식 캘린더 오면 교체)")
calr.to_csv(OUT / "calendar_used.csv", index=False, encoding="utf-8-sig")

# ── 4. 공연 종료 시각 + 게이트별 분당 출차 ─────────────
# 종료 기준: 공연 일정의 '예정 종료'(시작+러닝타임)가 있으면 그것을 쓴다 → 출차 데이터와 독립(순환 논리 방지).
#            없으면 출차 급증으로 추정. 둘 다 계산해 차이를 검산으로 보고.
# 대상: 대형·중형 공연장 공연일 (소형만 있는 날은 배경에서만 제외)
calr_fit = calr[calr.venue_class.isin(["대형", "중형"])] if "venue_class" in calr else calr
m1 = ok.groupby(ok.t_out.dt.floor("min")).size()
rows, curves = [], []
for _, r in calr_fit.iterrows():
    d0 = r.date
    s = m1.reindex(pd.date_range(d0, periods=1440, freq="min"), fill_value=0)
    base = s[d0 + pd.Timedelta("10h"): d0 + pd.Timedelta("16h59min")].mean()
    eve = s[d0 + pd.Timedelta("19h30min"):]
    roll5 = eve.rolling(5).sum()
    hit = roll5 > 3 * 5 * base
    if base > 0 and hit.any():
        end, how = hit.idxmax() - pd.Timedelta(minutes=4), "급증시작(5분합>평시3배)"
    else:
        r15 = eve.rolling(15).sum()
        end, how = r15.idxmax() - pd.Timedelta(minutes=14), "저녁 15분 최대 시작"
    end_exit = end
    sched = r.get("end_time_sched") if hasattr(r, "get") else None
    if isinstance(sched, str) and re.fullmatch(r"\d{2}:\d{2}", sched):
        hh, mm = map(int, sched.split(":"))
        end = d0 + pd.Timedelta(hours=hh + (24 if hh < 6 else 0), minutes=mm); how = "예정종료(공연일정)"
    rows.append(dict(date=d0.date(), show_name=r.show_name, attendance=r.attendance, source=r.source,
                     venue=r.get("main_venue", ""), venue_class=r.get("venue_class", ""),
                     end_est=end.strftime("%H:%M"), end_method=how, end_exit_est=end_exit.strftime("%H:%M"),
                     end_gap_min=round((end_exit - end).total_seconds() / 60), complete=bool(complete.get(d0, False))))
    w = ok[(ok.t_out >= end + pd.Timedelta(minutes=WIN[0])) & (ok.t_out < end + pd.Timedelta(minutes=WIN[1]))]
    w = w[w.out_gate.isin(MAIN_GATES)].copy()
    st = r.get("last_start") if hasattr(r, "get") else None
    if isinstance(st, str) and re.fullmatch(r"\d{2}:\d{2}", st):
        start = d0 + pd.Timedelta(hours=int(st[:2]), minutes=int(st[3:]))
    else:
        start = end - pd.Timedelta(minutes=120)                     # 시작시각 없으면 종료 − 120분(가정)
    w["show"] = (w.t_in >= start - pd.Timedelta(minutes=ENTRY_BEFORE)) & (w.t_in <= start + pd.Timedelta(minutes=ENTRY_AFTER)) \
                & (w.t_out >= end - pd.Timedelta(minutes=EXIT_MARGIN))
    rel = ((w.t_out - end).dt.total_seconds() // 60).astype(int)
    idx = pd.MultiIndex.from_product([MAIN_GATES, range(*WIN)], names=["gate", "rel_min"])
    cc = w.groupby([w.out_gate.astype(str), rel]).size().reindex(idx, fill_value=0).rename("exits").to_frame()
    cc["show_exits"] = w[w.show].groupby([w[w.show].out_gate.astype(str), rel[w.show]]).size().reindex(idx, fill_value=0)
    cc["bg"] = cc.exits - cc.show_exits
    cc = cc.reset_index(); cc.insert(0, "date", d0.date())
    rows[-1]["start_used"] = start.strftime("%H:%M")
    curves.append(cc)
# 배경(A안): 같은 날, 공연 차량이 아닌 차의 분당 출차 (관측값 그대로)
allc = pd.concat(curves)
allc[["date", "gate", "rel_min", "bg"]].to_csv(OUT / "show_bg.csv", index=False, encoding="utf-8-sig")
for row in rows: row["n_bg_days"] = 1   # (호환용) 같은 날 배경
say(f"[공연차량] 정의: 입차 [시작−{ENTRY_BEFORE}분, 시작+{ENTRY_AFTER}분], 출차 ≥ 예정종료−{EXIT_MARGIN}분")
_sc = allc.groupby(["date", "gate"]).show_exits.sum()
say(f"[공연차량] 공연일×문 중앙 {_sc.median():.0f}대 (사분위 {_sc.quantile(.25):.0f}~{_sc.quantile(.75):.0f}), 창 안 출차 중 비중 {allc.show_exits.sum()/allc.exits.sum():.0%}")
SD = pd.DataFrame(rows); SD.to_csv(OUT / "show_days.csv", index=False, encoding="utf-8-sig")
pd.concat(curves).to_csv(OUT / "show_exits.csv", index=False, encoding="utf-8-sig")
say(f"[종료] {len(SD)}일 중 수집 온전 {int(SD.complete.sum())}일, 방법: {SD.end_method.value_counts().to_dict()}")
g = SD[SD.complete & (SD.end_method == "예정종료(공연일정)")].end_gap_min
if len(g):
    say(f"[검산] 출차급증 추정 − 예정종료 (분): 중앙 {g.median():.0f}, 사분위 {g.quantile(.25):.0f}~{g.quantile(.75):.0f}, |차이|≤30분 {(g.abs()<=30).mean():.0%} (n={len(g)})")
# 검산: 분당 출차 합 = 해당 창의 원 데이터 건수
chk = pd.concat(curves).exits.sum()
say(f"[검산] show_exits 합계 {chk:,}대 (주요 3문, -60~+240분 창)")
(OUT / "prep_checks.txt").write_text("\n".join(log), encoding="utf-8")
