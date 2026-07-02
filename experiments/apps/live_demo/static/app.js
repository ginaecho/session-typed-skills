// STJP Live Demo — client side
//
// Four stages wired together:
//   stage 1: pick case → edit intent → POST /api/draft → SSE per attempt
//   stage 2: render kept valid/unsafe + canonical when drafting finishes
//   stage 3: POST /api/run → SSE; route events into per-arm panels.
//            Each panel has three views: events / diagram / errors.
//   stage 4: render summary tables when the run job emits "summary"
//
// Plus two modals:
//   - "roles"            → per-role drilldown (description + goals + system.md)
//   - "expected protocol"→ canonical + LLM-drafted .scr files

"use strict";

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Wave classification matches experiments/scripts/case_runner.py:
// wave 1 runs Foundry arms in parallel; wave 2 runs MAF arms sequentially
// after wave 1 completes. We surface this so the user sees why some arms
// sit at "queued" for a while.
const WAVE_1 = new Set(["bare", "spec_llmvalid", "min_llmvalid"]);

const state = {
  cases: [],
  caseId: null,
  case: null,           // full record for the selected case
  draftJobId: null,
  runJobId: null,
  panels: {},           // arm -> panel ref bundle
  armTabs: {},          // arm -> tab button DOM element
  armState: {},         // arm -> {trial, attempt, goals_pass, viol, tokens, succeeded}
  armEvents: {},        // arm -> [event, ...]   (for diagram + machine render)
  armErrors: {},        // arm -> [violation event, ...]  (for errors view)
  draftKept: { valid: null, unsafe: null },
  draftErrors: {},      // {kind: error text from Scribble} — for reasoning panel
  protocols: {},        // {canonical, valid, unsafe} → raw .scr text
  parsedProtocols: {},  // {canonical, valid, unsafe} → parsed graph
  activeArm: null,
};

// ───────────────────────────────────────────────────────────────── helpers

function setStageStatus(id, text, cls = "active") {
  const el = $(`#${id}-status`);
  if (!el) return;
  el.textContent = text;
  el.className = `stage-status ${cls}`;
}

function openStage(stageId) {
  const el = $(`#${stageId}`);
  if (el) el.classList.remove("collapsed");
}

function clamp(s, n) {
  if (!s) return "";
  s = String(s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function escHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ───────────────────────────────────────────────────────────────── case picker

async function loadCases() {
  const r = await fetch("/api/cases");
  const cases = await r.json();
  state.cases = cases;
  const sel = $("#case-select");
  sel.innerHTML = "";
  for (const c of cases) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.id} — ${clamp(c.description, 60)}`;
    sel.appendChild(opt);
  }
  const defaultId = cases.some(c => c.id === "finance") ? "finance" : cases[0]?.id;
  sel.value = defaultId;
  selectCase(defaultId);
}

function selectCase(caseId) {
  state.caseId = caseId;
  state.case = state.cases.find(x => x.id === caseId) || null;
  $("#foot-case").textContent = caseId;
  if (!state.case) return;
  $("#intent").value = state.case.intent || "";

  // Reset state from the previous case — otherwise the proto-card text
  // panes + parsed graphs from the OLD case would linger if the new
  // case has no LLM drafts, leaving stale visuals.
  state.draftKept = {valid: null, unsafe: null};
  state.draftErrors = {};
  state.protocols = {};
  state.parsedProtocols = {};
  $("#valid-pre").textContent = "(no draft yet)";
  $("#unsafe-pre").textContent = "(no draft yet)";
  $("#unsafe-err") && ($("#unsafe-err").textContent = "");
  $("#valid-attempt").textContent = "";
  $("#unsafe-attempt").textContent = "";
  $("#canon-pre").textContent = "(loading…)";
  // Wipe the proto-card SVGs immediately so the previous case's graphs
  // don't sit there while the new fetches complete.
  $$(".proto-machine-svg, .proto-seq-svg").forEach(s => { while (s.firstChild) s.removeChild(s.firstChild); });
  $$(".reasoning-body").forEach(b => b.innerHTML = `<div class="muted small">loading…</div>`);
  $("#btn-run").disabled = true;

  // Always reveal stage 2 so the CANONICAL state machine + message sequence
  // render immediately on case selection — they don't depend on drafting.
  // (Previously this only opened when a saved llm_draft loaded, so the 10
  // cases without pre-saved drafts showed a blank/collapsed stage.)
  openStage("stage-drafts");

  fetch(`/api/case/${caseId}/protocol`).then(r => r.ok ? r.text() : "(missing)")
    .then(t => { $("#canon-pre").textContent = t; });

  ["valid", "unsafe"].forEach(kind => {
    fetch(`/api/case/${caseId}/draft/${kind}`).then(r => r.ok ? r.text() : null)
      .then(t => {
        if (t) {
          $(`#${kind}-pre`).textContent = t;
          state.draftKept[kind] = t;
          openStage("stage-drafts");
          $("#btn-run").disabled = !state.draftKept.valid;
        }
      });
  });

  // Load + parse all three protocol sources for the state-machine view.
  // We do this proactively so switching the machine-source dropdown is
  // instant rather than blocking on an HTTP fetch each time.
  loadProtocolSources();
}

// ───────────────────────────────────────────────────────────────── stage 1: draft

async function startDraft() {
  $("#btn-draft").disabled = true;
  $("#draft-feed").innerHTML = "";
  $("#draft-summary").textContent = "";
  $("#valid-pre").textContent = "(drafting…)";
  $("#unsafe-pre").textContent = "(drafting…)";
  $("#unsafe-err").textContent = "";
  setStageStatus("intent", "running");

  const body = {
    case_id: state.caseId,
    intent: $("#intent").value,
    max_attempts: parseInt($("#max-attempts").value, 10) || 4,
  };
  const r = await fetch("/api/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j.error) {
    setStageStatus("intent", "error: " + j.error, "err");
    $("#btn-draft").disabled = false;
    return;
  }
  state.draftJobId = j.job_id;
  openStage("stage-drafts");
  setStageStatus("drafts", "streaming attempts");
  subscribeDraftStream(j.job_id);
}

function appendDraftRow(ev) {
  const feed = $("#draft-feed");
  const row = document.createElement("div");
  row.className = "row" + (ev.valid ? " pass" : " fail") + (ev.kept_as ? " kept" : "");
  const mode = ev.mode ? `[${ev.mode}] ` : "";
  const fullErr = ev.error || `(${ev.draft_chars || 0} chars)`;
  // Show enough of the error to be informative; hover (title) reveals full
  // text, and clicking the row expands a full-error pane below.
  const shown = clamp(fullErr, 220);
  row.innerHTML = `
    <span class="att-i">#${ev.attempt}</span>
    <span class="v">${ev.valid ? "PASS" : "FAIL"}</span>
    <span class="err" title="${escHtml(fullErr)}">${escHtml(mode)}${escHtml(shown)}</span>
    ${ev.kept_as ? `<span class="kept-tag">kept · ${ev.kept_as}</span>` : ""}
  `;
  // Click any FAIL row to toggle a full-error expansion.
  if (!ev.valid && ev.error) {
    row.classList.add("expandable");
    row.addEventListener("click", () => {
      const existing = row.querySelector(".draft-row-detail");
      if (existing) { existing.remove(); return; }
      const detail = document.createElement("div");
      detail.className = "draft-row-detail";
      detail.textContent = ev.error;
      row.appendChild(detail);
    });
  }
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

function renderKept(kept_valid, kept_unsafe) {
  if (kept_valid && kept_valid.content) {
    $("#valid-pre").textContent = kept_valid.content;
    $("#valid-attempt").textContent = `attempt #${kept_valid.attempt}`;
    state.draftKept.valid = kept_valid.content;
    // Re-parse so the VALID state-machine + message-sequence graphs render
    // from the freshly drafted global type (not just the canonical).
    state.protocols.valid = kept_valid.content;
    state.parsedProtocols.valid = parseScribble(kept_valid.content);
  } else {
    $("#valid-pre").textContent = "(no valid draft produced)";
    state.protocols.valid = null;
    state.parsedProtocols.valid = null;
  }
  if (kept_unsafe && kept_unsafe.content) {
    $("#unsafe-pre").textContent = kept_unsafe.content;
    $("#unsafe-attempt").textContent = `attempt #${kept_unsafe.attempt}`;
    $("#unsafe-err").textContent = kept_unsafe.error
      ? `Scribble: ${kept_unsafe.error}` : "";
    state.draftKept.unsafe = kept_unsafe.content;
    // Stash the verdict so the reasoning panel can parse + explain it.
    state.draftErrors.unsafe = kept_unsafe.error || "";
    state.protocols.unsafe = kept_unsafe.content;
    state.parsedProtocols.unsafe = parseScribble(kept_unsafe.content);
  } else {
    $("#unsafe-pre").textContent = "(every draft passed Scribble — no unsafe sample)";
    state.protocols.unsafe = null;
    state.parsedProtocols.unsafe = null;
  }
  // Re-render all three proto cards so the new VALID/UNSAFE graphs replace
  // the "(protocol unavailable)" placeholders the moment drafting finishes.
  renderProtoCards();
  // The per-arm state-machine view reads the same parsedProtocols, so refresh
  // whichever arm panel is currently showing the machine view.
  if (state.activeArm) {
    const activeVtab = state.panels[state.activeArm]?.root.querySelector(".vtab.active");
    if (activeVtab?.dataset.view === "machine") renderMachine(state.activeArm);
  }
}

function subscribeDraftStream(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  $("#sse-state").textContent = "draft connected";

  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.phase === "start") {
      $("#draft-summary").textContent =
        `case=${ev.case_id} · roles=${ev.roles.join(",")} · terminal=${ev.terminal_label}`;
    } else if (ev.phase === "attempt") {
      appendDraftRow(ev);
    } else if (ev.phase === "done") {
      renderKept(ev.kept_valid, ev.kept_unsafe);
      setStageStatus("intent", "done", "ok");
      setStageStatus("drafts", "ready", "ok");
      $("#btn-run").disabled = !state.draftKept.valid;
      openStage("stage-run");
    } else if (ev.phase === "error") {
      setStageStatus("intent", "error", "err");
      appendDraftRow({attempt: "!", valid: false, error: ev.error});
    }
  };
  es.addEventListener("done", () => {
    es.close();
    $("#btn-draft").disabled = false;
    $("#sse-state").textContent = "idle";
  });
  es.onerror = () => { $("#sse-state").textContent = "draft disconnected"; };
}

// ───────────────────────────────────────────────────────────────── panels + views

function indexPanels() {
  $$(".panel").forEach(p => {
    const arm = p.dataset.arm;
    state.panels[arm] = {
      root: p,
      viewEvents: p.querySelector(".view-events"),
      viewDiagram: p.querySelector(".view-diagram"),
      viewMachine: p.querySelector(".view-machine"),
      viewInteractions: p.querySelector(".view-interactions"),
      viewErrors: p.querySelector(".view-errors"),
      svg: p.querySelector(".seq-svg"),
      machineSvg: p.querySelector(".machine-svg"),
      machineSelect: p.querySelector(".machine-source"),
      interactionsSvg: p.querySelector(".interactions-svg"),
      interactionsSelect: p.querySelector(".interactions-source"),
      localsGrid: p.querySelector(".locals-grid"),
      stateBadge: p.querySelector(".arm-state"),
      goals: p.querySelector(".counter.goals"),
      viol: p.querySelector(".counter.viol"),
      footTk: p.querySelector(".tk"),
      footAtt: p.querySelector(".att"),
      footOk: p.querySelector(".ok-rate"),
      btnRoles: p.querySelector(".btn-roles"),
      vtabs: p.querySelectorAll(".vtab"),
      vtabCount: p.querySelector(".vtab[data-view='errors'] .vtab-count"),
    };
    state.armState[arm] = {trial: 0, attempt: 0, goals_pass: 0, viol: 0,
                          tokens: 0, succeeded: false, goals_total: 6};
    state.armEvents[arm] = [];
    state.armErrors[arm] = [];

    // View tab clicks (events / diagram / machine / errors)
    state.panels[arm].vtabs.forEach(tab => {
      tab.addEventListener("click", () => {
        state.panels[arm].vtabs.forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        p.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        const view = tab.dataset.view;
        p.querySelector(`.view-${view}`).classList.add("active");
        if (view === "diagram") renderDiagram(arm);
        if (view === "machine") renderMachine(arm);
        if (view === "interactions") renderInteractions(arm);
        if (view === "skills")  renderSkills(arm);
      });
    });
    state.panels[arm].machineSelect.addEventListener("change", () => renderMachine(arm));
    state.panels[arm].interactionsSelect.addEventListener("change",
                                () => renderInteractions(arm));

    state.panels[arm].btnRoles.addEventListener("click", () => openRolesModal(arm));
  });

  // Arm tabs (top strip)
  $$(".arm-tab").forEach(tab => {
    const arm = tab.dataset.arm;
    state.armTabs[arm] = tab;
    tab.addEventListener("click", () => activateArm(arm));
  });
  // Default-active arm is the first tab.
  const first = $$(".arm-tab")[0];
  if (first) state.activeArm = first.dataset.arm;
}

