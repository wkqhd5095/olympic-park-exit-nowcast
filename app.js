// 올림픽공원 출구별 출차 나우캐스트 — 서버 없이 브라우저에서 계산한다.
// 입력: data/*.json(2025 학습 결과, 동결 계수) + GitHub 로거의 실시간 주차현황 기록(raw CSV)
// 화면: 관람객용(결과만, 그림 중심) / 운영자용(숫자·범위·시간표) / 검증(평가자용 근거)
"use strict";
const LOGGER = "https://raw.githubusercontent.com/wkqhd5095/kspo-parking-logger/parking-data/data/parking/";
const GATES = ["남2문", "동2문", "북2문"];
const DOT = { "남2문": "#0369A1", "동2문": "#F97316", "북2문": "#059669" };
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
// 기획서 수치(data/*.json에 없는 값). 바꾸면 기획서와 함께 바꿀 것.
const PEAK_ALL = [45, 71, 97];  // 3-3: 예정 종료 → 출차 급증 시점(분), 2025 공연 94일 사분위·중앙
const INFO_ONLY_MAE = 116;      // 3-2 표: 공연 정보만으로 예측(공연장 규모×출구별 중앙값)
const CHECK2026 = { hit: 3, n: 5, relerr: 22, bias: 112 };  // 3-2 한계: 2026 공연일 5일 사후 점검
const SUBST = { from: 52.5, to: 64.3, days: 23 };           // 3-2 한계: 기관 집계 주차대수로 입력 대체

let D = {}, view = "vis", sel = null, token = 0;
const cache = {};

const $ = (s) => document.querySelector(s);
const fmt = (n) => Math.round(n).toLocaleString("ko-KR");
const kst = (dateStr, hhmm) => new Date(`${dateStr}T${hhmm}:00+09:00`);
const hm = (d) => d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const addMin = (d, m) => new Date(d.getTime() + m * 60000);
const todayKST = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const md = (d) => `${+d.slice(5, 7)}/${+d.slice(8)}(${DOW[new Date(d).getUTCDay()]})`;
const dur = (ms) => { const m = Math.round(ms / 60000); return `${Math.floor(m / 60)}시간${m % 60 ? ` ${m % 60}분` : ""}`; };

async function loadJSON(n) { const r = await fetch(`data/${n}.json`, { cache: "no-cache" }); return r.json(); }

/* ───────── 초기화 ───────── */
async function init() {
  [D.model, D.gates, D.back, D.shows] = await Promise.all(["model", "gates", "backtest", "shows"].map(loadJSON));
  const t = todayKST(), now = new Date();
  const chips = $("#chips");
  D.shows.forEach((s) => {
    const b = document.createElement("button");
    b.className = "chip" + (s.date === t ? " today" : ""); b.dataset.date = s.date;
    b.innerHTML = `<b>${md(s.date)} ${s.start}</b><span>${s.show}</span>`;
    b.title = s.show;
    b.addEventListener("click", () => pick(s.date));
    chips.appendChild(b);
  });
  // 기본 공연: 오늘 공연 → 결과가 나왔을 가장 최근 공연 → 다음 공연
  const ready = D.shows.filter((s) => addMin(kst(s.date, s.start), D.model.lead_min) < now);
  sel = (D.shows.find((s) => s.date === t) || ready[ready.length - 1] || D.shows[0]).date;

  renderProof();
  window.addEventListener("hashchange", route);
  route();
  pick(sel);
  setInterval(() => { if (cur && (cur.state === "wait" || cur.state === "pre") && cur.s.date === todayKST()) pick(sel); }, 5 * 60e3);
}

// 주소의 # 으로 화면 전환: 없음/#home = 첫 화면, #vis·#op·#proof = 결과 화면
function route() {
  const h = location.hash.replace("#", "");
  const home = !["vis", "op", "proof"].includes(h);
  $("#home").classList.toggle("on", home);
  $("#main").classList.toggle("on", !home);
  if (home) return;
  view = h;
  document.querySelectorAll(".seg a").forEach((b) => b.setAttribute("aria-selected", b.dataset.view === h));
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("on", el.id === "view-" + h));
  $("#picker").style.display = h === "proof" ? "none" : "";
  window.scrollTo(0, 0);
  if (cur) draw();
}

