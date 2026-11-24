// 올림픽공원 공연 후 출구별 출차 나우캐스트 — 서버 없이 브라우저에서 계산한다.
// 입력: data/*.json(2025 학습 결과, 동결 계수) + GitHub 로거의 실시간 주차현황 기록(raw CSV)
"use strict";
const LOGGER = "https://raw.githubusercontent.com/wkqhd5095/kspo-parking-logger/parking-data/data/parking/";
const GATES = ["남2문", "동2문", "북2문"];
const COLORS = { "남2문": "#1f5fbf", "동2문": "#d9822b", "북2문": "#2f9e6b" };
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
let D = {}, role = "op";

const $ = (s) => document.querySelector(s);
const fmt = (n) => Math.round(n).toLocaleString("ko-KR");
const kst = (dateStr, hhmm) => new Date(`${dateStr}T${hhmm}:00+09:00`);
const hm = (d) => d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const addMin = (d, m) => new Date(d.getTime() + m * 60000);
const todayKST = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

async function loadJSON(n) { const r = await fetch(`data/${n}.json`, { cache: "no-cache" }); return r.json(); }

async function init() {
  [D.model, D.gates, D.sat, D.back, D.shows] = await Promise.all(["model", "gates", "saturation", "backtest", "shows"].map(loadJSON));
  $("#bcoef").textContent = D.model.b.toFixed(2);
  $("#frozen").textContent = `${D.model.frozen_at_kst} (결과를 보기 전, 파라미터 해시 ${D.model.sha256_of_params})`;
  const sel = $("#date");
  D.shows.forEach((s) => {
    const o = document.createElement("option"); o.value = s.date;   // new Date("YYYY-MM-DD")는 UTC 자정 → getUTCDay가 그 날짜의 요일
    o.textContent = `${+s.date.slice(5, 7)}/${+s.date.slice(8)}(${DOW[new Date(s.date).getUTCDay()]}) ${s.start} · ${s.show}`;
    sel.appendChild(o);
  });
  const t = todayKST(); const up = D.shows.find((s) => s.date >= t);
  sel.value = (D.shows.find((s) => s.date === t) || up || D.shows[D.shows.length - 1]).date;
  sel.addEventListener("change", run);
  document.querySelectorAll("#who button").forEach((b) => b.addEventListener("click", () => {
    role = b.dataset.role; document.querySelectorAll("#who button").forEach((x) => x.classList.toggle("on", x === b)); run();
  }));
  renderBacktest(); run();
  setInterval(() => { if ($("#status").dataset.live === "1") run(); }, 5 * 60e3);   // 공연 당일엔 5분마다 다시 확인
}