function activateArm(arm) {
  state.activeArm = arm;
  $$(".arm-tab").forEach(t => t.classList.toggle("active", t.dataset.arm === arm));
  $$(".panel").forEach(p => p.classList.toggle("active", p.dataset.arm === arm));
  // If the user is on the diagram or machine view, the SVGs may need a
  // re-layout because they were hidden when the events arrived.
  const p = state.panels[arm];
  const activeTab = p?.root.querySelector(".vtab.active");
  if (activeTab?.dataset.view === "diagram") renderDiagram(arm);
  if (activeTab?.dataset.view === "machine") renderMachine(arm);
}

function setArmState(arm, st, label) {
  const p = state.panels[arm];
  if (!p) return;
  p.stateBadge.dataset.state = st;
  p.stateBadge.textContent = label || st;
  const tab = state.armTabs[arm];
  if (tab) {
    const badge = tab.querySelector(".arm-tab-state");
    badge.dataset.state = st;
    badge.textContent = label || st;
  }
}

function syncArmTabCounters(arm) {
  const tab = state.armTabs[arm];
  if (!tab) return;
  const armSt = state.armState[arm];
  tab.querySelector(".arm-tab-g").textContent =
    `G ${armSt.goals_pass}/${armSt.goals_total}`;
  const v = tab.querySelector(".arm-tab-v");
  v.textContent = `V ${armSt.viol}`;
  v.dataset.zero = armSt.viol === 0 ? "1" : "0";
}

// ───────────────────────────────────────────────────────────────── events view

function pushEventRow(arm, ev) {
  const p = state.panels[arm];
  if (!p) return;
  const row = document.createElement("div");

  const m = ev.marker;
  if (m) {
    row.className = "evt marker " + (m === "attempt_end" ? "attempt-end" : "");
    let text;
    if (m === "trial_start")  text = `↳ trial ${ev.trial}  branch=${ev.branch || "—"}`;
    else if (m === "trial_end")
      text = `╞ trial ${ev.trial} end · ${ev.succeeded ? "OK" : "FAIL"} · attempts=${ev.attempts} · tokens=${ev.tokens?.total_tokens || 0}`;
    else if (m === "attempt_start") text = `  attempt ${ev.attempt} start`;
    else if (m === "attempt_end")
      text = `  attempt ${ev.attempt} end · events=${ev.events} · goals=${ev.goals_pass}/${ev.goals_total} · ${ev.all_goals_pass ? "GOALS PASS" : "retry"}`;
    else if (m === "protocol_unprojectable")
      text = `⚠ protocol_unprojectable (observational mode)`;
    else if (m === "attempt_timeout")
      text = `⏱ attempt_timeout after ${ev.timeout_s}s`;
    else text = m;
    row.innerHTML = `<span class="stp"></span><span class="body">${escHtml(text)}</span>`;
  } else if (ev.step) {
    const violType = ev.violation?.type;
    row.className = "evt" + (violType ? ` viol-${violType}` : "");
    const payload = ev.payload ? `(${escHtml(clamp(ev.payload, 24))})` : "";
    const violTag = violType ? ` <span style="color:var(--err)">[${violType}]</span>` : "";
    row.innerHTML = `
      <span class="stp">${ev.step}</span>
      <span class="body"><span class="sender">${escHtml(ev.sender)}</span><span class="arrow"> → </span><span class="recv">${escHtml(ev.receiver)}</span> : <span class="label">${escHtml(ev.label)}</span><span class="payload">${payload}</span>${violTag}</span>
    `;
    const armSt = state.armState[arm];
    if (ev.goals_pass !== undefined) {
      armSt.goals_pass = ev.goals_pass;
      armSt.goals_total = ev.goals_total;
      p.goals.textContent = `G ${ev.goals_pass}/${ev.goals_total}`;
    }
    if (violType) {
      armSt.viol += 1;
      p.viol.textContent = `V ${armSt.viol}`;
      p.viol.dataset.zero = "0";
      p.vtabCount.textContent = String(armSt.viol);
      p.vtabCount.dataset.zero = "0";
    }
    syncArmTabCounters(arm);
  } else {
    return;
  }

  p.viewEvents.appendChild(row);
  if (p.viewEvents.scrollHeight - p.viewEvents.scrollTop - p.viewEvents.clientHeight < 80) {
    p.viewEvents.scrollTop = p.viewEvents.scrollHeight;
  }
}

// ───────────────────────────────────────────────────────────────── errors view

const VIOLATION_WHY = {
  off_protocol:
    "Role's projected state machine has no transition matching " +
    "(sender, receiver, label) at the current state — the agent " +
    "emitted/received a message that's wrong here.",
  unexpected_peer:
    "The message LABEL is valid at the current state but the OTHER " +
    "party is wrong (e.g. agent sent it to itself or to a non-peer).",
};

function updateErrorsView(arm) {
  const p = state.panels[arm];
  if (!p) return;
  const errs = state.armErrors[arm];
  if (!errs.length) {
    p.viewErrors.innerHTML = `<div class="empty">no violations yet</div>`;
    return;
  }
  // Group by violation.type → then by (role, state) → list of {step, label, sender, receiver}
  const byType = new Map();
  for (const e of errs) {
    const t = e.violation.type || "unknown";
    if (!byType.has(t)) byType.set(t, new Map());
    const byKey = byType.get(t);
    const key = `${e.violation.role}@${e.violation.state}`;
    if (!byKey.has(key)) byKey.set(key, {
      role: e.violation.role,
      state: e.violation.state,
      expected: e.violation.expected || [],
      items: [],
    });
    byKey.get(key).items.push({
      step: e.step, label: e.label,
      sender: e.sender, receiver: e.receiver,
    });
  }
  const parts = [];
  for (const [type, byKey] of byType) {
    const total = Array.from(byKey.values()).reduce((s, g) => s + g.items.length, 0);
    parts.push(`<div class="err-group"><div class="err-group-head">
      <span><strong>${escHtml(type)}</strong>
        <span class="why">${escHtml(VIOLATION_WHY[type] || "")}</span></span>
      <span class="err-count">${total}</span>
    </div>`);
    for (const g of byKey.values()) {
      parts.push(`<div class="err-item">
        <div><span class="role">${escHtml(g.role)}</span><span class="state"> @ state ${escHtml(g.state)}</span> &nbsp; <em>occurred ${g.items.length}×</em></div>
        <div class="exp"><strong>expected:</strong> ${g.expected.map(escHtml).join(" │ ") || "—"}</div>
        <div class="got"><strong>got:</strong> ${g.items.slice(0, 3).map(i =>
          `<span class="step-tag">step ${i.step}</span>${escHtml(i.sender)}→${escHtml(i.receiver)}:${escHtml(i.label)}`
        ).join(" &nbsp;·&nbsp; ")}${g.items.length > 3 ? ` … (+${g.items.length - 3} more)` : ""}</div>
      </div>`);
    }
    parts.push(`</div>`);
  }
  p.viewErrors.innerHTML = parts.join("");
}

// ───────────────────────────────────────────────────────────────── diagram view

function renderDiagram(arm) {
  const p = state.panels[arm];
  if (!p || !p.svg) return;
  const events = state.armEvents[arm].filter(e => e.step);
  const roles = state.case?.roles || [];
  if (!roles.length) {
    p.svg.innerHTML = `<text x="10" y="20" class="seq-label">(no roles)</text>`;
    p.svg.setAttribute("viewBox", "0 0 200 30");
    return;
  }

  // Cap rendered events so the SVG stays readable on long traces.
  const MAX = 60;
  const recent = events.slice(-MAX);
  const startStep = events.length - recent.length;

  // Compact-ish dimensions. rowH=16 was too tight — labels overlap
  // adjacent rows. 20px gives clear separation while still fitting
  // ~25 events vertically before the panel needs to scroll.
  const W = 520;
  const laneGap = W / (roles.length + 1);
  const headerY = 14;
  const rowH = 20;
  const padTop = 24;
  const totalH = padTop + recent.length * rowH + 12;

  const ns = "http://www.w3.org/2000/svg";
  while (p.svg.firstChild) p.svg.removeChild(p.svg.firstChild);
  p.svg.setAttribute("viewBox", `0 0 ${W} ${Math.max(60, totalH)}`);
  p.svg.setAttribute("preserveAspectRatio", "xMidYMin meet");

  // Lane labels + vertical dashed lines
  roles.forEach((r, i) => {
    const x = laneGap * (i + 1);
    const lbl = document.createElementNS(ns, "text");
    lbl.setAttribute("x", x);
    lbl.setAttribute("y", headerY);
    lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("class", "seq-lane-label");
    lbl.textContent = r;
    p.svg.appendChild(lbl);

    const ln = document.createElementNS(ns, "line");
    ln.setAttribute("x1", x); ln.setAttribute("x2", x);
    ln.setAttribute("y1", headerY + 4); ln.setAttribute("y2", totalH - 4);
    ln.setAttribute("class", "seq-lane-line");
    p.svg.appendChild(ln);
  });

  if (events.length > recent.length) {
    const note = document.createElementNS(ns, "text");
    note.setAttribute("x", 6);
    note.setAttribute("y", headerY);
    note.setAttribute("class", "seq-step");
    note.textContent = `… (showing last ${MAX} of ${events.length})`;
    p.svg.appendChild(note);
  }

  // Arrows
  const lastIdx = recent.length - 1;
  recent.forEach((e, idx) => {
    const y = padTop + idx * rowH + rowH / 2;
    const fromI = roles.indexOf(e.sender);
    const toI = roles.indexOf(e.receiver);
    if (fromI < 0 || toI < 0) return;  // skip unrecognised roles
    const x1 = laneGap * (fromI + 1);
    const x2 = laneGap * (toI + 1);
    const isViol = !!e.violation;
    const isCurrent = idx === lastIdx;  // most recent arrow gets the pulse
    const cls = (isViol ? "viol" : "") + (isCurrent ? " current" : "");

    // step gutter
    const step = document.createElementNS(ns, "text");
    step.setAttribute("x", 4);
    step.setAttribute("y", y + 3);
    step.setAttribute("class", "seq-step");
    step.textContent = String(startStep + idx + 1);
    p.svg.appendChild(step);

    // line
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", x1); line.setAttribute("y1", y);
    line.setAttribute("x2", x2); line.setAttribute("y2", y);
    line.setAttribute("class", `seq-arrow ${cls}`);
    p.svg.appendChild(line);

    // arrow head (small triangle at x2)
    const head = document.createElementNS(ns, "polygon");
    const dir = x2 > x1 ? 1 : -1;
    const hx = x2 - dir * 6;
    head.setAttribute("points",
      `${x2},${y} ${hx},${y - 3} ${hx},${y + 3}`);
    head.setAttribute("class", `seq-arrow-head ${cls}`);
    p.svg.appendChild(head);

    // label
    const lbl = document.createElementNS(ns, "text");
    lbl.setAttribute("x", (x1 + x2) / 2);
    lbl.setAttribute("y", y - 3);
    lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("class", `seq-label ${cls}`);
    let text = e.label;
    if (e.payload) text += `(${clamp(e.payload, 12)})`;
    lbl.textContent = text;
    p.svg.appendChild(lbl);
  });
}

// ───────────────────────────────────────────────────────────────── 8-arm run

async function startRun() {
  $("#btn-run").disabled = true;

  for (const arm of Object.keys(state.panels)) {
    const p = state.panels[arm];
    p.viewEvents.innerHTML = "";
    p.viewErrors.innerHTML = `<div class="empty">no violations yet</div>`;
    while (p.svg.firstChild) p.svg.removeChild(p.svg.firstChild);
    p.goals.textContent = `G 0/${state.case?.goals?.length || 6}`;
    p.viol.textContent  = "V 0";
    p.viol.dataset.zero = "1";
    p.vtabCount.textContent = "0";
    p.vtabCount.dataset.zero = "1";
    p.footTk.textContent = "tokens 0";
    p.footAtt.textContent = "attempt 0/0";
    p.root.classList.remove("success", "failed");
    setArmState(arm, "idle", "idle");
    state.armState[arm] = {trial: 0, attempt: 0, goals_pass: 0, viol: 0,
                          tokens: 0, succeeded: false,
                          goals_total: state.case?.goals?.length || 6};
    state.armEvents[arm] = [];
    state.armErrors[arm] = [];
  }
  setStageStatus("run", "spawning case_runner");

  const r = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      case_id: state.caseId,
      n_trials: parseInt($("#n-trials").value, 10) || 1,
    }),
  });
  const j = await r.json();
  if (j.error) {
    setStageStatus("run", "error: " + j.error, "err");
    return;
  }
  state.runJobId = j.job_id;
  subscribeRunStream(j.job_id);
}

