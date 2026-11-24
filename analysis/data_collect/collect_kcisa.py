"""문화공공데이터광장(KCISA) API로 한국체육산업개발 데이터를 내려받는다.

  이름     데이터                                         .env 키 이름
  perf     올림픽공원 공연정보 (data.go.kr 3037877)         KCISA_KEY_PERF
  cpm009   올림픽공원 2025 차량입출차·공연·날씨 연계 데이터  KCISA_KEY_CPM009
  cpm008   올림픽공원 2025 입출차정보 (약 240만 건)          KCISA_KEY_CPM008
  rental   올림픽공원 대관정보 (선택, 키 있으면 사용)        KCISA_KEY_RENTAL
  ops      올림픽공원 운영현황 (선택, 키 있으면 사용)        KCISA_KEY_OPS

동작 순서
  1) collect : 페이지 단위로 받아 응답 원본을 raw/kcisa/<이름>/page_00001.* 에 그대로 저장
               (이미 받은 페이지는 건너뛰므로, 중간에 끊겨도 다시 실행하면 이어받는다)
  2) parse   : 원본 페이지들을 합쳐 processed/kcisa_<이름>.csv 로 정리
               DESCRIPTION 의 "키:값, 키:값" 문자열은 열로 나눈다

사용법 (03_데이터 폴더에서)
  python scripts/collect_kcisa.py test            # 키가 있는 API마다 5건씩 받아 응답 구조 확인 (저장 안 함)
  python scripts/collect_kcisa.py collect perf    # 공연정보 수집
  python scripts/collect_kcisa.py collect cpm009  # 연계 데이터 수집
  python scripts/collect_kcisa.py collect cpm008  # 입출차 수집 (오래 걸림)
  python scripts/collect_kcisa.py parse perf      # CSV 정리
  python scripts/collect_kcisa.py status          # 수집 진행 상황

인증키: 03_데이터/.env 파일에 한 줄씩 (예: KCISA_KEY_PERF=발급받은키)
        같은 이름의 환경변수가 있으면 그쪽이 우선. 모든 API에 같은 키를 쓰면 KCISA_API_KEY 한 줄만 써도 됨
        (코드·로그에 키를 남기지 않는다. 화면 출력의 URL도 키를 가린다)
표준 라이브러리만 사용 (pip 설치 불필요)
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # 03_데이터
RAW = BASE / "raw" / "kcisa"
OUT = BASE / "processed"
# 이름: (요청 주소, .env 키 이름, 설명)
APIS = {
    "perf":   ("https://api.kcisa.kr/openapi/service/rest/meta/KSCperf", "KCISA_KEY_PERF", "올림픽공원 공연정보"),
    "cpm009": ("https://api.kcisa.kr/openapi/API_CPM_009/request", "KCISA_KEY_CPM009", "2025 차량입출차·공연·날씨 연계"),
    "cpm008": ("https://api.kcisa.kr/openapi/API_CPM_008/request", "KCISA_KEY_CPM008", "2025 입출차정보"),
    "rental": ("https://api.kcisa.kr/openapi/service/rest/meta12/getKSCD0803", "KCISA_KEY_RENTAL", "올림픽공원 대관정보"),
    "ops":    ("https://api.kcisa.kr/openapi/service/rest/meta12/getKSCD0801", "KCISA_KEY_OPS", "올림픽공원 운영현황"),
}
ROWS = int(os.environ.get("KCISA_ROWS", "1000"))   # 한 번에 받을 건수 (거부되면 자동으로 줄임)
SLEEP = 0                                            # (요청 간격은 MIN_GAP으로 관리)
TIMEOUT = 120                                        # 서버 응답 대기 한도(초) — 큰 쪽은 느릴 수 있음
MIN_GAP = 2.2                                        # 서버 한도: 2초에 1건 → 여유 두고 2.2초 간격
_last_call = [0.0]
_lock = threading.Lock()
WORKERS = int(os.environ.get("KCISA_WORKERS", "8"))   # 동시에 기다리는 요청 수 (시작 간격은 MIN_GAP 유지)
RETRIES = 4


# ---------------------------------------------------------------- 키·요청
def read_env() -> dict:
    env, f = {}, BASE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_key(api: str, required: bool = True) -> str:
    name = APIS[api][1]
    env = read_env()
    for k in (name, "KCISA_API_KEY"):
        v = os.environ.get(k, "").strip() or env.get(k, "")
        if v and not v.startswith("여기에"):
            return v
    if required:
        sys.exit(f"[오류] {api}({APIS[api][2]})의 키가 없습니다. 03_데이터/.env 에 {name}=발급받은키 를 적어 주세요.")
    return ""


def build_url(api: str, key: str, page: int, rows: int) -> str:
    # 키가 이미 URL 인코딩돼 있으면(%가 있으면) 그대로, 아니면 인코딩
    k = key if "%" in key else urllib.parse.quote(key, safe="")
    return f"{APIS[api][0]}?serviceKey={k}&numOfRows={rows}&pageNo={page}"


def mask(url: str) -> str:
    return re.sub(r"serviceKey=[^&]+", "serviceKey=****", url)


def _throttle():
    """서버 한도(2초에 1건)를 지키도록 요청 '시작'을 MIN_GAP초 이상 띄운다.
    여러 스레드가 함께 써도 시작 간격이 지켜지도록 잠금을 건다."""
    with _lock:
        wait = MIN_GAP - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def fetch(api: str, key: str, page: int, rows: int) -> str:
    """요청 1회. 4xx(키·권한 문제)는 재시도하지 않고 서버 메시지를 보여 준다.
    429(요청 한도)는 잠깐 쉬고 다시 보내며 실패 횟수에 넣지 않는다.
    5xx·네트워크 오류는 재시도하고, JSON 요청이 계속 실패하면 XML(기본 형식)로도 시도한다."""
    url = build_url(api, key, page, rows)
    last, i, limited = None, 1, 0
    while i <= RETRIES:
        accept = "application/json" if i <= 2 else "*/*"      # 3번째부터는 JSON 요청 헤더 없이
        _throttle()
        try:
            req = urllib.request.Request(url, headers={
                "Accept": accept, "User-Agent": "kspo-contest-collector/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:300].replace(key, "****")
            except Exception:                                    # 오류 본문을 읽다 끊겨도 멈추지 않게
                body = "(본문 읽기 실패)"
            last = f"HTTP {e.code} {e.reason} | 서버 응답: {body}"
            if e.code == 429:
                limited += 1
                if limited > 20:
                    raise RuntimeError(f"{page}쪽: 요청 한도(429)가 계속됨 — 잠시 뒤 다시 실행하세요")
                time.sleep(3)
                continue                                         # 실패 횟수에 넣지 않음
            if 400 <= e.code < 500:
                raise RuntimeError(f"{page}쪽 요청 거부 — {last}")
            print(f"  ! {page}쪽 HTTP {e.code} (Accept={accept}) | {body[:150]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            print(f"  ! {page}쪽 요청 실패({last[:120]})")
        if i < RETRIES:
            wait = 5 * i
            print(f"    → {wait}초 후 재시도 {i}/{RETRIES - 1}")
            time.sleep(wait)
        i += 1
    raise RuntimeError(f"{page}쪽을 {RETRIES}번 시도했지만 실패 — {last}  ({mask(url)})")


# ---------------------------------------------------------------- 응답 해석
def parse_body(text: str) -> dict:
    """JSON/XML 어느 쪽이 와도 {code, msg, total, items} 로 통일."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        d = json.loads(t)
        resp = d.get("response", d)
        head = resp.get("header", {}) or {}
        body = resp.get("body", {}) or {}
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        return {"code": str(head.get("resultCode", "")), "msg": head.get("resultMsg", ""),
                "total": int(body.get("totalCount", 0) or 0), "items": items or []}
    root = ET.fromstring(t)
    get = lambda tag: (root.findtext(f".//{tag}") or "").strip()
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return {"code": get("resultCode"), "msg": get("resultMsg"),
            "total": int(get("totalCount") or 0), "items": items}


