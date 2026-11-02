/* HardRead front end: vanilla JS, no framework, no innerHTML with data (every text goes through textContent).
   Routes: #/  ·  #/review/<id>  ·  #/job/<id>  ·  #/about
   A static export (app/static_export.py) sets window.HARDREAD_STATIC = {views, reviews, showcases} and the same
   code renders without a server. */
(function () {
  "use strict";

  var STATIC = window.HARDREAD_STATIC || null;
  // On the static site, live.json names the live app. Its address can change, so the site asks rather than assumes.
  function liveStatus(done) {
    var timer = function (ms) { var c = new AbortController(); setTimeout(function () { c.abort(); }, ms); return c.signal; };
    fetch("live.json", { cache: "no-store", signal: timer(4000) }).then(function (r) { return r.json(); }).then(function (l) {
      if (!l.url) return done(null);
      return fetch(l.url + "/api/health", { cache: "no-store", signal: timer(45000) }).then(function (r) { return r.json(); })
        .then(function (hh) { done(hh && hh.ok ? l.url : null); });
    }).catch(function () { done(null); });
  }
  var PUBLIC_COPY = !!STATIC || !!document.querySelector('meta[name="hardread-public"]');
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
    d.addEventListener("cancel", function (e) { e.preventDefault(); });   // Esc does not skip the gate
    document.getElementById("gate-privacy").addEventListener("click", function () {
      d.close();   // read the privacy notice first; the gate comes back on leaving it (route) or on submit
    });
    document.getElementById("gate-form").addEventListener("submit", function () {   // one tap: the button is the 18+ confirmation
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
    else if (hsh === "/own-ladder") { renderOwnLadder(); nav = ""; }
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
      h("p", { id: "paste-hint", class: "hint" }, PUBLIC_COPY ? "Paste a public replay.pokemonshowdown.com link. This copy handles one review at a time, so you may have to wait." : "Public or private (password) replay links both work. Private replays are kept 24 hours and never get a share page."),
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
      showError("This page keeps saved showcase reviews. Checking whether live review is online, which can take up to a minute while it wakes up…");
      liveStatus(function (url) {
        var note = form.parentNode.querySelector(".error, .form-error, [role=alert]");
        if (url) {
          showError("");
          form.parentNode.insertBefore(h("p", { class: "live-link" }, h("a", { class: "btn", href: url }, "Review your own game on the live app →"),
            h("span", { class: "hint" }, " It handles one review at a time and may take a minute to wake up.")), form);
        } else if (note) {
          showError("Live review is temporarily offline. The saved showcases below still work.");
        }
      });
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
        d.team_species && d.team_species.length ? h("span", { class: "chip" }, "team export: " + d.team_species.length + " Pokémon") : h("span", { class: "chip" }, "no export: your sets will be assumed"));
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
      h("h2", { id: "how-title", class: "section-title" }, "How a game becomes one sum"),
      h("p", { class: "section-sub" }, "Every turn is replayed through the search engine, with sampled guesses at what the opponent's team could hold."),
      h("div", { class: "how" },
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "1"), h("b", null, "Start"),
          h("p", null, "Your win chance at team preview, before anyone moved.")),
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "2"), h("b", null, "Luck"),
          h("p", null, "Each crit, miss, roll and speed tie, measured against every outcome that could have happened instead.")),
        h("div", { class: "step" }, h("span", { class: "n", "aria-hidden": "true" }, "3"), h("b", null, "Choices, both players"),
          h("p", null, "Everything that was not dice. Plus the few turns where the engine clearly disagreed with you."))));

    var showcaseSec = h("section", { "aria-labelledby": "sc-title", id: "showcases", style: "margin-top:34px" },
      h("h2", { id: "sc-title", class: "section-title" }, "See a finished review"),
      h("p", { class: "section-sub" }, "One click, nothing to paste. Real ladder games from public replays, each reviewed from one player's side. Names removed; that player's sets (moves, items) are assumed."));
    var scGrid = h("div", { class: "showcases" }, h("p", { class: "muted" }, "Loading showcases…"));
    showcaseSec.appendChild(scGrid);
    // The sample sum in the hero is the first showcase's real result, and it links to that review.
    var sampleBox = h("a", { class: "hero-sample", hidden: true, href: "#/review/showcase-6" });
    var seeBtn = h("a", { class: "btn btn-primary", href: "#/review/showcase-6" }, "See a real review →");
    getShowcases().then(function (d) {
      clear(scGrid);
      var list = (d && d.showcases) || [];
      if (!list.length) {
        scGrid.appendChild(h("p", { class: "muted" }, "No showcases in this build."));
        seeBtn.hidden = true;
        return;
      }
      // the first thing a judge opens: not showcase-1, whose top disagreement rests on an assumed spread
      var hero = list.filter(function (x) { return x.id === "showcase-6"; })[0] || list[0];
      seeBtn.setAttribute("href", "#/review/" + hero.id);
      var first = hero.bridge || {};
      if (first.s !== undefined && first.s !== null) {
        sampleBox.setAttribute("href", "#/review/" + hero.id);
        append(sampleBox, [
          h("span", { class: "hs-label" }, "One real game, as one sum"),
          sumLine(first, hero.result),
          h("span", { class: "hs-note" }, "Luck is our estimate of crits, misses, rolls and speed ties. Choices is everything else, for both players together.")]);
        sampleBox.hidden = false;
      }
      list.forEach(function (sc) {
        var b = sc.bridge || {};
        scGrid.appendChild(h("a", { class: "showcase", href: "#/review/" + sc.id },
          h("div", { class: "chips", style: "display:flex;gap:6px;flex-wrap:wrap" },
            h("span", { class: "chip " + (sc.result === "win" ? "result-win" : "result-loss") }, sc.result === "win" ? "✓ Win" : "✗ Loss"),
            h("span", { class: "chip" }, sc.turns + " turns")),
          h("h3", null, sc.title || "Ladder game"),
          h("div", { class: "meta" }, "Ladder game " + (sc.band || "") + " · " + endText(sc.end_reason)),
          b.s !== undefined && b.s !== null ? h("div", { class: "sc-sum" }, sumLine(b, sc.result)) : null,
          miniBridge(b),
          h("div", { class: "go" }, "Open the review →")));
      });
    });

    var ownBox = h("div", null);
    if (!STATIC) api("/api/own-ladder").then(function (o) {   // only offered when this server has the bot's records
      var has = o && (o.games || []).some(function (g) { return (g.decisions || []).length; });
      if (has) ownBox.appendChild(h("section", { class: "card", style: "margin-top:34px" },
        h("h2", null, "Why the bot played this"),
        h("p", null, "The search evidence our bot recorded before acting, in selected games from its own ladder runs."),
        h("a", { href: "#/own-ladder" }, "Open the bot’s games →")));
    });

    var differ = h("section", { "aria-labelledby": "diff-title", class: "differ" },
      h("h2", { id: "diff-title", class: "section-title" }, "The engine analyzes. The model explains. Code fills in every number."),
      h("div", { class: "how" },
        h("div", { class: "step" }, h("b", null, "A search engine does the analysis"),
          h("p", null, "Foul Play, the open-source engine that won the 2025 PokéAgent Gen 9 OU track, samples the hidden sets and searches the continuations.")),
        h("div", { class: "step" }, h("b", null, "Muse Glimmer explains it"),
          h("p", null, "Meta's open model chooses which engine results to look up, then explains them in plain words.")),
        h("div", { class: "step" }, h("b", null, "The model can't write a number"),
          h("p", null, "It writes sentences with slots pointing at engine results. Code fills in the values. A checker rejects uncited numbers and citations of the wrong kind of value. It can't tell if a number is the right one, and it doesn't check the words."))),
      h("p", { class: "hint" }, "On 28 October's versions of three questions, 41 of 60 explanations passed the checker first time and 59 after one repair. ", h("a", { href: "#/about" }, "How this was measured")));

    show([h("section", { class: "hero" },
      h("h1", null, "Was it luck, or was it you?", h("span", { class: "h1-sub" }, "An engine review for competitive Pokémon")),
      h("p", { class: "lede" }, "Chess players get an engine review after every game. Pokémon has luck calculators and win-chance tools, but none we found splits a game into start, luck and both players' choices using search over hidden sets. You can't see the opponent's moves and items, and dice decide many turns. HardRead reviews a finished game anyway, and estimates how much was luck."),
      h("div", { class: "form-row hero-cta" }, seeBtn,
        h("button", { type: "button", class: "btn btn-ghost", onclick: function () {
          form.scrollIntoView({ behavior: "smooth", block: "center" });
          input.focus({ preventScroll: true });
        } }, "Review your own game")),
      sampleBox),
      differ, showcaseSec, h("div", { style: "margin-top:34px" }, form), how, ownBox], null, false);
  }

  // "53% start + 73 luck − 26 choices = 100% (win)"; b holds the rounded values. Used wherever a sum appears in a line.
  function sumLine(b, result) {
    return h("span", { class: "sum-line num" },
      h("b", null, b.s + "%"), " start ", b.l < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.l)), " luck ",
      b.c < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.c)), " choices = ", h("b", null, b.r + "%"), " (" + (result === "win" ? "win" : "loss") + ")");
  }

  function ownCitation(field) {
    return h("span", { class: "own-citation" }, String(field.value),
      h("details", { class: "own-citation-source" },
        h("summary", { "aria-label": "Show source for " + field.field }, "source"),
        h("small", null, h("code", null, field.field), "; source ", h("code", null, field.ref))));
  }

  function renderOwnLadder() {
    show([h("p", { class: "muted" }, "Loading the bot’s games…")], "Why the bot played this", false);
    var request = STATIC ? Promise.resolve({ games: [] }) : api("/api/own-ladder");
    request.then(function (data) {
      var games = (data && data.games && data.games.length === 3) ? data.games :
        ["G1", "G2", "G3"].map(function (alias) { return { alias: alias, decisions: [] }; });
      var recordedFacts = data && data.presentation === "recorded-facts";
      var cards = games.map(function (game) {
        var decisions = game.decisions || [];
        return h("section", { class: "card own-game" }, h("h2", null, game.alias),
          h("p", { class: "card-sub" }, "HardReadBot vs Opponent"),
          recordedFacts ? h("p", null, h("span", { class: "badge-model" }, game.selection_mode === "glimmer-selected" ? "Supporting facts selected by Glimmer · Template wording" : "Template only")) : null,
          decisions.length < 2 ? h("p", { class: "empty" }, decisions.length ? "Partial evidence: only one decision is available." : "Decision records for this game aren't available in this copy of the app.") : null,
          game.conflicts_unavailable ? h("p", { class: "notice" }, "Conflicting source records are unavailable.") : null,
          decisions.map(function (d) {
            if (recordedFacts) return h("article", { class: "dis" },
              h("p", null, "On turn ", ownCitation(d.turn), ", HardReadBot recorded ", ownCitation(d.chosen), " as its choice."),
              d.duration ? h("p", null, "The log records ", ownCitation(d.duration.value), " ", ownCitation(d.duration.unit), " of search.") : null,
              (d.visits || []).map(function (v) { return h("p", null, "The log records ", ownCitation(v.value), " search visits for ", ownCitation(v.scope), "."); }));
            return h("article", { class: "dis" },
              h("h3", null, "Turn ", ownCitation(d.turn)),
              h("p", null, "Before acting, the bot’s active Pokémon: ", d.own_active ? ownCitation(d.own_active) : "unavailable", "."),
              h("p", null, "The bot chose ", ownCitation(d.chosen), "; its logged legal candidate score was ", ownCitation(d.chosen_score), "."),
              h("p", null, "Best other logged legal candidate: ", ownCitation(d.other), ", score ", ownCitation(d.other_score), "."),
              h("p", { class: "footnote" }, "These are search scores on the same recorded scale; they are not calibrated win probabilities. Score order: ", ownCitation(d.score_order), "; scale: ", ownCitation(d.scale), "; unit: ", d.score_unit ? ownCitation(d.score_unit) : "unavailable", ". Legality sources: ", ownCitation(d.chosen_legal), " and ", ownCitation(d.other_legal), "."));
          }), recordedFacts ? postgamePane(game) : null);
      });
      show([h("a", { class: "back", href: "#/" }, "← Home"), h("h1", null, "Why the bot played this"),
        recordedFacts ? h("p", null, h("span", { class: "badge-model" }, "Recorded decision facts")) : null,
        h("p", { class: "lede" }, recordedFacts ? "The bot’s recorded choices and search work, captured before it acted." : "Examples chosen from what the bot recorded before acting. The wording comes from fixed templates."),
        recordedFacts ? h("p", { class: "footnote" }, "Detailed candidate comparisons are unavailable in these logs.") : null, cards], "Why the bot played this");
    });
  }

  function postgamePane(game) {
    var p = game.postgame;
    var section = h("section", { class: "dis", "aria-label": game.alias + " postgame engine review" }, h("h3", null, "Postgame engine review"));
    if (!p) { section.appendChild(h("p", { class: "empty" }, "Engine review not available yet.")); return section; }
    if (p.status !== "ok") {
      section.appendChild(h("p", { class: "empty" }, p.reason === "cache_missing" ? "Engine review unavailable in this copy of the app." : "Engine review unavailable.")); return section;
    }
    section.appendChild(h("p", { class: "footnote" }, "Postgame estimates from the existing review search and calibration; they are not the bot’s logged beliefs. Luck is measured after observed outcomes. Choices includes both players."));
    if (p.human_fit) section.appendChild(h("p", { class: "footnote" }, "Calibration was fitted on human games."));
    if (p.percent_hp_approx) section.appendChild(h("p", { class: "footnote" }, "Own HP was approximated from recorded percentages."));
    var b=p.bridge;
    section.appendChild(bridgeWidget({ bridge:b, summary_text:"", result:p.result }, { subject:"HardReadBot" }));
        var points=p.wp || [], W=640, H=160, vals=points.filter(function(x){return x.wp !== null;});
    if (vals.length) {
      var sv=s("svg",{viewBox:"0 0 640 160",role:"img","aria-label":"HardReadBot win probability by turn; detailed values follow"});
      var lo=points[0].turn, hi=points[points.length-1].turn, span=Math.max(1,hi-lo), path="", open=false;
      points.forEach(function(x){if(x.wp===null){open=false;return;} var xx=20+(x.turn-lo)/span*600, yy=10+(1-x.wp)*140; path+=(open?" L":" M")+xx+","+yy; open=true;});
      sv.appendChild(s("path",{d:path,fill:"none",stroke:"currentColor","stroke-width":"2"}));
      section.appendChild(sv);
    } else section.appendChild(h("p", { class:"empty" }, "Win probability data unavailable."));
    section.appendChild(h("p", { class:"footnote" }, "Percentages use Math.round(100 × the cited probability)."));
    var rows=points.map(function(x,i){return h("tr",null,h("td",null,String(x.turn)," [wp["+i+"].turn]"), h("td",null,x.wp===null?"Unavailable":pct(x.wp),x.wp===null?"":" [wp["+i+"].wp]"));});
    section.appendChild(h("details",null,h("summary",null,"Turn-by-turn numbers"),h("div",{class:"table-scroll"},h("table",{class:"data"},h("thead",null,h("tr",null,h("th",null,"Turn"),h("th",null,"Win probability"))),h("tbody",null,rows)))));
    if (!p.disagreements.length) section.appendChild(h("p",{class:"empty"},"No decisions meet the existing disagreement rule."));
    else p.disagreements.forEach(function(d,i){
      section.appendChild(h("article",{class:"dis"},h("p",null,"Turn ",String(d.turn)," [disagreements["+i+"].turn]: ",d.chosen," [disagreements["+i+"].chosen]; engine choice ",d.best," [disagreements["+i+"].best]."),
        h("p",null,"Gap ",String(d.gap_pts)," points [disagreements["+i+"].gap_pts]; win chance before ",String(d.wp_before_pct),"% [disagreements["+i+"].wp_before_pct]."),
        h("p",null,"Chosen option ",pct(d.chosen_wp)," [disagreements["+i+"].chosen_wp]; engine option ",pct(d.best_wp)," [disagreements["+i+"].best_wp].")));
    });
    section.appendChild(h("details",{class:"ex-sources"},h("summary",null,"Where these numbers came from"),h("p",null,"Run record ",h("code",null,p.run_receipt_sha256)),h("ul",null,p.citations.map(function(c){return h("li",null,h("code",null,c.pointer)," = ",String(c.value)," · view ",h("code",null,c.view_sha256));}))));
    return section;
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
    ["evaluate", "Searching each of your decisions"], ["luck", "Measuring every chance event"]];
  function renderJob(id) {
    lastRender = null;
    if (STATIC) { show([h("p", null, "This page only keeps the saved showcases. Reviews of your own games run on the live app, linked from the home page.")], "Review"); return; }
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
      h("span", { class: "chip chip-side" }, (sharedPage || v.showcase ? "Reviewed side: " : "You: ") + (v.side === "p1" ? "Player 1" : "Player 2")),
      v.mode === "export" ? h("span", { class: "chip", title: "Sets from your team export" }, "your team export") : v.sets_note ? null :
        h("span", { class: "chip", title: "Smogon's most common set consistent with what the replay showed" }, "sets assumed"),
      v.private ? h("span", { class: "chip" }, "private · kept 24 h") : null);
    var shareBox = h("div", { class: "share-box", hidden: true });
    var shareBtn = (!STATIC && v.features && v.features.share) ? h("button", { type: "button", class: "btn btn-ghost btn-sm",
      "aria-expanded": "false", onclick: function () { toggleShare(id, v, shareBtn, shareBox); } }, "Share") : null;
    var setsNote = v.sets_note ? h("p", { class: "sets-note" }, h("span", { "aria-hidden": "true" }, "ⓘ "),
      (sharedPage || v.showcase ? "“You” means the reviewed player. Their sets (moves, items) are assumed: Smogon's most common set consistent with what the replay showed."
        : "Your sets are assumed (Smogon's most common set consistent with what the replay showed). Add your team export for exact numbers and to check decisions.")) : null;
    var head = h("div", null,
      h("div", { class: "head-top" }, h("a", { class: "back", href: location.pathname.indexOf("/r/") === 0 ? "/" : "#/" }, "← Review " + (location.pathname.indexOf("/r/") === 0 ? "your own game" : "another game")), shareBtn),
      h("div", { class: "review-head" }, h("h1", null, title), chips), setsNote, shareBox);

    var bridgeCard = h("section", { class: "card", "aria-labelledby": "bridge-title" },
      h("h2", { id: "bridge-title" }, "The whole game as one sum"),
      h("p", { class: "card-sub" }, "Start → luck → choices → result, in points of win chance. Each bar starts at zero."),
      bridgeWidget(v));

    var wpCard = h("section", { class: "card", "aria-labelledby": "wp-title" },
      h("h2", { id: "wp-title" }, "Your win chance, turn by turn"),
      h("p", { class: "card-sub" }, "At the start of each turn. ▲ / ▼ mark turns where luck moved it by 3 points or more; ◆ marks a clear disagreement with the engine."),
      wpChart(v));

    var oneDecision = oneDecisionCard(v);
    var disCard = h("section", { class: "card", "aria-labelledby": "dis-title" },
      h("h2", { id: "dis-title" }, "Biggest disagreements with the engine"),
      h("p", { class: "card-sub" }, "Up to three decisions made while the game was still open (win chance 20–80%) where the engine preferred another option by 5 points or more. Never added up: searches differ by a few points from run to run. Most offered disagreements aren't decisive: when 184 like these from bot games were rechecked with a deeper search, 139 came out within noise."),
      disagreementsList(v, id));

    var luckCard = h("section", { class: "card", "aria-labelledby": "luck-title" },
      h("h2", { id: "luck-title" }, "Biggest luck moments"),
      h("p", { class: "card-sub" }, "Chance outcomes measured against everything that could have happened on that turn."),
      luckMoments(v));

    var receipts = receiptsDrawer(id, v);
    // Desktop: the sum and Muse Glimmer's answer sit side by side above the fold. Phone: the CSS orders the cards
    // so the explanation comes straight after the sum.
    var exCard = explainCard(id, v);
    if (exCard) head.appendChild(h("button", { type: "button", class: "btn btn-ghost btn-sm jump-ex", onclick: function () {
      exCard.scrollIntoView({ behavior: "smooth", block: "start" });
    } }, "Jump to Muse Glimmer's explanation ↓"));
    var nodes = [head, h("div", { class: "grid-2 review-grid" },
      h("div", { class: "stack" }, bridgeCard, oneDecision, wpCard, disCard), h("div", { class: "stack" }, exCard, luckCard, aboutCard(v))), receipts];
    show(nodes, title, focus);
  }

  // ---- what to do with a review: one place to start, chosen by the largest gap on the list below
  function oneDecisionCard(v) {
    var top = (v.disagreements || []).slice().sort(function (x, y) { return y.gap_pts - x.gap_pts; })[0];
    if (!top) return null;
    return h("section", { class: "card one-decision", "aria-labelledby": "one-title" },
      h("h2", { id: "one-title" }, "The one decision to look at again"),
      h("p", null, h("b", null, "Turn " + top.turn + ". "), "You chose ", h("b", null, top.chosen), ". The engine preferred ", h("b", null, top.best),
        " by " + top.gap_pts + " points of win chance. Searches differ by a few points from run to run."),
      h("p", { class: "hint" }, (top.ev_caveat ? "This may depend on your exact EVs, so add your team export to check. " : "") +
        "It's the largest gap on the list below, and a place to start thinking, not a verdict: the engine can be wrong, especially about what you knew at the time."));
  }

  // ---- the bridge: four bars on one points axis, each anchored at zero (never a running total)
  function bridgeWidget(v, options) {
    var b = v.bridge;
    var subject = options && options.subject;
    if (!b || !b.available) {
      return h("div", { class: "empty" }, b && b.tie ? "This game ended in a tie, so there is no luck and choices sum." : "No sum is available for this game.");
    }
    var rows = [
      { key: "start", label: "Start", sub: "win chance at team preview", val: b.s, unit: "%", kind: "level" },
      { key: "luck", label: "Luck", sub: "crits, misses, rolls, speed ties", val: b.l, unit: " pts", kind: "signed" },
      { key: "choices", label: subject ? "Choices, HardReadBot and its opponent" : "Choices, yours and your opponent's", sub: "everything that wasn't dice", val: b.c, unit: " pts", kind: "signed" },
      { key: "result", label: "Result", sub: subject ? (b.result === "win" ? subject + " won" : subject + " lost") : (b.result === "win" ? "you won" : "you lost"), val: b.r, unit: "%", kind: "level" }];
    var maxAbs = 100;
    var grid = h("div", { class: "bridge", role: "list", "aria-label": "From start to result" });
    rows.forEach(function (r, i) {
      var lbl = h("div", { class: "b-label" }, r.label, h("small", null, r.sub));
      var barBox = h("div", { class: "b-bar", "aria-hidden": "true" });
      barBox.appendChild(signedBar(r.val, maxAbs, r.kind));
      var valTxt = r.kind === "level" ? r.val + "%" : signed(r.val);
      var val = h("div", { class: "b-val num" },
        r.kind === "signed" ? h("span", { class: "glyph", "aria-hidden": "true" }, glyph(r.val)) : null,
        valTxt, r.kind === "signed" ? h("span", { class: "unit" }, "pts") : null,
        h("span", { class: "sr-only" }, r.kind === "signed" ? " points, " + (subject ? (r.val > 0 ? "in HardReadBot’s favour" : r.val < 0 ? "against HardReadBot" : "neutral") : favour(r.val)) : ""));
      grid.appendChild(h("div", { class: "b-row", role: "listitem" }, lbl, barBox, val));
    });
    var ax = h("div", { class: "b-axis" });   // real text: an SVG axis here would squash its labels with the bars
    [[-100, MINUS + "100"], [-50, MINUS + "50"], [0, "0"], [50, "+50"], [100, "+100"]].forEach(function (t) {
      ax.appendChild(h("span", { class: t[0] === -100 ? "first" : t[0] === 100 ? "last" : null, style: "left:" + (50 + t[0] / 2) + "%" }, t[1]));
    });
    grid.appendChild(h("div", { class: "b-row b-axis-row", "aria-hidden": "true" }, h("div", { class: "b-label" }), h("div", { class: "b-axis-box" }, ax),
      h("div", { class: "muted b-val", style: "font-size:11px;font-weight:500;text-align:right" }, "points")));
    var eq = h("p", { class: "bridge-eq sum-line num" },
      h("b", null, b.s + "%"), " start ", b.l < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.l)), " luck ",
      b.c < 0 ? MINUS + " " : "+ ", h("b", null, Math.abs(b.c)), " choices = ", h("b", null, b.r + "%"), " (" + (b.result === "win" ? "win" : "loss") + ")");
    var legend = h("div", { class: "bridge-legend" },
      legendKey("pos", subject ? "▲ solid: in HardReadBot’s favour" : "▲ solid: in your favour"), legendKey("neg", subject ? "▼ striped: against HardReadBot" : "▼ striped: against you"),
      legendKey("level", "grey: a win chance, not a change"));
    var extra = [];
    if (b.tag) extra.push(h("div", { class: "notice" }, b.tag + "." + (b.tag_wp !== null && b.tag_wp !== undefined && b.tag_wp >= 0.05 && b.tag_wp <= 0.95 ?
      " The jump from " + pct(b.tag_wp) + " to the result is the resignation itself; it counts under choices." : "")));
    if (b.swingy) extra.push(h("div", { class: "notice" }, "Unusually swingy game: " +
      (Math.abs(b.L) > 0.5 ? (subject ? "luck alone moved HardReadBot’s win chance by more than 50 points" : "luck alone moved your win chance by more than 50 points") : "choices moved more than a whole game's worth of win chance") +
      ". The numbers still add up exactly; read the split with care."));
    return h("div", null, eq, grid, legend,
      h("p", { class: "footnote" }, subject ? "Choices = everything that wasn't dice: both players' moves and switches, what each side revealed, and resigning. It is not a score of HardReadBot’s play alone." : "Choices = everything that wasn't dice: both players' moves and switches, what each side revealed, and resigning. It is not a score of your play alone."),
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
    // The viewBox follows the space the chart gets (phone, one column, two columns), so labels stay near their real size.
    var vw = window.innerWidth;
    var W = vw < 560 ? 340 : vw > 860 ? 520 : 640, H = vw < 560 ? 240 : 280, m = { l: 40, r: 16, t: 22, b: 34 };
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
    wrap.addEventListener("pointerdown", function (e) { place(nearest(e.clientX)); });
    wrap.addEventListener("pointerleave", function (e) { if (e.pointerType !== "touch" && document.activeElement !== wrap) place(-1); });
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
    return h("div", null, wrap, live, legend, h("p", { class: "chart-help" }, "Tap or hover on the chart, or focus it and use ← →, to read each turn."),
      h("p", { class: "summary-text", style: "margin-top:12px" }, txt), table);
  }

  function disagreementsList(v, id) {
    var list = v.disagreements || [];
    if (!list.length) {
      var emptyText = v.mode === "draft"
        ? "No disagreements are shown for this review. Because its sets are assumed, the display leaves out choices that depend on them, and it only shows decisions made while the game was open."
        : "No disagreements met the display filter for this review.";
      return h("div", { class: "empty" }, emptyText);
    }
    var box = h("div", { class: "dis-list" });
    if (v.sets_note) box.appendChild(h("p", { class: "sets-note" }, h("span", { "aria-hidden": "true" }, "ⓘ "),
      (v.showcase || location.pathname.indexOf("/r/") === 0 ? "The reviewed player's sets" : "Your sets") +
      " are assumed (Smogon's most common set consistent with what the replay showed), so damage ranges, and with them some of these preferences, may differ with the real sets."));
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
        h("p", { class: "claim" }, "The engine prefers ", h("b", null, gerund(d.best)), " over ", h("b", null, gerund(d.chosen)), " by ", h("b", { class: "num" }, gap + " points"), " (searches differ by a few points)."),
        bars,
        opp ? h("p", { class: "opp" }, "The engine expected the opponent to pick: " + opp + ".") : null,
        d.ev_caveat ? h("p", { class: "ev-caveat" }, h("span", { "aria-hidden": "true" }, "⚠ "), v.ev_caveat_text || "May depend on your exact EVs: add your team export to check.") : null,
        readBlock(id, v, d)));
    });
    var note = v.mode === "draft" ? h("p", { class: "footnote" }, "Options that rely on a move that was never revealed are left out." +
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
        h("li", null, "Luck is an estimate. In two tests where one bot had a real information edge, the estimate shifted part of that edge, in opposite directions, and the cause isn't known. So a luck number doesn't rule out skill. It measures crits, misses, secondary effects, speed ties and KO-deciding damage rolls. It was checked on 500 engine-vs-engine games, where both seats' luck mirror each other."),
        h("li", null, "Choices is the rest of the swing, for both players together. A big negative number can come from your opponent's good play as much as from yours."),
        h("li", null, "Win chances come from the engine's search, mapped to real ladder outcomes; early turns carry little signal.")));
  }

  function receiptsDrawer(id, v) {
    var e = v.engine || {}, sec = v.seconds || {};
    var body = h("div", { class: "rc-body" },
      h("dl", null,
        h("dt", null, "Search"), h("dd", null, e.search || "–"),
        h("dt", null, "Luck"), h("dd", null, String(e.luck || "–").replace(/^luck v\d+ \([^)]*\):\s*/i, "")),
        h("dt", null, "Win-chance mapping"), h("dd", null, e.calibrator || "–"),
        h("dt", null, "Sum computed by"), h("dd", null, (v.bridge && v.bridge.source) || "–"),
        h("dt", null, "Disagreement filter"), h("dd", null, "win chance " + Math.round(100 * v.filter.wp_lo) + "–" + Math.round(100 * v.filter.wp_hi) + "%, gap ≥ " + Math.round(100 * v.filter.min_gap) + " points, at most 3, never summed"),
        h("dt", null, "Decisions searched"), h("dd", null, (v.counts && v.counts.decisions) + " (" + ((v.counts && v.counts.hidden) || 0) + " with a move the log does not show)"),
        h("dt", null, "Time"), h("dd", { class: "num" }, "rebuild " + sec.reconstruct + " s · search " + sec.evaluate + " s · luck " + sec.luck + " s"),
        h("dt", null, "Exact numbers"), h("dd", null, "start " + fmt(v.bridge.S) + " · luck " + fmt(v.bridge.L) + " · choices " + fmt(v.bridge.C))),
      h("div", null, h("b", null, "Limits"), h("ul", null,
        h("li", null, "“Choices” is what remains after estimated luck. It includes both players, later information and model error, so it isn't a skill score."),
        h("li", null, v.mode === "export" ? "Your sets come from your team export; the opponent's hidden sets are sampled." : "Without a team export, the reviewed player's sets are assumed, and that can change a verdict."),
        h("li", null, "Luck counts damage rolls only where a roll decides a KO. Small gaps between moves are within search noise."),
        h("li", null, "When the replay hides a move (fainted first, paralysis, sleep, flinch), the choice is unknown and its gap is given as a range."),
        h("li", null, "We haven't yet run a study with human players."))),
      (STATIC || PUBLIC_COPY) ? null : h("p", null, h("a", { href: "/api/review/" + encodeURIComponent(id) + "/receipts", target: "_blank", rel: "noopener" }, "Open the full engine output (JSON)")));
    return h("details", { class: "card receipts" }, h("summary", null, "Engine settings, timings and exact numbers"), body);
  }
  function fmt(x) { return x === null || x === undefined ? "–" : Number(x).toFixed(3); }

  // ------------------------------------------------------------------ explanation (Muse Glimmer, async)
  var MODE_TEXT = {
    glimmer_raw: "Written by Muse Glimmer-30B (base model, no fine-tuning). It passed the checker on the first draft. The checker doesn't judge the words.",
    glimmer_repaired: "Written by Muse Glimmer-30B (base model, no fine-tuning). It passed the checker after one repair. The checker doesn't judge the words.",
    template: "Fixed template: the words come from application code, not the model."
  };
  // The same model on the same game with no engine: what a one-prompt analyzer gives you.
  var PLAIN_NUM = /(\b\d+(?:\.\d+)?\s*(?:%|percent|points?|pts)\b|\b\d+(?:\.\d+)?%|\b\d+\s*(?:in|out of)\s*\d+\b)/gi;
  function plainCompare(v) {
    var pc = v.plain_compare;
    if (!pc || !pc.answer) return null;
    var p = h("p", { class: "ex-text plain-answer" }), parts = pc.answer.split(PLAIN_NUM), n = 0;
    parts.forEach(function (t, i) {
      if (i % 2) { n++; p.appendChild(h("mark", { class: "no-source", title: "No citation" }, t)); }
      else p.appendChild(document.createTextNode(t));
    });
    return h("details", { class: "ex-sources proof plain-compare", open: window.innerWidth >= 860 },
      h("summary", null, "Compare: the same model with a plain prompt and no engine"),
      h("p", { class: "muted" }, "Muse Glimmer given only the battle log and the question “" + pc.question + "”. " +
        (n ? "Nothing links the " + n + " marked number" + (n > 1 ? "s" : "") + " to a source: some may come from the log, others may be guesses, and you can't tell which." : "Nothing in it links back to a calculation.")), p,
      h("p", { class: "footnote" }, "Across our test, this setup put numbers in 27 of 30 answers, with nothing linking them to a source. Asked for the biggest piece of luck, it named the same turn as the engine in 3 of 10 games. That is a small sample, and the engine's estimate is not ground truth."));
  }
  // Short tab labels; the full question stays on the button (aria-label, title) and above the answer.
  var Q_SHORT = { q4: "Turning point", q1: "Luck or choices?", q2: "Biggest luck", q3: "Biggest disagreement" };
  var Q_ORDER = ["q4", "q1", "q2", "q3"];
  function explainCard(id, v) {
    var qs = v.questions || {};
    var body = h("div", { class: "explain-body", "aria-live": "polite" });
    var tabs = h("div", { class: "q-tabs", role: "group", "aria-label": "Questions" });
    var qIds = Q_ORDER.filter(function (q) { return qs[q]; }).concat(Object.keys(qs).filter(function (q) { return Q_ORDER.indexOf(q) < 0; }));
    // The turning point opens first. A static copy can't generate an answer, so there it opens on the first one it has.
    var current = (STATIC && qIds.filter(function (q) { return (v.explanations || {})[q]; })[0]) || qIds[0] || "q1";
    var workOpen = true;   // stays as the reader left it when they switch question
    var badge = h("span", { class: "badge-model" }, "Muse Glimmer");
    var card = h("section", { class: "card explain-card", "aria-labelledby": "ex-title" },
      h("div", { class: "ex-head" }, h("h2", { id: "ex-title" }, "In plain words"), badge),
      h("p", { class: "card-sub" }, "The engine did the analysis. Muse Glimmer, Meta's open model, explains it, but it can't write a number: it writes sentences with slots pointing at engine results, code fills in the values, and a checker rejects uncited numbers and citations of the wrong kind of value. It can't tell if a number is the right one, and it doesn't check the words."),
      tabs, body);
    var pcBtn = null;
    function select(q) {
      current = q;
      tabs.querySelectorAll(".q-tab").forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-q") === q ? "true" : "false"); });
      if (pcBtn) pcBtn.hidden = pcBtn.getAttribute("data-q") === q;
      load(q);
    }
    qIds.forEach(function (q) {
      tabs.appendChild(h("button", { type: "button", class: "q-tab", "data-q": q, "aria-pressed": q === current ? "true" : "false", "aria-label": qs[q], title: qs[q],
        onclick: function () { select(q); } }, Q_SHORT[q] || qs[q]));
    });
    function draw(ex, pending) {
      clear(body);
      var written = !pending && /^glimmer/.test(ex.final || "") && !ex.policy_rejected;
      badge.textContent = written ? "Written by Muse Glimmer" : "Fixed template";
      if (qs[current]) body.appendChild(h("p", { class: "ex-q" }, qs[current]));
      var r = ex.rendered || {};
      var p = h("p", { class: "ex-text" });
      var sourceBox = null, citeN = 0;
      function citeBtn(c) {
        var i = citeN++;
        return h("button", { type: "button", class: "cite", title: "from " + c.tool + " · " + c.field, "aria-label": c.shown + ": show its source",
          onclick: function () {
            if (!sourceBox) return;
            sourceBox.open = true;
            sourceBox.querySelectorAll("li").forEach(function (li, k) { li.classList.toggle("hit", k === i); });
            sourceBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
          } }, c.shown);
      }
      (r.sentences || []).forEach(function (snt, i) {
        if (i) p.appendChild(document.createTextNode(" "));
        var text = snt.text, pos = 0;
        (snt.citations || []).forEach(function (c) {
          var at = text.indexOf(c.shown, pos);
          if (at < 0) return;
          p.appendChild(document.createTextNode(text.slice(pos, at)));
          p.appendChild(citeBtn(c));
          pos = at + c.shown.length;
        });
        p.appendChild(document.createTextNode(text.slice(pos)));
      });
      var cites = [];
      (r.sentences || []).forEach(function (snt) {   // same order as the buttons above: only citations found in the text
        var text = snt.text, pos = 0;
        (snt.citations || []).forEach(function (c) {
          var at = text.indexOf(c.shown, pos);
          if (at < 0) return;
          cites.push(c);
          pos = at + c.shown.length;
        });
      });
      body.appendChild(p);
      if (cites.length) body.appendChild(h("p", { class: "ex-mode" }, "Highlighted values were filled in by code from engine results. Tap one to see its source."));
      body.appendChild(h("p", { class: "ex-mode" }, (pending ? "Fixed template, shown while Muse Glimmer writes." :
        ex.policy_rejected ? "Fixed template: Muse Glimmer's draft used wording HardRead does not allow (for example “cost” for choices)." : (MODE_TEXT[ex.final] || "")) +
        (ex.precomputed ? " Saved answer for this showcase, so you don't have to wait." : "")));
      if (pending) body.appendChild(h("p", { class: "muted ex-wait ex-pending" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("span", null, "Muse Glimmer is writing its own version…")));
      var work = pending ? null : workLog(ex);
      if (work) body.appendChild(work);
      if (cites.length) {
        sourceBox = h("details", { class: "ex-sources proof" }, h("summary", null, "Sources for each number (" + cites.length + ")"),
          h("ul", null, cites.map(function (c) {
            return h("li", null, h("b", null, c.shown), " ← ", h("code", null, c.tool + "." + c.field), " = " + JSON.stringify(c.value));
          })));
        body.appendChild(sourceBox);
      }
    }
    function workLog(ex) {
      var calls = (ex.trace || []), checks = (ex.checks || []);
      if (ex.final === "template" || (!calls.length && !checks.length)) return null;
      var items = calls.map(function (c) {
        var a = c.args || {}, arg = Object.keys(a).map(function (k) { return k + " " + a[k]; }).join(", ");
        return h("li", null, "Looked up ", h("code", null, c.name), arg ? " (" + arg + ")" : "");
      });
      checks.forEach(function (c, i) {
        items.push(h("li", { class: c.pass ? "check-pass" : "check-fail" },
          h("b", null, "Draft " + (i + 1) + (c.pass ? ": passed the checker" : ": rejected by the checker")),
          c.draft ? h("div", { class: "muted" }, c.draft) : null,
          (c.failures || []).map(function (f) { return h("div", { class: "check-why" }, f); })));
      });
      return h("details", { class: "ex-sources proof ex-work", open: workOpen, ontoggle: function (e) { workOpen = e.target.open; } },
        h("summary", null, "How Muse Glimmer worked this out (" + items.length + " steps)"), h("ol", null, items));
    }
    function waiting(msg) { clear(body); body.appendChild(h("p", { class: "muted ex-wait" }, h("span", { class: "spinner", "aria-hidden": "true" }), msg)); }
    function load(q) {
      var have = (v.explanations || {})[q];
      if (have) { draw(have); return; }
      if (STATIC) { clear(body); body.appendChild(h("p", { class: "muted" }, "This saved page doesn't include that answer.")); return; }
      clear(body);
      body.appendChild(h("p", { class: "muted" }, "Preparing the explanation…"));
      var offeredKeys = (v.disagreements || []).map(function (d) { return { turn: d.turn, k: d.k }; });
      api("/api/explain", { review_id: id, question: q, offered_keys: offeredKeys }).then(function (d) {
        if (!d.ok) { clear(body); body.appendChild(h("p", { class: "muted" }, d.error || "No explanation available.")); return; }
        if (d.cached) { refresh(q); return; }
        if (d.template) { draw(d.template, true); }
        else { waiting("Explanation job queued…"); }
        poll(d.job_id, q, !!d.template);
      });
    }
    function poll(jid, q, hasTemplate) {
      api("/api/job/" + jid).then(function (st) {
        if (current !== q || !document.body.contains(body)) return;
        if (st.state === "done" && st.result === "pending") {
          var wp = body.querySelector(".ex-pending");
          if (wp) wp.lastChild.textContent = "Explanation pending: the model is busy right now. Reload later for Muse Glimmer's version.";
          if (wp && wp.firstChild.classList) wp.firstChild.remove();
          return;
        }
        if (st.state === "done") { refresh(q); return; }
        if (st.state === "error") {
          if (!hasTemplate) { clear(body); body.appendChild(h("p", { class: "muted" }, "The explanation could not be written: " + (st.message || ""))); }
          else { var w0 = body.querySelector(".ex-pending"); if (w0) w0.textContent = "Muse Glimmer could not finish; the template above stands."; }
          return;
        }
        var msg = (st.pending || st.stage === "pending") ? (st.message || "Explanation pending") + "; the template stands meanwhile." :
          st.state === "queued" ? "Muse Glimmer is queued…" : "Muse Glimmer is looking through the engine's results… " + Math.round(st.elapsed || 0) + " s";
        var w = body.querySelector(".ex-pending");
        if (hasTemplate && w) w.lastChild.textContent = msg; else if (!hasTemplate) waiting(msg);
        setTimeout(function () { poll(jid, q, hasTemplate); }, 1500);
      });
    }
    function refresh(q) {
      getView(id).then(function (nv) { if (nv && nv.explanations) { v.explanations = nv.explanations; } if (current === q && v.explanations[q]) draw(v.explanations[q]); });
    }
    if (!v.features || v.features.explain === false) return null;
    var plain = plainCompare(v);
    if (plain) {
      var pcq = qIds.filter(function (q) { return qs[q] === v.plain_compare.question; })[0];   // it answers one question: let the reader see HardRead's answer to the same one
      if (pcq) {
        pcBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm", style: "margin-top:10px", hidden: current === pcq, onclick: function () { select(pcq); tabs.scrollIntoView({ block: "nearest", behavior: "smooth" }); } }, "See HardRead's answer to this question");
        pcBtn.setAttribute("data-q", pcq);
        plain.appendChild(pcBtn);
      }
      card.appendChild(plain);
    }
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
    // A review view has no current note/chip identity. Never show its most
    // recently cached read as if it answered the read the user is entering now.
    var have = STATIC ? (v.verdicts || {})[key] : null;
    var wrap = h("div", { class: "read-block" });
    if (STATIC) return have ? verdictCard(have) : null;
    if (!v.features || !v.features.verdict) return have ? verdictCard(have, id) : null;
    var panel = h("div", { class: "read-panel", hidden: true });
    var btn = h("button", { type: "button", class: "btn btn-ghost btn-sm", "aria-expanded": "false" }, have ? "Check again with another read" : "What were you thinking?");
    btn.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && !panel.firstChild) buildReadPanel(id, v, d, panel, wrap);
      if (open) { var t = panel.querySelector("textarea"); if (t) t.focus(); }
    });
    if (have) wrap.appendChild(verdictCard(have, id));
    wrap.appendChild(h("div", { class: "form-row", style: "margin-top:10px" }, btn));
    wrap.appendChild(panel);
    return wrap;
  }

  function buildReadPanel(id, v, d, panel, wrap) {
    var chips = [];
    var revision = 0;
    function invalidateRead() {
      revision += 1;
      var old = wrap.querySelector(".verdict");
      if (old) old.remove();
      if (v.verdicts) delete v.verdicts[d.turn + ":" + d.k];
      msg.textContent = "Your read changed. Check this decision again.";
    }
    var uid = "n" + d.turn + "_" + d.k;
    var note = h("textarea", { id: uid, rows: 3, class: "note", placeholder: "e.g. I thought Kingambit had Black Glasses and would Sucker Punch, so I switched" });
    var chipList = h("ul", { class: "chip-list", "aria-label": "Your read, as details" });
    var msg = h("p", { class: "hint", "aria-live": "polite" });
    var droppedBox = h("div", { class: "dropped-box" });
    var readBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, "Read my note");
    var checkBtn = h("button", { type: "button", class: "btn btn-primary btn-sm" }, "Check this decision");
    note.addEventListener("input", invalidateRead);
    function drawChips() {
      clear(chipList);
      if (!chips.length) chipList.appendChild(h("li", { class: "muted small" }, "No details yet. With none, the check uses the engine's own guess at their sets."));
      chips.forEach(function (c, i) {
        chipList.appendChild(h("li", { class: "chip chip-edit" }, chipText(c),
          h("button", { type: "button", class: "chip-x", "aria-label": "Remove " + chipText(c), onclick: function () {
            chips.splice(i, 1); invalidateRead(); drawChips();
            var xs = chipList.querySelectorAll(".chip-x");   // keep keyboard focus in the list
            (xs[Math.min(i, xs.length - 1)] || readBtn).focus();
          } }, "×")));
      });
    }
    readBtn.addEventListener("click", function () {
      if (!note.value.trim()) { msg.textContent = "Write what you were thinking first."; return; }
      readBtn.disabled = true; msg.textContent = "Reading your note…";
      var noteRevision = revision;
      api("/api/note/parse", { review_id: id, note: note.value }).then(function (r) {
        readBtn.disabled = false;
        if (noteRevision !== revision) { msg.textContent = "Your note changed. Read it again."; return; }
        if (!r.ok) { msg.textContent = r.error || "Could not read the note."; return; }
        if ((r.chips || []).length) { chips = chips.concat(r.chips); invalidateRead(); }
        drawChips();
        var dropped = r.dropped || [];
        msg.textContent = (r.chips || []).length + " detail(s) found by the " + (r.parser.indexOf("glimmer") === 0 ? "Muse Glimmer note reader" : "keyword reader") +
          (dropped.length ? "; " + dropped.length + " left out (see why below)" : "") + ". Edit them before checking.";
        clear(droppedBox);
        if (dropped.length) {
          droppedBox.appendChild(h("details", { class: "dropped" }, h("summary", null, "Left out of your read (" + dropped.length + ")"),
            h("ul", null, dropped.map(function (x) {
              var val = typeof x.value === "string" ? x.value : (x.value && (x.value.move || x.value.kind)) || "";
              var label = (FIELD_LABEL[x.field] || x.field) + (val ? ": " + val : "");
              return h("li", { title: x.reason || "" }, h("b", null, label), " — " + (x.reason || "not used"));
            }))));
        }
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
        invalidateRead(); val.value = ""; drawChips(); val.focus();
      });
      add.appendChild(h("div", { class: "add-row" }, spSel, fSel, val, dl, ok));
    });
    checkBtn.addEventListener("click", function () {
      checkBtn.disabled = true; msg.textContent = "Starting the check…";
      var submittedBelief = beliefFromChips(chips);
      var submittedRevision = revision;
      var sameSubmittedRead = function () { return revision === submittedRevision && JSON.stringify(beliefFromChips(chips)) === JSON.stringify(submittedBelief); };
      api("/api/verdict", { review_id: id, turn: d.turn, k: d.k, belief: submittedBelief }).then(function (r) {
        if (!sameSubmittedRead()) { checkBtn.disabled = false; msg.textContent = "Your read changed. Check this decision again."; return; }
        if (!r.ok) { checkBtn.disabled = false; msg.textContent = r.error || "Could not start the check."; return; }
        var finish = function (jobResult) {
          if (!r.identity_sha256 || (jobResult && jobResult.identity_sha256 !== r.identity_sha256)) {
            checkBtn.disabled = false;
            msg.textContent = "The check result did not match this read. Retry the check.";
            return;
          }
          if (!sameSubmittedRead()) {
            checkBtn.disabled = false;
            msg.textContent = "Your chips changed while the check was running. Check the current read again.";
            return;
          }
          var receiptUrl = "/api/review/" + encodeURIComponent(id) + "/verdict/" + d.turn + "/" + d.k +
            "?identity_sha256=" + encodeURIComponent(r.identity_sha256);
          api(receiptUrl).then(function (receipt) {
            checkBtn.disabled = false;
            if (!receipt.ok || receipt.identity_sha256 !== r.identity_sha256 || !receipt.raw || !receipt.card ||
                receipt.raw.cache_identity_sha256 !== r.identity_sha256 || !sameSubmittedRead()) {
              msg.textContent = "No check result matched this read. Retry the check.";
              return;
            }
            v.verdicts = v.verdicts || {};
            v.verdicts[d.turn + ":" + d.k] = receipt.card;
            var old = wrap.querySelector(".verdict"); var fresh = verdictCard(receipt.card, id, r.identity_sha256);
            if (old) old.replaceWith(fresh); else wrap.insertBefore(fresh, wrap.firstChild);
            msg.textContent = "Done."; fresh.querySelector("h3").focus();
          });
        };
        if (r.cached) { finish({ identity_sha256: r.identity_sha256 }); return; }
        (function poll() {
          api("/api/job/" + r.job_id).then(function (st) {
            if (!sameSubmittedRead()) { checkBtn.disabled = false; msg.textContent = "Your read changed. Check this decision again."; return; }
            if (st.state === "done") { finish(st.result); return; }
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
      chipList, droppedBox, add, h("div", { class: "form-row" }, checkBtn), msg,
      h("p", { class: "footnote" }, "We check your read against the engine. Spreads and EVs are not used, and we don't yet replay the turn under the move you expected.")]);
  }

  var VERDICT_GLYPH = { "Misplay": "▼", "Misread": "◐", "Defensible": "✓", "Within noise": "≈" };
  function readCheckBlock(rc) {
    if (!rc || (rc.status !== "checked" && rc.status !== "no_opponent_choice")) return null;
    return h("p", { class: "small read-check", "data-read-check-status": rc.status },
      rc.sentence || "", h("br"), h("span", { class: "muted" }, rc.scope || ""));
  }

  function verdictCard(c, reviewId, identitySha) {
    var cls = { "Misplay": "v-misplay", "Misread": "v-misread", "Defensible": "v-ok", "Within noise": "v-noise" }[c.verdict] || "v-noise";
    var box = h("div", { class: "verdict " + cls },
      h("h3", { tabindex: "-1" }, h("span", { class: "v-badge" }, h("span", { "aria-hidden": "true" }, VERDICT_GLYPH[c.verdict] + " "), c.verdict)),
      h("p", { class: "v-head" }, c.headline),
      runsPlot(c),
      !c.has_stated_hypothesis ? h("p", { class: "small muted" }, "No testable read was recorded. This card compares the moves using the engine's assumptions; it does not check what you expected the opponent to do.") :
        c.stated && c.stated.length ? h("p", { class: "small muted" }, "Your read: " + c.stated.join("; ") + ".") : null,
      (c.contradictions || []).map(function (t) { return h("p", { class: "small notice-inline" }, t + ". That detail was left out."); }),
      c.third_option ? h("p", { class: "small" }, c.third_option) : null,
      readCheckBlock(c.read_check),
      c.has_expected_action && !c.read_check ? h("p", { class: "small read-check" }, "The expected opponent action could not be checked for this decision.") : null,
      h("p", { class: "footnote" }, (c.runs || 3) + " independent runs on the same sampled worlds · " + (c.worlds || 4) + " worlds × " + (c.iterations || 20000).toLocaleString("en") + " iterations each" + (c.seconds ? " · " + Math.round(c.seconds) + " s" : "") + ". ",
        reviewId && identitySha && !STATIC ? h("a", { href: "/api/review/" + encodeURIComponent(reviewId) + "/verdict/" + c.turn + "/" + c.k +
          "?identity_sha256=" + encodeURIComponent(identitySha), target: "_blank", rel: "noopener" }, "Full output (JSON)") : null));
    return box;
  }
  function runsPlot(c) {
    var W = 520, rowH = 34, top = 8, lab = 92, R = 20;
    var rows = [[c.has_retained_set_hypothesis ? "Your read" : "Engine baseline", c.delta_U], ["Reference", c.delta_R]];
    var H = top + rows.length * rowH + 22;
    var plotW = W - lab - 14;
    function X(p) { var q = Math.max(-R, Math.min(R, p)); return lab + (q + R) / (2 * R) * plotW; }
    var sv = s("svg", { viewBox: "0 0 " + W + " " + H, class: "runs-plot", role: "img",
      "aria-label": c.x + " minus your move, in points, three runs per row: " + (c.has_retained_set_hypothesis ? "under your read " : "Engine baseline ") + (c.delta_U || []).join(", ") + "; under " + c.reference_label + " " + (c.delta_R || []).join(", ") + ". Threshold " + c.margin_pts + " points." });
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
        h("p", { class: "hint" }, "The card shows numbers only: no names, no chat, no notes." + (/^(localhost|127\.|\[::1\])/.test(location.hostname) ? " This copy runs on your machine, so the link works only here." : ""))]);
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
      h("p", null, "Chess players get an engine review after every game. Pokémon has luck calculators and win-chance tools, but none we found splits a game into start, luck and both players' choices using search over hidden sets. You can't see the opponent's moves and items, and dice decide many turns. HardRead reviews a finished Gen 9 OU game anyway, and estimates how much was luck."),
      h("p", null, "The analysis comes from Foul Play (GPL-3.0), an open-source search engine by pmariglia that won the 2025 PokéAgent Gen 9 OU track, running on poke-engine. For each decision it samples teams your opponent could have and searches each of them; for each turn it goes through the chance outcomes and measures the one that happened against the rest."),
      h("h2", null, "How Muse Glimmer is used"),
      h("p", null, "Muse Glimmer-30B, Meta's open model, writes the explanations as an agent: it chooses which read-only engine tools to call, then submits an explanation. It cannot write a number. It writes sentences with empty slots that point at engine results, code fills in the values, and a checker rejects uncited numbers and citations of the wrong kind of value. It gets one repair, then a labeled template takes over. Passing means each number comes from an engine result and has the right type. It doesn't mean the explanation is true: a real number can still be the wrong one for its sentence, and plain-word claims aren't checked."),
      h("p", null, "On 28 October's versions of three questions, 60 answers about 20 games (base model, no fine-tuning), 41 passed the checker (numbers and required wording) first time and 59 after one repair. The newer turning-point question, tuned on those games, passed 9 of 14 first time and 14 of 14 after repair. Sentences flagged in a blind review: 3 of 60. We ran that review because the checker doesn't judge the words. It was done by an AI reviewer; the human label check is still pending, so we don't claim the prose is accurate."),
      h("p", null, "For comparison, the same model with only the battle log and no engine put numbers in 27 of 30 answers, with nothing linking them to a source. That is a small sample, and the engine's estimate is not ground truth."),
      h("p", null, "Separately, we fine-tuned Glimmer to play. It won 178 of 200 games against the Abyssal heuristic bot. Those teams were partly seen in development, so this doesn't measure general strength. The fine-tuned model doesn't write the explanations."),
      h("h2", null, "What the numbers mean"),
      h("ul", null,
        h("li", null, "Start: your win chance at team preview."),
        h("li", null, "Luck: the sum over turns of what the dice gave you, compared with the average outcome of the same turn."),
        h("li", null, "Choices, yours and your opponent's: the rest of the way to the result. The three always add up to the result."),
        h("li", null, "Disagreements: at most three decisions in open positions where the engine preferred another option by 5 points or more. They are never added up and are not a skill score."),
        h("li", null, "Without your team export, your own sets are assumed, and that can change a verdict. We haven't yet run a study with human players.")),
      h("h2", { id: "privacy" }, "Privacy"),
      h("ul", null,
        h("li", null, "HardRead runs on its own server: your machine if you started it yourself, the public copy's own server if live review is online. The open Muse Glimmer weights run in the same setup, so nothing goes to an outside AI service. The only other outside request is fetching the replay link you paste, from replay.pokemonshowdown.com."),
        h("li", null, "Player names and chat are removed before the review runs; the review says “You” and “Opponent”. Names are shown once, on the side picker, and never stored."),
        h("li", null, PUBLIC_COPY ? "This public copy only takes public replay links. Reviews are kept 14 days at most, so a reload is instant, and may be cleared sooner." : "Reviews of public replays are kept 14 days so a reload is instant; private (password) replays 24 hours, with no share page and no card."),
        h("li", null, "The pasted log is deleted when the review finishes, unless you added your team export: then the name-free log and the export stay with the review, for the same time, so you can check decisions. A note you write about a decision is read by the same server and not stored; the details you confirm are kept with that check."),
        h("li", null, "Sharing is opt-in: a share page and card exist only after you press Share, and they show numbers only (no names, chat or notes)."),
        h("li", null, "No accounts, no tracking, no cookies. Your theme and the 18+ confirmation are remembered in this browser only.")),
      provenanceSection(),
      h("h2", null, "Built during the hackathon"),
      h("p", null, "HardRead was built during the Meta Global AI Developer Hackathon (October 26 to November 4, 2026). The repository's BUILT_DURING_HACKATHON.md lists what existed before the kickoff and what was built after it, commit by commit."),
      h("h2", null, "Fair play"),
      h("p", null, "HardRead only reviews finished games. Links to live battle rooms are refused."),
      h("h2", null, "Not affiliated"),
      h("p", null, "HardRead is a fan-made research project. It is not affiliated with, endorsed or sponsored by Nintendo, The Pokémon Company, Game Freak or Pokémon Showdown. Pokémon and character names are trademarks of their owners and are used only to refer to the game. No official art, sprites, logos or music are used."),
      h("p", null, h("a", { href: "#/" }, "← Review a game")))], "About");
  }

  var MODE_LABEL = { auto: "Muse Glimmer when the model is available, otherwise fixed templates", loop: "Muse Glimmer agent with the checker", prefetch: "Muse Glimmer, prepared in advance", off: "Explanations turned off" };
  function provenanceSection() {
    var box = h("div", null, h("p", { class: "muted" }, STATIC ? "Engine and model details are in the GitHub repository." : "Loading…"));
    if (!STATIC) api("/api/provenance").then(function (p) {
      clear(box);
      if (!p.ok) { box.appendChild(h("p", { class: "muted" }, "Not available.")); return; }
      var ex = p.explainer || {}, en = p.engine || {}, lv = p.live || {};
      var rows = [
        ["Explanation mode", lv.explain_mode === "template" ? "Fixed application templates" : (MODE_LABEL[lv.explain_mode] || lv.explain_mode || "–")],
        ["Available model", (ex.model || "–") + (ex.license ? " (" + ex.license + ")" : "")],
        ["Weights file", (ex.gguf || "–") + (ex.gguf_bytes ? " · " + (ex.gguf_bytes / 1e9).toFixed(1) + " GB" : "")],
        ["Weights SHA-256", ex.gguf_sha256 || "–"],
        ["Served with", String(ex.server || "–").replace(/\s*\(Metal\)/, "") + (ex.server_flags ? " · " + ex.server_flags : "")],
        ["Model awake now", lv.explainer_reachable ? "yes" : "no (saved showcase answers still work)"],
        ["Search engine", (en.name || "–") + (en.license ? " (" + en.license + ")" : "") + (lv.foulplay_commit ? " · commit " + lv.foulplay_commit : "")],
        ["This app", lv.hardread_commit ? "commit " + lv.hardread_commit : "–"]];
      box.appendChild(h("dl", { class: "prov" }, rows.map(function (r) { return [h("dt", null, r[0]), h("dd", { class: r[0].indexOf("SHA") >= 0 ? "mono" : null }, r[1])]; })));
    });
    return h("section", { "aria-labelledby": "prov-title" }, h("h2", { id: "prov-title" }, "Provenance"),
      h("p", null, "What is running behind this page. The explanation mode says how this server produces explanations. In template mode, fixed application code supplies the words."), box);
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