function handleRunEvent(envelope) {
  if (envelope.phase === "run_dir") {
    $("#foot-run").textContent = envelope.run_dir.split(/[\\/]/).pop();
    setStageStatus("run", "running", "active");
    // Wave 1 (Foundry-stack) → "setup", Wave 2 (MAF) → "queued"
    for (const arm of Object.keys(state.panels)) {
      if (WAVE_1.has(arm)) setArmState(arm, "setup", "setup");
      else setArmState(arm, "queued", "queued");
    }
  } else if (envelope.phase === "event") {
    const {arm, ev} = envelope;
    if (ev.marker === "trial_start") {
      setArmState(arm, "running");
    } else if (ev.marker === "trial_end") {
      const armSt = state.armState[arm];
      armSt.succeeded = !!ev.succeeded;
      armSt.tokens = ev.tokens?.total_tokens || 0;
      state.panels[arm].footTk.textContent = `tokens ${armSt.tokens}`;
      state.panels[arm].footAtt.textContent = `attempt ${ev.attempts}/${ev.attempts}`;
      // Make the "finished" state explicit per user request — show
      // "finished · ok" or "finished · fail" in the badge.
      const cls = armSt.succeeded ? "ok" : "fail";
      setArmState(arm, cls, "finished · " + (armSt.succeeded ? "ok" : "fail"));
      state.panels[arm].root.classList.add(armSt.succeeded ? "success" : "failed");
    } else if (ev.marker === "attempt_start") {
      const armSt = state.armState[arm];
      armSt.attempt = ev.attempt;
      armSt.viol = 0;
      state.panels[arm].viol.dataset.zero = "1";
      state.panels[arm].viol.textContent = "V 0";
      state.panels[arm].vtabCount.textContent = "0";
      state.panels[arm].vtabCount.dataset.zero = "1";
      state.panels[arm].footAtt.textContent = `attempt ${ev.attempt}/3`;
      // Clear per-attempt history so the diagram + errors view show only
      // the current attempt's interactions, not a cumulative mess.
      state.armEvents[arm] = [];
      state.armErrors[arm] = [];
      while (state.panels[arm].svg.firstChild)
        state.panels[arm].svg.removeChild(state.panels[arm].svg.firstChild);
      state.panels[arm].viewErrors.innerHTML = `<div class="empty">no violations yet</div>`;
    }
    pushEventRow(arm, ev);
    // Keep history for diagram + errors views
    if (ev.step) {
      state.armEvents[arm].push(ev);
      if (ev.violation) {
        state.armErrors[arm].push(ev);
        updateErrorsView(arm);
      }
      // If the diagram or interactions view is the active one, redraw
      // live. State-machine view also benefits from a live re-render so
      // the visited overlay + pulse pointer track each event.
      const activeTab = state.panels[arm].root.querySelector(".vtab.active");
      const v = activeTab?.dataset.view;
      if (v === "diagram") renderDiagram(arm);
      else if (v === "interactions") renderInteractions(arm);
      else if (v === "machine") renderMachine(arm);
    }
  } else if (envelope.phase === "summary") {
    renderSummary(envelope.summary, envelope.summary_eval);
    setStageStatus("run", envelope.returncode === 0 ? "done" : "exit " + envelope.returncode,
                   envelope.returncode === 0 ? "ok" : "err");
  } else if (envelope.phase === "error") {
    setStageStatus("run", "error", "err");
  }
}

function subscribeRunStream(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  $("#sse-state").textContent = "run connected";
  es.onmessage = (msg) => {
    const envelope = JSON.parse(msg.data);
    handleRunEvent(envelope);
  };
  es.addEventListener("done", () => {
    es.close();
    $("#sse-state").textContent = "idle";
    $("#btn-run").disabled = false;
    openStage("stage-summary");
    setStageStatus("summary", "ready", "ok");
  });
  es.onerror = () => { $("#sse-state").textContent = "run disconnected"; };
}

// ───────────────────────────────────────────────────────────────── stage 4: summary

function renderSummary(summary, summary_eval) {
  const root = $("#summary-tables");
  root.innerHTML = "";
  if (summary && summary.scenarios) {
    const sc = summary.scenarios;
    const cols = Object.keys(sc);
    const rows = [
      ["success rate",     k => sc[k].success_rate_pct + "%"],
      ["avg attempts",     k => sc[k].avg_attempts_all],
      ["avg seconds/trial",k => sc[k].avg_seconds_per_trial],
      ["avg tokens/trial", k => sc[k].avg_tokens_per_trial],
      ["calls/trial",      k => sc[k].avg_calls_per_trial],
      ["total events",     k => sc[k].events],
      ["violations",       k => sc[k].violations],
    ];
    root.appendChild(renderTable("Set A · process cost + monitor verdicts",
      cols, rows, k => sc[k].scenario_name));
  }
  if (summary_eval && summary_eval.arms) {
    const sc = summary_eval.arms;
    const cols = Object.keys(sc);
    const goalIds = (state.case?.goals || []).map(g => g.id);
    const baseRows = [
      ["strict %",     k => sc[k].strict_pct ?? "N/A"],
      ["role-pair %",  k => sc[k].role_pair_pct ?? "N/A"],
    ];
    const perGoalRows = goalIds.map(gid =>
      [`per-goal ${gid}`, k => sc[k].role_pair_per_goal?.[gid] ?? "—"]);
    root.appendChild(renderTable("Set B · goal achievement",
      cols, [...baseRows, ...perGoalRows], k => sc[k].arm_name));
  }
}

function renderTable(title, cols, rows, nameOf) {
  const box = document.createElement("div");
  box.className = "summary-table";
  let html = `<h3>${escHtml(title)}</h3><table><thead><tr><th>metric</th>`;
  for (const c of cols) html += `<th>${escHtml(nameOf(c))}</th>`;
  html += "</tr></thead><tbody>";
  for (const [label, fn] of rows) {
    html += `<tr><td>${escHtml(label)}</td>`;
    for (const c of cols) html += `<td>${escHtml(fn(c))}</td>`;
    html += "</tr>";
  }
  html += "</tbody></table>";
  box.innerHTML = html;
  return box;
}

// ───────────────────────────────────────────────────────────────── role drilldown modal

async function openRolesModal(arm) {
  const c = state.case;
  if (!c) return;
  // We can show role description + goals even before a run; system.md only
  // exists once setup() has persisted prompts under runs/<ts>/prompts/.
  const haveRun = !!state.runJobId;
  const indexResp = haveRun
    ? await fetch(`/api/runs/${state.runJobId}/prompts/${arm}/index.json`)
    : null;
  const indexJson = (indexResp && indexResp.ok) ? await indexResp.json() : null;
  const promptRoles = indexJson?.roles?.map(r => r.role) || [];

  const tabs = c.roles.map(role => ({label: role}));
  if (promptRoles.includes("__orchestrator__"))
    tabs.push({label: "__orchestrator__"});

  $("#modal-title").textContent = `roles · ${arm}` + (haveRun ? "" : "  (run hasn't started yet — system prompts unavailable)");
  $("#modal-tabs").innerHTML = "";
  // Replace the default pre body with the structured role-detail container.
  let body = document.getElementById("modal-body");
  if (body.tagName === "PRE") {
    const div = document.createElement("div");
    div.id = "modal-body";
    div.className = "role-detail";
    body.replaceWith(div);
  } else {
    body.className = "role-detail";
    body.innerHTML = "";
  }
  $("#modal").classList.remove("hidden");

  let firstTab = null;
  tabs.forEach((t, i) => {
    const tab = document.createElement("span");
    tab.className = "tab" + (i === 0 ? " active" : "");
    tab.textContent = t.label;
    tab.addEventListener("click", () => {
      $$("#modal-tabs .tab").forEach(x => x.classList.remove("active"));
      tab.classList.add("active");
      renderRoleDetail(arm, t.label, haveRun);
    });
    $("#modal-tabs").appendChild(tab);
    if (i === 0) firstTab = t.label;
  });
  if (firstTab) renderRoleDetail(arm, firstTab, haveRun);
}

async function renderRoleDetail(arm, role, haveRun) {
  const body = document.getElementById("modal-body");
  body.innerHTML = `<div class="section"><h4>Loading…</h4></div>`;

  const c = state.case;
  const isOrch = role === "__orchestrator__";
  const desc = isOrch
    ? "MAF GroupChat speaker-selection LLM (not a participant role — picks who speaks each turn)."
    : (c.role_descriptions?.[role] || c.roles_block?.[role] || "(no description)");
  // case_loader keeps role_descriptions on case.role_descriptions; the API
  // doesn't return that field today (we only get roles list). Pull it from
  // /api/cases via state.case if present.
  const roleDesc = isOrch ? desc : (
    state.case?.role_descriptions?.[role] ||
    findRoleDescription(role) ||
    "(no description on file)"
  );

  // Goals this role anchors.
  const goals = (c.goals || []);
  const anchored = goals.filter(g => {
    // Goals come from case.yaml — case-loader-served version doesn't include
    // anchor info, so we fall back to "applies to all" when anchor missing.
    return (g.anchor?.sender === role) || (g.anchor?.receiver === role);
  });

  // system.md — only available after a run started.
  let systemMd = isOrch
    ? "(orchestrator instructions — open after run starts)"
    : "(start a run first — prompts are persisted per run.)";
  if (haveRun) {
    const fname = isOrch ? "__orchestrator__.system.md" : `${role}.system.md`;
    const r = await fetch(`/api/runs/${state.runJobId}/prompts/${arm}/${fname.replace('.system.md','')}.md`);
    if (r.ok) systemMd = await r.text();
    else systemMd = `(no system.md found at runs/.../prompts/${arm}/${fname})`;
  }

  // Render
  const goalCards = (goals.length === 0)
    ? `<div class="muted small">no goals defined on this case</div>`
    : goals.map(g => {
        const isAnchor = anchored.some(a => a.id === g.id);
        const cls = isOrch ? "other" : (isAnchor ? "anchored" : "other");
        const anchorInfo = g.anchor
          ? `anchor: ${g.anchor.sender}→${g.anchor.receiver}:${g.anchor.label}`
          : "anchor: (not specified)";
        const branchInfo = g.branch ? ` · branch=${g.branch}` : "";
        return `<div class="goal ${cls}">
          <span class="gid">${escHtml(g.id)}</span>
          <div>
            <div class="gbody">${escHtml(g.description || "")}</div>
            <div class="gmeta">${escHtml(anchorInfo)}${escHtml(branchInfo)}
              ${g.predicate ? ` · pred: <code>${escHtml(g.predicate)}</code>` : ""}
              ${g.threshold ? ` · thresh: ${escHtml(g.threshold)}` : ""}
            </div>
          </div>
        </div>`;
      }).join("");

  body.innerHTML = `
    <div class="section">
      <h4>Role description</h4>
      <div class="section-body">${escHtml(roleDesc)}</div>
    </div>
    <div class="section">
      <h4>Goals this role anchors (green = anchored, grey = doesn't apply)</h4>
      <div class="section-body"><div class="goals-list">${goalCards}</div></div>
    </div>
    <div class="section system-md">
      <h4>System prompt installed on the agent (full pre-truncation)</h4>
      <div class="section-body">${escHtml(systemMd)}</div>
    </div>`;
}

// case.yaml has `role_descriptions: {RoleName: "...", ...}` but our
// /api/cases response keeps only roles[] today. Until the API surfaces it,
// fall back to grabbing it from the canonical protocol pane if present.
function findRoleDescription(role) {
  // Pull from state.case if the API has been extended.
  return state.case?.role_descriptions?.[role] || null;
}

// ───────────────────────────────────────────────────────────────── expected-protocol modal

async function openExpectedProtocolModal() {
  $("#modal-title").textContent = `expected protocols · ${state.caseId}`;
  $("#modal-tabs").innerHTML = "";
  let body = document.getElementById("modal-body");
  if (body.tagName !== "PRE") {
    const pre = document.createElement("pre");
    pre.id = "modal-body";
    pre.className = "modal-pre";
    body.replaceWith(pre);
    body = pre;
  } else {
    body.className = "modal-pre";
  }
  $("#modal").classList.remove("hidden");

  const tabs = [
    {label: "canonical (v1.scr)",     url: `/api/case/${state.caseId}/protocol`},
    {label: "LLM-drafted valid",      url: `/api/case/${state.caseId}/draft/valid`},
    {label: "LLM-drafted unsafe",     url: `/api/case/${state.caseId}/draft/unsafe`},
    {label: "refinements (v1.refn)",  url: `/api/case/${state.caseId}/refinement`},
  ];
  tabs.forEach((t, i) => {
    const tab = document.createElement("span");
    tab.className = "tab" + (i === 0 ? " active" : "");
    tab.textContent = t.label;
    tab.addEventListener("click", async () => {
      $$("#modal-tabs .tab").forEach(x => x.classList.remove("active"));
      tab.classList.add("active");
      $("#modal-body").textContent = "(loading…)";
      const r = await fetch(t.url);
      $("#modal-body").textContent = r.ok ? await r.text() : `(missing: ${t.url})`;
    });
    $("#modal-tabs").appendChild(tab);
  });
  if (tabs.length) {
    fetch(tabs[0].url).then(r => r.ok ? r.text() : `(missing: ${tabs[0].url})`)
      .then(t => $("#modal-body").textContent = t);
  }
}

// ───────────────────────────────────────────────────────────────── state-machine view
//
// The renderer takes a parsed Scribble protocol (see parseScribble below)
// and lays it out as the global state machine the reference image shows:
// s0 at top, optional pre-choice chain, branch clusters in dashed boxes
// labelled with the branch tag, a merge node m, then the post-choice tail.
// Edges are labelled `From→To !Label` using letter abbreviations of roles
// so the labels fit between nodes.

const ROLE_ABBR_OVERRIDES = {
  // case-specific shortenings that match the convention in the reference
  // image (state_machine_graph.png). The renderer falls back to first-letter
  // abbreviation if a role isn't here.
  "Fetcher": "F",
  "RevenueAnalyst": "RA",
  "ExpenseAnalyst": "EA",
  "Writer": "W",
  "TaxVerifier": "TV",
  "TaxSpecialist": "TS",
};

