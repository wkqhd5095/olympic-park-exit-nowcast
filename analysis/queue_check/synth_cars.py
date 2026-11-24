"""정답을 아는 가상 데이터: 공연일 × 문 곡선, 차 한 대 단위 출차 시각.

세 세계(world):
  'disperse' : 줄 없음. 출차 = 희망 시각 + 이동시간.
  'capacity' : 위쪽(주차장 안) 병목 μ에서 FIFO로 방출 → 이동시간 흩어짐 → 차단기 통과.
  'mixed'    : 'capacity'와 같지만 μ가 커서 대형 공연에서만 묶임(가설: 평소엔 수요, 대형에서만 천장).
희망 시각 = loc + Gamma(k=2, scale) (분, 예정 종료 기준). 배경 차량은 포아송.
"""
import numpy as np

REL0, REL1 = -60, 240


def one_curve(N, world, mu=5.0, loc=None, scale=None, bg=0.6, travel_sd_s=40, rng=None, lanes=2,
              barrier_s=10.0):
    rng = rng or np.random.default_rng()
    loc = loc if loc is not None else rng.uniform(15, 50)
    scale = scale if scale is not None else rng.uniform(8, 15)
    desire = (loc + rng.gamma(2.0, scale, N)) * 60.0        # 초
    desire.sort()
    if world == "disperse":
        rel = desire
    else:
        dep = np.empty(N); last = -np.inf
        for i, a in enumerate(desire):
            last = max(a, last + 60.0 / mu); dep[i] = last
        rel = dep
    # 이동 중 흩어짐(병목→차단기): 반정규 지연
    rel = rel + np.abs(rng.normal(0, travel_sd_s, N)) + 30
    # 배경 차량
    nb = rng.poisson(bg * (REL1 - REL0))
    bgt = rng.uniform(REL0 * 60, REL1 * 60, nb)
    # 차로 배정 + 차단기 최소 간격(barrier_s) 적용
    allt = np.concatenate([rel, bgt]); is_show = np.r_[np.ones(N, bool), np.zeros(nb, bool)]
    lane = rng.integers(0, lanes, len(allt))
    order = np.argsort(allt); allt, is_show, lane = allt[order], is_show[order], lane[order]
    for L in range(lanes):
        idx = np.flatnonzero(lane == L); last = -np.inf
        for i in idx:
            allt[i] = max(allt[i], last + barrier_s); last = allt[i]
    keep = (allt >= REL0 * 60) & (allt < REL1 * 60)
    allt, is_show, lane = allt[keep], is_show[keep], lane[keep]
    minute = np.floor(allt / 60).astype(int) - REL0
    exits = np.bincount(minute, minlength=REL1 - REL0)
    show_ex = np.bincount(minute[is_show], minlength=REL1 - REL0)
    return {"exits": exits, "show_exits": show_ex, "N": int(is_show.sum()),
            "times_s": allt, "lane": lane, "is_show": is_show}


def dataset(world, n_show=40, gates=("동2문", "북2문", "남2문"), mu=5.0, seed=0, Nrange=(60, 800), scale_rng=(8, 15)):
    """공연 n_show개, 각 공연 1~3일 연속(같은 공연=클러스터), 문 3개.
    공연 크기 S ~ 로그균등, 연속일은 비슷한 크기(±10%)."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_show):
        S = np.exp(rng.uniform(np.log(Nrange[0]), np.log(Nrange[1])))
        for day in range(rng.integers(1, 4)):
            for gi, g in enumerate(gates):
                N = int(S * rng.uniform(0.9, 1.1) * (1.0, 0.8, 0.7)[gi])
                sc_ = rng.uniform(*scale_rng)
                if world == "disperse_scale":          # 큰 공연일수록 퇴장이 넓게 퍼짐(줄 없이 β<1을 만드는 교란)
                    sc_ = sc_ * (N / 200) ** 0.6
                c = one_curve(N, "disperse" if world == "disperse_scale" else world, mu=mu, scale=sc_, rng=rng)
                rows.append({"show_id": s, "date": f"S{s}D{day}", "gate": g, **c})
    return rows
