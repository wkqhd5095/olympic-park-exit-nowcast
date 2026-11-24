"""공연 후 출구별 출차 나우캐스트 — 시작 30분 뒤 '주차장에 남은 차량 수'로 문별 출차를 예측한다.

무엇을: 공연일×문(남2·동2·북2)마다
  입력  S_rel = S(시작 +30분) − S(같은 날 03:00), S = 주차장 안 차량 수(입차 ≤ t < 출차, 동2·남2·북2 입차 기준)
        → 운영 시에는 기관 실시간 주차현황 API(parking_sum)를 03:00과 시작 +30분에 읽어 뺀다.
          API 값에는 입출차 기록에 없는 상시 차량 약 800대가 더 얹혀 있으므로(api_check.py) 수준(S)이 아니라 증가분을 쓴다.
  출력  Y1 = 공연 차량 출차 대수 N (A안 정의, 문별)     ← '얼마나'
        Y2 = 문 전체 출차 10분 평균 피크(대/분)        ← '포화 경보'(차단기 문당 약 10대/분)
        T  = 피크 시각(예정 종료 기준 분)              ← '언제'
왜: 공연 정보(공연장·요일·시작시각)만으로는 관객층 차이(ENHYPEN 389 vs god 1,694대)를 못 잡는다(공연차량모델 결론 1·2).
    시작 30분 뒤면 관객 차량이 대부분 이미 들어와 있으므로, 그 수가 관객층을 대신 측정한다.
검증: 시간순 분할 — 1~6월 학습 → 7~12월 평가(8·9월은 원천 공백). 보조로 공연 단위 LOSO(같은 공연 연속일 묶음).
베이스라인: B0 문별 학습 중앙값, B1 공연장 규모(대형/그 외)×문 중앙값 = '공연 정보만'.
입력 파일: 05_개발/sim/cache/cpm008_clean.pkl(재수집 2,303,876건), 05_개발/sim/out/{show_exits,fit_results,calendar_used}.csv
출력: out/nowcast_dataset.csv, out/nowcast_eval.csv, out/nowcast_pred_test.csv, out/nowcast_summary.txt, figures/nowcast.png
실행: python nowcast.py   (약 30초)
"""
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SIM = ROOT / "05_개발" / "sim"
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
FIG = HERE / "figures"; FIG.mkdir(exist_ok=True)
GATES = ["남2문", "동2문", "북2문"]
LEAD_MIN = 30                 # 시작 +30분에 예측
SAT = 10.0                    # 포화 경보 기준(문 전체 10분 평균, 대/분) — 줄판정검증 결론 3(문당 10~12대/분)
TRAIN_MONTHS = range(1, 7)    # 1~6월 학습, 7~12월 평가

# ── 1. 데이터 ──────────────────────────────────────
df = pd.read_pickle(SIM / "cache" / "cpm008_clean.pkl")
df = df[~df.bad_order & df.t_in.notna()].copy()
df["in3"] = df.in_gate.astype(str).isin(GATES)
se = pd.read_csv(SIM / "out" / "show_exits.csv")
fit = pd.read_csv(SIM / "out" / "fit_results.csv", encoding="utf-8-sig")
cal = pd.read_csv(SIM / "out" / "calendar_used.csv", encoding="utf-8-sig")
days = fit.drop_duplicates("date")[["date", "show_name", "end_est"]].merge(
    cal[["date", "main_venue", "venue_class", "last_start"]], on="date")
days = days[days.last_start.notna()].reset_index(drop=True)          # GMF(시작 시각 없음) 제외 → 94일

t_in = df.t_in.values; t_out = df.t_out.values; in3 = df.in3.values
rows = []
for r in days.itertuples():
    day = pd.Timestamp(r.date)
    st = day + pd.Timedelta(r.last_start + ":00")
    t0 = np.datetime64(st + pd.Timedelta(minutes=LEAD_MIN))
    inside = (t_in <= t0) & ((t_out > t0) | pd.isna(t_out))
    S3 = int((inside & in3).sum())                                      # 실시간 API와 같은 범위(동2·남2·북2)
    T3 = np.datetime64(day + pd.Timedelta(hours=3))                     # 같은 날 새벽 3시(공원 빈 시간) 잔류량
    S03 = int(((t_in <= T3) & ((t_out > T3) | pd.isna(t_out)) & in3).sum())
    S_all = int(inside.sum())
    S_new = int((inside & in3 & (t_in >= np.datetime64(st - pd.Timedelta(minutes=240)))).sum())   # 참고: 시작 4시간 전 이후 입차분
    for g in GATES:
        c = se[(se.date == r.date) & (se.gate == g)].sort_values("rel_min")
        ma = c.exits.astype(float).rolling(10, center=True).mean()
        rows.append(dict(date=r.date, month=day.month, show=r.show_name, venue=r.main_venue,
                         large=int(r.venue_class == "대형"), start=r.last_start, end=r.end_est, gate=g,
                         S3=S3, S03=S03, S_rel=S3 - S03, S_all=S_all, S_new=S_new, N=int(c.show_exits.sum()), exits=int(c.exits.sum()),
                         peak10=float(ma.max()), t_peak=int(c.rel_min.values[int(np.nanargmax(ma.values))]),
                         lead_min=(pd.Timedelta(r.end_est + ":00") - pd.Timedelta(r.last_start + ":00")).total_seconds() / 60
                                  - LEAD_MIN + int(c.rel_min.values[int(np.nanargmax(ma.values))])))