function abbr(role) {
  if (ROLE_ABBR_OVERRIDES[role]) return ROLE_ABBR_OVERRIDES[role];
  // Fall back to capitals (CamelCase → CC) or first 2 chars
  const caps = role.match(/[A-Z]/g);
  if (caps && caps.length >= 2) return caps.join("");
  return role.slice(0, 2);
}

// Minimal Scribble parser — enough for the rendering. Extracts:
//   - roles: from the `global protocol Name(role A, role B, ...)` header
//   - chooser: from `choice at <Role>`
//   - preMessages:  messages BEFORE the first `choice at`
//   - branches:     [{messages: [...]}], one per `} or {` block (no per-branch label
//                   in Scribble itself, so we name them branch1/branch2)
//   - postMessages: messages AFTER the closing `}` of the choice block
// Messages look like `Label(Type) from Sender to Receiver;`. We ignore
// nested choice blocks and aux/sub-protocols — the canonical and the LLM
// drafts we care about don't use those patterns.
/** Strip `rec X { body }` wrappers — replace each block with its body so
 *  the rest of the parser sees a flattened "one iteration" view. Repeated
 *  until no `rec` blocks remain so nested recursions also flatten. */
function stripRecBlocks(text) {
  let safety = 20;
  while (safety-- > 0) {
    const m = text.match(/\brec\s+\w+\s*\{/);
    if (!m) break;
    const openIdx = m.index + m[0].length - 1;   // position of the `{`
    let depth = 1, closeIdx = -1;
    for (let i = openIdx + 1; i < text.length; i++) {
      if (text[i] === "{") depth++;
      else if (text[i] === "}") { depth--; if (depth === 0) { closeIdx = i; break; } }
    }
    if (closeIdx < 0) break;
    const body = text.slice(openIdx + 1, closeIdx);
    text = text.slice(0, m.index) + " " + body + " " + text.slice(closeIdx + 1);
  }
  return text;
}

function parseScribble(text) {
  if (!text) return null;
  let src = text.replace(/\/\/[^\n]*/g, "");
  // Flatten Scribble features the simple renderer doesn't model — rec/continue
  // (loops) and `do SubProto(...)` (sub-protocol calls). This way cases like
  // retry_loop, iterative_polling, banking (nested choices), nested_retry
  // render their top-level message flow correctly instead of producing
  // a blank graph because the parser couldn't find balanced braces.
  src = stripRecBlocks(src);
  src = src.replace(/\bcontinue\s+\w+\s*;/g, "");
  src = src.replace(/\bdo\s+(\w+)\s*\(([^)]*)\)\s*;/g,
                    "DoSubProtocol() from SubProtoCaller to $1;");
  const roleMatch = src.match(/global\s+protocol\s+\w+\s*\(([^)]*)\)/);
  const roles = roleMatch
    ? Array.from(roleMatch[1].matchAll(/role\s+(\w+)/g)).map(m => m[1])
    : [];

  // Find body — the outermost { ... } after the protocol header.
  const headerEnd = roleMatch ? roleMatch.index + roleMatch[0].length : 0;
  const bodyStart = src.indexOf("{", headerEnd);
  if (bodyStart < 0) return {roles, chooser: null, preMessages: [], branches: [], postMessages: []};
  // Find matching closing brace by counting depth — handles nested blocks.
  let depth = 0, bodyEnd = -1;
  for (let i = bodyStart; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) { bodyEnd = i; break; } }
  }
  const body = src.slice(bodyStart + 1, bodyEnd === -1 ? src.length : bodyEnd);

  // Find the first `choice at <Role>` (top-level only; nested choice not handled).
  const choiceMatch = body.match(/choice\s+at\s+(\w+)\s*\{/);
  const msgRe = /(\w+)\s*\(([^)]*)\)\s+from\s+(\w+)\s+to\s+(\w+)\s*;/g;

  const parseMessages = (s) => {
    const out = []; let m;
    msgRe.lastIndex = 0;
    while ((m = msgRe.exec(s))) {
      out.push({label: m[1], type: m[2].trim() || null, from: m[3], to: m[4]});
    }
    return out;
  };

  if (!choiceMatch) {
    return {roles, chooser: null, preMessages: parseMessages(body),
            branches: [], postMessages: []};
  }

  // Slice body into [pre, choice-content, post]. The walker has to skip
  // the `} or {` separators between branches — otherwise the first `}`
  // ending branch 1 looks like the end of the whole choice block and we
  // only see one branch. Empirically this was making canonical render
  // as ONE branch at x=0, which made every node overlap with the spine.
  const preBody = body.slice(0, choiceMatch.index);
  let cIdx = choiceMatch.index + choiceMatch[0].length;
  depth = 1;
  let choiceEnd = -1;
  let i = cIdx;
  while (i < body.length) {
    const ch = body[i];
    if (ch === "{") { depth++; i++; }
    else if (ch === "}") {
      const sep = body.slice(i + 1).match(/^\s*or\s*\{/);
      if (depth === 1 && sep) {
        // `} or {` — branch separator, depth stays at 1. Skip past it.
        i = i + 1 + sep[0].length;
      } else {
        depth--;
        if (depth === 0) { choiceEnd = i; break; }
        i++;
      }
    } else {
      i++;
    }
  }
  const choiceContent = body.slice(cIdx, choiceEnd === -1 ? body.length : choiceEnd);
  const postBody = body.slice(choiceEnd === -1 ? body.length : choiceEnd + 1);

  // Split branches on `} or {`. Note Scribble syntax: closing brace of one
  // branch, the keyword `or`, opening brace of the next.
  // The branches we see in choiceContent are like
  //    msg1; msg2; } or { msg3; msg4;
  // because the first `{` was already consumed.
  const branchTexts = choiceContent.split(/\}\s*or\s*\{/);
  const branchLabel = (i, msgs) => {
    // Heuristic: take the first message's label and strip common suffixes.
    if (!msgs.length) return `branch${i + 1}`;
    const lbl = msgs[0].label.replace(/(Revenue|Notice|Notification|Branch)$/g, "");
    return lbl || `branch${i + 1}`;
  };
  const branches = branchTexts.map((t, i) => {
    const msgs = parseMessages(t);
    return {name: branchLabel(i, msgs), messages: msgs};
  });

  return {
    roles,
    chooser: choiceMatch[1],
    preMessages: parseMessages(preBody),
    branches,
    postMessages: parseMessages(postBody),
  };
}

async function loadProtocolSources() {
  if (!state.caseId) return;
  const sources = ["canonical", "valid", "unsafe"];
  await Promise.all(sources.map(async kind => {
    const url = kind === "canonical"
      ? `/api/case/${state.caseId}/protocol`
      : `/api/case/${state.caseId}/draft/${kind}`;
    const r = await fetch(url);
    if (r.ok) {
      const text = await r.text();
      state.protocols[kind] = text;
      state.parsedProtocols[kind] = parseScribble(text);
    } else {
      state.protocols[kind] = null;
      state.parsedProtocols[kind] = null;
    }
  }));
  // Now that all three protocols are parsed, paint the proto-card
  // state-machine + sequence views in stage 2.
  renderProtoCards();
}

// Compact dimensions for the state machine — halved from the original so
// both EXPECTED and ACTUAL SVGs fit comfortably side-by-side without
// scrolling for typical protocols (6 roles, 1 choice, 2 branches).
// Full-size dimensions for the per-arm state machine view (panel is ~1500px
// wide on a typical monitor — labels + clusters have room to breathe).
const SM_DIMS = {
  NODE_R: 12,
  COL_W: 260,
  ROW_H: 54,
  BRANCH_GAP_TOP: 28,
  CLUSTER_PAD: 80,
  TOP_PAD: 24,
  W_MIN: 540,
  LABEL_MARGIN: 160,
  EDGE_LABEL_MAX: 22,
};

// Compact dimensions for the stage-2 proto-cards. Each card is ~1/3 of the
// stage width (~460px in a 3-col grid), so the same SVG-content viewBox
// scaled to fit looks tiny. Tighter spacing + shorter labels keep the
// graph readable at the proto-card's natural width.
const SM_DIMS_COMPACT = {
  NODE_R: 10,
  COL_W: 170,
  ROW_H: 38,
  BRANCH_GAP_TOP: 18,
  CLUSTER_PAD: 40,
  TOP_PAD: 18,
  W_MIN: 380,
  LABEL_MARGIN: 90,
  EDGE_LABEL_MAX: 13,   // tight — matches the reference image abbreviations
};

/** Aggressive abbreviation for edge labels. Picks the first CamelCase
 *  segment of the message name and trims to `max` chars. Matches the
 *  reference image (HighRevenue → HighRev, AuditedRevenue → Audited). */
function compactMsgName(name, max) {
  if (!name) return name;
  // First CamelCase word — split before each capital letter, take the
  // FIRST chunk that's not just a single letter.
  const parts = name.match(/[A-Z][a-z0-9]*/g) || [name];
  let head = parts[0] || name;
  if (head.length < max && parts[1]) head += parts[1];
  if (head.length > max) head = head.slice(0, max);
  return head;
}

/** Truncate an edge label to keep the graph readable on busy protocols.
 *  Full label still goes into the SVG <title> tooltip so hovering reveals it. */
function truncLabel(s, max = 22) {
  if (!s) return s;
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

// ─── live interactions view ──────────────────────────────────────────────
//
// A simpler graph than the state machine. Roles arranged in a circle,
// edges drawn between any (sender, receiver) pair that appears in the
// protocol. As events stream in, edges flash in real time:
//
//   valid message              → green flash on the edge
//   off_protocol violation     → red flash on the edge (still in protocol)
//   unexpected_peer violation  → red pulse on the TWO ROLES involved
//                                (no edge exists for that pair)
//
// Alongside the graph, per-role lanes show `!Label` / `?Label` for every
// send/receive that role has been involved in, with the latest one pulsing.

/** Per-pair counters so the graph can show "visited N times" badges. */
function _emptyPairKey(a, b) { return `${a}→${b}`; }

function _buildExpectedPairs(parsed) {
  // Returns Set of "from→to" strings — every directed pair appearing in
  // the protocol (pre, branches, post).
  const pairs = new Set();
  if (!parsed) return pairs;
  for (const m of _flatMessages(parsed)) pairs.add(_emptyPairKey(m.from, m.to));
  return pairs;
}

function renderInteractions(arm) {
  const p = state.panels[arm];
  if (!p) return;
  const source = p.interactionsSelect.value || "canonical";
  const parsed = state.parsedProtocols[source];
  const roles = state.case?.roles || [];

  // Full repaint of the global graph + the per-role columns.
  drawInteractionsGraph(p.interactionsSvg, roles, parsed, arm);
  renderLocals(arm, roles);
}

function drawInteractionsGraph(svg, roles, parsed, arm) {
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const ns = "http://www.w3.org/2000/svg";
  const W = 460, H = 380;
  const cx = W / 2, cy = H / 2;
  const R = Math.min(W, H) / 2 - 60;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

  if (!roles.length) {
    appendSvgText(svg, "(no case selected)", 12, 24, "sm-empty");
    return;
  }

  // Lay roles around a circle, starting at top.
  const N = roles.length;
  const positions = {};
  roles.forEach((r, i) => {
    const angle = -Math.PI / 2 + (i / N) * 2 * Math.PI;
    positions[r] = {x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle)};
  });

  // Expected pairs from the protocol — those are the edges we DRAW. Pairs
  // not in this set will trigger the role-pulse path on violation.
  const expected = _buildExpectedPairs(parsed);

  // Walk this arm's events to compute per-edge {visited count, last status}
  // and per-pair sets of "off_protocol" and "unexpected_peer" violations.
  const visited = new Map();      // "a→b" -> {ok, viol, lastViolType}
  const violRoles = new Set();    // roles touched by an unexpected_peer
  let latestEdgeKey = null;
  let latestKlass   = null;       // "active-ok" | "active-viol"
  for (const e of state.armEvents[arm] || []) {
    if (!e.step) continue;
    const key = _emptyPairKey(e.sender, e.receiver);
    if (!visited.has(key)) visited.set(key, {ok: 0, viol: 0, last: null});
    const v = visited.get(key);
    if (e.violation) {
      v.viol++;
      v.last = "viol";
      latestEdgeKey = key;
      latestKlass = "active-viol";
      if (e.violation.type === "unexpected_peer") {
        violRoles.add(e.sender);
        violRoles.add(e.receiver);
      }
    } else {
      v.ok++;
      v.last = "ok";
      latestEdgeKey = key;
      latestKlass = "active-ok";
    }
  }

  // Draw protocol edges (the static ones) first so they sit BEHIND visited
  // overlays. Edges have a small curve so bidirectional pairs don't overlap.
  // Use a quadratic Bezier with a control point offset perpendicular to the
  // edge by ~22px.
  const drawCurve = (a, b, klass, count, lbl) => {
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.hypot(dx, dy) || 1;
    const ux = dx / dist, uy = dy / dist;
    const px = -uy, py = ux;
    const off = 22;
    const mx = (a.x + b.x) / 2 + px * off;
    const my = (a.y + b.y) / 2 + py * off;
    // Endpoint clipping so curve starts/ends on node circumference (r=22).
    const r = 22;
    const x1 = a.x + ux * r, y1 = a.y + uy * r;
    const x2 = b.x - ux * r, y2 = b.y - uy * r;
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", `M ${x1},${y1} Q ${mx},${my} ${x2},${y2}`);
    path.setAttribute("class", `gx-edge ${klass}`);
    svg.appendChild(path);
    if (count) {
      const cnt = document.createElementNS(ns, "text");
      cnt.setAttribute("x", mx); cnt.setAttribute("y", my);
      cnt.setAttribute("class", "gx-counter");
      cnt.textContent = String(count);
      svg.appendChild(cnt);
    }
  };

  // Protocol-defined edges (faint base)
  for (const key of expected) {
    const [from, to] = key.split("→");
    const a = positions[from], b = positions[to];
    if (!a || !b) continue;
    const v = visited.get(key) || {ok: 0, viol: 0, last: null};
    let cls = "";
    if (v.last === "ok") cls = "visited-ok";
    else if (v.last === "viol") cls = "visited-viol";
    if (key === latestEdgeKey) cls = latestKlass;
    drawCurve(a, b, cls, v.ok + v.viol > 0 ? v.ok + v.viol : 0);
  }

  // Now: edges that the arm USED but that aren't in the protocol. These
  // are the "undefined role-pair" cases — typically the unexpected_peer
  // class. We draw them as dashed red curves so the audience sees the
  // bogus connection.
  for (const [key, v] of visited) {
    if (expected.has(key)) continue;
    const [from, to] = key.split("→");
    const a = positions[from], b = positions[to];
    if (!a || !b) continue;
    const cls = key === latestEdgeKey ? "active-viol" : "visited-viol";
    drawCurve(a, b, cls, v.ok + v.viol);
  }

  // Role nodes ON TOP so they cover edge tails. Roles involved in an
  // unexpected_peer violation get the red-pulse class.
  for (const r of roles) {
    const pos = positions[r];
    if (!pos) continue;
    const cls = violRoles.has(r) ? "gx-node role-err" : "gx-node";
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", pos.x); c.setAttribute("cy", pos.y);
    c.setAttribute("r", 22); c.setAttribute("class", cls);
    svg.appendChild(c);
    const lbl = document.createElementNS(ns, "text");
    lbl.setAttribute("x", pos.x); lbl.setAttribute("y", pos.y);
    lbl.setAttribute("class", "gx-node-label");
    lbl.textContent = r.length > 7 ? r.slice(0, 6) + "…" : r;
    // Full name in title tooltip.
    const title = document.createElementNS(ns, "title");
    title.textContent = r;
    lbl.appendChild(title);
    svg.appendChild(lbl);
  }
}