async function loggerDay(date) {
  const r = await fetch(LOGGER + date + ".csv", { cache: "no-cache" });
  if (!r.ok) return [];
  return (await r.text()).trim().split("\n").slice(1).map((l) => l.split(","))
    .filter((c) => c[3] === "200" && c[2] !== "" && !isNaN(+c[2]))
    .map((c) => ({ t: new Date(c[1].replace(" ", "T") + "+09:00"), v: +c[2] }));
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
const satFor = (n) => D.sat.find((b) => n >= b.lo && n < b.hi);

async function run() {
  const s = D.shows.find((x) => x.date === $("#date").value);
  const st = kst(s.date, s.start), t0 = addMin(st, D.model.lead_min), end = addMin(st, s.runtime_min);
  $("#showline").textContent = `${s.venue} · 시작 ${s.start} · 예정 종료 ${hm(end)}(러닝타임 ${s.runtime_min}분)${s.note ? " · " + s.note : ""}`;
  const stEl = $("#status"); stEl.className = "status"; stEl.dataset.live = "0";
  const now = new Date();
  if (now < t0) {
    stEl.classList.add("wait"); stEl.dataset.live = s.date === todayKST() ? "1" : "0";
    stEl.innerHTML = `예측은 <b>${hm(t0)}</b>(시작 30분 뒤)에 나옵니다. 아래는 2025년 같은 출구의 <b>평소 규모</b>입니다.`;
    return render(s, end, null);
  }
  let rows = [];
  try { rows = await loggerDay(s.date); } catch (e) { /* 네트워크 오류 */ }
  const a3 = nearest(rows, kst(s.date, "03:00"), 60), a0 = nearest(rows, t0, 10);
  if (!a3 || !a0) {
    stEl.classList.add(now - t0 < 40 * 60e3 ? "wait" : "err"); stEl.dataset.live = now - t0 < 40 * 60e3 ? "1" : "0";
    stEl.innerHTML = now - t0 < 40 * 60e3
      ? `주차현황 기록을 기다리는 중입니다(기록이 몇 분 늦게 올라옵니다). 5분마다 자동으로 다시 확인합니다.`
      : `이 날의 주차현황 기록이 없어 예측할 수 없습니다(${!a3 ? "새벽 3시" : "시작 30분 뒤"} 값 없음).`;
    return render(s, end, null);
  }
  const S = a0.v - a3.v;
  if (S <= 0) { stEl.classList.add("err"); stEl.textContent = `주차 대수가 새벽보다 늘지 않아(S=${S}) 예측하지 않습니다.`; return render(s, end, null); }
  const [lo, hi] = D.model.S_rel_train_range;
  stEl.innerHTML = `새벽 ${hm(a3.t)} <b>${fmt(a3.v)}대</b> → 시작 30분 뒤 ${hm(a0.t)} <b>${fmt(a0.v)}대</b> · 공연으로 늘어난 차량 <b>S = ${fmt(S)}대</b>` +
    (S < lo || S > hi ? ` <span class="badge">학습 범위(${fmt(lo)}~${fmt(hi)}대) 밖 — 오차가 클 수 있음</span>` : "");
  render(s, end, predict(S));
}

function render(s, end, P) {
  const box = $("#result"); box.innerHTML = "";
  const order = P && role === "vis" ? [...GATES].sort((x, y) => P[x].p - P[y].p) : GATES;
  for (const g of order) {
    const h = D.gates[g], q = h.peak_min_q, el = document.createElement("div"); el.className = "gate";
    const n = P ? P[g].p : h.N_median, top = P && P[g].p >= h.N_p90;
    const scaleMax = Math.max(600, P ? P[g].hi : 0, h.N_p90) * 1.05, pc = (v) => `${Math.min(100, (v / scaleMax) * 100)}%`;
    const sb = satFor(n);
    const tag = !P ? "" : role === "vis"
      ? (g === order[0] ? `<span class="badge calm">상대적으로 여유</span>` : g === order[2] ? `<span class="badge">가장 붐빔 예상</span>` : "")
      : (top ? `<span class="badge">2025 상위 10% 수준</span>` : `<span class="badge calm">평소 범위</span>`);
    el.innerHTML = `<h3>${g} ${tag}</h3>
      <div class="big">${fmt(n)}<small>대 ${P ? "예상" : "(2025 중앙값)"}</small></div>
      <div class="range">${P ? `80% 범위 ${fmt(P[g].lo)}~${fmt(P[g].hi)}대` : `2025 상위 10% 기준 ${fmt(h.N_p90)}대`}</div>
      <div class="bar">${P ? `<div class="iv" style="left:${pc(P[g].lo)};width:calc(${pc(P[g].hi)} - ${pc(P[g].lo)})"></div>` : ""}
        <div class="p90" style="left:${pc(h.N_p90)}" title="2025 상위 10% 기준"></div><div class="pt" style="left:${pc(n)}"></div></div>
      <div class="scale"><span>0</span><span>점선 = 2025 상위 10% (${fmt(h.N_p90)}대)</span><span>${fmt(scaleMax)}</span></div>
      <ul class="facts">
        <li>출차 몰리는 시간(2025 분포): <b>${hm(addMin(end, q[1]))}</b> 전후 (보통 ${hm(addMin(end, q[0]))}~${hm(addMin(end, q[2]))})</li>
        ${role === "op" ? `<li>과거 이 규모(${sb.label}) 출구에서 차단기 처리 한계 근접 신호: <b>${sb.signal}/${sb.curves}</b>회 (${Math.round(100 * sb.signal / sb.curves)}%)</li>` : ""}
      </ul>`;
    box.appendChild(el);
  }
  $("#resnote").textContent = P
    ? (role === "vis" ? "공연 관람 차량 기준 예측입니다. 실제로 이용할 출구는 주차한 구역과 가까운 곳을 우선 고려하세요." :
      "공연 관람 차량만의 예측입니다(같은 시간 공원·시설 이용 차량 제외). 상위 10% 표시는 대기열 판정이 아니라 인력 배치 우선순위 참고용입니다.")
    : "";
}

function renderBacktest() {
  const m = D.model.metrics, k = $("#kpis");
  k.innerHTML = [
    [`${Math.round(m.time_split.M)}대`, `평균 오차 (출구별 평소값으로 찍으면 ${Math.round(m.time_split.B0)}대)`],
    [`−${Math.round(m.time_split.reduction * 100)}%`, `오차 감소 (95% 구간 ${m.time_split.ci95.map((v) => Math.round(v * 100)).join("~")}%)`],
    [`−${Math.round(m.loso.reduction * 100)}%`, `공연 41개 하나씩 빼고 검증해도`],
    [`${Math.round(m.time_split.cover80 * 100)}%`, `80% 범위 안에 실제값이 든 비율`],
  ].map(([b, s]) => `<div class="kpi"><b>${b}</b><span>${s}</span></div>`).join("");
  const W = 640, H = 400, L = 52, B = 44, R = 14, T = 14, mx = 800;
  const X = (v) => L + (Math.min(v, mx) / mx) * (W - L - R), Y = (v) => H - B - (Math.min(v, mx) / mx) * (H - B - T);
  let g = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="2025년 7~12월 실제 출차량과 예측 비교">`;
  for (let v = 0; v <= mx; v += 200) {
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eef0f4"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" text-anchor="end" fill="#5d6878">${v}</text>`;
    g += `<text x="${X(v)}" y="${H - B + 16}" font-size="11" text-anchor="middle" fill="#5d6878">${v}</text>`;
  }
  g += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(mx)}" y2="${Y(mx)}" stroke="#9aa4b2" stroke-dasharray="4 4"/>`;
  for (const r of D.back) {
    g += `<line x1="${X(r.N)}" x2="${X(r.N)}" y1="${Y(r.lo)}" y2="${Y(r.hi)}" stroke="${COLORS[r.gate]}" stroke-opacity=".28" stroke-width="3"/>`;
    g += `<circle cx="${X(r.N)}" cy="${Y(r.pred)}" r="4" fill="${COLORS[r.gate]}"><title>${r.date} ${r.gate} · 실제 ${r.N}대 / 예측 ${r.pred}대 (80% ${r.lo}~${r.hi}) · ${r.show}</title></circle>`;
  }
  g += `<text x="${(L + W - R) / 2}" y="${H - 8}" font-size="12" text-anchor="middle" fill="#1d2430">실제 공연 차량 출차량 (대)</text>`;
  g += `<text transform="translate(14 ${(T + H - B) / 2}) rotate(-90)" font-size="12" text-anchor="middle" fill="#1d2430">예측 (대)</text>`;
  GATES.forEach((gt, i) => { g += `<circle cx="${L + 14 + i * 74}" cy="${T + 10}" r="5" fill="${COLORS[gt]}"/><text x="${L + 23 + i * 74}" y="${T + 14}" font-size="12">${gt}</text>`; });
  $("#chart").innerHTML = g + "</svg>";
}
init();