/* ───────── 계산 ───────── */
async function loggerDay(date) {
  if (cache[date]) return cache[date];
  const r = await fetch(LOGGER + date + ".csv", { cache: "no-cache" });
  if (!r.ok) return [];
  const rows = (await r.text()).trim().split("\n").slice(1).map((l) => l.split(","))
    .filter((c) => c[3] === "200" && c[2] !== "" && !isNaN(+c[2]))
    .map((c) => ({ t: new Date(c[1].replace(" ", "T") + "+09:00"), v: +c[2] }));
  if (date < todayKST()) cache[date] = rows;   // 지난 날짜만 캐시(오늘은 계속 쌓임)
  return rows;
}
function nearest(rows, T, tolMin) {
  let best = null;
  for (const r of rows) { const d = Math.abs(r.t - T); if (!best || d < best.d) best = { ...r, d }; }
  return best && best.d <= tolMin * 60e3 ? best : null;
}
function predict(S) {
  const [q1, q9] = D.model.resid_q10_q90, x = Math.log(S), out = {};
  for (const g of GATES) { const mu = D.model.b * x + D.model.a[g]; out[g] = { p: Math.exp(mu) - 1, lo: Math.exp(mu + q1) - 1, hi: Math.exp(mu + q9) - 1 }; }
  return out;
}
// state: pre(예측 전) / wait(기록 대기) / nodata(기록 없음) / bad(증가 없음) / ok
async function compute(s) {
  const st = kst(s.date, s.start), t0 = addMin(st, D.model.lead_min), end = addMin(st, s.runtime_min);
  const base = { s, st, t0, end }, now = new Date();
  if (now < t0) return { ...base, state: "pre" };
  let rows = [];
  try { rows = await loggerDay(s.date); } catch (e) { /* 네트워크 오류 → 기록 없음으로 처리 */ }
  const a3 = nearest(rows, kst(s.date, "03:00"), 60), a0 = nearest(rows, t0, 10);
  if (!a3 || !a0) return { ...base, state: now - t0 < 40 * 60e3 ? "wait" : "nodata", miss: !a3 ? "새벽 3시" : "공연 시작 30분 뒤" };
  const S = a0.v - a3.v;
  if (S <= 0) return { ...base, state: "bad", a3, a0, S };
  return { ...base, state: "ok", a3, a0, S, P: predict(S) };
}

let cur = null;
async function pick(date) {
  sel = date; const my = ++token;
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c.dataset.date === date));
  const s = D.shows.find((x) => x.date === date);
  $("#showline").textContent = `${s.show} · ${s.venue} · ${s.start} 시작 · ${hm(addMin(kst(s.date, s.start), s.runtime_min))} 끝날 예정`;
  const r = await compute(s);
  if (my !== token) return;   // 더 늦게 누른 공연이 우선
  cur = r; draw();
}
function draw() { if (view === "vis") drawVis(cur); else if (view === "op") drawOp(cur); }

// 가장 최근에 결과가 나왔을 공연(예측 전 화면에서 '지난 공연 보기'용)
const lastReady = () => [...D.shows].reverse().find((s) => addMin(kst(s.date, s.start), D.model.lead_min) < new Date() && s.date !== sel);

/* ───────── 그림 조각 ───────── */
function face(kind, size = 64) {
  const c = { go: "#059669", mid: "#F59E0B", stop: "#E11D48", unk: "#CBD5E1" }[kind];
  const mouth = {
    go: "M21 38 Q32 50 43 38", mid: "M22 41 L42 41", stop: "M21 45 Q32 34 43 45", unk: ""
  }[kind];
  const eyes = kind === "unk"
    ? `<text x="32" y="43" font-size="30" font-weight="900" text-anchor="middle" fill="#fff">?</text>`
    : `<circle cx="23" cy="27" r="3.6" fill="#0F172A"/><circle cx="41" cy="27" r="3.6" fill="#0F172A"/>`;
  return `<svg viewBox="0 0 64 64" width="${size}" height="${size}" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="${c}"/>${eyes}${mouth ? `<path d="${mouth}" fill="none" stroke="#0F172A" stroke-width="3.6" stroke-linecap="round"/>` : ""}</svg>`;
}
const CAR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11h.5a1.5 1.5 0 0 1 1.5 1.5V17h-2v1.5a1 1 0 0 1-2 0V17H7v1.5a1 1 0 0 1-2 0V17H3v-4.5A1.5 1.5 0 0 1 4.5 11zm2.3 0h9.4l-1-3.2a1 1 0 0 0-1-.8H9.3a1 1 0 0 0-1 .8zM6.5 13a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6zm11 0a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6z"/></svg>`;
const WORD = { go: "가장 여유", mid: "보통", stop: "가장 붐빔", unk: "아직 몰라요" };
const COL = { go: "#059669", mid: "#F59E0B", stop: "#E11D48", unk: "#CBD5E1" };