function renderLocals(arm, roles) {
  const p = state.panels[arm];
  if (!p?.localsGrid) return;
  const grid = p.localsGrid;
  grid.innerHTML = "";
  if (!roles.length) {
    grid.innerHTML = `<div class="muted small">(no roles)</div>`;
    return;
  }
  // For each role, walk the arm's events and emit !label (when role is
  // sender) or ?label (when role is receiver). Each item gets a class for
  // send/recv and an optional viol marker if the event was a violation.
  const events = state.armEvents[arm] || [];
  // Index the latest event per role so we can mark "latest" with a pulse.
  const lastIdxPerRole = {};
  events.forEach((e, i) => {
    if (!e.step) return;
    lastIdxPerRole[e.sender] = i;
    lastIdxPerRole[e.receiver] = i;
  });
  for (const role of roles) {
    const col = document.createElement("div");
    col.className = "local-col";
    let inner = `<header>${escHtml(role)}</header><div class="local-stream">`;
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (!e.step) continue;
      let row;
      if (e.sender === role) {
        const cls = "send" + (e.violation ? " viol" : "")
                  + (lastIdxPerRole[role] === i ? " latest" : "");
        row = `<div class="local-evt ${cls}" title="${escHtml(`step ${e.step}: → ${e.receiver}`)}">!${escHtml(e.label)}</div>`;
      } else if (e.receiver === role) {
        const cls = "recv" + (e.violation ? " viol" : "")
                  + (lastIdxPerRole[role] === i ? " latest" : "");
        row = `<div class="local-evt ${cls}" title="${escHtml(`step ${e.step}: ← ${e.sender}`)}">?${escHtml(e.label)}</div>`;
      } else {
        continue;
      }
      inner += row;
    }
    inner += `</div>`;
    col.innerHTML = inner;
    grid.appendChild(col);
  }
}

/**
 * Render the state machine for a given arm panel.
 *
 * Two SVGs side-by-side:
 *   - EXPECTED: static protocol structure (no visited overlay, no pulse)
 *   - ACTUAL:   same structure plus the arm's live trace highlighting
 *               (visited nodes/edges in green, violation edges in red,
 *                and a pulse on the most recent visited node)
 *
 * The shared layout is computed once from the parsed protocol and then
 * the two SVGs are drawn from it: the expected pass gets no overlay,
 * the actual pass adds class names based on the arm's events.
 */
function renderMachine(arm) {
  const p = state.panels[arm];
  if (!p) return;

  const source = p.machineSelect.value || "canonical";
  const parsed = state.parsedProtocols[source];
  const expectedSvg = p.root.querySelector(".machine-svg-expected");
  const actualSvg   = p.root.querySelector(".machine-svg-actual");
  const roles = state.case?.roles || parsed?.roles || [];

  if (!parsed || !roles.length) {
    for (const svg of [expectedSvg, actualSvg]) {
      while (svg && svg.firstChild) svg.removeChild(svg.firstChild);
      if (svg) {
        appendSvgText(svg, "(protocol not loaded — pick a case)", 12, 24, "sm-empty");
        svg.setAttribute("viewBox", "0 0 400 60");
      }
    }
    return;
  }

  // Simple agent-topology graph (matches state_machine_graph.png) for both
  // panes. EXPECTED = static topology (no overlay); ACTUAL = same topology
  // plus this arm's live trace highlighting (visited edges green, violations
  // red, latest step pulses). Reusing the robust topology renderer means
  // every case draws — even nested-choice / rec protocols that the detailed
  // state-machine layout left blank.
  drawInteractionsGraph(expectedSvg, roles, parsed, null);
  drawInteractionsGraph(actualSvg, roles, parsed, arm);
}

/** Sentinel for "no overlay" — every field drawMachine reads is present.
 *  Pre-fix, callers built {visited, viol, current} by hand and forgot
 *  edgeKlass, then drawMachine threw on `.get()`. */
function emptyOverlay() {
  return {visited: new Set(), viol: new Set(),
          edgeKlass: new Map(), current: null, currentIsViol: false};
}

/**
 * Render a single state machine (used by the proto-card "state machine"
 * view — stage 2 cards have no live trace, so no overlay is applied).
 */
function renderMachineSingle(svg, parsed) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!parsed || !(parsed.roles && parsed.roles.length)) {
    appendSvgText(svg, "(protocol unavailable)", 12, 24, "sm-empty");
    svg.setAttribute("viewBox", "0 0 400 60");
    return;
  }
  // Simple agent-topology graph (matches state_machine_graph.png): roles
  // arranged in a circle, dashed edges for every role-pair in the protocol.
  // This robust renderer only needs roles + message pairs, so it draws for
  // every case (including nested-choice / rec protocols the old detailed
  // state-machine layout couldn't handle and left blank).
  drawInteractionsGraph(svg, parsed.roles, parsed, null);
}

/**
 * Build a topology + 2D layout for the protocol's global state machine.
 * Returns {nodes, edges, clusters, dims}. The same layout is used to
 * paint both EXPECTED and ACTUAL SVGs so they line up exactly.
 */
function buildMachineLayout(parsed, dims) {
  const D = dims || SM_DIMS;
  const nodes = [];
  const edges = [];
  let clusters = [];

  let y = D.TOP_PAD;
  let nextId = 0;
  const mkId = () => "n" + (nextId++);

  const startNode = {id: mkId(), x: 0, y, label: "s0", klass: "start"};
  nodes.push(startNode);
  y += D.ROW_H;

  // Pre-choice chain (sequential vertical column down the centre)
  let prevId = startNode.id;
  parsed.preMessages.forEach((m, idx) => {
    const id = mkId();
    nodes.push({id, x: 0, y, label: `p${idx + 1}`, klass: "node"});
    edges.push({from: prevId, to: id, label: edgeLabel(m, D.EDGE_LABEL_MAX), msgLabel: m.label});
    prevId = id;
    y += D.ROW_H;
  });

  let postPrevId = prevId;

  if (parsed.chooser && parsed.branches.length > 0) {
    // The current prev node IS the choice node — annotate it. If it's
    // the start node (no preMessages) keep "start" so the badge keeps
    // its s0 styling and just add the choice label underneath.
    const choiceNode = nodes.find(n => n.id === prevId);
    if (choiceNode) {
      if (choiceNode.klass !== "start") choiceNode.klass = "choice";
      choiceNode.choiceLabel = `choice@${parsed.chooser}`;
    }

    const totalBranchWidth = D.COL_W * parsed.branches.length;
    const branchStartX = -totalBranchWidth / 2 + D.COL_W / 2;
    const branchTop = y + D.BRANCH_GAP_TOP;

    parsed.branches.forEach((b, bi) => {
      const colX = branchStartX + bi * D.COL_W;
      let by = branchTop;
      const branchNodeIds = [];
      let prevInBranch = prevId;
      b.messages.forEach((m, mi) => {
        const id = mkId();
        nodes.push({id, x: colX, y: by,
                    label: `${b.name[0] || "b"}${mi + 1}`, klass: "node"});
        edges.push({from: prevInBranch, to: id, label: edgeLabel(m, D.EDGE_LABEL_MAX), msgLabel: m.label});
        prevInBranch = id;
        branchNodeIds.push(id);
        by += D.ROW_H;
      });
      if (branchNodeIds.length) {
        clusters.push({
          x: colX - D.NODE_R - D.CLUSTER_PAD,
          y: branchTop - 16,
          w: D.NODE_R * 2 + D.CLUSTER_PAD * 2,
          h: (by - branchTop) + 4,
          label: `${b.name.toUpperCase()} branch`,
        });
      }
      b._tailNodeId = prevInBranch;
    });

    const mergeY = branchTop + Math.max(
      ...parsed.branches.map(b => b.messages.length * D.ROW_H),
      D.ROW_H,
    ) + D.BRANCH_GAP_TOP;
    const mergeNode = {id: mkId(), x: 0, y: mergeY, label: "m", klass: "merge"};
    nodes.push(mergeNode);
    parsed.branches.forEach(b => {
      if (b.messages.length > 0) {
        edges.push({from: b._tailNodeId, to: mergeNode.id, label: "", msgLabel: null});
      } else {
        edges.push({from: prevId, to: mergeNode.id,
                    label: `(empty ${b.name})`, msgLabel: null});
      }
    });
    postPrevId = mergeNode.id;
    y = mergeY + D.ROW_H;
  }

  parsed.postMessages.forEach((m, idx) => {
    const id = mkId();
    const isLast = idx === parsed.postMessages.length - 1;
    nodes.push({id, x: 0, y, label: "q" + (idx + 1),
                klass: isLast ? "accept" : "node"});
    edges.push({from: postPrevId, to: id, label: edgeLabel(m, D.EDGE_LABEL_MAX), msgLabel: m.label});
    postPrevId = id;
    y += D.ROW_H;
  });

  // Normalise x coordinates into positive pixels and compute SVG bounds.
  // LABEL_MARGIN reserves horizontal room for edge labels that hang off
  // the cluster columns (160px for full size, 90px for compact mode where
  // labels are abbreviated to ~13 chars).
  const margin = D.LABEL_MARGIN || 100;
  const minX = Math.min(...nodes.map(n => n.x))
             - D.NODE_R - D.CLUSTER_PAD - margin;
  const offsetX = -minX;
  nodes.forEach(n => n.x += offsetX);
  clusters.forEach(c => c.x += offsetX);
  const maxX = Math.max(...nodes.map(n => n.x))
             + D.NODE_R + margin;
  const W = Math.max(D.W_MIN, maxX);
  const H = Math.max(...nodes.map(n => n.y)) + D.NODE_R + 24;

  return {nodes, edges, clusters, startNode, dims: D, W, H};
}

/**
 * Compute the "visited / current / violation" overlay for one arm.
 * Returns sets of node IDs to highlight and a `current` node ID for the
 * pulsing pointer.
 */
function computeOverlay(arm, layout) {
  const visitedLabels = new Set();
  const violLabels = new Set();
  const events = state.armEvents[arm] || [];
  let lastLabel = null;
  let lastWasViol = false;
  for (const e of events) {
    if (!e.step) continue;
    if (e.violation) { violLabels.add(e.label); lastLabel = e.label; lastWasViol = true; }
    else             { visitedLabels.add(e.label); lastLabel = e.label; lastWasViol = false; }
  }
  const edgeKlass = new Map();
  const visitedNodeIds = new Set();
  const violNodeIds = new Set();
  layout.edges.forEach((eg, i) => {
    if (!eg.msgLabel) return;
    if (visitedLabels.has(eg.msgLabel)) { edgeKlass.set(i, "visited"); visitedNodeIds.add(eg.to); }
    else if (violLabels.has(eg.msgLabel)) { edgeKlass.set(i, "viol"); violNodeIds.add(eg.to); }
  });
  // The current pointer goes on the most recent label's target node.
  let currentId = null;
  if (lastLabel) {
    for (let i = layout.edges.length - 1; i >= 0; i--) {
      if (layout.edges[i].msgLabel === lastLabel) { currentId = layout.edges[i].to; break; }
    }
  }
  return {visited: visitedNodeIds, viol: violNodeIds,
          edgeKlass, current: currentId, currentIsViol: lastWasViol};
}