D = pd.DataFrame(rows)
D.to_csv(OUT / "nowcast_dataset.csv", index=False, encoding="utf-8-sig")


# ── 2. 모델 ────────────────────────────────────────
def fit_loglin(tr, y, x="S3"):
    """log(y+1) = a_g + b·log S  (문 고정효과, 공통 기울기). 역변환은 로그 스케일 중앙 → 중앙값 예측.
    80% 구간 = 학습 잔차 10·90% 분위."""
    X = np.column_stack([np.log(tr[x])] + [(tr.gate == g).astype(float) for g in GATES])
    yy = np.log(tr[y] + 1)
    b, *_ = np.linalg.lstsq(X, yy, rcond=None)
    res = yy - X @ b
    return b, np.quantile(res, [.1, .9])


def pred_loglin(m, te, x="S3"):
    b, (q1, q9) = m
    X = np.column_stack([np.log(te[x])] + [(te.gate == g).astype(float) for g in GATES])
    mu = X @ b
    return np.exp(mu) - 1, np.exp(mu + q1) - 1, np.exp(mu + q9) - 1


def predict_all(tr, te, y):
    out = {}
    med_g = tr.groupby("gate")[y].median()
    out["B0 문별 중앙값"] = te.gate.map(med_g).values
    med_vg = tr.groupby(["large", "gate"])[y].median()
    out["B1 공연장규모×문 중앙값"] = [med_vg.get((l, g), med_g[g]) for l, g in zip(te.large, te.gate)]
    for x, name in [("S_rel", "M 잔류 증가분"), ("S3", "M' 잔류 수준"), ("S_all", "M'' 잔류 수준(전 출구)")]:
        p, lo, hi = pred_loglin(fit_loglin(tr, y, x), te, x)
        out[name] = p
        if x == "S_rel":
            out["_lo"], out["_hi"] = lo, hi
    return out


def score(te, y, P):
    rows = []
    for k, p in P.items():
        if k.startswith("_"):
            continue
        e = np.abs(te[y].values - p)
        rows.append({"모델": k, "MAE": e.mean(), "MdAE": np.median(e),
                     "MdAPE": np.median(e / np.maximum(te[y].values, 1))})
    R = pd.DataFrame(rows)
    R["B1 대비 MAE 감소"] = 1 - R.MAE / R.loc[R.모델.str.startswith("B1"), "MAE"].iloc[0]
    return R


tr = D[D.month.isin(TRAIN_MONTHS)]; te = D[~D.month.isin(TRAIN_MONTHS)]
lines = ["# 출구별 출차 나우캐스트 — 재수집 데이터(2,303,876건)",
         f"표본: 공연일 {D.date.nunique()}일 × 3문 = {len(D)} (공연 {D.show.nunique()}개)",
         f"시간순 분할: 학습 1~6월 {tr.date.nunique()}일·{tr.show.nunique()}공연 / 평가 7~12월 {te.date.nunique()}일·{te.show.nunique()}공연 "
         f"(평가 월: {sorted(te.month.unique())})",
         f"예측 시점: 시작 +{LEAD_MIN}분 → 실제 문별 피크까지 리드타임 중앙 {D.lead_min.median():.0f}분 "
         f"(사분위 {D.lead_min.quantile(.25):.0f}~{D.lead_min.quantile(.75):.0f})", ""]