// 간략 배치도: 공연장에서 세 출구로 가는 길. 길 굵기 = 나갈 차의 상대적 양
function parkMap(levels, P, best, tagText = "추천 출구", showNum = false) {
  const pos = { "북2문": [212, 52], "동2문": [326, 178], "남2문": [156, 290] }, dome = [150, 160];
  const lab = { "북2문": [168, 58, "end"], "동2문": [326, 236, "middle"], "남2문": [200, 296, "start"] };
  const maxP = P ? Math.max(...GATES.map((g) => P[g].p)) : 1;
  let s = `<svg viewBox="0 0 380 340" role="img" aria-label="올림픽공원 출구 간략 배치도">`;
  s += `<rect x="40" y="52" width="286" height="238" rx="46" fill="#ECFDF5" stroke="#A7F3D0" stroke-width="2"/>`;
  s += `<text x="58" y="108" font-size="12" fill="#047857">올림픽공원</text>`;
  for (const g of GATES) {
    const [x, y] = pos[g], w = P ? 6 + 14 * (P[g].p / maxP) : 6;
    s += `<path d="M${dome[0]} ${dome[1]} L${x} ${y}" stroke="${COL[levels[g]]}" stroke-opacity=".55" stroke-width="${w}" stroke-linecap="round"/>`;
  }
  s += `<circle cx="${dome[0]}" cy="${dome[1]}" r="44" fill="#fff" stroke="#0369A1" stroke-width="3"/>`;
  s += `<text x="${dome[0]}" y="${dome[1] - 3}" font-size="14" font-weight="700" text-anchor="middle" fill="#0369A1">공연장</text>`;
  s += `<text x="${dome[0]}" y="${dome[1] + 15}" font-size="11" text-anchor="middle" fill="#0369A1">KSPO DOME</text>`;
  for (const g of GATES) {
    const [x, y] = pos[g], [lx, ly, an] = lab[g];
    if (g === best) s += `<circle cx="${x}" cy="${y}" r="38" fill="none" stroke="#0369A1" stroke-width="3" stroke-dasharray="5 4"/>`;
    s += `<g transform="translate(${x - 30} ${y - 30})">${face(levels[g], 60).replace("<svg ", '<svg x="0" y="0" ')}</g>`;
    s += `<text x="${lx}" y="${ly}" font-size="17" font-weight="900" text-anchor="${an}" fill="#0F172A">${g}</text>`;
    if (g === best) s += `<text x="${lx}" y="${ly + 18}" font-size="13" font-weight="700" text-anchor="${an}" fill="#0369A1">${tagText}</text>`;
    if (showNum && P) s += `<text x="${lx}" y="${ly + (g === best ? 36 : 18)}" font-size="14" font-weight="700" text-anchor="${an}" fill="#0F172A">${fmt(P[g].p)}대</text>`;
  }
  s += `<g transform="translate(350 30)"><path d="M0 -14 L6 4 L0 0 L-6 4Z" fill="#64748B"/><text y="18" font-size="11" text-anchor="middle" fill="#64748B">북</text></g>`;
  return s + "</svg>";
}

