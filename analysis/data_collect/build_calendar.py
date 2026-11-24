"""올림픽공원 홈페이지 공연 일정(raw/ksponco_eventinfo/ksponco_eventinfo_2025.tsv) → 2025 공연 캘린더.

출력
  raw/공연캘린더_2025_상세.csv   공연 × 날짜 (시작시각들, 예정 종료시각, 해석 방법)
  raw/공연캘린더_2025.csv        날짜 단위 (05_개발/sim/prep.py 가 자동 사용)

시간 안내(tm) 해석 순서
  ① 요일형  "금 오후 8시 / 토 오후 6시 / 일 오후 4시", "(토) 오후 6시", "금요일 오후 8시", "금, 토, 일 오후 5시"
  ② 날짜형  "2025년 1월 10일(금) 오후 7시 30분 ...", "5.17(토) 18:00", "11/15(토) 18시", "17일(토) 6PM"
  ③ 시각만  "17시", "11:00" → 기간 전체에 적용
  ④ 실패    시각 없음(예정 종료시각 비움) — 표시만
시각 규칙: 오후/저녁/PM → +12. 오전·AM·표시 없음이면 1~9시는 오후로 봄(공연이 새벽 1~9시에 시작하지 않으므로), 10~12시는 그대로.
공연장 등급(가정): 02 KSPO DOME·06 88잔디마당/옥외 = 대형, 01 올림픽홀·03 핸드볼경기장 = 중형, 04 우리금융아트홀·07 뮤즈라이브 = 소형
"""
from __future__ import annotations
import csv, re, sys
from datetime import date, timedelta, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "raw" / "ksponco_eventinfo" / "ksponco_eventinfo_2025.tsv"
VENUE = {"01": "올림픽홀", "02": "KSPO DOME", "03": "핸드볼경기장", "04": "우리금융아트홀", "06": "88잔디마당·옥외", "07": "뮤즈라이브"}
CLASS = {"02": "대형", "06": "대형", "01": "중형", "03": "중형", "04": "소형", "07": "소형"}
WD = "월화수목금토일"
TIME = re.compile(r"(오전|오후|저녁)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시\s*(?:(\d{1,2})\s*분|반)?)\s*(PM|AM)?|(\d{1,2})\s*(PM|AM)", re.I)

def times_in(s: str, carry_pm=False) -> list[str]:
    out, pm = [], carry_pm
    for m in TIME.finditer(s):
        if m.group(6):                                   # "7PM"
            h, mi, ap = int(m.group(6)), 0, m.group(7).upper()
        else:
            h = int(m.group(2)); mi = int(m.group(3) or m.group(4) or (30 if "반" in m.group(0) else 0))
            ap = (m.group(5) or "").upper()
            if m.group(1) in ("오후", "저녁"): pm = True
            elif m.group(1) == "오전": pm = False
            if not m.group(3) and "시" not in m.group(0) and not ap: continue
        if h > 24 or mi > 59: continue
        if ap == "PM" or (pm and h < 12) or (not ap and m.group(1) != "오전" and 1 <= h <= 9):
            h = h + 12 if h < 12 else h
        out.append(f"{h:02d}:{mi:02d}")
    return out

def weekday_map(tm: str) -> dict[int, list[str]]:
    """요일형 안내 → {요일: [시각]}. 날짜(예: 25일(토))의 '일'·괄호 요일은 제외."""
    s = re.sub(r"\d{1,2}\s*일\s*\(?\s*[월화수목금토일](요일)?\s*\)?", " ", tm)     # 날짜+요일 표기 제거
    s = re.sub(r"(\d{4}\s*년)|(\d{1,2}\s*월)", " ", s)
    pat = re.compile(r"(?<![0-9가-힣])\(?([월화수목금토일](?:\s*[,~]\s*[월화수목금토일])*)(?:요일)?\)?(?![가-힣])\s*[:：]?\s*"
                     r"((?:(?:오전|오후|저녁)?\s*\d{1,2}\s*(?::\d{2}|시(?:\s*\d{1,2}\s*분)?)?\s*(?:PM|AM)?\s*(?:\(KST\))?\s*,?\s*/?\s*)+)", re.I)
    out: dict[int, list[str]] = {}
    for m in pat.finditer(s):
        days_txt, times_txt = m.group(1), m.group(2)
        ts = times_in(times_txt)
        if not ts: continue
        if "~" in days_txt:
            a, b = [WD.index(x) for x in re.findall(r"[월화수목금토일]", days_txt)][:2]
            ds = [(a + i) % 7 for i in range((b - a) % 7 + 1)]
        else:
            ds = [WD.index(x) for x in re.findall(r"[월화수목금토일]", days_txt)]
        for d in ds: out[d] = sorted(set(out.get(d, []) + ts))
    return out