def ext(text: str) -> str:
    return ".json" if text.lstrip().startswith(("{", "[")) else ".xml"


def ok(p: dict) -> bool:
    return p["code"] in ("0000", "00", "0", "")


# ---------------------------------------------------------------- 명령
def cmd_test():
    for api in APIS:
        key = load_key(api, required=False)
        if not key:
            print(f"\n=== {api} ({APIS[api][2]}): .env 에 {APIS[api][1]} 없음 → 건너뜀")
            continue
        print(f"\n=== {api} ({APIS[api][2]})  {mask(build_url(api, key, 1, 5))}")
        try:
            text = fetch(api, key, 1, 5)
        except RuntimeError as e:
            print("→ 요청 실패:", str(e).replace(key, "****"))
            continue
        print("응답 앞부분:", text[:500].replace(key, "****"))
        try:
            p = parse_body(text)
            print(f"→ 결과코드 {p['code']} {p['msg']} | 전체 {p['total']:,}건 | 이번 쪽 {len(p['items'])}건")
            if p["items"]:
                print("→ 첫 항목:", json.dumps(p["items"][0], ensure_ascii=False)[:500])
        except Exception as e:
            print("→ 해석 실패:", e)


def _download(api: str, key: str, d: Path, todo: list, rows: int):
    # 아직 안 받은 쪽만 골라, WORKERS개를 동시에 기다리며 받는다.
    # 서버 응답이 수십 초 걸리므로 한 번에 하나씩 기다리면 대부분 시간을 놀게 된다.
    # 요청 '시작'은 _throttle()이 2.2초 간격으로 줄 세우므로 서버 한도(2초 1건)는 그대로 지킨다.
    print(f"  남은 {len(todo):,}쪽을 동시 {WORKERS}개로 받습니다 (중단: Ctrl+C, 다시 실행하면 이어받음)")
    stop = threading.Event()
    t0, done_now, fails = time.time(), 0, []

    def job(pg):
        if stop.is_set():
            return pg, "skip"
        try:
            text = fetch(api, key, pg, rows)
        except RuntimeError as e:
            return pg, f"fail: {e}"
        p = parse_body(text)
        if not ok(p):
            stop.set()                                    # 하루 한도 초과 등 → 전체 멈춤
            return pg, f"server: {p['code']} {p['msg']}"
        tmp = d / f"page_{pg:05d}{ext(text)}.part"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(d / f"page_{pg:05d}{ext(text)}")      # 다 쓴 뒤 이름을 바꿔 깨진 파일이 남지 않게
        return pg, "ok"

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(job, pg) for pg in todo]
        try:
            for fut in as_completed(futs):
                pg, res = fut.result()
                if res == "ok":
                    done_now += 1
                    if done_now % 20 == 0:
                        el = time.time() - t0
                        left = len(todo) - done_now
                        print(f"  이번 실행 {done_now:,}/{len(todo):,}쪽 | {el/60:.1f}분 | "
                              f"쪽당 {el/done_now:.1f}초 | 남은 예상 {left*el/done_now/60:.0f}분")
                elif res != "skip":
                    fails.append(pg)
                    print(f"  ! {pg}쪽 {res[:160]}")
        except KeyboardInterrupt:
            stop.set()
            print("\n  중단 요청 — 진행 중인 요청이 끝나면 멈춥니다. 다시 실행하면 이어받습니다.")
            for f in futs:
                f.cancel()
            raise
    for f in d.glob("page_*.part"):                       # 혹시 남은 미완성 파일 정리
        f.unlink(missing_ok=True)
    if fails:
        print(f"  실패 {len(fails)}쪽 (예: {fails[:10]}) → 같은 명령을 다시 실행하면 그 쪽만 다시 받습니다.")
    if stop.is_set():
        print("  서버가 오류 코드를 보내 멈췄습니다. 잠시 뒤 다시 실행하세요.")