/**
 * Paint a layout into an SVG. Overlay `{visited, viol, edgeKlass, current}`
 * decides per-node + per-edge classes. The expected pass passes empty
 * sets for visited/viol and current=null.
 */
function drawMachine(svg, layout, overlay) {
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute("viewBox", `0 0 ${layout.W} ${layout.H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMin meet");

  for (const c of layout.clusters) {
    appendSvgRect(svg, c.x, c.y, c.w, c.h, "sm-cluster");
    appendSvgText(svg, c.label, c.x + c.w / 2, c.y - 4,
                  "sm-cluster-label", {anchor: "middle"});
  }
  appendSvgText(svg, "start →",
                layout.startNode.x - layout.dims.NODE_R - 3,
                layout.startNode.y,
                "sm-start-label");

  const nodeById = Object.fromEntries(layout.nodes.map(n => [n.id, n]));
  const edgeKlass = overlay.edgeKlass || new Map();
  const visitedIds = overlay.visited || new Set();
  const violIds    = overlay.viol    || new Set();
  layout.edges.forEach((e, i) => {
    const a = nodeById[e.from], b = nodeById[e.to];
    if (!a || !b) return;
    const klass = edgeKlass.get(i) || "";
    drawEdge(svg, a, b, e.label, klass, layout.dims.NODE_R);
  });

  for (const n of layout.nodes) {
    let klass = n.klass || "node";
    if (visitedIds.has(n.id) && klass !== "start" && klass !== "accept")
      klass = "visited";
    if (violIds.has(n.id)) klass = "viol";
    if (overlay.current === n.id) klass += " current";
    appendSvgCircle(svg, n.x, n.y, layout.dims.NODE_R, `sm-node ${klass}`);
    appendSvgText(svg, n.label, n.x, n.y, "sm-node-label", {anchor: "middle"});
    if (n.choiceLabel) {
      appendSvgText(svg, n.choiceLabel, n.x, n.y + layout.dims.NODE_R + 10,
                    "sm-choice-label", {anchor: "middle"});
    }
  }
}

function edgeLabel(m, abbrevMax) {
  // Compact mode (used by proto cards) shortens long message names like
  // `HighRevenue` → `HighRev` and `AuditedRevenue` → `Audited` to match
  // the reference diagram. Full mode just truncates aggressively long
  // labels (the SVG <title> tooltip still has the full text on hover).
  const name = abbrevMax ? compactMsgName(m.label, abbrevMax) : m.label;
  return `${abbr(m.from)}→${abbr(m.to)} !${name}`;
}

function drawEdge(svg, a, b, label, klass, R) {
  const ns = "http://www.w3.org/2000/svg";
  // Shorten endpoints so they end on the node circumference, not centre.
  const dx = b.x - a.x, dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist, uy = dy / dist;
  const x1 = a.x + ux * R, y1 = a.y + uy * R;
  const x2 = b.x - ux * R, y2 = b.y - uy * R;

  const line = document.createElementNS(ns, "line");
  line.setAttribute("x1", x1); line.setAttribute("y1", y1);
  line.setAttribute("x2", x2); line.setAttribute("y2", y2);
  line.setAttribute("class", `sm-edge ${klass}`);
  svg.appendChild(line);

  // Triangle arrowhead at (x2, y2) pointing in direction (ux, uy)
  const hx = 8, hw = 5;
  const px = -uy, py = ux;
  const head = document.createElementNS(ns, "polygon");
  head.setAttribute("points",
    `${x2},${y2} ${x2 - ux * hx + px * hw},${y2 - uy * hx + py * hw} ${x2 - ux * hx - px * hw},${y2 - uy * hx - py * hw}`);
  head.setAttribute("class", `sm-edge-head ${klass}`);
  svg.appendChild(head);

  if (label) {
    // Place label perpendicular to the edge with a comfortable offset
    // (was 6px → labels crossed the node they were labeling FROM on
    // long vertical edges). Now 10px + adaptive text-anchor: the side
    // the perpendicular points to determines whether text grows left
    // (anchor=end) or right (anchor=start). This keeps labels clear of
    // the column of nodes.
    const off = 10;
    const mx = (x1 + x2) / 2 + px * off;
    const my = (y1 + y2) / 2 + py * off;
    let anchor;
    if (Math.abs(ux) < 0.3) {
      // (near-)vertical edge: text grows away from the column
      anchor = px < 0 ? "end" : "start";
    } else {
      anchor = "middle";
    }
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", mx); t.setAttribute("y", my);
    t.setAttribute("class", `sm-edge-label ${klass}`);
    t.setAttribute("text-anchor", anchor);
    t.setAttribute("dominant-baseline", "middle");
    // Truncate long labels so they don't bleed past the cluster.
    // Full label is preserved in the SVG <title> for hover.
    t.textContent = truncLabel(label);
    if (label.length > 22) {
      const title = document.createElementNS(ns, "title");
      title.textContent = label;
      t.appendChild(title);
    }
    svg.appendChild(t);
  }
}

function appendSvgCircle(svg, cx, cy, r, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, "circle");
  el.setAttribute("cx", cx); el.setAttribute("cy", cy);
  el.setAttribute("r", r); el.setAttribute("class", cls);
  svg.appendChild(el);
}
function appendSvgRect(svg, x, y, w, h, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, "rect");
  el.setAttribute("x", x); el.setAttribute("y", y);
  el.setAttribute("width", w); el.setAttribute("height", h);
  el.setAttribute("class", cls);
  svg.appendChild(el);
}
function appendSvgText(svg, text, x, y, cls, opts = {}) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, "text");
  el.setAttribute("x", x); el.setAttribute("y", y);
  el.setAttribute("class", cls);
  if (opts.anchor) el.setAttribute("text-anchor", opts.anchor);
  el.textContent = text;
  svg.appendChild(el);
}

// ───────────────────────────────────────────────────────────────── skills inline view
//
// Per-role inline browser: for each role in the case + the orchestrator
// (if applicable), show role description, the goals the role anchors, and
// a collapsible System Prompt block fetched from
// /api/runs/<jobId>/prompts/<arm>/<Role>.md. Updates each time the user
// switches to this view tab so the modal is no longer needed for skills.

async function renderSkills(arm) {
  const p = state.panels[arm];
  if (!p) return;
  const root = p.root.querySelector(".view-skills .skills-list");
  if (!root) return;

  const c = state.case;
  if (!c || !c.roles) {
    root.innerHTML = `<div class="muted small">no case loaded</div>`;
    return;
  }

  // Find which roles have a persisted prompt (only after a run started).
  const haveRun = !!state.runJobId;
  let promptRoles = [];
  if (haveRun) {
    try {
      const r = await fetch(`/api/runs/${state.runJobId}/prompts/${arm}/index.json`);
      if (r.ok) promptRoles = (await r.json()).roles?.map(r => r.role) || [];
    } catch (e) { /* ignore */ }
  }
  const allRoles = c.roles.slice();
  if (promptRoles.includes("__orchestrator__")) allRoles.push("__orchestrator__");

  // Render cards (without prompt text yet — fetched lazily on expand)
  root.innerHTML = allRoles.map(role => {
    const isOrch = role === "__orchestrator__";
    const desc = isOrch
      ? "MAF GroupChat speaker-selection LLM — picks who speaks each turn (not a participant)."
      : (c.role_descriptions?.[role] || "(no description on file)");
    const goals = isOrch ? [] : (c.goals || []).filter(g =>
      g.anchor?.sender === role || g.anchor?.receiver === role);
    const allGoals = isOrch ? [] : (c.goals || []);
    const goalsHtml = isOrch ? "<em>n/a — orchestrator doesn't anchor case goals</em>" : (
      allGoals.length === 0
        ? "<em>(no goals defined)</em>"
        : `<div class="skill-goals">${allGoals.map(g => {
            const anchored = goals.some(x => x.id === g.id);
            return `<div class="skill-goal ${anchored ? "anchored" : ""}">
              <span class="gid">${escHtml(g.id)}</span>
              <div>
                <div>${escHtml(g.description || "")}</div>
                <div class="gmeta">${escHtml(
                  g.anchor
                    ? `anchor: ${g.anchor.sender}→${g.anchor.receiver}:${g.anchor.label}`
                    : "anchor: —"
                )} ${g.predicate ? "· pred: " + escHtml(g.predicate) : ""}</div>
              </div>
            </div>`;
          }).join("")}</div>`
    );
    const promptAvail = haveRun && (
      isOrch ? promptRoles.includes("__orchestrator__") : promptRoles.includes(role)
    );
    const promptHint = haveRun
      ? (promptAvail ? "system prompt — click to expand"
                     : "(no system.md for this role — yet to be persisted)")
      : "system prompt available after Run starts";
    return `
      <div class="skill-card" data-role="${escHtml(role)}">
        <header>
          <span class="skill-role">${escHtml(role)}</span>
          <span class="skill-desc">${escHtml(desc)}</span>
        </header>
        <div class="skill-section">
          <h6>Goals this role anchors</h6>
          ${goalsHtml}
        </div>
        <details${promptAvail ? "" : " disabled"}>
          <summary>${escHtml(promptHint)}</summary>
          <div class="skill-prompt" data-role="${escHtml(role)}">${promptAvail ? "(loading…)" : ""}</div>
        </details>
      </div>`;
  }).join("");

  // Wire details>summary expansion to lazily fetch the prompt body.
  root.querySelectorAll("details").forEach(det => {
    det.addEventListener("toggle", async () => {
      if (!det.open) return;
      const body = det.querySelector(".skill-prompt");
      if (!body || body.dataset.loaded === "1") return;
      const role = body.dataset.role;
      const fname = role.replace(/\.system\.md$/, "");
      const url = `/api/runs/${state.runJobId}/prompts/${arm}/${fname}.md`;
      try {
        const r = await fetch(url);
        body.textContent = r.ok ? await r.text() : `(no system.md at ${url})`;
      } catch (e) {
        body.textContent = `(error fetching: ${e.message})`;
      }
      body.dataset.loaded = "1";
    });
  });
}

// ───────────────────────────────────────────────────────────────── stage 2 proto-card visualisations

// ─── reasoning: per-goal coverage analysis ───────────────────────────────
//
// Goal coverage answers "does this protocol achieve the same task as the
// canonical?" by walking every goal in case.yaml and looking for a message
// in the parsed protocol that fulfils its anchor. Match grades, strongest
// first:
//   strict   — same (sender, receiver, label) as the canonical anchor
//   rolepair — same (sender, receiver), label differs — semantically same
//              flow with a different name
//   renamed  — same label but different sender/receiver — likely the same
//              data moving along a refactored path
//   missing  — nothing matched, the goal can't be evaluated against this
//              protocol's trace
function _flatMessages(parsed) {
  const out = [];
  parsed.preMessages.forEach(m => out.push({...m, branch: null}));
  parsed.branches.forEach(b => b.messages.forEach(m =>
    out.push({...m, branch: b.name})));
  parsed.postMessages.forEach(m => out.push({...m, branch: null}));
  return out;
}

function analyzeGoals(caseData, parsedProto) {
  if (!caseData || !parsedProto) return [];
  const messages = _flatMessages(parsedProto);
  return (caseData.goals || []).map(g => {
    const a = g.anchor || {};
    // Strict: exact triple match
    let found = messages.find(m =>
      m.from === a.sender && m.to === a.receiver && m.label === a.label);
    if (found) return {goal: g, match: "strict", found,
      why: `Exact anchor present in this protocol: <code>${a.sender}→${a.receiver}:${a.label}</code>.`};
    // Role-pair: same flow, different name (the LLM picked its own label)
    found = messages.find(m => m.from === a.sender && m.to === a.receiver);
    if (found) return {goal: g, match: "rolepair", found,
      why: `Same role-pair, different label. Canonical: <code class="anchor-canon">${a.sender}→${a.receiver}:${a.label}</code>. Here: <code class="anchor-found">${found.from}→${found.to}:${found.label}(${found.type || ""})</code>.`};
    // Renamed: same label, different roles (rare but possible)
    found = messages.find(m => m.label === a.label);
    if (found) return {goal: g, match: "renamed", found,
      why: `Label exists but with a different role pair. Canonical: <code class="anchor-canon">${a.sender}→${a.receiver}:${a.label}</code>. Here: <code class="anchor-found">${found.from}→${found.to}:${found.label}</code>.`};
    // Heuristic: label words overlap
    found = _fuzzyLabelMatch(a.label, messages);
    if (found) return {goal: g, match: "renamed", found,
      why: `Approximate match by label keyword. Canonical: <code class="anchor-canon">${a.sender}→${a.receiver}:${a.label}</code>. Closest here: <code class="anchor-found">${found.from}→${found.to}:${found.label}</code>.`};
    return {goal: g, match: "missing", found: null,
      why: `No message in this protocol matches canonical anchor <code class="anchor-canon">${a.sender}→${a.receiver}:${a.label}</code>. The goal predicate <code>${g.predicate || ""}</code> can't be evaluated against this protocol's trace.`};
  });
}

// ─── reasoning: dynamic protocol comparison ──────────────────────────────
//
// Compares any two parsed protocols (typically canonical vs an LLM-drafted
// variant) and returns a table-of-rows the UI can render. The heuristics
// are case-agnostic — they look at structural signals only:
//
//   • Does the protocol begin with pre-choice data messages or jump
//     straight into a `choice at ROLE`?  → "pipeline" vs "pure interaction"
//   • How many total messages?            → "verbose" vs "minimal"
//   • Who's the chooser?                  → identifies decision-holder
//   • How many branches + post-choice msgs? → flow shape
//
// Returns { differ: bool, rows: [[dim, leftValue, rightValue, hint?], ...],
//           leftLabel, rightLabel }
function compareProtocols(left, right, leftLabel = "Canonical", rightLabel = "Other") {
  if (!left || !right) return null;
  const lm = _flatMessages(left), rm = _flatMessages(right);
  const lTotal = lm.length, rTotal = rm.length;
  const lPre = left.preMessages.length, rPre = right.preMessages.length;
  const lStartsChoice = lPre === 0 && !!left.chooser;
  const rStartsChoice = rPre === 0 && !!right.chooser;

  const abstraction = (startsChoice, preCount) => startsChoice
    ? "Pure interaction protocol (omits internal steps)"
    : `Pipeline protocol (steps modeled as messages${preCount ? `; ${preCount} pre-choice` : ""})`;
  const firstEvent = (proto, startsChoice) => {
    if (startsChoice) {
      const firstBranchMsg = proto.branches[0]?.messages?.[0];
      const tag = firstBranchMsg
        ? `branch label <code>${firstBranchMsg.label}</code> carries the data`
        : "(no branch messages)";
      return `Categorization decision itself — <code>choice at ${proto.chooser}</code>, ${tag}`;
    }
    const first = proto.preMessages[0];
    if (!first) return "(no first event)";
    return `An explicit <code>${first.label}(${first.type || ""})</code> from <code>${first.from}</code> to <code>${first.to}</code>`;
  };
  const conveys = (count, otherCount) => {
    if (count <= otherCount * 0.75) return `"Roles communicate this way" — minimal, only what crosses role boundaries`;
    if (count >= otherCount * 1.25) return `"Here's the workflow step by step" — explicit at each pipeline stage`;
    return `Roles communicate this way (mid-verbosity)`;
  };
  const analogue = (startsChoice) => startsChoice
    ? "A formal contract — only the communication observable to others"
    : "An activity diagram — every step is a message";

  const branchSummary = proto => {
    if (!proto.chooser) return "no branching";
    return `${proto.branches.length} branch${proto.branches.length === 1 ? "" : "es"} from <code>choice at ${proto.chooser}</code>`;
  };
  const postSummary = proto => `${proto.postMessages.length} post-choice message${proto.postMessages.length === 1 ? "" : "s"}`;

  // Decide whether they really differ. Same structure + similar size → no
  // comparison table needed (would be misleading).
  const sameStructure = lStartsChoice === rStartsChoice
    && left.chooser === right.chooser
    && left.branches.length === right.branches.length
    && Math.abs(lTotal - rTotal) <= 2;
  if (sameStructure) return {differ: false, rows: [], leftLabel, rightLabel};

  // Build the dimensions row-by-row. Each row [dim, leftVal, rightVal,
  // optional hint that the UI may bold or render as a footnote].
  const rows = [
    ["Abstraction",         abstraction(lStartsChoice, lPre), abstraction(rStartsChoice, rPre)],
    ["First protocol event", firstEvent(left,  lStartsChoice), firstEvent(right, rStartsChoice)],
    ["Message count",       `${lTotal} total (${lPre} pre + ${left.branches.reduce((s,b)=>s+b.messages.length,0)} in branches + ${left.postMessages.length} post)`,
                            `${rTotal} total (${rPre} pre + ${right.branches.reduce((s,b)=>s+b.messages.length,0)} in branches + ${right.postMessages.length} post)`],
    ["Choice / branch shape", branchSummary(left) + ", " + postSummary(left),
                              branchSummary(right) + ", " + postSummary(right)],
    ["What it conveys",     conveys(lTotal, rTotal), conveys(rTotal, lTotal)],
    ["Real-world analogue", analogue(lStartsChoice), analogue(rStartsChoice)],
  ];
  return {differ: true, rows, leftLabel, rightLabel};
}

function _fuzzyLabelMatch(canonLabel, messages) {
  if (!canonLabel) return null;
  // Tokenise CamelCase, drop short / generic words.
  const generic = new Set(["the","a","an","of","to","with","high","standard","branch"]);
  const tokens = canonLabel.replace(/([A-Z])/g, " $1").trim()
                           .toLowerCase().split(/\s+/)
                           .filter(t => t.length > 3 && !generic.has(t));
  if (!tokens.length) return null;
  let best = null, bestScore = 0;
  for (const m of messages) {
    const lbl = m.label.toLowerCase();
    const score = tokens.filter(t => lbl.includes(t)).length;
    if (score > bestScore) { bestScore = score; best = m; }
  }
  return bestScore > 0 ? best : null;
}

// ─── reasoning: parse a Scribble safety-violation verdict ────────────────
//
// Pulls out the structured pieces of a "Safety violation(s) at session
// state N: ... Trace=[...] ... Wait-for cycles: [[A, B, C], ...]" verdict
// so the reasoning panel can render them in a readable form.
//
// Returns: {state, trace: [{sender, receiver, label, payload, dir}], cycles}
// — or null fields if a piece couldn't be parsed.
function parseScribbleViolation(text) {
  if (!text) return {state: null, trace: [], cycles: []};

  const stateMatch = text.match(/session\s+state\s+(\d+)/i);
  const state = stateMatch ? Number(stateMatch[1]) : null;

  let trace = [];
  const traceMatch = text.match(/Trace\s*=\s*\[([^\]]*)\]/);
  if (traceMatch) {
    const parts = traceMatch[1].split(",").map(s => s.trim()).filter(Boolean);
    // Format examples:
    //   Fetcher!RevenueAnalyst:FetchRevenueData(Double)
    //   RevenueAnalyst?Fetcher:FetchRevenueData(Double)
    // `!` = sender side ("Fetcher sends..."), `?` = receiver side
    for (const p of parts) {
      const m = p.match(/^(\w+)([!?])(\w+):(\w+)\(([^)]*)\)$/);
      if (m) {
        trace.push({
          actor: m[1],
          dir:   m[2] === "!" ? "send" : "recv",
          peer:  m[3],
          label: m[4],
          payload: m[5],
        });
      }
    }
  }

  let cycles = [];
  const cyclesMatch = text.match(/Wait-for cycles?\s*:\s*\[(.+)\]/);
  if (cyclesMatch) {
    // Each cycle is itself a [Role, Role, Role] list — parse all of them.
    const cycleRe = /\[([^\]]+)\]/g;
    let m;
    while ((m = cycleRe.exec(cyclesMatch[1]))) {
      cycles.push(m[1].split(",").map(s => s.trim()).filter(Boolean));
    }
  }

  return {state, trace, cycles};
}

// ─── reasoning: Scribble error explainer ─────────────────────────────────
//
// Pattern-matches Scribble's compile-time verdict on the unsafe protocol
// and turns it into plain-language reasoning. Patterns cover the common
// well-formedness rules: external choice, deadlock cycles, role enabling.
function explainScribbleError(text) {
  if (!text) return null;
  const lower = text.toLowerCase();

  if (lower.includes("inconsistent external choice")) {
    const m = text.match(/Inconsistent external choice subjects for (\w+):\s*\[([^\]]+)\]/);
    const role = m ? m[1] : "(some role)";
    const senders = m ? m[2] : "";
    return {
      type: "inconsistent_external_choice",
      heading: `Inconsistent external choice subjects for ${role}`,
      summary: `${role} can't tell which branch it's in.`,
      why: `Inside the <code>choice at …</code> block, ${role} would receive its <strong>first</strong> message from a <strong>different sender</strong> depending on the branch (${senders ? `<code>${senders}</code>` : "see raw verdict"}). Scribble forbids this because ${role}'s local protocol would be non-deterministic — at runtime it has no way to identify which branch was taken.`,
      fix: `The chooser must send a notification message to ${role} at the <strong>top of every branch</strong>, with the chooser as the sender in both. Then ${role}'s first message comes from a consistent source.`,
    };
  }
  if (lower.includes("wait-for") || lower.includes("safety violation")) {
    return {
      type: "wait_for_cycle",
      heading: "Wait-for cycle (deadlock)",
      summary: "The protocol structurally deadlocks on at least one branch.",
      why: `Scribble found a cycle: role A waits for B's message, B waits for C, …, and somewhere the cycle closes. Each role's local view is consistent in isolation, but the <strong>global</strong> flow can't make progress — the agents would block forever.`,
      fix: `Inside the choice block, every role that the protocol expects to act after the choice must receive at least one message in each branch (typically a notification from the chooser at the top of the branch). Otherwise, in the branches where a role is silent, downstream messages requiring its participation can never fire.`,
    };
  }
  if (lower.includes("source role not enabled") || lower.includes("not enabled")) {
    return {
      type: "source_not_enabled",
      heading: "Source role not enabled",
      summary: "A role tries to send before it has been told the branch was taken.",
      why: `Inside the choice block, a role attempts to <strong>send</strong> a message before any message has informed it which branch was taken. Until the chooser notifies this role, it has no basis to act.`,
      fix: `Have the chooser notify this role at the top of every branch in which it must act.`,
    };
  }
  if (lower.includes("not bound") || lower.includes("unused role")) {
    return {
      type: "role_decl_mismatch",
      heading: "Role declaration mismatch",
      summary: "A role used in a message isn't declared in the header (or vice versa).",
      why: `Every role used in a <code>from … to …</code> clause must also appear in the <code>global protocol Name(role A, role B, …)</code> header — and every declared role must participate at least once.`,
      fix: `Re-check the protocol header against the role names used in messages.`,
    };
  }
  return {
    type: "unknown",
    heading: "Scribble rejected this protocol",
    summary: "Scribble's well-formedness check failed for an unrecognised reason.",
    why: `The raw verdict is shown below; the live demo's pattern matcher doesn't recognise this class.`,
    fix: null,
  };
}

