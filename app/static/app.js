/* HardRead front end: vanilla JS, no framework, no innerHTML with data (every text goes through textContent).
   Routes: #/  ·  #/review/<id>  ·  #/job/<id>  ·  #/about
   A static export (app/static_export.py) sets window.HARDREAD_STATIC = {views, reviews, showcases} and the same
   code renders without a server. */
(function () {
  "use strict";

  var STATIC = window.HARDREAD_STATIC || null;
  var SVGNS = "http://www.w3.org/2000/svg";
  var MINUS = "−";

  // ------------------------------------------------------------------ DOM helpers
  function h(tag, attrs) {
    var el = document.createElement(tag);
    setAttrs(el, attrs);
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function s(tag, attrs) {
    var el = document.createElementNS(SVGNS, tag);
    setAttrs(el, attrs);
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function setAttrs(el, attrs) {
    if (!attrs) return;
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") el.textContent = v;
      else if (k === "class") el.setAttribute("class", v);
      else if (k.slice(0, 2) === "on" && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if ((k === "fill" || k === "stroke") && String(v).indexOf("var(") >= 0) el.style.setProperty(k, v);   // theme-aware
      else el.setAttribute(k, v === true ? "" : String(v));
    });
  }
  function append(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { append(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function signed(n) { return n > 0 ? "+" + n : n < 0 ? MINUS + Math.abs(n) : "0"; }
  function pct(x) { return Math.round(100 * x) + "%"; }
  function glyph(n) { return n > 0 ? "▲" : n < 0 ? "▼" : "="; }
  function favour(n) { return n > 0 ? "in your favour" : n < 0 ? "against you" : "neutral"; }
  function endText(r) { return r === "forfeit" ? "ended by forfeit" : r === "timeout" ? "ended on the timer" : r === "ko" ? "ended by KO" : ""; }

  // ------------------------------------------------------------------ API
  function api(path, body) {
    var opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Unexpected answer from the server." }; })
        .then(function (d) { d._status = r.status; return d; });
    }, function () { return { ok: false, error: "The HardRead server is not reachable." }; });
  }
  function getView(id) {
    if (STATIC) return Promise.resolve((STATIC.views || {})[id] || { ok: false, error: "Not in this static page." });
    return api("/api/review/" + encodeURIComponent(id));
  }
  function getShowcases() {
    if (STATIC) return Promise.resolve({ showcases: STATIC.showcases || [] });
    return api("/api/showcases");
  }

  // ------------------------------------------------------------------ theme
  var THEMES = ["system", "light", "dark"];
  function currentTheme() {
    try { return localStorage.getItem("hardread.theme") || "system"; } catch (e) { return "system"; }
  }
  function applyTheme(t) {
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    try { if (t === "system") localStorage.removeItem("hardread.theme"); else localStorage.setItem("hardread.theme", t); } catch (e) { /* ignore */ }
    var b = document.getElementById("theme-toggle");
    if (b) {
      clear(b);
      var icon = t === "dark" ? "M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" : t === "light" ?
        "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-5v3m0 14v3M2 12h3m14 0h3M4.9 4.9l2.1 2.1m10 10 2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" :
        "M12 3a9 9 0 0 0 0 18zM12 3a9 9 0 0 1 0 18";
      b.appendChild(s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" },
        s("path", { d: icon, fill: t === "light" ? "none" : "currentColor", stroke: "currentColor", "stroke-width": t === "light" ? 2 : 1.5, "stroke-linecap": "round" })));
      b.appendChild(h("span", { class: "t-label" }, "Theme: " + t));
      b.setAttribute("aria-label", "Colour theme: " + t + ". Activate to change.");
    }
  }
  function initTheme() {
    applyTheme(currentTheme());
    var b = document.getElementById("theme-toggle");
    b.addEventListener("click", function () {
      var i = THEMES.indexOf(currentTheme());
      applyTheme(THEMES[(i + 1) % THEMES.length]);   // charts use CSS variables, so they follow without a redraw
    });
  }

  // ------------------------------------------------------------------ S0 gate (18+, not affiliated)
  var gateOk = false;
  function gateAccepted() {
    if (gateOk) return true;
    try { return localStorage.getItem("hardread.gate.v1") === "yes"; } catch (e) { return false; }
  }
  function initGate() {
    var d = document.getElementById("gate");
    if (gateAccepted() || !d || typeof d.showModal !== "function") return;
    var box = document.getElementById("gate-age"), ok = document.getElementById("gate-ok");
    box.addEventListener("change", function () { ok.disabled = !box.checked; });
    d.addEventListener("cancel", function (e) { e.preventDefault(); });   // Esc does not skip the gate
    document.getElementById("gate-privacy").addEventListener("click", function () {
      d.close();   // read the privacy notice first; the gate comes back on leaving it (route) or on submit
    });
    document.getElementById("gate-form").addEventListener("submit", function () {
      if (!box.checked) return;
      gateOk = true;
      try { localStorage.setItem("hardread.gate.v1", "yes"); } catch (e) { /* this page view only */ }
    });
  }
  function requireGate() {
    if (gateAccepted()) return true;
    var d = document.getElementById("gate");
    if (d && !d.open && typeof d.showModal === "function") d.showModal();
    return false;
  }

  // ------------------------------------------------------------------ router
  var main = document.getElementById("main");
  var lastRender = null;
  var pollTimer = null;
  function route() {
    var hsh = location.hash.replace(/^#/, "") || "/";
    if (hsh.charAt(0) !== "/") return;   // in-page anchors (e.g. the skip link) are not routes
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    var m;
    var nav = "home";
    var pm = location.pathname.match(/^\/r\/([A-Za-z0-9_-]+)$/);
    if (pm && hsh === "/") { renderReview(pm[1]); nav = ""; }
    else if ((m = hsh.match(/^\/review\/([A-Za-z0-9_-]+)$/))) { renderReview(m[1]); nav = ""; }
    else if ((m = hsh.match(/^\/job\/([A-Za-z0-9_-]+)$/))) { renderJob(m[1]); nav = ""; }
    else if (hsh === "/about") { renderAbout(); nav = "about"; }
    else renderLanding();
    if (hsh !== "/about" && !gateAccepted()) requireGate();
    document.querySelectorAll("[data-nav]").forEach(function (a) {
      if (a.getAttribute("data-nav") === nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function show(nodes, title, focus) {
    clear(main);
    append(main, nodes);
    document.title = title ? title + " · HardRead" : "HardRead · was it luck, or was it you?";
    if (focus !== false) {
      var target = main.querySelector("h1") || main;
      if (target !== main) target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: false });
      window.scrollTo(0, 0);
    }
  }

  // ------------------------------------------------------------------ landing (S1)
  function renderLanding() {
    lastRender = null;
    var formState = { preflight: null };
    var input = h("textarea", { id: "paste", class: "paste", rows: 1, spellcheck: "false", autocomplete: "off",
      placeholder: "https://replay.pokemonshowdown.com/gen9ou-…   or paste the battle log",
      "aria-describedby": "paste-hint" });
    var exportBox = h("textarea", { id: "export", class: "export", rows: 7, spellcheck: "false",
      placeholder: "Great Tusk @ Booster Energy\nAbility: Protosynthesis\nTera Type: …\n- Headlong Rush\n…",
      "aria-describedby": "export-hint" });
    var errBox = h("div", { class: "form-error", role: "alert", hidden: true });
    var step2 = h("div", { id: "step2", hidden: true });
    var go = h("button", { class: "btn btn-primary", type: "submit" }, "Check this game");
    var form = h("form", { class: "card paste-card", novalidate: true, "aria-labelledby": "paste-title" },
      h("h2", { id: "paste-title", class: "section-title", style: "font-size:18px" }, "Review a finished game"),
      h("p", { class: "section-sub", style: "margin-bottom:12px" }, "Gen 9 OU singles. Saved replays only: HardRead never looks at a live battle."),
      h("label", { class: "field-label", for: "paste" }, "Replay link or battle log"),
      input,
      h("p", { id: "paste-hint", class: "hint" }, "Public or private (password) replay links both work. Private replays are kept 24 hours and never get a share page."),
      h("details", { class: "export-box" },
        h("summary", null, "Add your team export (optional, for exact sets)"),
        h("label", { class: "field-label sr-only", for: "export" }, "Your team export"),
        exportBox,
        h("p", { id: "export-hint", class: "hint" }, "Teambuilder → Export. Without it, HardRead drafts your sets from what the replay revealed plus the most common set for the rest.")),
      errBox,
      step2,
      h("div", { class: "form-row" }, go, h("span", { class: "hint", id: "go-hint", style: "margin:0" }, "Takes 30–100 seconds once it starts.")));

    function showError(msg) { errBox.textContent = msg; errBox.hidden = !msg; }
    function resetStep2() { formState.preflight = null; clear(step2); step2.hidden = true; go.textContent = "Check this game"; }
    input.addEventListener("input", resetStep2);
    exportBox.addEventListener("input", resetStep2);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && input.value.indexOf("\n") < 0 && /^\s*(https?:\/\/)?replay\./i.test(input.value)) {
        e.preventDefault(); form.requestSubmit ? form.requestSubmit() : go.click();
      }
    });
    if (STATIC) {
      input.disabled = true; exportBox.disabled = true; go.disabled = true;
      showError("This is a static copy of HardRead: open the app to review your own games. The showcases below work.");
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!requireGate()) return;
      showError("");
      if (!formState.preflight) {
        go.disabled = true; go.textContent = "Checking…";
        api("/api/preflight", { input: input.value, team: exportBox.value }).then(function (d) {
          go.disabled = false;
          if (!d.ok) { go.textContent = "Check this game"; showError(d.error || "Something went wrong."); return; }
          formState.preflight = d;
          buildStep2(d);
          go.textContent = "Review my side";
        });
        return;
      }
      var side = (form.querySelector('input[name="side"]:checked') || {}).value;
      if (!side) { showError("Pick the side you played."); return; }
      go.disabled = true; go.textContent = "Starting…";
      api("/api/review", { input: input.value, team: exportBox.value, side: side }).then(function (d) {
        go.disabled = false; go.textContent = "Review my side";
        if (!d.ok) { showError(d.error || "Something went wrong."); return; }
        location.hash = d.review_id ? "#/review/" + d.review_id : "#/job/" + d.job_id;
      });
    });

    function buildStep2(d) {
      clear(step2);
      var facts = h("div", { class: "game-facts" },
        h("span", { class: "chip" }, d.turns + " turns"),
        d.end_reason ? h("span", { class: "chip" }, endText(d.end_reason)) : null,
        d.private ? h("span", { class: "chip" }, "private replay · kept 24 h") : null,
        d.team_species && d.team_species.length ? h("span", { class: "chip" }, "team export: " + d.team_species.length + " Pokémon") : h("span", { class: "chip" }, "sets drafted from the replay"));
      var opts = h("div", { class: "side-options" });
      ["p1", "p2"].forEach(function (sd) {
        var id = "side-" + sd;
        var won = d.winner === sd ? "won this game" : d.winner === "tie" ? "tie" : "lost this game";
        opts.appendChild(h("div", { class: "side-option" },
          h("input", { type: "radio", name: "side", id: id, value: sd, checked: d.suggested_side === sd }),
          h("label", { for: id }, h("strong", null, (sd === "p1" ? "Player 1 · " : "Player 2 · ") + (d.players[sd] || "")), h("span", null, won))));
      });
      append(step2, [facts, h("fieldset", { class: "sides" },
        h("legend", { id: "side-legend", tabindex: "-1" }, d.suggested_side ? "Your side (matched to your team export; confirm)" : "Which side were you?"),
        opts),
        h("p", { class: "hint" }, "Names are shown only here, to help you pick. The review replaces them with “You” and “Opponent”.")]);
      step2.hidden = false;
      var first = step2.querySelector('input[name="side"]:checked') || step2.querySelector('input[name="side"]');
      if (first) first.focus();
    }

    var how = h("section", { "aria-labelledby": "how-title", style: "margin-top:34px" },
      h("h2", { id: "how-title", class: "section-title" }, "How it works"),
      h("p", { class: "section-sub" }, "Every turn is replayed through Foul Play, the search engine that won the PokéAgent 2025 Gen 9 OU track, with sampled guesses at what the opponent's team could hold."),
      h("div", { class: "how" },
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "1"), h("b", null, "Start"),
          h("p", null, "Your win chance at team preview, before anyone moved.")),
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "2"), h("b", null, "Luck"),
          h("p", null, "Each crit, miss, roll and speed tie, priced against every outcome that could have happened instead.")),
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "3"), h("b", null, "Choices, both players"),
          h("p", null, "Everything that was not dice. Plus the few turns where the engine clearly disagreed with you."))));

    var showcaseSec = h("section", { "aria-labelledby": "sc-title", style: "margin-top:34px" },
      h("h2", { id: "sc-title", class: "section-title" }, "Try a showcase"),
      h("p", { class: "section-sub" }, "Ladder games from public replays across rating bands, each reviewed from one player's side. Names removed; sets drafted from the replay."));
    var scGrid = h("div", { class: "showcases" }, h("p", { class: "muted" }, "Loading showcases…"));
    showcaseSec.appendChild(scGrid);
    getShowcases().then(function (d) {
      clear(scGrid);
      var list = (d && d.showcases) || [];
      if (!list.length) { scGrid.appendChild(h("p", { class: "muted" }, "No showcases in this build.")); return; }
      list.forEach(function (sc) {
        var b = sc.bridge || {};
        scGrid.appendChild(h("a", { class: "showcase", href: "#/review/" + sc.id },
          h("div", { class: "chips", style: "display:flex;gap:6px;flex-wrap:wrap" },
            h("span", { class: "chip " + (sc.result === "win" ? "result-win" : "result-loss") }, sc.result === "win" ? "✓ Win" : "✗ Loss"),
            h("span", { class: "chip" }, sc.turns + " turns")),
          h("h3", null, sc.title || "Ladder game"),
          h("div", { class: "meta" }, "Ladder game " + (sc.band || "") + " · " + endText(sc.end_reason)),
          miniBridge(b),
          h("div", { class: "go" }, "Open the review →")));
      });
    });

    show([h("section", { class: "hero" },
      h("p", { class: "eyebrow" }, "Hidden information · search engine · receipts"),
      h("h1", null, "Was it luck, or was it you?"),
      h("p", { class: "lede" }, "Paste a competitive Pokémon battle. HardRead separates the dice from the decisions, turn by turn, and shows where a champion search engine would have played differently.")),
      form, how, showcaseSec], null, false);
  }

  function miniBridge(b) {
    if (b.s === undefined || b.s === null) return null;
    var rows = [["Start", b.s, "%"], ["Luck", b.l, " pts"], ["Choices", b.c, " pts"]];
    var wrap = h("div", { class: "small", style: "margin-top:10px;display:grid;grid-template-columns:62px 1fr 56px;gap:4px 8px;align-items:center" });
    rows.forEach(function (r) {
      var v = r[1];
      var isStart = r[0] === "Start";
      wrap.appendChild(h("span", { class: "muted" }, r[0]));
      var svg = s("svg", { viewBox: "0 0 100 8", preserveAspectRatio: "none", style: "width:100%;height:8px", "aria-hidden": "true" });
      svg.appendChild(s("line", { x1: 50, x2: 50, y1: 0, y2: 8, stroke: "var(--line-strong)", "stroke-width": 0.6 }));
      var w = Math.min(50, Math.abs(v) / 2);
      var x = v >= 0 ? 50 : 50 - w;
      svg.appendChild(s("rect", { x: x, y: 1, width: Math.max(w, 0.6), height: 6, rx: 1,
        fill: isStart ? "var(--neutral-bar)" : v >= 0 ? "var(--pos)" : "url(#hr-hatch-neg)" }));
      wrap.appendChild(svg);
      wrap.appendChild(h("span", { class: "num", style: "text-align:right;font-weight:600" },
        isStart ? v + "%" : glyph(v) + " " + signed(v)));
    });
    return wrap;
  }

  // ------------------------------------------------------------------ job progress
  var STAGES = [["team", "Preparing your team"], ["reconstruct", "Replaying the log through the engine"],
    ["evaluate", "Searching each of your decisions"], ["luck", "Pricing every chance event"]];
  function renderJob(id) {
    lastRender = null;
    if (STATIC) { show([h("p", null, "Reviews need the HardRead app.")], "Review"); return; }
    var fill = h("div", { class: "progress-fill" });
    var bar = h("div", { class: "progress-track", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0, "aria-label": "Review progress" }, fill);
    var msg = h("p", { class: "muted", "aria-live": "polite", style: "margin:0" }, "Starting…");
    var stages = h("ol", { class: "stages" });
    var stageEls = {};
    STAGES.forEach(function (st) {
      var li = h("li", null, h("span", { class: "dot", "aria-hidden": "true" }), h("span", null, st[1]));
      stageEls[st[0]] = li; stages.appendChild(li);
    });
    var card = h("section", { class: "card progress-card", "aria-labelledby": "job-title" },
      h("p", { class: "eyebrow" }, "Review in progress"),
      h("h1", { id: "job-title", style: "font-size:26px" }, "Reviewing your game"),
      bar, msg, stages,
      h("p", { class: "hint", style: "margin-top:16px" }, "Every decision gets a full search on sampled opponent teams; a typical game takes 30–100 seconds. You can leave this page open."));
    show([card], "Reviewing");
    var started = Date.now();
    function tick() {
      api("/api/job/" + encodeURIComponent(id)).then(function (d) {
        if (location.hash !== "#/job/" + id) return;
        if (!d || d.ok === false) {
          msg.textContent = (d && d.error) || "This review job was not found."; return;
        }
        if (d.state === "done" && d.review_id) { location.replace("#/review/" + d.review_id); return; }
        if (d.state === "error") {
          msg.textContent = d.message || "The engine could not review this game.";
          card.appendChild(h("p", null, h("a", { href: "#/" }, "Try another game")));
          return;
        }
        var p = d.state === "queued" ? 0.01 : Math.max(0.02, d.progress || 0.02);
        fill.style.width = Math.round(100 * p) + "%";
        bar.setAttribute("aria-valuenow", Math.round(100 * p));
        msg.textContent = d.state === "queued" ? "Waiting for a free engine slot: you are #" + (d.position || 1) + " in line." :
          (d.message || "Working…") + " · " + Math.round(d.elapsed || (Date.now() - started) / 1000) + " s";
        var idx = d.state === "queued" ? -1 : STAGES.map(function (x) { return x[0]; }).indexOf(d.stage);
        STAGES.forEach(function (st, i) {
          var li = stageEls[st[0]];
          li.className = i < idx ? "done" : i === idx ? "active" : "";
          li.querySelector(".dot").textContent = i < idx ? "✓" : "";
        });
        pollTimer = setTimeout(tick, 1000);
      });
    }
    tick();
  }

  // ------------------------------------------------------------------ review (S2)
  function renderReview(id) {
    show([h("p", { class: "muted" }, "Loading the review…")], "Review", false);
    getView(id).then(function (v) {
      if (!v || v.ok === false || !v.bridge) {
        show([h("a", { class: "back", href: "#/" }, "← Review another game"),
          h("h1", null, "Review not found"), h("p", null, (v && v.error) || "This review does not exist or has expired.")], "Not found");
        return;
      }
      lastRender = function () { drawReview(id, v, false); };
      drawReview(id, v, true);
    });
  }

  function drawReview(id, v, focus) {
    var b = v.bridge;
    var res = v.result;
    var sharedPage = location.pathname.indexOf("/r/") === 0;
    var title = v.showcase || sharedPage ? (v.showcase ? "Showcase: " : "Shared review: ") + (res === "win" ? "a win" : res === "loss" ? "a loss" : "a tie") + " in " + v.turns + " turns"
      : (res === "win" ? "Your win" : res === "loss" ? "Your loss" : "Your game") + " in " + v.turns + " turns";
    var chips = h("div", { class: "chips" },
      h("span", { class: "chip " + (res === "win" ? "result-win" : res === "loss" ? "result-loss" : "") }, res === "win" ? "✓ Win" : res === "loss" ? "✗ Loss" : "Tie"),
      v.end_reason ? h("span", { class: "chip" }, endText(v.end_reason)) : null,
      h("span", { class: "chip" }, (sharedPage || v.showcase ? "Reviewed side: " : "You: ") + (v.side === "p1" ? "Player 1" : "Player 2")),
      h("span", { class: "chip", title: "Sets drafted from the replay: what you revealed plus the most common set for the rest" },
        v.mode === "export" ? "your team export" : "sets drafted from the replay"),
      v.private ? h("span", { class: "chip" }, "private · kept 24 h") : null);
    var shareBox = h("div", { class: "share-box", hidden: true });
    var shareBtn = (!STATIC && v.features && v.features.share) ? h("button", { type: "button", class: "btn btn-ghost btn-sm",
      "aria-expanded": "false", onclick: function () { toggleShare(id, v, shareBtn, shareBox); } }, "Share") : null;
    var head = h("div", null, h("a", { class: "back", href: location.pathname.indexOf("/r/") === 0 ? "/" : "#/" }, "← Review " + (location.pathname.indexOf("/r/") === 0 ? "your own game" : "another game")),
      h("div", { class: "review-head" }, h("h1", null, title), chips, shareBtn ? h("div", { class: "head-actions" }, shareBtn) : null), shareBox);

    var bridgeCard = h("section", { class: "card", "aria-labelledby": "bridge-title" },
      h("h2", { id: "bridge-title" }, "Start → Luck → Choices → Result"),
      h("p", { class: "card-sub" }, "Where your win chance came from. Each bar starts at zero; luck and choices are signed, in win-chance points."),
      bridgeWidget(v));

    var wpCard = h("section", { class: "card", "aria-labelledby": "wp-title" },
      h("h2", { id: "wp-title" }, "Your win chance, turn by turn"),
      h("p", { class: "card-sub" }, "At the start of each turn. ▲ / ▼ mark turns where luck moved it by 3 points or more; ◆ marks a clear disagreement with the engine."),
      wpChart(v));

    var disCard = h("section", { class: "card", "aria-labelledby": "dis-title" },
      h("h2", { id: "dis-title" }, "Biggest disagreements with the engine"),
      h("p", { class: "card-sub" }, "Up to three decisions made while the game was still open (win chance 20–80%) where the engine preferred another option by 5 points or more. Never added up: one search can be off by about 3 points."),
      disagreementsList(v, id));

    var luckCard = h("section", { class: "card", "aria-labelledby": "luck-title" },
      h("h2", { id: "luck-title" }, "Biggest luck moments"),
      h("p", { class: "card-sub" }, "Chance outcomes priced against everything that could have happened on that turn."),
      luckMoments(v));

    var receipts = receiptsDrawer(id, v);
    var nodes = [head, bridgeCard, h("div", { class: "grid-2", style: "margin-top:18px" },
      h("div", { class: "stack" }, wpCard, disCard), h("div", { class: "stack" }, explainCard(id, v), luckCard, aboutCard(v))), receipts];
    show(nodes, title, focus);
  }

  // ---- the bridge: four bars on one points axis, each anchored at zero (never a running total)
  function bridgeWidget(v) {
    var b = v.bridge;
    if (!b || !b.available) {
      return h("div", { class: "empty" }, b && b.tie ? "This game ended in a tie, so there is no luck/choices bridge." : "No bridge for this game.");
    }
    var rows = [
      { key: "start", label: "Start", sub: "win chance at team preview", val: b.s, unit: "%", kind: "level" },
      { key: "luck", label: "Luck", sub: "crits, misses, rolls, speed ties", val: b.l, unit: " pts", kind: "signed" },
      { key: "choices", label: "Choices, yours and your opponent's", sub: "everything that wasn't dice", val: b.c, unit: " pts", kind: "signed" },
      { key: "result", label: "Result", sub: b.result === "win" ? "you won" : "you lost", val: b.r, unit: "%", kind: "level" }];
    var maxAbs = 100;
    var grid = h("div", { class: "bridge", role: "list", "aria-label": "Bridge from start to result" });
    rows.forEach(function (r, i) {
      if (i > 0) grid.appendChild(h("div", { class: "row-sep", "aria-hidden": "true" }));
      var lbl = h("div", { class: "b-label", role: "listitem" }, r.label, h("small", null, r.sub));
      var barBox = h("div", { class: "b-bar", "aria-hidden": "true" });
      barBox.appendChild(signedBar(r.val, maxAbs, r.kind));
      var valTxt = r.kind === "level" ? r.val + "%" : signed(r.val);
      var val = h("div", { class: "b-val num" },
        r.kind === "signed" ? h("span", { class: "glyph", "aria-hidden": "true" }, glyph(r.val)) : null,
        valTxt, r.kind === "signed" ? h("span", { class: "unit" }, "pts") : null,
        h("span", { class: "sr-only" }, r.kind === "signed" ? " points, " + favour(r.val) : ""));
      append(grid, [lbl, barBox, val]);
    });
    var ax = s("svg", { viewBox: "0 0 400 14", preserveAspectRatio: "none", "aria-hidden": "true" });
    [[-100, MINUS + "100"], [-50, MINUS + "50"], [0, "0"], [50, "+50"], [100, "+100"]].forEach(function (t) {
      ax.appendChild(s("text", { x: 200 + t[0] / 100 * 198, y: 11, "text-anchor": t[0] === -100 ? "start" : t[0] === 100 ? "end" : "middle", text: t[1] }));
    });
    append(grid, [h("div", { "aria-hidden": "true" }), h("div", { class: "b-axis-box", "aria-hidden": "true" }, ax),
      h("div", { class: "muted", style: "font-size:11px;text-align:right", "aria-hidden": "true" }, "points")]);
    var eq = h("p", { class: "bridge-eq" },
      h("b", null, b.s), " start ", b.l < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.l)), " luck ",
      b.c < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.c)), " choices = ", h("b", null, b.r), " (" + (b.result === "win" ? "win" : "loss") + ")");
    var legend = h("div", { class: "bridge-legend" },
      legendKey("pos", "▲ solid: in your favour"), legendKey("neg", "▼ striped: against you"),
      legendKey("level", "grey: a win chance, not a change"));
    var extra = [];
    if (b.tag) extra.push(h("div", { class: "notice" }, b.tag + "." + (b.tag_wp !== null && b.tag_wp !== undefined && b.tag_wp >= 0.05 && b.tag_wp <= 0.95 ?
      " The jump from " + pct(b.tag_wp) + " to the result is the resignation itself; it counts under choices." : "")));
    if (b.swingy) extra.push(h("div", { class: "notice" }, "Unusually swingy game: " +
      (Math.abs(b.L) > 0.5 ? "luck alone moved your win chance by more than 50 points" : "choices moved more than a whole game's worth of win chance") +
      ". The numbers still add up exactly; read the split with care."));
    return h("div", null, grid, eq, legend,
      h("p", { class: "footnote" }, "Choices = everything that wasn't dice: both players' moves and switches, what each side revealed, and resigning. It is not a score of your play alone."),
      extra, h("p", { class: "summary-text" }, v.summary_text));
  }

  function legendKey(kind, text) {
    var sv = s("svg", { viewBox: "0 0 22 12", "aria-hidden": "true" });
    sv.appendChild(s("rect", { x: 1, y: 1, width: 20, height: 10, rx: 2,
      fill: kind === "pos" ? "var(--pos)" : kind === "neg" ? "url(#hr-hatch-neg)" : "var(--neutral-bar)",
      stroke: kind === "neg" ? "var(--neg-dark)" : "none", "stroke-width": 1 }));
    return h("span", { class: "legend-key" }, sv, text);
  }

  function signedBar(val, maxAbs, kind) {
    var W = 400, H = 40, mid = W / 2, bh = 18, y = (H - bh) / 2;
    var sv = s("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    // axis ticks at -100, -50, 0, +50, +100 points
    [-100, -50, 50, 100].forEach(function (t) {
      var x = mid + (t / maxAbs) * (W / 2 - 2);
      sv.appendChild(s("line", { x1: x, x2: x, y1: 6, y2: H - 6, stroke: "var(--line)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    });
    var w = Math.max(Math.abs(val) / maxAbs * (W / 2 - 2), val === 0 ? 0 : 2);
    var x0 = val >= 0 ? mid : mid - w;
    var fill = kind === "level" ? "var(--neutral-bar)" : val >= 0 ? "var(--pos)" : "url(#hr-hatch-neg)";
    if (w > 0) {
      sv.appendChild(s("path", { d: roundedBar(x0, y, w, bh, val >= 0 ? "right" : "left", 4), fill: fill,
        stroke: kind !== "level" && val < 0 ? "var(--neg-dark)" : "none", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    }
    sv.appendChild(s("line", { x1: mid, x2: mid, y1: 2, y2: H - 2, stroke: "var(--ink-3)", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
    return sv;
  }
  // rounded data end, square at the zero line
  function roundedBar(x, y, w, hgt, end, r) {
    r = Math.min(r, w / 2, hgt / 2);
    if (end === "right") {
      return "M" + x + "," + y + " H" + (x + w - r) + " Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) +
        " V" + (y + hgt - r) + " Q" + (x + w) + "," + (y + hgt) + " " + (x + w - r) + "," + (y + hgt) + " H" + x + " Z";
    }
    return "M" + (x + w) + "," + y + " H" + (x + r) + " Q" + x + "," + y + " " + x + "," + (y + r) +
      " V" + (y + hgt - r) + " Q" + x + "," + (y + hgt) + " " + (x + r) + "," + (y + hgt) + " H" + (x + w) + " Z";
  }

  // ---- win-chance line with luck glyphs, keyboard crosshair and a table view
  function wpChart(v) {
    var pts = (v.wp || []).filter(function (p) { return p.wp !== null && p.wp !== undefined; });
    if (!pts.length) return h("div", { class: "empty" }, "No turn data.");
    var dis = {};
    (v.disagreements || []).forEach(function (d) { dis[d.turn] = d; });
    var wrap = h("div", { class: "chart-wrap", tabindex: "0", role: "img" });
    var tip = h("div", { class: "tooltip", hidden: true });
    var W = 640, H = 300, m = { l: 40, r: 16, t: 22, b: 34 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var t0 = pts[0].turn, t1 = pts[pts.length - 1].turn;
    var hasEnd = v.result === "win" || v.result === "loss";
    var tEnd = t1 + (hasEnd ? Math.max(0.6, (t1 - t0) / 18) : 0);
    var span = Math.max(1, tEnd - t0);
    function X(t) { return m.l + (t - t0) / span * iw; }
    function Y(p) { return m.t + (1 - p) * ih; }
    var sv = s("svg", { viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" });
    var grid = s("g", { class: "grid" });
    var axis = s("g", { class: "axis" });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (p) {
      grid.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: Y(p), y2: Y(p), class: p === 0.5 ? "mid" : "" }));
      axis.appendChild(s("text", { x: m.l - 8, y: Y(p) + 4, "text-anchor": "end", text: Math.round(p * 100) + "%" }));
    });
    var step = span > 40 ? 10 : span > 16 ? 5 : span > 8 ? 2 : 1;
    for (var t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      axis.appendChild(s("text", { x: X(t), y: H - m.b + 18, "text-anchor": "middle", text: String(t) }));
    }
    axis.appendChild(s("text", { x: W - m.r, y: H - 4, "text-anchor": "end", text: "turn" }));
    sv.appendChild(grid);
    var line = pts.map(function (p, i) { return (i ? "L" : "M") + X(p.turn).toFixed(1) + "," + Y(p.wp).toFixed(1); }).join(" ");
    sv.appendChild(s("path", { class: "wp-area", d: line + " L" + X(t1) + "," + Y(0) + " L" + X(t0) + "," + Y(0) + " Z" }));
    sv.appendChild(s("path", { class: "wp-line", d: line }));
    if (hasEnd) {   // the result, after the last turn start: a dashed step, labelled
      var R = v.result === "win" ? 1 : 0, xl = X(t1), xe = X(tEnd);
      sv.appendChild(s("path", { d: "M" + xl + "," + Y(pts[pts.length - 1].wp) + " L" + xe + "," + Y(R), fill: "none",
        stroke: "var(--ink-3)", "stroke-width": 1.5, "stroke-dasharray": "3 3" }));
      sv.appendChild(s("circle", { cx: xe, cy: Y(R), r: 4, fill: "var(--ink)", stroke: "var(--surface)", "stroke-width": 2 }));
      sv.appendChild(s("text", { class: "glyph-label", x: xe - 4, y: R ? Y(R) + 18 : Y(R) - 10, "text-anchor": "end", text: v.result === "win" ? "won" : "lost" }));
    }
    sv.appendChild(axis);
    // luck glyphs: ▲ above the line in your favour (solid), ▼ below it against you (striped); top 3 labelled
    var labelled = {};
    (v.luck_moments || []).forEach(function (lm) { labelled[lm.turn] = true; });
    pts.forEach(function (p) {
      if (p.luck_pts === null || p.luck_pts === undefined || Math.abs(p.luck) < 0.03) return;
      var x = X(p.turn), y = Y(p.wp);
      var up = p.luck_pts > 0;
      var gy = up ? y - 13 : y + 13;
      var size = 7;
      var d = up ? "M" + x + "," + (gy - size) + " L" + (x + size) + "," + (gy + size * 0.7) + " L" + (x - size) + "," + (gy + size * 0.7) + " Z"
        : "M" + x + "," + (gy + size) + " L" + (x + size) + "," + (gy - size * 0.7) + " L" + (x - size) + "," + (gy - size * 0.7) + " Z";
      sv.appendChild(s("path", { d: d, class: up ? "glyph-pos" : "glyph-neg", stroke: "var(--surface)", "stroke-width": up ? 1.5 : 1 }));
      if (labelled[p.turn]) {
        var nearRight = x > W - m.r - 40;
        sv.appendChild(s("text", { class: "glyph-label", x: nearRight ? x - 10 : x + 10, "text-anchor": nearRight ? "end" : "start",
          y: up ? Math.max(m.t - 4, gy - 2) : gy + 10, text: signed(p.luck_pts) }));
      }
    });
    // disagreement diamonds on the turn axis
    Object.keys(dis).forEach(function (tk) {
      var d = dis[tk], x = X(d.turn), y = H - m.b - 1;
      sv.appendChild(s("path", { class: "dis-mark", d: "M" + x + "," + (y - 6) + " L" + (x + 5) + "," + (y - 1) + " L" + x + "," + (y + 4) + " L" + (x - 5) + "," + (y - 1) + " Z", stroke: "var(--surface)", "stroke-width": 1.5 }));
    });
    var cross = s("line", { class: "crosshair", y1: m.t, y2: H - m.b, x1: -10, x2: -10, visibility: "hidden" });
    var dot = s("circle", { class: "cursor-dot", r: 5, cx: -10, cy: -10, visibility: "hidden" });
    sv.appendChild(cross); sv.appendChild(dot);
    wrap.appendChild(sv); wrap.appendChild(tip);

    var first = pts[0], last = pts[pts.length - 1];
    var lo = pts.reduce(function (a, p) { return p.wp < a.wp ? p : a; }), hi = pts.reduce(function (a, p) { return p.wp > a.wp ? p : a; });
    var nUp = pts.filter(function (p) { return p.luck >= 0.03; }).length, nDown = pts.filter(function (p) { return p.luck <= -0.03; }).length;
    wrap.setAttribute("aria-label", "Win chance line: " + pct(first.wp) + " on turn " + first.turn + ", " + pct(last.wp) + " on turn " + last.turn +
      "; highest " + pct(hi.wp) + " on turn " + hi.turn + ", lowest " + pct(lo.wp) + " on turn " + lo.turn + ". " +
      nUp + " turns with luck of 3 points or more in your favour, " + nDown + " against. Use the left and right arrow keys to read each turn.");

    var cur = -1;
    function place(i) {
      if (i < 0 || i >= pts.length) { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); return; }
      cur = i;
      cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
      var p = pts[i], x = X(p.turn), y = Y(p.wp);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); dot.setAttribute("cx", x); dot.setAttribute("cy", y);
      clear(tip);
      tip.appendChild(h("div", { class: "tt-head" }, "Turn " + p.turn + " · " + pct(p.wp) + " to win"));
      tip.appendChild(h("div", { class: "tt-row" }, "You", h("b", null, p.action || "not shown in the log")));
      if (p.opponent_action) tip.appendChild(h("div", { class: "tt-row" }, "Opponent", h("b", null, p.opponent_action)));
      if (p.luck_pts !== null && p.luck_pts !== undefined) tip.appendChild(h("div", { class: "tt-row" }, "Luck this turn", h("b", null, glyph(p.luck_pts) + " " + signed(p.luck_pts) + " pts")));
      (p.events || []).forEach(function (e) { tip.appendChild(h("div", { class: "tt-row" }, "• " + e.text + (e.n > 1 ? " ×" + e.n : ""))); });
      if (dis[p.turn]) tip.appendChild(h("div", { class: "tt-row" }, "◆ Engine preferred", h("b", null, dis[p.turn].best)));
      tip.hidden = false;
      var rect = sv.getBoundingClientRect(), sc = rect.width / W;
      var left = x * sc + 14, top = y * sc - 20;
      if (left + 240 > rect.width) left = x * sc - 254;
      tip.style.left = Math.max(0, left) + "px"; tip.style.top = Math.max(0, top) + "px";
    }
    function nearest(clientX) {
      var rect = sv.getBoundingClientRect(), sc = rect.width / W;
      var xv = (clientX - rect.left) / sc, best = 0, bd = 1e9;
      pts.forEach(function (p, i) { var dd = Math.abs(X(p.turn) - xv); if (dd < bd) { bd = dd; best = i; } });
      return best;
    }
    wrap.addEventListener("pointermove", function (e) { place(nearest(e.clientX)); });
    wrap.addEventListener("pointerleave", function () { if (document.activeElement !== wrap) place(-1); });
    wrap.addEventListener("focus", function () { place(cur < 0 ? 0 : cur); });
    wrap.addEventListener("blur", function () { place(-1); });
    wrap.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft" || e.key === "Home" || e.key === "End") {
        e.preventDefault();
        var i = e.key === "Home" ? 0 : e.key === "End" ? pts.length - 1 : Math.max(0, Math.min(pts.length - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
        place(i);
        live.textContent = tip.textContent;
      } else if (e.key === "Escape") { place(-1); }
    });
    var live = h("p", { class: "sr-only", "aria-live": "polite" });

    var legend = h("div", { class: "chart-legend" },
      legendKey("pos", "▲ luck in your favour"), legendKey("neg", "▼ luck against you"),
      h("span", { class: "legend-key" }, "◆ disagreement with the engine"));
    var rows = pts.map(function (p) {
      return h("tr", null, h("td", null, String(p.turn)), h("td", { class: "r" }, pct(p.wp)),
        h("td", { class: "r" }, p.luck_pts === null || p.luck_pts === undefined ? "–" : signed(p.luck_pts)),
        h("td", null, p.action || "–"), h("td", null, p.opponent_action || "–"),
        h("td", null, (p.events || []).map(function (e) { return e.text; }).join("; ") || ""));
    });
    var table = h("details", { style: "margin-top:12px" }, h("summary", null, "Show the numbers as a table"),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("caption", { class: "sr-only" }, "Win chance and luck per turn"),
        h("thead", null, h("tr", null, h("th", { scope: "col" }, "Turn"), h("th", { scope: "col", class: "r" }, "Win chance"),
          h("th", { scope: "col", class: "r" }, "Luck (pts)"), h("th", { scope: "col" }, "Your move"), h("th", { scope: "col" }, "Opponent"),
          h("th", { scope: "col" }, "Chance events"))),
        h("tbody", null, rows))));
    var txt = "Your win chance went from " + pct(first.wp) + " on turn " + first.turn + " to " + pct(last.wp) + " on turn " + last.turn +
      " (highest " + pct(hi.wp) + " on turn " + hi.turn + ", lowest " + pct(lo.wp) + " on turn " + lo.turn + ").";
    return h("div", null, wrap, live, legend, h("p", { class: "chart-help" }, "Hover, or focus the chart and use ← →, to read each turn."),
      h("p", { class: "summary-text", style: "margin-top:12px" }, txt), table);
  }

  function disagreementsList(v, id) {
    var list = v.disagreements || [];
    if (!list.length) {
      return h("div", { class: "empty" }, "No clear disagreement while the game was open: wherever the result was still in doubt, the engine agreed with you or preferred another option by less than 5 points.");
    }
    var box = h("div", { class: "dis-list" });
    list.forEach(function (d) {
      var gap = d.gap_pts;
      var bw = function (p) { return Math.max(2, Math.round(100 * p)) + "%"; };
      var bars = h("div", { class: "opt-bars", "aria-hidden": "true" },
        h("span", null, d.best + " (engine)"), h("div", { class: "ob-track" }, h("div", { class: "ob-fill best", style: "width:" + bw(d.best_wp || 0) })), h("span", { class: "num" }, pct(d.best_wp || 0)),
        h("span", null, d.chosen + " (you)"), h("div", { class: "ob-track" }, h("div", { class: "ob-fill chosen", style: "width:" + bw(d.chosen_wp || 0) })), h("span", { class: "num" }, pct(d.chosen_wp || 0)));
      var opp = (d.opponent_policy_top || []).filter(function (x) { return x[0] && x[0] !== "No Move"; }).slice(0, 2)
        .map(function (x) { return x[0] + " (" + Math.round(100 * x[1]) + "%)"; }).join(", ");
      box.appendChild(h("article", { class: "dis", "aria-label": "Turn " + d.turn },
        h("div", { class: "dis-top" },
          h("span", { class: "dis-turn" }, h("span", { class: "diamond", "aria-hidden": "true" }, "◆"),
            d.kind === "teampreview" ? "Team preview (your lead)" : "Turn " + d.turn + (d.kind === "switch" ? " (switch-in)" : "")),
          h("span", { class: "muted small" }, "your win chance before: " + d.wp_before_pct + "%")),
        h("p", { class: "claim" }, "The engine prefers ", h("b", null, gerund(d.best)), " over ", h("b", null, gerund(d.chosen)), " by ", h("b", { class: "num" }, gap + " points"), " (search noise ≈ ±3)."),
        bars,
        opp ? h("p", { class: "opp" }, "The engine expected the opponent to pick: " + opp + ".") : null,
        readBlock(id, v, d)));
    });
    var note = v.mode === "draft" ? h("p", { class: "footnote" }, "Your sets were drafted from the replay, so options that rely on a move you never revealed are left out." +
      (v.showcase ? "" : " To check one of these decisions against what you were thinking, review the game again with your team export.")) : null;
    return h("div", null, box, note);
  }

  function gerund(a) { return /^switch to /.test(a || "") ? "switching to " + a.slice(10) : a; }

  function luckMoments(v) {
    var list = v.luck_moments || [];
    if (!list.length) return h("div", { class: "empty" }, "No single turn moved your win chance by 3 points or more through luck.");
    var ul = h("ul", { class: "moments" });
    list.forEach(function (m) {
      var ev = (m.events || []).map(function (e) { return e.text; }).join("; ");
      var p = m.realised_prob;
      var odds = p !== null && p !== undefined && p < 0.5 ? " What happened had about a " + Math.max(1, Math.round(100 * p)) + "% chance." : "";
      ul.appendChild(h("li", null, h("span", { class: "muted" }, "Turn " + m.turn),
        h("span", { class: "m-pts num" }, h("span", { "aria-hidden": "true" }, glyph(m.luck_pts) + " "), signed(m.luck_pts), h("span", { class: "sr-only" }, " points " + favour(m.luck_pts))),
        h("span", null, (ev ? ev.charAt(0).toUpperCase() + ev.slice(1) + "." : "A damage roll or other hidden chance outcome.") + odds)));
    });
    return ul;
  }

  function aboutCard(v) {
    return h("section", { class: "card", "aria-labelledby": "how2-title" },
      h("h2", { id: "how2-title" }, "How to read this"),
      h("p", { class: "card-sub", style: "margin-bottom:8px" }, "Luck and choices are measured on the same scale, in points of win chance."),
      h("ul", { style: "margin:0;padding-left:18px;color:var(--ink-2);font-size:14px" },
        h("li", null, "Luck counts only chance: crits, misses, secondary effects, speed ties, KO-deciding damage rolls. It was checked on 500 engine-vs-engine games, where both seats' luck mirror each other."),
        h("li", null, "Choices is the rest of the swing, for both players together. A big negative number can come from your opponent's good play as much as from yours."),
        h("li", null, "Win chances come from the engine's search, mapped to real ladder outcomes; early turns carry little signal.")));
  }

  function receiptsDrawer(id, v) {
    var e = v.engine || {}, sec = v.seconds || {};
    var body = h("div", { class: "rc-body" },
      h("dl", null,
        h("dt", null, "Search"), h("dd", null, e.search || "–"),
        h("dt", null, "Luck"), h("dd", null, e.luck || "–"),
        h("dt", null, "Win-chance mapping"), h("dd", null, e.calibrator || "–"),
        h("dt", null, "Bridge numbers"), h("dd", null, (v.bridge && v.bridge.source) || "–"),
        h("dt", null, "Disagreement filter"), h("dd", null, "win chance " + Math.round(100 * v.filter.wp_lo) + "–" + Math.round(100 * v.filter.wp_hi) + "%, gap ≥ " + Math.round(100 * v.filter.min_gap) + " points, at most 3, never summed"),
        h("dt", null, "Decisions searched"), h("dd", null, (v.counts && v.counts.decisions) + " (" + ((v.counts && v.counts.hidden) || 0) + " with a move the log does not show)"),
        h("dt", null, "Time"), h("dd", { class: "num" }, "rebuild " + sec.reconstruct + " s · search " + sec.evaluate + " s · luck " + sec.luck + " s"),
        h("dt", null, "Exact numbers"), h("dd", null, "start " + fmt(v.bridge.S) + " · luck " + fmt(v.bridge.L) + " · choices " + fmt(v.bridge.C))),
      h("div", null, h("b", null, "Caveats"), h("ul", null, (v.caveats || []).map(function (c) { return h("li", null, c); }))),
      STATIC ? null : h("p", null, h("a", { href: "/api/review/" + encodeURIComponent(id) + "/receipts", target: "_blank", rel: "noopener" }, "Open the full engine output (JSON)")));
    return h("details", { class: "card receipts" }, h("summary", null, "Receipts: engine settings, timings and exact numbers"), body);
  }
  function fmt(x) { return x === null || x === undefined ? "–" : Number(x).toFixed(3); }

  // ------------------------------------------------------------------ explanation (Muse Glimmer, async)
  var MODE_TEXT = {
    glimmer_raw: "Written by Muse Glimmer-30B (base); it passed every check on the first draft.",
    glimmer_repaired: "Written by Muse Glimmer-30B (base); it passed the checks after one repair.",
    template: "Fixed template: Muse Glimmer was not available, or its draft failed the checks."
  };
  function explainCard(id, v) {
    var qs = v.questions || {};
    var body = h("div", { class: "explain-body", "aria-live": "polite" });
    var tabs = h("div", { class: "q-tabs", role: "group", "aria-label": "Questions" });
    var current = "q1";
    var card = h("section", { class: "card explain-card", "aria-labelledby": "ex-title" },
      h("div", { class: "ex-head" }, h("h2", { id: "ex-title" }, "In plain words"),
        h("span", { class: "badge-model" }, "Muse Glimmer-30B (base) · explainer")),
      h("p", { class: "card-sub" }, "Every number below is copied from a cited engine result; the model only writes the words."),
      tabs, body);
    Object.keys(qs).forEach(function (q) {
      tabs.appendChild(h("button", { type: "button", class: "q-tab", "data-q": q, "aria-pressed": q === current ? "true" : "false",
        onclick: function () { current = q; tabs.querySelectorAll(".q-tab").forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-q") === q ? "true" : "false"); }); load(q); } }, qs[q]));
    });
    function draw(ex, pending) {
      clear(body);
      var r = ex.rendered || {};
      var p = h("p", { class: "ex-text" });
      (r.sentences || []).forEach(function (snt, i) {
        if (i) p.appendChild(document.createTextNode(" "));
        var text = snt.text, pos = 0;
        (snt.citations || []).forEach(function (c) {
          var at = text.indexOf(c.shown, pos);
          if (at < 0) return;
          p.appendChild(document.createTextNode(text.slice(pos, at)));
          p.appendChild(h("mark", { class: "cite", title: "from " + c.tool + " · " + c.field }, c.shown));
          pos = at + c.shown.length;
        });
        p.appendChild(document.createTextNode(text.slice(pos)));
      });
      var cites = [];
      (r.sentences || []).forEach(function (snt) { (snt.citations || []).forEach(function (c) { cites.push(c); }); });
      body.appendChild(p);
      body.appendChild(h("p", { class: "ex-mode" }, pending ? "Fixed template, shown while Muse Glimmer writes." : (MODE_TEXT[ex.final] || ""),
        !pending && ex.final !== "template" && ex.latency_s ? " (" + Math.round(ex.latency_s) + " s)" : ""));
      if (pending) body.appendChild(h("p", { class: "muted ex-wait ex-pending" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("span", null, "Muse Glimmer is writing its own version…")));
      if (cites.length) {
        body.appendChild(h("details", { class: "ex-sources" }, h("summary", null, "Sources for each number (" + cites.length + ")"),
          h("ul", null, cites.map(function (c) {
            return h("li", null, h("b", null, c.shown), " ← ", h("code", null, c.tool + "." + c.field), " = " + JSON.stringify(c.value));
          }))));
      }
    }
    function waiting(msg) { clear(body); body.appendChild(h("p", { class: "muted ex-wait" }, h("span", { class: "spinner", "aria-hidden": "true" }), msg)); }
    function load(q) {
      var have = (v.explanations || {})[q];
      if (have) { draw(have); return; }
      if (STATIC) { clear(body); body.appendChild(h("p", { class: "muted" }, "Not included in this static copy.")); return; }
      waiting("Muse Glimmer is reading the review…");
      api("/api/explain", { review_id: id, question: q }).then(function (d) {
        if (!d.ok) { clear(body); body.appendChild(h("p", { class: "muted" }, d.error || "No explanation available.")); return; }
        if (d.cached) { refresh(q); return; }
        if (d.template) { draw(d.template, true); }
        poll(d.job_id, q, !!d.template);
      });
    }
    function poll(jid, q, hasTemplate) {
      api("/api/job/" + jid).then(function (st) {
        if (current !== q || !document.body.contains(body)) return;
        if (st.state === "done") { refresh(q); return; }
        if (st.state === "error") {
          if (!hasTemplate) { clear(body); body.appendChild(h("p", { class: "muted" }, "The explanation could not be written: " + (st.message || ""))); }
          else { var w0 = body.querySelector(".ex-pending"); if (w0) w0.textContent = "Muse Glimmer could not finish; the template above stands."; }
          return;
        }
        var msg = st.state === "queued" ? "Muse Glimmer is queued…" : "Muse Glimmer is writing its own version (it runs on this machine)… " + Math.round(st.elapsed || 0) + " s";
        var w = body.querySelector(".ex-pending");
        if (hasTemplate && w) w.lastChild.textContent = msg; else if (!hasTemplate) waiting(msg);
        setTimeout(function () { poll(jid, q, hasTemplate); }, 1500);
      });
    }
    function refresh(q) {
      getView(id).then(function (nv) { if (nv && nv.explanations) { v.explanations = nv.explanations; } if (current === q && v.explanations[q]) draw(v.explanations[q]); });
    }
    if (!v.features || v.features.explain === false) return null;
    load(current);
    return card;
  }

  // ------------------------------------------------------------------ "What were you thinking?" -> chips -> verdict
  var FIELD_LABEL = { item: "item", ability: "ability", tera: "Tera type", moves_has: "has the move", moves_not: "does not have the move",
    expects_action: "I expected them to", confidence: "confidence" };
  function chipText(c) {
    if (c.field === "confidence") return "Confidence: " + c.value;
    if (c.field === "expects_action") return "Expected: " + c.value;
    return (c.species ? c.species + " · " : "") + FIELD_LABEL[c.field] + ": " + c.value;
  }
  function beliefFromChips(chips) {
    var b = { sets: {} };
    chips.forEach(function (c) {
      if (c.field === "item" || c.field === "ability" || c.field === "tera") { (b.sets[c.species] = b.sets[c.species] || {})[c.field] = c.id; }
      else if (c.field === "moves_has" || c.field === "moves_not") {
        var e = (b.sets[c.species] = b.sets[c.species] || {}); e[c.field] = e[c.field] || [];
        if (e[c.field].indexOf(c.id) < 0) e[c.field].push(c.id);
      } else if (c.field === "expects_action") { b.expects_action = c.id; }
      else if (c.field === "confidence") { b.confidence = c.id; }
    });
    return b;
  }

  function readBlock(id, v, d) {
    var key = d.turn + ":" + d.k;
    var have = (v.verdicts || {})[key];
    var wrap = h("div", { class: "read-block" });
    if (STATIC) return have ? verdictCard(have) : null;
    if (!v.features || !v.features.verdict) return have ? verdictCard(have) : null;
    var panel = h("div", { class: "read-panel", hidden: true });
    var btn = h("button", { type: "button", class: "btn btn-ghost btn-sm", "aria-expanded": "false" }, have ? "Check again with another read" : "What were you thinking?");
    btn.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && !panel.firstChild) buildReadPanel(id, v, d, panel, wrap);
      if (open) { var t = panel.querySelector("textarea"); if (t) t.focus(); }
    });
    if (have) wrap.appendChild(verdictCard(have));
    wrap.appendChild(h("div", { class: "form-row", style: "margin-top:10px" }, btn));
    wrap.appendChild(panel);
    return wrap;
  }

  function buildReadPanel(id, v, d, panel, wrap) {
    var chips = [];
    var uid = "n" + d.turn + "_" + d.k;
    var note = h("textarea", { id: uid, rows: 3, class: "note", placeholder: "e.g. I thought Kingambit had Black Glasses and would Sucker Punch, so I switched" });
    var chipList = h("ul", { class: "chip-list", "aria-label": "Your read, as details" });
    var msg = h("p", { class: "hint", "aria-live": "polite" });
    var readBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, "Read my note");
    var checkBtn = h("button", { type: "button", class: "btn btn-primary btn-sm" }, "Check this decision");
    function drawChips() {
      clear(chipList);
      if (!chips.length) chipList.appendChild(h("li", { class: "muted small" }, "No details yet. With none, the check uses the engine's own guess at their sets."));
      chips.forEach(function (c, i) {
        chipList.appendChild(h("li", { class: "chip chip-edit" }, chipText(c),
          h("button", { type: "button", class: "chip-x", "aria-label": "Remove " + chipText(c), onclick: function () { chips.splice(i, 1); drawChips(); } }, "×")));
      });
    }
    readBtn.addEventListener("click", function () {
      if (!note.value.trim()) { msg.textContent = "Write what you were thinking first."; return; }
      readBtn.disabled = true; msg.textContent = "Reading your note…";
      api("/api/note/parse", { review_id: id, note: note.value }).then(function (r) {
        readBtn.disabled = false;
        if (!r.ok) { msg.textContent = r.error || "Could not read the note."; return; }
        chips = chips.concat(r.chips || []);
        drawChips();
        var dropped = (r.dropped || []).length;
        msg.textContent = (r.chips || []).length + " detail(s) found by the " + (r.parser.indexOf("glimmer") === 0 ? "Muse Glimmer note reader" : "keyword reader") +
          (dropped ? "; " + dropped + " left out because the note does not name them" : "") + ". Edit them before checking.";
      });
    });
    // add a detail by hand
    var add = h("details", { class: "add-detail" }, h("summary", null, "Add a detail by hand"));
    api("/api/options/" + encodeURIComponent(id)).then(function (o) {
      if (!o.ok) return;
      var active = (o.opp_active || {})[d.turn + ":" + d.k];
      var spSel = h("select", { "aria-label": "Their Pokémon" }, (o.opp_species || []).map(function (sp) { return h("option", { value: sp, selected: sp === active }, sp); }));
      var fSel = h("select", { "aria-label": "Detail" }, ["item", "ability", "tera", "moves_has", "moves_not", "expects_action"].map(function (f) { return h("option", { value: f }, FIELD_LABEL[f]); }));
      var dl = h("datalist", { id: uid + "-dl" });
      var val = h("input", { type: "text", list: uid + "-dl", "aria-label": "Value", placeholder: "value" });
      function fillList() {
        clear(dl);
        var opts = (o.options || {})[spSel.value] || {};
        var f = fSel.value;
        var src = f === "item" ? opts.items : f === "ability" ? opts.abilities : f === "tera" ? opts.teras : f === "expects_action" ? [{ name: "switch out" }, { name: "Terastallize" }].concat(opts.moves || []) : opts.moves;
        (src || []).forEach(function (x) { dl.appendChild(h("option", { value: x.name })); });
      }
      spSel.addEventListener("change", fillList); fSel.addEventListener("change", fillList); fillList();
      var ok = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, "Add");
      ok.addEventListener("click", function () {
        var t = val.value.trim(); if (!t) return;
        var f = fSel.value, idv = t.toLowerCase().replace(/[^a-z0-9]/g, "");
        if (f === "expects_action") {
          var kind = /^switch/i.test(t) ? "switch" : /^tera/i.test(t) ? "tera" : "move";
          chips.push({ species: spSel.value, field: f, value: t + " (" + spSel.value + ")", id: kind === "move" ? { kind: kind, move: idv, species: spSel.value } : { kind: kind, species: spSel.value } });
        } else chips.push({ species: spSel.value, field: f, value: t, id: idv });
        val.value = ""; drawChips(); val.focus();
      });
      add.appendChild(h("div", { class: "add-row" }, spSel, fSel, val, dl, ok));
    });
    checkBtn.addEventListener("click", function () {
      checkBtn.disabled = true; msg.textContent = "Starting the check…";
      api("/api/verdict", { review_id: id, turn: d.turn, k: d.k, belief: beliefFromChips(chips) }).then(function (r) {
        if (!r.ok) { checkBtn.disabled = false; msg.textContent = r.error || "Could not start the check."; return; }
        var finish = function () {
          getView(id).then(function (nv) {
            checkBtn.disabled = false;
            var card = (nv.verdicts || {})[d.turn + ":" + d.k];
            if (!card) { msg.textContent = "The check finished but returned nothing."; return; }
            v.verdicts = nv.verdicts;
            var old = wrap.querySelector(".verdict"); var fresh = verdictCard(card);
            if (old) old.replaceWith(fresh); else wrap.insertBefore(fresh, wrap.firstChild);
            msg.textContent = "Done."; fresh.querySelector("h3").focus();
          });
        };
        if (r.cached) { finish(); return; }
        (function poll() {
          api("/api/job/" + r.job_id).then(function (st) {
            if (st.state === "done") { finish(); return; }
            if (st.state === "error") { checkBtn.disabled = false; msg.textContent = st.message || "The check failed."; return; }
            msg.textContent = st.state === "queued" ? "Waiting for a free engine slot (#" + (st.position || 1) + ")…" : "Running 3 independent searches on the same sampled worlds… " + Math.round(st.elapsed || 0) + " s";
            setTimeout(poll, 1500);
          });
        })();
      });
    });
    drawChips();
    append(panel, [h("label", { class: "field-label", for: uid }, "Your read at the time, in your own words"), note,
      h("div", { class: "form-row" }, readBtn, h("span", { class: "hint", style: "margin:0" }, "Their items, abilities, Tera, moves, or what you expected them to do.")),
      chipList, add, h("div", { class: "form-row" }, checkBtn), msg,
      h("p", { class: "footnote" }, "We check your read against the engine. Spreads and EVs are not used, and we don't yet replay the turn under the move you expected.")]);
  }

  var VERDICT_GLYPH = { "Misplay": "▼", "Misread": "◐", "Defensible": "✓", "Within noise": "≈" };
  function verdictCard(c) {
    var cls = { "Misplay": "v-misplay", "Misread": "v-misread", "Defensible": "v-ok", "Within noise": "v-noise" }[c.verdict] || "v-noise";
    var box = h("div", { class: "verdict " + cls },
      h("h3", { tabindex: "-1" }, h("span", { class: "v-badge" }, h("span", { "aria-hidden": "true" }, VERDICT_GLYPH[c.verdict] + " "), c.verdict)),
      h("p", { class: "v-head" }, c.headline),
      runsPlot(c),
      c.stated && c.stated.length ? h("p", { class: "small muted" }, "Your read: " + c.stated.join("; ") + ".") : h("p", { class: "small muted" }, "Your read: no details (the engine's own guess at their sets)."),
      (c.contradictions || []).map(function (t) { return h("p", { class: "small notice-inline" }, t + ". That detail was left out."); }),
      c.third_option ? h("p", { class: "small" }, c.third_option) : null,
      c.read_check ? h("p", { class: "small" }, c.read_check.sentence, h("br"), h("span", { class: "muted" }, c.read_check.scope)) : null,
      h("p", { class: "footnote" }, (c.runs || 3) + " independent runs on the same sampled worlds · " + (c.worlds || 4) + " worlds × " + (c.iterations || 20000).toLocaleString("en") + " iterations each" + (c.seconds ? " · " + Math.round(c.seconds) + " s" : "") + "."));
    return box;
  }
  function runsPlot(c) {
    var W = 520, rowH = 34, top = 8, lab = 92, R = 20;
    var rows = [["Your read", c.delta_U], ["Reference", c.delta_R]];
    var H = top + rows.length * rowH + 22;
    var plotW = W - lab - 14;
    function X(p) { var q = Math.max(-R, Math.min(R, p)); return lab + (q + R) / (2 * R) * plotW; }
    var sv = s("svg", { viewBox: "0 0 " + W + " " + H, class: "runs-plot", role: "img",
      "aria-label": c.x + " minus your move, in points, three runs per row: under your read " + (c.delta_U || []).join(", ") + "; under " + c.reference_label + " " + (c.delta_R || []).join(", ") + ". Threshold " + c.margin_pts + " points." });
    sv.appendChild(s("rect", { x: X(c.margin_pts), y: top - 4, width: X(R) - X(c.margin_pts), height: rows.length * rowH, fill: "var(--pos-wash)" }));
    sv.appendChild(s("line", { x1: X(0), x2: X(0), y1: top - 4, y2: top + rows.length * rowH, stroke: "var(--ink-3)", "stroke-width": 1.5 }));
    sv.appendChild(s("line", { x1: X(c.margin_pts), x2: X(c.margin_pts), y1: top - 4, y2: top + rows.length * rowH, stroke: "var(--pos)", "stroke-width": 1, "stroke-dasharray": "4 3" }));
    rows.forEach(function (r, i) {
      var y = top + i * rowH + rowH / 2;
      sv.appendChild(s("text", { x: 0, y: y + 4, class: "rp-label", text: r[0].length > 24 ? r[0].slice(0, 23) + "…" : r[0] }));
      sv.appendChild(s("line", { x1: lab, x2: lab + plotW, y1: y, y2: y, stroke: "var(--line)", "stroke-width": 1 }));
      (r[1] || []).forEach(function (dv) {
        if (dv === null || dv === undefined) return;
        sv.appendChild(s("circle", { cx: X(dv), cy: y, r: 5.5, fill: "var(--ink)", stroke: "var(--surface)", "stroke-width": 2 }));
      });
    });
    [-20, -10, 0, 10, 20].forEach(function (t) {
      sv.appendChild(s("text", { x: X(t), y: H - 4, "text-anchor": "middle", class: "rp-axis", text: t > 0 ? "+" + t : t < 0 ? MINUS + Math.abs(t) : "0" }));
    });
    return h("figure", { class: "runs" }, sv, h("figcaption", { class: "small muted" },
      c.x + " minus your move (" + c.chosen + "), in win-chance points. Each dot is one run. Reference: " + c.reference_label +
      ". The shaded zone (+" + c.margin_pts + " or more, in all three runs) is where the engine's option is clearly better."));
  }

  // ------------------------------------------------------------------ share (opt-in; public replays only)
  function toggleShare(id, v, btn, box) {
    var open = box.hidden;
    box.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open || box.firstChild) return;
    box.appendChild(h("p", { class: "muted" }, "Creating the share page…"));
    api("/api/share/" + encodeURIComponent(id), {}).then(function (r) {
      clear(box);
      if (!r.ok) { box.appendChild(h("p", { class: "form-error" }, r.error || "Cannot share this review.")); return; }
      var url = location.origin + r.url;
      var link = h("input", { type: "text", readonly: true, value: url, "aria-label": "Share link" });
      var copy = h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: function () {
        link.select(); try { navigator.clipboard.writeText(url); copy.textContent = "Copied"; } catch (e) { document.execCommand("copy"); copy.textContent = "Copied"; }
      } }, "Copy link");
      var dl = h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: function () { downloadPng(r.card, "hardread-" + id + ".png"); } }, "Download image");
      append(box, [h("img", { src: r.card, alt: "Share card: " + (v.summary_text || ""), class: "share-img", width: 600, height: 315 }),
        h("div", { class: "form-row" }, link, copy, dl),
        h("p", { class: "hint" }, "The card shows numbers only: no names, no chat, no notes. This build runs on your machine, so the link works only here.")]);
    });
  }
  function downloadPng(src, name) {
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas"); c.width = 2400; c.height = 1260;
      c.getContext("2d").drawImage(img, 0, 0, 2400, 1260);
      c.toBlob(function (b) { var a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500); }, "image/png");
    };
    img.src = src;
  }

  // ------------------------------------------------------------------ about & privacy
  function renderAbout() {
    lastRender = null;
    show([h("article", { class: "prose" },
      h("p", { class: "eyebrow" }, "About"),
      h("h1", { style: "font-size:36px" }, "About HardRead"),
      h("p", null, "HardRead audits decisions made under hidden information against a champion search engine, with receipts. Competitive Pokémon is the testbed: each player hides most of their team, chance decides a lot, and a finished replay lets us price every decision and every roll of the dice."),
      h("p", null, "The engine is Foul Play (GPL-3.0), the search agent that won the PokéAgent 2025 Gen 9 OU track, running on poke-engine. For every one of your decisions it samples teams your opponent could have and searches each of them; for every turn it enumerates the chance outcomes and prices the one that happened against the rest."),
      h("h2", null, "What the numbers mean"),
      h("ul", null,
        h("li", null, "Start: your win chance at team preview."),
        h("li", null, "Luck: the sum over turns of what the dice gave you, compared with the average outcome of the same turn."),
        h("li", null, "Choices, yours and your opponent's: the rest of the way to the result. The three always add up to the result."),
        h("li", null, "Disagreements: at most three decisions in open positions where the engine preferred another option by 5 points or more. They are never added up and are not a skill score.")),
      h("h2", { id: "privacy" }, "Privacy"),
      h("ul", null,
        h("li", null, "This build runs on your own machine. The only outside request is fetching the replay link you paste, from replay.pokemonshowdown.com."),
        h("li", null, "Player names and chat are removed before the review runs; the review says “You” and “Opponent”. Names are shown once, on the side picker, and never stored."),
        h("li", null, "Reviews of public replays are kept 14 days so a reload is instant; private (password) replays 24 hours, with no share page and no card."),
        h("li", null, "The pasted log is deleted when the review finishes, unless you added your team export: then the name-free log and the export stay with the review, for the same time, so you can check decisions. A note you write about a decision is read on this machine and not stored; the details you confirm are kept with that check."),
        h("li", null, "Sharing is opt-in: a share page and card exist only after you press Share, and they show numbers only (no names, chat or notes)."),
        h("li", null, "No accounts, no tracking, no cookies. Your theme and the 18+ confirmation are remembered in this browser only.")),
      h("h2", null, "Fair play"),
      h("p", null, "HardRead only reviews finished games. Links to live battle rooms are refused."),
      h("h2", null, "Not affiliated"),
      h("p", null, "HardRead is a fan-made research project. It is not affiliated with, endorsed or sponsored by Nintendo, The Pokémon Company, Game Freak or Pokémon Showdown. Pokémon and character names are trademarks of their owners and are used only to refer to the game. No official art, sprites, logos or music are used."),
      h("p", null, h("a", { href: "#/" }, "← Review a game")))], "About");
  }

  // ------------------------------------------------------------------ shared SVG defs (hatch for "against you")
  function installDefs() {
    var sv = s("svg", { width: 0, height: 0, style: "position:absolute", "aria-hidden": "true", focusable: "false" },
      s("defs", null,
        s("pattern", { id: "hr-hatch-neg", patternUnits: "userSpaceOnUse", width: 6, height: 6, patternTransform: "rotate(45)" },
          s("rect", { width: 6, height: 6, fill: "var(--neg)" }),
          s("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--neg-dark)", "stroke-width": 3 }))));
    document.body.insertBefore(sv, document.body.firstChild);
  }

  // ------------------------------------------------------------------ boot
  installDefs();
  initTheme();
  var skip = document.querySelector(".skip");
  if (skip) skip.addEventListener("click", function (e) { e.preventDefault(); main.focus(); });
  window.addEventListener("hashchange", route);
  initGate();
  route();
})();