EV = []
for y, lab in [("N", "Y1 공연 차량 출차 N(문별, 대)"), ("exits", "Y1' 문 전체 출차(종료 −60~+240분, 대)"),
               ("peak10", "Y2 문 전체 10분 피크(대/분)")]:
    P = predict_all(tr, te, y); R = score(te, y, P); R.insert(0, "대상", lab); R.insert(1, "검증", "시간순 1~6→7~12월")
    EV.append(R)
    if y == "N":
        cover = ((te.N >= P["_lo"]) & (te.N <= P["_hi"])).mean()
        PT = te[["date", "show", "gate", "S3", "S_rel", "N", "peak10", "t_peak"]].assign(
            pred_B1=np.round(P["B1 공연장규모×문 중앙값"]), pred_M=np.round(P["M 잔류 증가분"]),
            lo80=np.round(P["_lo"]), hi80=np.round(P["_hi"]))
        PT.to_csv(OUT / "nowcast_pred_test.csv", index=False, encoding="utf-8-sig")
        PN = P
    if y == "peak10":
        PP = P

# 보조: 공연 단위 LOSO (한 공연의 모든 날·문을 빼고 학습)
for y, lab in [("N", "Y1 공연 차량 출차 N(문별, 대)"), ("peak10", "Y2 문 전체 10분 피크(대/분)")]:
    preds = {}
    for s in D.show.unique():
        trr, tee = D[D.show != s], D[D.show == s]
        for k, v in predict_all(trr, tee, y).items():
            preds.setdefault(k, pd.Series(index=D.index, dtype=float)).loc[tee.index] = v
    R = score(D, y, {k: v.values for k, v in preds.items()}); R.insert(0, "대상", lab); R.insert(1, "검증", "공연 LOSO")
    EV.append(R)
    if y == "N":
        loso_cover = ((D.N >= preds["_lo"]) & (D.N <= preds["_hi"])).mean()
EV = pd.concat(EV, ignore_index=True)
EV.to_csv(OUT / "nowcast_eval.csv", index=False, encoding="utf-8-sig")
fmt = EV.copy(); fmt[["MAE", "MdAE"]] = fmt[["MAE", "MdAE"]].round(2)
fmt["MdAPE"] = (fmt.MdAPE * 100).round(0).astype(int).astype(str) + "%"
fmt["B1 대비 MAE 감소"] = (fmt["B1 대비 MAE 감소"] * 100).round(0).astype(int).astype(str) + "%"
lines += ["## 검증표", fmt.to_string(index=False), "",
          f"Y1 80% 예측구간 포함률: 시간순 평가 {cover:.0%}, 공연 LOSO {loso_cover:.0%}"]

# 계수 (전체 적합)
b, q = fit_loglin(D, "N", "S_rel")
lines += [f"전체 적합 log(N+1) = a_문 + {b[0]:.2f}·log S_rel  (a: " + ", ".join(f"{g} {a:.2f}" for g, a in zip(GATES, b[1:])) + ")",
          f"  → 잔류 차량 10% 증가 시 문별 공연 차량 출차 약 {(1.1 ** b[0] - 1) * 100:.0f}% 증가"]

# 문 전체 부하 = 예측 N + 배경(공연 차량 외) 학습 중앙값 — 배경이 얼마나 오차를 만드는지 분해
bg_tr = (tr.exits - tr.N).groupby(tr.gate).median(); bg_te = te.gate.map(bg_tr)
mae_tot = {k: np.abs(te.exits.values - (np.asarray(PN[k]) + bg_te.values)).mean() for k in ["M 잔류 증가분", "B1 공연장규모×문 중앙값"]}
lines += ["", "## 문 전체 출차(종료 −60~+240분) 분해: 예측 N + 배경 중앙값",
          f"MAE: M {mae_tot['M 잔류 증가분']:.1f} / B1 {mae_tot['B1 공연장규모×문 중앙값']:.1f} / B0(문 전체 중앙값) "
          f"{np.abs(te.exits - te.gate.map(tr.groupby('gate').exits.median())).mean():.1f}",
          f"배경만의 MAE {np.abs((te.exits - te.N) - bg_te).mean():.1f}대 — 문 전체 부하 오차의 주인은 공연 차량이 아니라 배경(공원·체육시설 이용 차량)",
          f"공연 차량 비중(창 안 출차 중) 중앙 {(D.N / D.exits).median():.0%}"]

# ── 3. 포화 경보 (예측 피크 ≥ SAT) ───────────────────
pk = PP["M 잔류 증가분"]; obs = te.peak10.values >= SAT; alarm = pk >= SAT
lines += ["", f"## 포화 경보 (문 전체 10분 피크 ≥ {SAT:.0f}대/분), 평가 기간",
          f"관측 포화 곡선 {obs.sum()}개 / 경보 {alarm.sum()}개 / 적중 {(obs & alarm).sum()} / 놓침 {(obs & ~alarm).sum()} / 오경보 {(~obs & alarm).sum()}",
          te.assign(pred_peak=np.round(pk, 1))[obs | alarm][["date", "show", "gate", "S_rel", "peak10", "pred_peak"]]
          .round(1).to_string(index=False)]