/**
 * Render the structured violation detail block: trace, wait-for cycles,
 * and a step-by-step description of who is blocked on whom.
 */
function renderViolationDetail({state, trace, cycles}) {
  if (!trace.length && !cycles.length && state == null) return "";

  // Group consecutive send/receive of the SAME message into one row so the
  // trace reads as a chronological list of message events rather than two
  // entries per message.
  const events = [];
  for (let i = 0; i < trace.length; i++) {
    const t = trace[i];
    if (t.dir === "send") {
      events.push({
        sender: t.actor, receiver: t.peer,
        label: t.label, payload: t.payload, idx: events.length + 1,
      });
    }
    // We deliberately skip the matching `?` events — they describe the
    // receiver's side of the same global event we already recorded.
  }

  const traceHtml = events.length === 0 ? "" : `
    <div class="trace-block">
      <h6 class="trace-h">Trace Scribble simulated up to the deadlock${state != null ? ` (got stuck at session state <code>${state}</code>)` : ""}</h6>
      <div class="trace-rows">
        ${events.map(e => `
          <div class="trace-row">
            <span class="t-i">#${e.idx}</span>
            <span class="t-sender">${escHtml(e.sender)}</span>
            <span class="t-arrow">→</span>
            <span class="t-receiver">${escHtml(e.receiver)}</span>
            <span class="t-label">${escHtml(e.label)}</span>
            <span class="t-payload">${e.payload ? `(${escHtml(e.payload)})` : ""}</span>
          </div>`).join("")}
      </div>
      <div class="muted small" style="margin-top:6px">
        After step ${events.length}, the global type expected a next message — but
        the roles in the wait-for cycle below were all blocked waiting on each other.
      </div>
    </div>`;

  const cyclesHtml = cycles.length === 0 ? "" : `
    <div class="cycles-block">
      <h6 class="trace-h">Wait-for cycle${cycles.length > 1 ? "s" : ""} Scribble detected</h6>
      ${cycles.map((c, ci) => {
        const ringDisplay = c.concat(c[0]).map((r,i) =>
          i === 0
            ? `<span class="role-pill">${escHtml(r)}</span>`
            : `<span class="role-arrow">waits for →</span><span class="role-pill">${escHtml(r)}</span>`
        ).join(" ");
        return `
          <div class="cycle">
            <div class="cycle-h">Cycle ${ci + 1} <span class="muted small">(${c.length} roles)</span></div>
            <div class="cycle-ring">${ringDisplay}</div>
            <div class="cycle-explain">
              ${c.map((role, i) => {
                const next = c[(i + 1) % c.length];
                return `<div class="cycle-step"><code>${escHtml(role)}</code> is waiting for a message from <code>${escHtml(next)}</code>${i === c.length - 1 ? " — which is in turn waiting on the start of the cycle ⤴" : ""}.</div>`;
              }).join("")}
            </div>
          </div>`;
      }).join("")}
    </div>`;

  return traceHtml + cyclesHtml;
}