def cmd_collect(api: str):
    key = load_key(api)
    d = RAW / api
    d.mkdir(parents=True, exist_ok=True)
    rows = ROWS
    meta_f = d / "_meta.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    if meta.get("rows"):
        rows = meta["rows"]                       # 이어받을 때 같은 쪽 크기 유지

    # 1쪽으로 전체 건수와 허용 쪽 크기 확인
    olds = sorted(d.glob("page_00001.*"))
    if olds:
        first = olds[0]
        p = parse_body(first.read_text(encoding="utf-8"))
    else:
        while True:
            text = fetch(api, key, 1, rows)
            p = parse_body(text)
            if ok(p) and p["items"]:
                break
            if rows > 100:
                print(f"  {rows}건 단위가 거부됨({p['code']} {p['msg']}) → {rows // 2}건으로 줄여 재시도")
                rows //= 2
                continue
            sys.exit(f"[중단] 1쪽 요청 실패: {p['code']} {p['msg']}\n응답: {text[:300]}")
        if len(p["items"]) < min(rows, p["total"]):   # 서버가 상한을 몰래 적용한 경우
            rows = len(p["items"])
        first = d / f"page_00001{ext(text)}"
        first.write_text(text, encoding="utf-8")
    total = p["total"]
    pages = -(-total // rows)
    meta.update({"api": api, "rows": rows, "total": total, "pages": pages})
    meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{api}({APIS[api][2]}): 전체 {total:,}건, 쪽당 {rows}건 → {pages:,}쪽")

    have = {f.name[5:10] for f in d.glob("page_*.*")}
    todo = [pg for pg in range(1, pages + 1) if f"{pg:05d}" not in have]
    _download(api, key, d, todo, rows)
    cmd_status(api)


KV = re.compile(r"\s*([^:,]+?)\s*:\s*([^,]*)")


def split_desc(s: str) -> dict:
    """'주차대수:19,방문객:57, 기온:-3.4, 강수량:, ...' → {'주차대수':'19', ...}"""
    return {m.group(1).strip(): m.group(2).strip() for m in KV.finditer(s or "")}


def _flatten(it: dict) -> dict:
    r = dict(it)
    for col in ("DESCRIPTION", "SUB_DESCRIPTION"):
        if col in it:
            for k, v in split_desc(it[col]).items():
                r[f"{col[:4].lower()}_{k}"] = v
    return r


def cmd_parse(api: str):
    """페이지를 하나씩 읽어 바로 CSV에 쓴다(240만 행도 메모리에 다 올리지 않음)."""
    d = RAW / api
    files = sorted(f for f in d.glob("page_*.*") if not f.name.endswith(".part"))
    if not files:
        sys.exit(f"[오류] {d} 에 받은 페이지가 없습니다. 먼저 collect 를 실행하세요.")
    rd = RAW / f"{api}_refetch"
    if rd.exists():                                       # 다시 받은 쪽도 합친다
        extra_files = sorted(f for f in rd.glob("page_*.*") if not f.name.endswith(".part"))
        print(f"  다시 받은 쪽 {len(extra_files):,}개도 합칩니다 ({rd.name})")
        files += extra_files
    keys = []                                             # 앞쪽 몇 페이지로 열 목록 확정
    for f in files[:5]:
        for it in parse_body(f.read_text(encoding="utf-8"))["items"]:
            for k in _flatten(it):
                if k not in keys:
                    keys.append(k)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"kcisa_{api}.csv"
    n, ids, dup, extra, bad = 0, set(), 0, set(), []
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for i, f in enumerate(files, 1):
            try:
                items = parse_body(f.read_text(encoding="utf-8"))["items"]
            except Exception as e:
                bad.append(f.name)
                print(f"  ! {f.name} 읽기 실패: {e}")
                continue
            for it in items:
                r = _flatten(it)
                lid = r.get("LOCAL_ID")
                if lid:
                    if lid in ids:
                        dup += 1                          # 같은 기록은 한 번만 저장
                        continue
                    ids.add(lid)
                extra.update(k for k in r if k not in keys)
                w.writerow(r)
                n += 1
            if i % 200 == 0:
                print(f"  {i:,}/{len(files):,}쪽 정리 중… {n:,}행")
    meta_f = d / "_meta.json"
    total = json.loads(meta_f.read_text(encoding="utf-8")).get("total") if meta_f.exists() else None
    print(f"{out} 저장: 서로 다른 기록 {n:,}행, {len(keys)}열 (중복 {dup:,}행은 한 번만 저장)")
    if total:
        print(f"  검산 | 서버 totalCount {total:,} 중 {n:,}건 확보 = {n / total:.1%} → {'완전' if n == total else f'{total - n:,}건 부족'}")
    if extra:
        print(f"  참고 | 앞쪽 페이지에 없던 열 {sorted(extra)[:10]} 은 저장하지 않음")
    if bad:
        print(f"  ! 읽지 못한 페이지 {len(bad)}개: {bad[:10]} → 해당 파일을 지우고 collect 를 다시 실행하세요")


def cmd_verify(api: str):
    """저장된 쪽 몇 개를 지금 다시 받아 같은 행이 오는지 비교한다.
    같으면 서버의 쪽 순서가 지금은 안정적이고, 다르면 요청할 때마다 순서가 바뀐다는 뜻."""
    key = load_key(api)
    d = RAW / api
    m = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
    dup = json.loads((d / "_dup_pages.json").read_text(encoding="utf-8"))["pages"] if (d / "_dup_pages.json").exists() else {}
    sample = [1, 500, 1200, 2000]
    sample += [int(k) for k in list(dup)[:3]]                 # 겹침이 있었던 쪽도 3개
    for pg in sample:
        old = sorted(d.glob(f"page_{pg:05d}.*"))
        if not old:
            continue
        a = {it.get("LOCAL_ID") for it in parse_body(old[0].read_text(encoding="utf-8"))["items"]}
        b = {it.get("LOCAL_ID") for it in parse_body(fetch(api, key, pg, m["rows"]))["items"]}
        tag = " (겹침 있던 쪽)" if str(pg) in dup else ""
        print(f"  {pg}쪽{tag}: 저장본과 같은 ID {len(a & b)}/{len(b)}  → {'같음' if a == b else '다름'}")


def cmd_probe(api: str, rows: int):
    """쪽 크기를 바꿔(예: 500건) 몇 쪽 받아 보고, 아직 없는 기록이 얼마나 섞여 나오는지 잰다.
    같은 쪽 크기로는 서버가 늘 같은(겹치는) 묶음을 주므로, 크기를 바꿔야 빠진 기록이 나오는지 확인하는 실험."""
    key = load_key(api)
    d = RAW / api
    have_f = d / "_have_ids.txt"
    have = set(have_f.read_text().split()) if have_f.exists() else set()
    total = json.loads((d / "_meta.json").read_text(encoding="utf-8"))["total"]
    pages = -(-total // rows)
    sample = [round(pages * q) or 1 for q in (0.02, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.98)]
    new_all, got_all = 0, 0
    for pg in sample:
        ids = [it.get("LOCAL_ID") for it in parse_body(fetch(api, key, pg, rows))["items"]]
        new = sum(1 for i in ids if i not in have)
        new_all += new; got_all += len(ids)
        print(f"  {rows}건 단위 {pg}쪽: {len(ids)}건 중 새 기록 {new}건 ({new / max(len(ids), 1):.0%})")
    print(f"  합계: {got_all}건 중 새 기록 {new_all}건 ({new_all / max(got_all, 1):.1%}) — 빠진 비율은 약 16%")


def cmd_refetch(api: str, limit: int | None = None):
    """겹침이 있던 쪽(_dup_pages.json)만 다시 받아 <api>_refetch 폴더에 따로 저장한다(원본 raw는 그대로 둠).
    parse 할 때 두 폴더를 합치고 LOCAL_ID 중복을 걸러 낸다."""
    key = load_key(api)
    d = RAW / api
    m = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
    dup = json.loads((d / "_dup_pages.json").read_text(encoding="utf-8"))["pages"]
    out = RAW / f"{api}_refetch"
    out.mkdir(exist_ok=True)
    have = {f.name[5:10] for f in out.glob("page_*.*") if not f.name.endswith(".part")}
    todo = sorted(int(k) for k in dup if f"{int(k):05d}" not in have)
    if limit:                                             # 시범: 앞에서부터 고르게 limit쪽만
        step = max(1, len(todo) // limit)
        todo = todo[::step][:limit]
    print(f"{api}: 겹침 있던 {len(dup):,}쪽 중 {len(todo):,}쪽을 다시 받습니다 → {out}")
    _download(api, key, out, todo, m["rows"])
    # 수확률: 다시 받은 쪽에서 처음 보는 기록이 얼마나 나왔나
    have_f = d / "_have_ids.txt"
    if have_f.exists():
        known = set(have_f.read_text().split())
        got = new = 0
        newset = set()
        for f in out.glob("page_*.*"):
            if f.name.endswith(".part"):
                continue
            for it in parse_body(f.read_text(encoding="utf-8"))["items"]:
                got += 1
                lid = it.get("LOCAL_ID")
                if lid not in known and lid not in newset:
                    new += 1
                    newset.add(lid)
        pages_done = len([f for f in out.glob("page_*.*") if not f.name.endswith(".part")])
        miss = m["total"] - len(known)
        print(f"  수확 | 다시 받은 {pages_done:,}쪽 {got:,}건 중 처음 보는 기록 {new:,}건 ({new / max(got, 1):.1%}), "
              f"쪽당 {new / max(pages_done, 1):.0f}건 → 빠진 {miss:,}건 중 {new / miss:.1%} 채움")


def cmd_status(api: str | None = None):
    for a in ([api] if api else APIS):
        d = RAW / a
        meta_f = d / "_meta.json"
        if not meta_f.exists():
            print(f"{a}({APIS[a][2]}): 아직 시작 안 함")
            continue
        m = json.loads(meta_f.read_text(encoding="utf-8"))
        got = len(list(d.glob("page_*.*")))
        print(f"{a}({APIS[a][2]}): {got:,}/{m['pages']:,}쪽 ({got / m['pages'] * 100:.1f}%), 전체 {m['total']:,}건, 쪽당 {m['rows']}건")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("test", "collect", "parse", "status", "verify", "probe", "refetch"):
        print(__doc__)
    elif a[0] == "test":
        cmd_test()
    elif a[0] == "status":
        cmd_status(a[1] if len(a) > 1 else None)
    elif len(a) < 2 or a[1] not in APIS:
        print("API 이름을 붙여 주세요:", ", ".join(f"{k}({v[2]})" for k, v in APIS.items()))
    elif a[0] == "collect":
        cmd_collect(a[1])
    elif a[0] == "verify":
        cmd_verify(a[1])
    elif a[0] == "refetch":
        cmd_refetch(a[1], int(a[2]) if len(a) > 2 else None)
    elif a[0] == "probe":
        cmd_probe(a[1], int(a[2]) if len(a) > 2 else 500)
    else:
        cmd_parse(a[1])