/* ───────── 관람객용: 결과만 ───────── */
function drawVis(R) {
  const box = $("#view-vis"), s = R.s;
  const rushA = hm(addMin(R.end, PEAK_ALL[0])), rushB = hm(addMin(R.end, PEAK_ALL[2]));
  let levels = {}, P = R.P, order = GATES, best = null, hero = "";
  if (R.state === "ok") {
    order = [...GATES].sort((x, y) => P[x].p - P[y].p);
    order.forEach((g, i) => (levels[g] = ["go", "mid", "stop"][i]));
    best = order[0];
    const tot = GATES.reduce((a, g) => a + P[g].p, 0);
    const med = GATES.reduce((a, g) => a + D.gates[g].N_median, 0), p90 = GATES.reduce((a, g) => a + D.gates[g].N_p90, 0);
    const day = tot >= p90 ? ["평소보다 차가 아주 많은 날이에요", "#FFE4E6", "#BE123C"]
      : tot >= med ? ["평소와 비슷한 날이에요", "#FEF3C7", "#B45309"] : ["평소보다 차가 적은 날이에요", "#D1FAE5", "#047857"];
    hero = `<div class="hero go"><div class="face">${face("go")}</div><div>
      <h2><b style="color:#047857">${best}</b>으로 나가면 가장 여유로워요</h2>
      <p>차가 가장 많이 나가는 시간은 <b>${rushA} ~ ${rushB}</b> 쯤이에요.</p>
      <span class="daypill" style="background:${day[1]};color:${day[2]}">${day[0]}</span></div></div>`;
  } else {
    GATES.forEach((g) => (levels[g] = "unk"));
    const lr = lastReady();
    const more = lr ? `<button class="btn" data-go="${lr.date}">지난 공연(${md(lr.date)}) 결과 보기</button>` : "";
    const msg = {
      pre: [`<b>${hm(R.t0)}</b>에 알려 드릴게요`, `공연이 시작하고 30분이 지나면 결과가 나와요. 공연은 ${hm(R.end)}쯤 끝날 예정이에요.`],
      wait: ["곧 나와요!", "지금 주차장에 있는 차를 세고 있어요. 잠시 뒤 저절로 바뀌어요."],
      nodata: ["이 날은 알려 드릴 수 없어요", `주차장 차 수 기록(${R.miss})이 없어서 계산하지 못했어요.`],
      bad: ["이 날은 알려 드릴 수 없어요", "주차장 차 수가 새벽보다 늘지 않아 계산하지 않았어요."],
    }[R.state];
    hero = `<div class="hero ${R.state === "pre" || R.state === "wait" ? "wait" : "none"}"><div class="face">${face("unk")}</div><div>
      <h2>${msg[0]}</h2><p>${msg[1]}</p>${more}</div></div>`;
  }
  const maxP = P ? Math.max(...GATES.map((g) => P[g].p)) : 1;
  const cards = order.map((g) => {
    const lv = levels[g], n = P ? Math.max(1, Math.round(5 * P[g].p / maxP)) : 0;
    const cars = Array.from({ length: 5 }, (_, i) => `<span style="color:${i < n ? COL[lv] : "#E2E8F0"}">${CAR}</span>`).join("");
    const q = D.gates[g].peak_min_q;
    return `<div class="g ${lv}">
      <div class="face">${face(lv, 58)}</div>
      <div><div class="name">${g} <span class="word">${WORD[lv]}</span>${g === best ? ' <span class="star">추천</span>' : ""}</div></div>
      ${P ? `<div class="cars" aria-label="나갈 차 ${n}/5">${cars}</div>
      <div class="when">나갈 차 약 <b>${fmt(P[g].p)}대</b> · 가장 붐비는 때 <b>${hm(addMin(R.end, q[1]))}</b>쯤</div>` : ""}
    </div>`;
  }).join("");
  box.innerHTML = `${hero}
    <div class="vis-grid">
      <div class="mapbox">${parkMap(levels, P, best)}<p class="mapnote">간단히 그린 배치도예요 (실제 지도와 다를 수 있어요)</p></div>
      <div class="glist">${cards}</div>
    </div>
    ${R.state === "ok" ? `<div class="tip"><b>알아 두세요</b> · 차를 세워 둔 곳에서 가까운 출구를 먼저 생각해 주세요. 시간 여유가 있다면 <b>${rushA}~${rushB}</b>을 피해 조금 늦게 출발하면 더 편해요.</div>` : ""}`;
  box.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.go)));
}

/* ───────── 운영자용: 숫자·범위·시간표 ───────── */
function timeline(R) {
  const W = 760, H = 176, L = 24, Rr = 24, y = 78;
  const t1 = R.st, t2 = addMin(R.end, PEAK_ALL[2] + 20), span = t2 - t1;
  const X = (t) => L + ((t - t1) / span) * (W - L - Rr);
  const ra = addMin(R.end, PEAK_ALL[0]), rm = addMin(R.end, PEAK_ALL[1]), rb = addMin(R.end, PEAK_ALL[2]);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="공연 시작부터 출차 집중까지 시간표">`;
  s += `<rect x="${X(R.st)}" y="${y - 9}" width="${X(R.end) - X(R.st)}" height="18" rx="9" fill="#E0F2FE"/>`;
  s += `<text x="${(X(R.st) + X(R.end)) / 2}" y="${y + 4}" font-size="12" text-anchor="middle" fill="#0369A1">공연 중</text>`;
  s += `<rect x="${X(ra)}" y="${y - 9}" width="${X(rb) - X(ra)}" height="18" rx="9" fill="#FFE4E6"/>`;
  s += `<text x="${(X(ra) + X(rb)) / 2}" y="${y + 32}" font-size="12" text-anchor="middle" fill="#BE123C" font-weight="700">출차 집중 구간</text>
    <text x="${(X(ra) + X(rb)) / 2}" y="${y + 48}" font-size="12" text-anchor="middle" fill="#BE123C">${hm(ra)}~${hm(rb)}</text>`;
  const mk = (t, label, up, color, bold) => {
    const x = X(t), ty = up ? y - 26 : y + 36;
    return `<line x1="${x}" x2="${x}" y1="${y - 14}" y2="${y + 14}" stroke="${color}" stroke-width="3"/>
      <text x="${x}" y="${ty}" font-size="13" text-anchor="middle" fill="${color}" ${bold ? 'font-weight="700"' : ""}>${label}</text>
      <text x="${x}" y="${ty + (up ? -16 : 16)}" font-size="13" text-anchor="middle" fill="#0F172A" font-weight="700">${hm(t)}</text>`;
  };
  s += mk(R.st, "시작", false, "#64748B");
  s += mk(R.t0, "예측 나옴", true, "#0369A1", true);
  s += mk(R.end, "끝날 예정", false, "#64748B");
  s += mk(rm, "출차 가장 많음(보통)", true, "#E11D48", true);
  // 준비 시간 화살표
  const ay = 158;
  s += `<line x1="${X(R.t0)}" x2="${X(rm)}" y1="${ay}" y2="${ay}" stroke="#0369A1" stroke-width="2" marker-end="url(#ah)"/>`;
  s += `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#0369A1"/></marker></defs>`;
  s += `<rect x="${(X(R.t0) + X(rm)) / 2 - 92}" y="${ay - 11}" width="184" height="22" rx="11" fill="#0369A1"/>`;
  s += `<text x="${(X(R.t0) + X(rm)) / 2}" y="${ay + 4}" font-size="13" text-anchor="middle" fill="#fff" font-weight="700">준비할 시간 약 ${dur(rm - R.t0)}</text>`;
  return s + "</svg>";
}