def dated_map(tm: str, b: date, e: date) -> dict[date, list[str]]:
    """날짜형 안내 → {날짜: [시각]}. 표기 형식: 'M월 D일', 'M.D(', 'M/D(', 'D일(' (월은 앞에서 이어받음)"""
    marks = []
    for m in re.finditer(r"(?:(\d{4})\s*[년.]\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일|(?:\d{4}\.)?(?<![\d])(\d{1,2})\s*[./]\s*(\d{1,2})\s*\(|(?<![\d월])\s(\d{1,2})\s*일\s*\(", tm):
        if m.group(2): mo, dy = int(m.group(2)), int(m.group(3))
        elif m.group(4): mo, dy = int(m.group(4)), int(m.group(5))
        else: mo, dy = (marks[-1][1].month if marks else b.month), int(m.group(6))
        yr = b.year if mo >= b.month else e.year
        try: dt = date(yr, mo, dy)
        except ValueError: continue
        if b <= dt <= e: marks.append((m.start(), dt, m.end()))
    out: dict[date, list[str]] = {}
    for i, (st, dt, en) in enumerate(marks):
        seg = tm[en: marks[i + 1][0] if i + 1 < len(marks) else len(tm)]
        ts = times_in(seg)
        if ts: out[dt] = sorted(set(out.get(dt, []) + ts))
    return out

def runtime(s: str):
    if not s: return None
    h = re.search(r"(\d+)\s*시간", s); m = re.search(r"(\d+)\s*분", s)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0) or None

DEFAULT_RT = None   # main()에서 중·대형 공연 러닝타임 중앙값으로 채움 (러닝타임 미기재 공연에 사용, rt_source='추정')

def expand(r: dict) -> list[dict]:
    b = date.fromisoformat(r["beginDt"]); e = date.fromisoformat(r["endDt"]); tm = r["tm"] or ""
    rt = runtime(r["ndTm"]); rt_src = "기재" if rt else ""
    if not rt and DEFAULT_RT and CLASS.get(r["plcCd"]) in ("대형", "중형"):
        rt, rt_src = DEFAULT_RT, "추정(중앙값)"
    days = [b + timedelta(i) for i in range((e - b).days + 1)]
    wm = weekday_map(tm); dm = dated_map(tm, b, e); flat = times_in(tm)
    out = []
    for d in days:
        if d.year != 2025: continue
        if dm and len(dm) == 1 and not wm and "~" in tm and len(days) > 1:
            ts, how = sorted({t for v in dm.values() for t in v}), "기간+시각"
        elif dm and len(dm) >= 2 or (dm and not wm):
            ts, how = dm.get(d), "날짜형"
            if ts is None: continue                      # 날짜형 안내에 없는 날 = 공연 없음
        elif wm:
            ts, how = wm.get(d.weekday()), "요일형"
            if ts is None: continue
        elif flat:
            ts, how = sorted(set(flat)), "시각만"
        else:
            ts, how = [], "실패"
        rng = re.findall(r"(\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분)?|:\d{2}))\s*~\s*(\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분)?|:\d{2}))", tm)
        explicit_end = max(times_in(" ".join(b for _, b in rng))) if rng else ""
        if explicit_end:
            ts = sorted(set(times_in(" ".join(a for a, _ in rng)))); how += "+종료명시"
        last = max(ts) if ts else ""
        end = explicit_end
        if not end and last and rt:
            end = (datetime(2000, 1, 1, *map(int, last.split(":"))) + timedelta(minutes=rt)).strftime("%H:%M")
        out.append({"date": d.isoformat(), "evtSeq": r["evtSeq"], "plcCd": r["plcCd"], "venue": VENUE.get(r["plcCd"], r["plc"]),
                    "venue_class": CLASS.get(r["plcCd"], "?"), "title": r["title"], "times": " ".join(ts), "last_start": last,
                    "runtime_min": rt or "", "rt_source": rt_src if not explicit_end else "종료명시", "end_time_sched": end, "parse": how, "run_days": len(days)})
    return out

