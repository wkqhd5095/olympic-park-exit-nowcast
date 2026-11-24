"""공유 폴더의 nowcast-site 전체를 GitHub 저장소 main에 커밋 1개로 올리고 Pages(main /docs)를 켠다.
git push가 막힌 환경을 위해 GitHub REST API(Git Data API)만 쓴다. 표준 라이브러리만 사용.
토큰: 05_개발/.env 의 GITHUB_TOKEN (코드·저장소에 남기지 않음)
실행: python scripts/deploy.py "커밋 메시지"""
import base64, fnmatch, json, os, sys, urllib.request, urllib.error
from pathlib import Path
SRC = Path(__file__).resolve().parents[1]; REPO = "wkqhd5095/olympic-park-exit-nowcast"; BR = "main"
TOKEN = next(l.split("=", 1)[1].strip().strip('"') for l in (SRC.parent / ".env").read_text(encoding="utf-8").splitlines() if l.startswith("GITHUB_TOKEN="))
IGNORE = [l.strip() for l in (SRC / ".gitignore").read_text().splitlines() if l.strip()]

def api(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

def ignored(rel):
    parts = rel.split("/")
    return any(fnmatch.fnmatch(p, pat.rstrip("/")) for pat in IGNORE for p in parts)

files = sorted(p for p in SRC.rglob("*") if p.is_file() and not ignored(p.relative_to(SRC).as_posix()))
code, ref = api("GET", f"/git/ref/heads/{BR}"); parent = ref["object"]["sha"] if code == 200 else None
tree = []
for p in files:
    c, b = api("POST", "/git/blobs", {"content": base64.b64encode(p.read_bytes()).decode(), "encoding": "base64"})
    assert c == 201, (p, c, b)
    mode = "100755" if p.suffix == ".sh" else "100644"
    tree.append({"path": p.relative_to(SRC).as_posix(), "mode": mode, "type": "blob", "sha": b["sha"]})
c, t = api("POST", "/git/trees", {"tree": tree}); assert c == 201, t
msg = (sys.argv[1] if len(sys.argv) > 1 else "나우캐스트 화면·분석 코드 갱신") + \
      "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01KPAg4XizGbTiXLGayxaaUN"
c, cm = api("POST", "/git/commits", {"message": msg, "tree": t["sha"], "parents": [parent] if parent else [],
                                     "author": {"name": "이대훈", "email": "wkqhd5095@users.noreply.github.com"}}); assert c == 201, cm
c, r = api("PATCH", f"/git/refs/heads/{BR}", {"sha": cm["sha"]}) if parent else api("POST", "/git/refs", {"ref": f"refs/heads/{BR}", "sha": cm["sha"]})
assert c in (200, 201), r
print(f"커밋 {cm['sha'][:7]} · 파일 {len(files)}개")
c, pg = api("GET", "/pages")
if c == 404: c, pg = api("POST", "/pages", {"source": {"branch": BR, "path": "/docs"}}); print("Pages 생성", c)
print("Pages:", pg.get("html_url"), pg.get("status"), pg.get("source"))
