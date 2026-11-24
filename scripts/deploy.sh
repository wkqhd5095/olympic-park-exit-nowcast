#!/usr/bin/env bash
# 공유 폴더의 nowcast-site를 GitHub 저장소 main에 올리고 Pages(main /docs)를 켠다.
# 토큰: 05_개발/.env 의 GITHUB_TOKEN (저장소에는 올라가지 않음). git 작업은 OneDrive 밖(~/nowcast-repo)에서 한다.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"; ENV="$SRC/../.env"; REPO=wkqhd5095/olympic-park-exit-nowcast; W="$HOME/nowcast-repo"
TOKEN=$(grep -E '^GITHUB_TOKEN=' "$ENV" | head -1 | cut -d= -f2- | tr -d '\r" ')
[ -n "$TOKEN" ] || { echo "토큰 없음: $ENV"; exit 1; }
API() { curl -sS -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "$@"; }
mkdir -p "$W"; rsync -a --delete --exclude .git "$SRC"/ "$W"/; cd "$W"
[ -d .git ] || { git init -q -b main; git remote add origin "https://github.com/$REPO.git"; }
git config user.name "이대훈"; git config user.email "wkqhd5095@users.noreply.github.com"
git add -A
git diff --cached --quiet || git commit -q -m "${1:-나우캐스트 화면·분석 코드 갱신}

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KPAg4XizGbTiXLGayxaaUN"
git -c http.extraheader="Authorization: Basic $(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)" push -q -u origin main
echo "push 완료: $(git rev-parse --short HEAD)"
code=$(API -o /tmp/pages.json -w '%{http_code}' "https://api.github.com/repos/$REPO/pages")
if [ "$code" = "404" ]; then
  API -X POST "https://api.github.com/repos/$REPO/pages" -d '{"source":{"branch":"main","path":"/docs"}}' -o /tmp/pages.json -w 'Pages 생성 %{http_code}\n'
fi
grep -E '"(html_url|status)"' /tmp/pages.json || cat /tmp/pages.json
