"""기획서 그림 1 재작성: 공연 규모(문별 공연 차량 수 N)와 피크 출차율 / 평탄도 — 재수집 데이터(THIN=1, 보정 없음).
입력: out/t4_metrics.csv (t4_recheck.py 산출)  출력: out/fig1_T4_scatter.png
기존 그림(줄판정검증/figures/T4_scatter.png, 재수집 전 ÷0.885 보정)과 같은 정의·같은 함수(queue_tests.design/_ols, flat_slope)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib import font_manager
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "줄판정검증" / "줄판정검증"))
import queue_tests as qt; from run_synth_rules import flat_slope   # noqa: E402
_have = {f.name for f in font_manager.fontManager.ttflist}   # 설치된 한글 글꼴 중 있는 것 사용(윈도·맥·리눅스)
plt.rcParams["font.family"] = [f for f in ["Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans CJK JP"] if f in _have] + ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
M = pd.read_csv(HERE / "out" / "t4_metrics.csv", encoding="utf-8-sig")
X = qt.design(M, np.log(M.N.values)); coef = qt._ols(np.log(M.r_peak.values), X)
fs = flat_slope(M, B=1000)
xx = np.linspace(np.log(M.N.min()), np.log(M.N.max()), 50)
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for g, c in M.groupby("gate"):
    ax[0].scatter(c.N, c.r_peak, s=14, alpha=.7, label=g); ax[1].scatter(c.N, c.flat, s=14, alpha=.7, label=g)
ax[0].plot(np.exp(xx), np.exp(coef[0] + coef[1] * xx), "k--", lw=1, label=f"로그선형 β={coef[1]:.2f}")
ax[0].set_xscale("log"); ax[0].set_yscale("log"); ax[0].set_xlabel("공연 차량 수 N (문별)"); ax[0].set_ylabel("공연 차량 피크 출차율 (10분 평균, 대/분)")
ax[0].set_title("큰 공연에서도 피크가 계속 커짐 (천장 없음)"); ax[0].legend(fontsize=8)
ax[1].set_xscale("log"); ax[1].set_xlabel("공연 차량 수 N (문별)"); ax[1].set_ylabel("평탄도 (T90/T50)")
ax[1].set_title(f"평탄도 기울기 {fs[0]:.3f} [{fs[1]:.3f}, {fs[2]:.3f}] — 규모와 무관"); ax[1].legend(fontsize=8)
fig.text(.99, .01, "2025 입출차 재수집본 2,303,876건 · 94공연일×3문 = 282곡선", ha="right", fontsize=7, color="#666")
fig.tight_layout(); fig.savefig(HERE / "out" / "fig1_T4_scatter.png", dpi=200)
print(f"β={coef[1]:.3f} (t4_recheck.txt 0.76과 일치해야 함), 평탄도 기울기 {fs[0]:.3f} [{fs[1]:.3f}, {fs[2]:.3f}], 곡선 {len(M)}")