function drawOp(R) {
  const box = $("#view-op"), P = R.state === "ok" ? R.P : null;
  const rm = addMin(R.end, PEAK_ALL[1]), rushA = hm(addMin(R.end, PEAK_ALL[0])), rushB = hm(addMin(R.end, PEAK_ALL[2]));
  const order = P ? [...GATES].sort((x, y) => P[y].p - P[x].p) : GATES;
  const levels = {};
  order.forEach((g, i) => (levels[g] = P ? ["stop", "mid", "go"][i] : "unk"));
  const first = P ? order[0] : null;

  // 한 문장 요약(관람객용 hero와 같은 형식)
  let hero;
  if (P) {
    const hot = order.filter((g) => P[g].p >= D.gates[g].N_p90);
    hero = `<div class="hero opz"><div class="face">${face("stop")}</div><div>
      <h2><b style="color:#BE123C">${first}</b>에 인력을 먼저 배치하세요</h2>
      <p>출차는 <b>${rushA} ~ ${rushB}</b>에 몰려요. 예측이 나온 뒤 <b>약 ${dur(rm - R.t0)}</b> 준비할 수 있어요.</p>
      <span class="daypill" style="background:${hot.length ? "#FFE4E6" : "#D1FAE5"};color:${hot.length ? "#BE123C" : "#047857"}">${hot.length ? `2025년 상위 10% 수준: ${hot.join("·")}` : "세 출구 모두 2025년 평소 범위"}</span></div></div>`;
  } else {
    const lr = lastReady();
    const more = lr ? `<button class="btn" data-go="${lr.date}">지난 공연(${md(lr.date)}) 결과 보기</button>` : "";
    const msg = {
      pre: [`예측은 <b>${hm(R.t0)}</b>에 나옵니다`, `공연 시작 30분 뒤 주차 대수로 계산합니다. 아래는 2025년 평소 규모입니다.`],
      wait: ["주차현황 기록을 기다리는 중", "기록이 몇 분 늦게 올라옵니다. 5분마다 자동으로 다시 확인합니다."],
      nodata: ["이 날은 예측할 수 없습니다", `주차현황 기록(${R.miss})이 없습니다. 아래는 2025년 평소 규모입니다.`],
      bad: ["이 날은 예측하지 않습니다", "주차 대수가 새벽보다 늘지 않았습니다."],
    }[R.state];
    hero = `<div class="hero ${R.state === "pre" || R.state === "wait" ? "wait" : "none"}"><div class="face">${face("unk")}</div><div>
      <h2>${msg[0]}</h2><p>${msg[1]}</p>${more}</div></div>`;
  }

  const cards = order.map((g, i) => {
    const h = D.gates[g], q = h.peak_min_q, n = P ? P[g].p : h.N_median, top = P && P[g].p >= h.N_p90, lv = levels[g];
    const scaleMax = Math.ceil(Math.max(600, P ? P[g].hi : 0, h.N_p90) * 1.05 / 100) * 100, pc = (v) => `${Math.min(100, (v / scaleMax) * 100)}%`;
    return `<div class="og ${lv}">
      <div class="oghead"><div class="face">${face(lv, 46)}</div>
        <div><div class="rank">${P ? `우선순위 ${i + 1}` : "2025 평소"}</div><h3>${g}</h3></div>
        ${P ? (top ? `<span class="badge hot">상위 10% 수준</span>` : `<span class="badge calm">평소 범위</span>`) : ""}</div>
      <div class="big">${fmt(n)}<small> 대 ${P ? "나갈 예정" : "(중앙값)"}</small></div>
      <div class="bar">${P ? `<div class="iv" style="left:${pc(P[g].lo)};width:calc(${pc(P[g].hi)} - ${pc(P[g].lo)})"></div>` : ""}
        <div class="p90" style="left:${pc(h.N_p90)}" title="2025 상위 10% 기준 ${fmt(h.N_p90)}대"></div><div class="pt" style="left:${pc(n)}"></div></div>
      <div class="ogfoot"><span>${P ? `80% 범위 ${fmt(P[g].lo)}~${fmt(P[g].hi)}대` : `상위 10% 기준 ${fmt(h.N_p90)}대`}</span><span>가장 많을 때 <b>${hm(addMin(R.end, q[1]))}</b></span></div>
    </div>`;
  }).join("");

  let input = "";
  if (R.state === "ok" || R.state === "bad") {
    const [lo, hi] = D.model.S_rel_train_range, out = R.S < lo || R.S > hi;
    input = `<div class="card"><h3>계산에 쓴 값</h3><div class="input">
        <div class="num"><small>새벽 3시 주차</small><b>${fmt(R.a3.v)}대</b></div><span class="op-arrow">→</span>
        <div class="num"><small>${hm(R.a0.t)} 주차 (시작 30분 뒤)</small><b>${fmt(R.a0.v)}대</b></div><span class="op-arrow">=</span>
        <div class="num s"><small>공연 때문에 늘어난 차</small><b>${R.S > 0 ? "+" : ""}${fmt(R.S)}대</b></div></div>
      ${out ? `<span class="warnbadge">2025년 학습 범위(${fmt(lo)}~${fmt(hi)}대) 밖이라 오차가 클 수 있어요</span>` : ""}</div>`;
  }

  box.innerHTML = `${hero}
    <div class="vis-grid">
      <div class="mapbox">${parkMap(levels, P, first, "먼저 배치", true)}<p class="mapnote">길이 굵을수록 그 출구로 나갈 공연 차량이 많아요 (간략 배치도)</p></div>
      <div class="glist">${cards}
        <div class="legend">
          <span><i style="width:5px;height:16px;background:#0369A1;border-radius:2px"></i>예측</span>
          <span><i style="width:24px;height:10px;background:#7DD3FC;border-radius:5px"></i>80% 범위</span>
          <span><i style="width:0;height:16px;border-left:2px dashed #E11D48"></i>2025 상위 10%</span>
        </div></div>
    </div>
    <div class="card timeline"><h3>언제 준비하면 되나</h3>${timeline(R)}</div>
    ${input}
    <p class="note">공연 관람 차량만 예측합니다(공원·체육시설 이용 차량 제외). '상위 10% 수준'은 줄이 생긴다는 판정이 아니라 인력·안내를 어디에 먼저 둘지 정하는 참고 표시입니다.</p>`;
  box.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.go)));
}