# 기준 낮춰 보기 (경보 민감도)
for thr in [8.0, 9.0]:
    a = pk >= thr
    lines.append(f"  경보 기준 {thr:.0f}대/분: 적중 {(obs & a).sum()}/{obs.sum()}, 오경보 {(~obs & a).sum()}")

# ── 4. '언제': 피크 시각 ────────────────────────────
tp = D.groupby("gate").t_peak.describe()[["25%", "50%", "75%"]]
lines += ["", "## 피크 시각(예정 종료 기준 분) — 문별", tp.to_string()]

# ── 5. 검산 ────────────────────────────────────────
c1 = (D.groupby("date").N.sum() / D.groupby("date").S3.first()).describe()
c2 = np.corrcoef(np.log(D.S3), np.log(D.S_all))[0, 1]
neg = D.assign(Sperm=D.groupby("gate").S_rel.transform(lambda s: s.sample(frac=1, random_state=0).values))
trn, ten = neg[neg.month.isin(TRAIN_MONTHS)], neg[~neg.month.isin(TRAIN_MONTHS)]
p_perm = pred_loglin(fit_loglin(trn, "N", "Sperm"), ten, "Sperm")[0]
mae_perm = np.abs(ten.N - p_perm).mean()
lines += ["", "## 검산",
          f"1) 3문 공연 차량 합 / 잔류 차량 S3 = 중앙 {c1['50%']:.2f} (사분위 {c1['25%']:.2f}~{c1['75%']:.2f}) — 1 이하여야 정상(공연 차량은 대부분 t0에 이미 주차장 안)",
          f"2) log S3 vs log S_all 상관 {c2:.3f} — 입력 범위 선택에 둔감",
          f"3) 순열 대조: S_rel을 무작위로 섞으면 시간순 MAE {mae_perm:.1f} (진짜 S_rel {np.abs(te.N - PN['M 잔류 증가분']).mean():.1f}, B1 {np.abs(te.N - np.array(PN['B1 공연장규모×문 중앙값'])).mean():.1f}) — 정보가 S에서 온다는 확인",
          f"4) 학습·평가 S_rel 범위: 학습 {tr.S_rel.min()}~{tr.S_rel.max()}, 평가 {te.S_rel.min()}~{te.S_rel.max()} — 평가 최대가 학습 범위 {'안' if te.S_rel.max() <= tr.S_rel.max() else '밖(외삽)'}",
          f"5) 새벽 3시 잔류량 S03: 중앙 {D.S03.median():.0f}대(범위 {D.S03.min()}~{D.S03.max()}) — 증가분과 수준의 상관 {np.corrcoef(D.S3, D.S_rel)[0, 1]:.3f}"]
(OUT / "nowcast_summary.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))

# ── 6. 그림 ────────────────────────────────────────
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
for f in ["Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "NanumGothic"]:
    try:
        from matplotlib import font_manager; font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.family"] = f; break
    except Exception:
        pass
plt.rcParams["axes.unicode_minus"] = False
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
col = {"남2문": "#C0504D", "동2문": "#4F81BD", "북2문": "#9BBB59"}
for g in GATES:
    c = D[D.gate == g]
    ax[0].scatter(c.S_rel, c.N, s=14, alpha=.75, color=col[g], label=g)
ax[0].set_xlabel(f"잔류 증가분 S_rel = S(시작+{LEAD_MIN}분) − S(03시)"); ax[0].set_ylabel("공연 후 문별 공연 차량 출차 N")
ax[0].set_title("잔류 차량이 많을수록 문별 공연 차량 출차도 많다 (산포 큼)"); ax[0].legend(fontsize=8)
pt = pd.read_csv(OUT / "nowcast_pred_test.csv")
lim = [0, max(pt.N.max(), pt.pred_M.max(), pt.pred_B1.max()) * 1.05]
ax[1].scatter(pt.N, pt.pred_B1, s=16, marker="x", color="#999", label="B1 공연 정보만")
ax[1].scatter(pt.N, pt.pred_M, s=18, color="k", label="M 잔류 증가분")
ax[1].plot(lim, lim, "k--", lw=.8); ax[1].set_xlim(lim); ax[1].set_ylim(lim)
ax[1].set_xlabel("실제 N (7~12월 평가)"); ax[1].set_ylabel("예측 N"); ax[1].set_title("시간순 평가(7~12월): 오른쪽 아래 = god 남2문 과소예측"); ax[1].legend(fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "nowcast.png", dpi=150)