def main():
    global DEFAULT_RT
    with open(SRC, encoding="utf-8") as f: ev = list(csv.DictReader(f, delimiter="\t"))
    import statistics
    known = [runtime(r["ndTm"]) for r in ev if runtime(r["ndTm"]) and CLASS.get(r["plcCd"]) in ("대형", "중형") and runtime(r["ndTm"]) <= 200]
    DEFAULT_RT = int(statistics.median(known))
    print(f"[러닝타임] 중·대형 기재 {len(known)}건 중앙값 {DEFAULT_RT}분 → 미기재 공연에 적용(추정 표시)")
    det = [x for r in ev for x in expand(r)]
    with open(BASE / "raw" / "공연캘린더_2025_상세.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(det[0])); w.writeheader(); w.writerows(det)
    # cpm009 관람객 수
    att = {}
    c9 = BASE / "processed" / "kcisa_cpm009.csv"
    if c9.exists():
        with open(c9, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("desc_공연명") and r.get("desc_공연관람객수"):
                    try: att[r["ISSUED_DATE"][:10]] = max(att.get(r["ISSUED_DATE"][:10], 0), float(r["desc_공연관람객수"]))
                    except ValueError: pass
    rank = {"대형": 0, "중형": 1, "소형": 2, "?": 3}
    byd: dict[str, list] = {}
    for x in det: byd.setdefault(x["date"], []).append(x)
    cal = []
    for d, xs in sorted(byd.items()):
        xs.sort(key=lambda x: (rank[x["venue_class"]], x["run_days"] > 14, -(int(x["end_time_sched"].replace(":", "")) if x["end_time_sched"] else 0)))
        m = xs[0]
        ends = [x["end_time_sched"] for x in xs if x["end_time_sched"] and x["venue_class"] != "소형"]
        cal.append({"date": d, "show_name": m["title"], "main_venue": m["venue"], "venue_class": m["venue_class"],
                    "n_shows": len(xs), "venues": " | ".join(sorted({x["venue"] for x in xs})),
                    "large": sum(x["venue_class"] == "대형" for x in xs), "mid": sum(x["venue_class"] == "중형" for x in xs),
                    "small": sum(x["venue_class"] == "소형" for x in xs),
                    "last_start": m["last_start"], "end_time_sched": m["end_time_sched"],
                    "end_time_latest_midlarge": max(ends) if ends else "",
                    "attendance": att.get(d, ""), "source": "기관홈페이지" + ("+cpm009" if d in att else "")})
    with open(BASE / "raw" / "공연캘린더_2025.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(cal[0])); w.writeheader(); w.writerows(cal)
    # 검산·요약
    from collections import Counter
    pc = Counter(x["parse"] for x in det)
    nonsmall = [x for x in det if x["venue_class"] != "소형"]
    miss = sorted(set(att) - set(byd))
    print(f"[입력] 행사 {len(ev)}건 → 공연×날짜 {len(det)}행 → 공연일 {len(cal)}일")
    print(f"[해석] {dict(pc)} | 중·대형 공연장 시작시각 확보 {sum(bool(x['last_start']) for x in nonsmall)}/{len(nonsmall)}, "
          f"예정 종료시각 {sum(bool(x['end_time_sched']) for x in nonsmall)}/{len(nonsmall)}")
    print(f"[등급] 대형 공연 있는 날 {sum(c['large']>0 for c in cal)}, 중형 {sum(c['mid']>0 for c in cal)}, 소형만 {sum(c['large']+c['mid']==0 for c in cal)}")
    print(f"[검산] cpm009 공연일 {len(att)}일 중 캘린더에 있음 {len(att)-len(miss)}일" + (f" / 없음: {miss}" if miss else ""))

if __name__ == "__main__":
    main()