// ─── reasoning: render the panel ─────────────────────────────────────────
function renderReasoning(kind) {
  const card = document.querySelector(`.proto-card[data-proto="${kind}"]`);
  if (!card) return;
  const body = card.querySelector(".reasoning-body");
  if (!body) return;

  const parsed = state.parsedProtocols[kind];
  const errorText = state.draftErrors[kind] || "";

  // UNSAFE always speaks through the Scribble error first — even if a
  // partial parse succeeded, the protocol is rejected so it's not meaningful
  // to compare it goal-by-goal.
  if (kind === "unsafe") {
    if (!errorText && !parsed) {
      body.innerHTML = `<div class="muted small">no draft yet — run <strong>Draft &amp; Verify</strong>.</div>`;
      return;
    }
    if (!errorText) {
      body.innerHTML = `<div class="reasoning-verdict ok">
        <span class="reasoning-verdict-icon">✓</span>
        <span>Every draft passed Scribble — no unsafe sample to explain.</span>
      </div>`;
      return;
    }
    const exp = explainScribbleError(errorText);
    const parsed = parseScribbleViolation(errorText);
    body.innerHTML = `
      <div class="reasoning-verdict err">
        <span class="reasoning-verdict-icon">✗</span>
        <span>${escHtml(exp.summary)}</span>
      </div>
      <div class="scribble-error-card">
        <h5>${escHtml(exp.heading)}</h5>
        <div class="err-explain"><strong>Why this happens:</strong> ${exp.why}</div>
        ${exp.fix ? `<div class="err-explain"><strong>How to fix it:</strong> ${exp.fix}</div>` : ""}
        ${renderViolationDetail(parsed)}
        <details><summary>raw Scribble verdict</summary><div class="err-cause">${escHtml(errorText)}</div></details>
      </div>
      <div class="muted small" style="margin-top:8px">
        <strong>Bottom line:</strong> agents driving this protocol would never produce a trace
        that reaches the goal anchors — the runtime arm <code>maf_groupchat_unsafe</code>
        runs it without a monitor, so you see what happens behaviourally
        (they get partway through the protocol and then hang).
      </div>
      ${parsed ? renderComparisonBlock("unsafe") : ""}`;
    return;
  }

  if (!parsed) {
    body.innerHTML = `<div class="muted small">protocol unavailable.</div>`;
    return;
  }

  // VALID + CANONICAL: per-goal coverage table
  const goals = analyzeGoals(state.case, parsed);
  const strict = goals.filter(g => g.match === "strict").length;
  const rolepair = goals.filter(g => g.match === "rolepair").length;
  const renamed = goals.filter(g => g.match === "renamed").length;
  const missing = goals.filter(g => g.match === "missing").length;
  const total = goals.length;
  const satisfied = total - missing;

  let verdictClass, verdictIcon, verdictText;
  if (kind === "canonical") {
    verdictClass = "ok";
    verdictIcon = "✓";
    verdictText = `Reference design — all ${total} goals were authored against THIS protocol's labels. Every other arm's protocol is judged equivalent (or not) against these anchors.`;
  } else if (missing === 0) {
    verdictClass = satisfied === strict ? "ok" : "ok";
    verdictIcon = "✓";
    verdictText = `Satisfies all ${total} goals — ${strict} via the exact canonical anchor, ${rolepair} via the same role-pair with a different label, ${renamed} via a renamed/refactored equivalent.`;
  } else {
    verdictClass = missing >= total / 2 ? "err" : "warn";
    verdictIcon = missing >= total / 2 ? "✗" : "⚠";
    verdictText = `Satisfies ${satisfied}/${total} goals — ${missing} canonical anchor${missing === 1 ? "" : "s"} have no equivalent message in this protocol.`;
  }

  const matchLabel = {strict: "exact", rolepair: "role-pair", renamed: "renamed", missing: "missing"};
  const rows = goals.map(({goal, match, found, why}) => `
    <div class="goal-row match-${match}">
      <span class="g-id">${escHtml(goal.id)}</span>
      <span class="g-status">${matchLabel[match]}</span>
      <span class="g-why">${why}</span>
    </div>`).join("");

  body.innerHTML = `
    <div class="reasoning-verdict ${verdictClass}">
      <span class="reasoning-verdict-icon">${verdictIcon}</span>
      <span>${escHtml(verdictText)}</span>
    </div>
    <div class="goal-coverage">${rows}</div>
    ${kind === "valid" ? `
    <div class="muted small" style="margin-top:8px; line-height:1.5">
      <strong>How this is measured at runtime:</strong> the benchmark's
      <em>Set B (role-pair)</em> metric only checks that the right pair of
      roles communicated a value satisfying the goal predicate — it doesn't
      require the same label. So a goal scored <em>role-pair</em> here is
      still considered satisfied by the runtime arm.
    </div>` : ""}
    ${kind !== "canonical" ? renderComparisonBlock(kind) : ""}`;
}

/** Render the dynamic comparison-with-canonical table for non-canonical
 *  cards. Returns empty string when both protocols are structurally
 *  similar (the comparator returns differ=false). */
function renderComparisonBlock(kind) {
  const canon = state.parsedProtocols.canonical;
  const other = state.parsedProtocols[kind];
  if (!canon || !other) return "";
  const cmp = compareProtocols(canon, other, "Canonical", `LLM-drafted ${kind}`);
  if (!cmp || !cmp.differ) {
    return `
      <div class="muted small" style="margin-top:10px">
        Structurally similar to the canonical (same choice location, same
        branch shape, comparable message count) — no design-decision table
        to show.
      </div>`;
  }
  return `
    <div class="comparison-block">
      <h6>Compared to the canonical — where the two designs differ</h6>
      <table class="cmp-table">
        <thead><tr>
          <th></th>
          <th class="cmp-left">${escHtml(cmp.leftLabel)}</th>
          <th class="cmp-right">${escHtml(cmp.rightLabel)}</th>
        </tr></thead>
        <tbody>
          ${cmp.rows.map(([dim, l, r]) => `
            <tr>
              <td class="cmp-dim">${escHtml(dim)}</td>
              <td class="cmp-cell cmp-left">${l}</td>
              <td class="cmp-cell cmp-right">${r}</td>
            </tr>`).join("")}
        </tbody>
      </table>
      <div class="muted small" style="margin-top:6px; line-height:1.5">
        Both designs are <strong>formally valid session types</strong>; they
        sit at different abstraction levels. The canonical follows the
        session-types convention that only inter-role communication appears
        in the type (internal computation like "Fetcher computed the
        revenue value" stays hidden). The LLM-drafted version tends toward
        the workflow / activity-diagram style where every step becomes a
        message. Both can satisfy the same goal predicates — see the
        per-goal table above.
      </div>
    </div>`;
}

function renderProtoCards() {
  for (const kind of ["valid", "unsafe", "canonical"]) {
    const card = document.querySelector(`.proto-card[data-proto="${kind}"]`);
    if (!card) continue;
    const parsed = state.parsedProtocols[kind];
    const machineSvg = card.querySelector(".proto-machine-svg");
    const seqSvg     = card.querySelector(".proto-seq-svg");
    // Isolate failures: one card's exception used to abort the for...of
    // loop, which is why a bad VALID render meant UNSAFE and CANONICAL
    // never drew either. Now each card renders independently.
    try { renderMachineSingle(machineSvg, parsed); }
    catch (e) {
      console.error(`[proto-machine ${kind}]`, e);
      machineSvg && (machineSvg.innerHTML =
        `<text x="10" y="20" class="sm-empty">[render error: ${e.message}]</text>`);
    }
    try { renderProtocolSequence(seqSvg, parsed); }
    catch (e) {
      console.error(`[proto-seq ${kind}]`, e);
      seqSvg && (seqSvg.innerHTML =
        `<text x="10" y="20" class="sm-empty">[render error: ${e.message}]</text>`);
    }
    try { renderReasoning(kind); }
    catch (e) {
      console.error(`[proto-reasoning ${kind}]`, e);
      const body = card.querySelector(".reasoning-body");
      if (body) body.innerHTML = `<div class="muted small">[reasoning error: ${escHtml(e.message)}]</div>`;
    }
  }
}

/**
 * Render a static sequence diagram for a protocol (one swim-lane per role,
 * arrows for every message including branch alternatives). Used by the
 * proto-card "sequence" view in stage 2.
 */
function renderProtocolSequence(svg, parsed) {
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!parsed) {
    appendSvgText(svg, "(protocol unavailable)", 12, 24, "sm-empty");
    svg.setAttribute("viewBox", "0 0 400 60");
    return;
  }
  const roles = parsed.roles || [];
  if (!roles.length) {
    appendSvgText(svg, "(no roles)", 12, 24, "sm-empty");
    svg.setAttribute("viewBox", "0 0 400 60");
    return;
  }
  const W = 520;
  const laneGap = W / (roles.length + 1);
  const headerY = 14;
  const rowH = 20;
  const padTop = 24;
  const ns = "http://www.w3.org/2000/svg";

  // Flatten messages: pre-choice, then each branch labelled, then post.
  const seq = [];
  parsed.preMessages.forEach(m => seq.push({...m, tag: ""}));
  if (parsed.chooser) {
    parsed.branches.forEach(b => {
      seq.push({_branchHeader: true, tag: `[${b.name.toUpperCase()} branch] (choice@${parsed.chooser})`});
      b.messages.forEach(m => seq.push({...m, tag: b.name}));
    });
  }
  parsed.postMessages.forEach(m => seq.push({...m, tag: ""}));

  const totalH = padTop + seq.length * rowH + 12;
  svg.setAttribute("viewBox", `0 0 ${W} ${Math.max(60, totalH)}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMin meet");

  roles.forEach((r, i) => {
    const x = laneGap * (i + 1);
    appendSvgText(svg, r, x, headerY, "seq-lane-label", {anchor: "middle"});
    const ln = document.createElementNS(ns, "line");
    ln.setAttribute("x1", x); ln.setAttribute("x2", x);
    ln.setAttribute("y1", headerY + 4); ln.setAttribute("y2", totalH - 4);
    ln.setAttribute("class", "seq-lane-line");
    svg.appendChild(ln);
  });

  seq.forEach((m, idx) => {
    const y = padTop + idx * rowH + rowH / 2;
    if (m._branchHeader) {
      appendSvgText(svg, m.tag, 4, y + 3, "sm-cluster-label");
      return;
    }
    const fromI = roles.indexOf(m.from);
    const toI   = roles.indexOf(m.to);
    if (fromI < 0 || toI < 0) return;
    const x1 = laneGap * (fromI + 1);
    const x2 = laneGap * (toI + 1);
    const cls = m.tag ? "branch" : "";

    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", x1); line.setAttribute("y1", y);
    line.setAttribute("x2", x2); line.setAttribute("y2", y);
    line.setAttribute("class", `seq-arrow ${cls}`);
    svg.appendChild(line);

    const dir = x2 > x1 ? 1 : -1;
    const hx = x2 - dir * 6;
    const head = document.createElementNS(ns, "polygon");
    head.setAttribute("points", `${x2},${y} ${hx},${y - 3} ${hx},${y + 3}`);
    head.setAttribute("class", `seq-arrow-head ${cls}`);
    svg.appendChild(head);

    const lbl = m.label + (m.type ? `(${m.type})` : "");
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", (x1 + x2) / 2);
    t.setAttribute("y", y - 3);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("class", "seq-label");
    t.textContent = lbl;
    svg.appendChild(t);
  });
}

function indexProtoCards() {
  // The state machine + sequence diagram are now stacked inline (no tabs)
  // so there's nothing to wire on click — renderProtoCards() is invoked
  // whenever a case is selected or its protocols finish parsing.
  // Kept as a no-op so the boot sequence stays stable.
}

// ───────────────────────────────────────────────────────────────── page tabs

function indexPageTabs() {
  $$(".page-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      $$(".page-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      const p = tab.dataset.page;
      $$(".page").forEach(pg => pg.classList.remove("active"));
      const target = $(`#page-${p}`);
      if (target) target.classList.add("active");
      // Re-render any active SVG view that was hidden when the page
      // wasn't active — SVG layout needs visibility to settle.
      if (p === "run" && state.activeArm) {
        const activeVtab = state.panels[state.activeArm]?.root.querySelector(".vtab.active");
        if (activeVtab?.dataset.view === "diagram") renderDiagram(state.activeArm);
        if (activeVtab?.dataset.view === "machine") renderMachine(state.activeArm);
      }
      if (p === "draft") renderProtoCards();
    });
  });
}

// ───────────────────────────────────────────────────────────────── boot

document.addEventListener("DOMContentLoaded", () => {
  indexPanels();
  indexProtoCards();
  indexPageTabs();
  loadCases();

  $("#case-select").addEventListener("change", e => selectCase(e.target.value));
  $("#btn-draft").addEventListener("click", startDraft);
  $("#btn-run").addEventListener("click", startRun);
  $("#btn-expected").addEventListener("click", openExpectedProtocolModal);
  $("#btn-reset").addEventListener("click", () => location.reload());
  $("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#modal").addEventListener("click", e => {
    if (e.target.id === "modal") $("#modal").classList.add("hidden");
  });
  window.addEventListener("keydown", e => {
    if (e.key === "Escape") $("#modal").classList.add("hidden");
  });
});