/* ───────── 검증 (평가자용) ───────── */
function renderProof() {
  const m = D.model.metrics, ts = m.time_split, lo = m.loso;
  const bars = [
    ["공연 정보만으로", "공연장 규모 × 출구별 평소값", INFO_ONLY_MAE, false],
    ["평소값으로 찍기", "출구별 2025년 중앙값 · 기준", ts.B0, false],
    ["이 서비스", "공연 시작 30분 뒤 주차 수 사용", ts.M, true],
  ];
  const bmax = Math.max(...bars.map((b) => b[2]));
  const CHECK = `<svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="#0369A1"/><path d="M19 33 l9 9 l17 -19" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const tile = (cls, big, title, sub) => `<div class="pk ${cls}"><b>${big}</b><div><div class="pt1">${title}</div><div class="pt2">${sub}</div></div></div>`;
  $("#view-proof").innerHTML = `
    <div class="hero proofz"><div class="face">${CHECK}</div><div>
      <h2>평소값으로 찍을 때보다 <b style="color:#0369A1">오차가 ${Math.round(ts.reduction * 100)}% 줄었어요</b></h2>
      <p>2025년 1~6월 공연으로 배우고, 처음 보는 7~12월 공연 ${ts.shows}개, 출구별 ${ts.curves}건에 그대로 적용한 결과예요.</p>
      <span class="daypill" style="background:#E0F2FE;color:#0369A1">계수는 결과를 보기 전 ${D.model.frozen_at_kst}에 고정</span></div></div>

    <div class="vis-grid">
      <div class="card flat">
        <h3>출구별 평균 오차 <span class="sub" style="font-weight:400">· 막대가 짧을수록 정확</span></h3>
        <div class="hbars">${bars.map(([a, b, v, me]) => `<div class="hbar ${me ? "me" : ""}"><div class="lab"><b>${a}</b><small>${b}</small></div>
          <div class="track"><div class="fill" style="width:${(v / bmax) * 100}%">${Math.round(v)}대</div></div></div>`).join("")}</div>
      </div>
      <div class="glist">
        ${tile("a", `${Math.round(ts.reduction * 100)}%`, "오차 감소 (시간순 검증)", `${Math.round(ts.B0)}대 → ${Math.round(ts.M)}대 · 95% 구간 ${ts.ci95.map((v) => Math.round(v * 100)).join("~")}%`)}
        ${tile("b", `${Math.round(lo.reduction * 100)}%`, `공연 ${lo.shows}개를 하나씩 빼고 다시 시험해도`, `${Math.round(lo.B0)}대 → ${Math.round(lo.M)}대 · 95% 구간 ${lo.ci95.map((v) => Math.round(v * 100)).join("~")}%`)}
        ${tile("c", `${Math.round(ts.cover80 * 100)}%`, `"80% 범위" 안에 실제 값이 든 비율`, `목표 약 80% · 교차검증 ${Math.round(lo.cover80 * 100)}%`)}
      </div>
    </div>

    <div class="card">
      <h3>예측과 실제를 나란히 놓으면</h3>
      <p class="sub">점 하나 = 출구 하나의 하루. <b>점선에 가까울수록 정확</b>해요. 세로 막대 = 80% 범위.</p>
      <div class="chart">${scatter()}</div>
    </div>

    <div class="card">
      <h3>왜 '차 수'를 예측하나요?</h3>
      <div class="logic">
        <div class="q"><span class="h">가설</span>출구가 막혀 줄이 선다면, 큰 공연에서도 나가는 속도가 어느 선에서 멈춰야 한다</div>
        <div class="ar">→</div>
        <div class="t"><span class="h">확인</span>공연이 클수록 나가는 속도도 꺾이는 지점 없이 계속 커졌다</div>
        <div class="ar">→</div>
        <div class="a"><span class="h">결론</span>막혀서가 아니라 차가 많아서 몰린다 → <b>몇 대가 나갈지</b>를 당일 미리 잰다</div>
      </div>
    </div>

    <div class="card">
      <h3>2026년 실제 공연 사후 점검 <span class="sub" style="font-weight:400">· 공연일 ${CHECK2026.n}일, 3개 문 합계</span></h3>
      <div class="kpis">
        ${tile("a", `${CHECK2026.hit}/${CHECK2026.n}`, "80% 범위 안에 든 날", "공연일 기준")}
        ${tile("b", `${CHECK2026.relerr}%`, "상대 오차 중앙값", "3개 문 합계 감소량 기준")}
        ${tile("w", `−${CHECK2026.bias}대`, "평균적으로 적게 예측", "보완이 필요한 부분")}
      </div>
    </div>

    <div class="card">
      <h3>계산 방법</h3>
      <ol class="steps2">
        <li><span class="no">1</span><div><b>주차 대수 읽기</b><p>새벽 3시와 공연 시작 30분 뒤, 동2·남2·북2문의 주차 대수를 기관 실시간 주차현황 API에서 읽습니다.</p></div></li>
        <li><span class="no">2</span><div><b>늘어난 차 S 구하기</b><p>두 값의 차이 S가 공연 때문에 늘어난 차량 수입니다. API 값에는 상시 주차 차량 약 700~870대가 늘 포함되어 있어 증가분을 씁니다.</p></div></li>
        <li><span class="no">3</span><div><b>출구별 출차량 계산</b><p>2025년 입출차 기록 230만 건으로 학습한 식에 S를 넣습니다. S가 10% 늘면 출차량은 약 11% 늘어납니다.</p>
          <div class="formula">log(출구별 공연 차량 + 1) = 출구별 상수 + ${D.model.b.toFixed(2)} × log S</div></div></li>
        <li><span class="no">4</span><div><b>80% 범위 정하기</b><p>학습 잔차의 10%·90% 분위로 범위의 아래·위 끝을 정합니다.</p></div></li>
        <li><span class="no">5</span><div><b>상위 10% 표시</b><p>예상 출차량이 그 출구의 2025년 공연일 상위 10% 기준을 넘으면 표시합니다.</p>
          <div class="chips2">${GATES.map((g) => `<span>${g} ${fmt(D.gates[g].N_p90)}대</span>`).join("")}</div></div></li>
      </ol>
      <p class="frozen">계수 고정 ${D.model.frozen_at_kst} · 파라미터 해시 ${D.model.sha256_of_params}</p>
    </div>

    <div class="card">
      <h3>한계</h3>
      <ul class="limits">
        <li><b>공연 관람 차량만 예측합니다.</b> 같은 시간 공원·체육시설을 이용하는 차량은 포함하지 않으며, 이 차량이 출구 출차의 약 2/3입니다.</li>
        <li><b>출구별 배분은 관객층에 따라 달라질 수 있습니다.</b> 2025년 god 공연 남2문은 실제 700대 안팎, 예측 280대 안팎이었습니다.</li>
        <li><b>출차가 몰리는 시각은 예측하지 않습니다.</b> 2025년 분포로 안내하며, 예정 종료 후 중앙 ${PEAK_ALL[1]}분, 보통 ${PEAK_ALL[0]}~${PEAK_ALL[2]}분입니다.</li>
        <li><b>대기열과 대기 시간은 다루지 않습니다.</b> 출차 기록만으로는 확인할 수 없습니다.</li>
        <li><b>실시간 API는 3개 문 합계만 제공합니다.</b> 2025년 기관 집계 주차대수로 입력을 바꾸면 평균 오차가 ${SUBST.from}대에서 ${SUBST.to}대로 늘었습니다. ${SUBST.days}개 공연일 기준입니다.</li>
      </ul>
    </div>

    <div class="card">
      <h3>사용한 공공데이터</h3>
      <div class="data">
        <div><span class="tag">15159093</span><b>올림픽공원 입출차정보 · 전체구역</b>2025년 입·출차 기록, 중복 제거 후 2,303,876건 · 학습과 검증</div>
        <div><span class="tag">15168686</span><b>실시간 주차현황</b>5분 간격 <a href="https://github.com/wkqhd5095/kspo-parking-logger/tree/parking-data/data/parking">자체 기록</a> · 당일 입력</div>
        <div><span class="tag">15164822</span><b>차량입출차·공연·날씨 연계 데이터</b>관람객 수·재차대수 검산</div>
        <div><span class="tag">기관 홈페이지</span><b>공연 일정</b>시작 시각·러닝타임 → 끝날 예정 시각</div>
      </div>
    </div>`;
}

function scatter() {
  const W = 640, H = 420, L = 56, B = 48, R = 16, T = 34, mx = 800;
  const X = (v) => L + (Math.min(v, mx) / mx) * (W - L - R), Y = (v) => H - B - (Math.min(v, mx) / mx) * (H - B - T);
  let g = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="2025년 7~12월 실제 출차량과 예측 비교">`;
  for (let v = 0; v <= mx; v += 200) {
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#F1F5F9"/><text x="${L - 8}" y="${Y(v) + 4}" font-size="12" text-anchor="end" fill="#64748B">${v}</text>`;
    g += `<text x="${X(v)}" y="${H - B + 18}" font-size="12" text-anchor="middle" fill="#64748B">${v}</text>`;
  }
  g += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(mx)}" y2="${Y(mx)}" stroke="#0F172A" stroke-opacity=".45" stroke-width="1.5" stroke-dasharray="6 5"/>`;
  g += `<text x="${X(690)}" y="${Y(735)}" font-size="12" fill="#64748B" text-anchor="end">정확히 맞춤</text>`;
  for (const r of D.back) {
    g += `<line x1="${X(r.N)}" x2="${X(r.N)}" y1="${Y(r.lo)}" y2="${Y(r.hi)}" stroke="${DOT[r.gate]}" stroke-opacity=".22" stroke-width="3"/>`;
    g += `<circle cx="${X(r.N)}" cy="${Y(r.pred)}" r="4.5" fill="${DOT[r.gate]}" stroke="#fff" stroke-width="1"><title>${r.date} ${r.gate} · 실제 ${r.N}대 / 예측 ${r.pred}대 (80% ${r.lo}~${r.hi}) · ${r.show}</title></circle>`;
  }
  g += `<text x="${(L + W - R) / 2}" y="${H - 8}" font-size="13" text-anchor="middle" fill="#0F172A">실제로 나간 공연 차량 (대)</text>`;
  g += `<text transform="translate(16 ${(T + H - B) / 2}) rotate(-90)" font-size="13" text-anchor="middle" fill="#0F172A">예측 (대)</text>`;
  GATES.forEach((gt, i) => { g += `<circle cx="${L + 10 + i * 78}" cy="${14}" r="6" fill="${DOT[gt]}"/><text x="${L + 20 + i * 78}" y="${19}" font-size="13">${gt}</text>`; });
  return g + "</svg>";
}

init();
