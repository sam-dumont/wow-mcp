/* The live parts of the person's own pages (wowstats.account): on /start
   the state (set up, or is it working?), the install command, the addon
   ticks and the folded extras; on /settings the toggles. Writes are
   JSON POSTs to /api/me/...; nothing here builds HTML from a string. */
(function () {
  "use strict";

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "cls") n.className = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function get(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; });
    });
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body || {}) }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; });
    });
  }
  function widget(id) { return document.querySelector('[data-widget="' + id + '"]'); }
  function msg(box, text, ok) {
    var m = box.querySelector(".msg") || box.appendChild(el("p", { cls: "msg" }));
    m.className = "msg " + (ok ? "ok" : "err");
    m.textContent = text;
  }
  function copyRow(text) {
    var code = el("code", { text: text });
    var b = el("button", { cls: "ghost", type: "button", text: "Copy", onclick: function () {
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
        b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1500);
      }, function () {
        var r = document.createRange(); r.selectNodeContents(code);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      });
    } });
    return el("div", { cls: "cmd" }, [code, b]);
  }

  var ME = null;

  var countdown = null;
  function showCommands(out, c, note) {
    out.textContent = "";
    out.appendChild(el("p", { cls: "os", text: "Windows (PowerShell)" }));
    out.appendChild(copyRow(c.windows));
    out.appendChild(el("p", { cls: "os", text: "Mac (Terminal)" }));
    out.appendChild(copyRow(c.mac));
    if (note) out.appendChild(el("p", { cls: "pending", text: note }));
  }
  // The last command handed out, kept across a reload of the page's state:
  // a reset re-reads /api/me, which redraws the step (or Settings), and the
  // new command must still be there.
  var lastCmd = null;
  function showFresh(out) {
    if (!lastCmd || Date.now() >= lastCmd.until) return false;
    showCommands(out, lastCmd.c, "");
    var t = el("p", { cls: "pending" });
    out.appendChild(t);
    clearInterval(countdown);
    var tick = function () {
      var left = Math.round((lastCmd.until - Date.now()) / 1000);
      t.textContent = left > 0 ? "Works once, for " + Math.floor(left / 60) + " min " + (left % 60) + " s." :
        "Expired: click the button above for a new command.";
      if (left <= 0) clearInterval(countdown);
    };
    tick();
    countdown = setInterval(tick, 1000);
    return true;
  }
  function codeButton(box, out, label, reset) {
    var btn = el("button", { cls: reset ? "ghost" : "", type: "button", text: label });
    btn.addEventListener("click", function () {
      if (reset && !window.confirm("Your current install stops sending until you run the new command. Reset?")) return;
      btn.disabled = true;
      post("/api/me/install-code", reset ? { reset: true } : {}).then(function (c) {
        lastCmd = { c: c, until: Date.now() + c.expires_in * 1000 };
        btn.disabled = false;
        if (reset) load(); else showFresh(out);
      }, function (e) { btn.disabled = false; msg(box, e.message, false); });
    });
    return btn;
  }
  // ---------------------------------------------------------------- start

  // The page has one state at a time (wowstats.account.live_start_page):
  // name (no address: the owner has a name to fix), new (not installed),
  // wait (installed in the last 15 minutes, nothing
  // yet), none (installed, nothing ever arrived), ok (data arrives).
  var WAIT_MS = 15 * 60 * 1000, QUIET_MS = 3 * 86400 * 1000;
  var STATE = null, STATUS = null;

  function fill(name) { return document.querySelector('[data-fill="' + name + '"]'); }

  function stateOf(st) {
    if (!ME.slug) return "name";
    if (st && st.events > 0) return "ok";
    if (!ME.shipper_set_at) return "new";
    return Date.now() - Date.parse(ME.shipper_set_at) < WAIT_MS ? "wait" : "none";
  }

  // Mac or Windows, and the AI app: the reader's pick (remembered), else
  // a guess. Parts for one value carry data-os / data-app.
  var OS = null;
  function guessOs() {
    var ua = navigator.userAgent || "";
    return /Macintosh/.test(ua) && !/iPhone|iPad/.test(ua) && !(navigator.maxTouchPoints > 1) ? "mac" : "windows";
  }
  function remembered(kind, ok) {
    try { var v = localStorage.getItem("wowstats-" + kind); return ok.indexOf(v) >= 0 ? v : null; }
    catch (e) { return null; }
  }
  function pick(kind, v, keep) {
    if (kind === "os") OS = v;
    if (keep) { try { localStorage.setItem("wowstats-" + kind, v); } catch (e) { /* no storage */ } }
    document.querySelectorAll("[data-" + kind + "]").forEach(function (n) { n.hidden = n.getAttribute("data-" + kind) !== v; });
    document.querySelectorAll("[data-pick-" + kind + "]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-pick-" + kind) === v));
    });
  }
  function pickOs(v, keep) { pick("os", v, keep); }

  function ago(iso) {
    var s = (Date.now() - Date.parse(iso)) / 1000;
    if (!(s >= 60)) return "just now";
    if (s < 3600) { var m = Math.round(s / 60); return m + (m === 1 ? " minute" : " minutes") + " ago"; }
    if (s < 2 * 86400) { var h = Math.round(s / 3600); return h + (h === 1 ? " hour" : " hours") + " ago"; }
    return Math.round(s / 86400) + " days ago";
  }

  function charsLine(chars) {
    var names = chars.map(function (c) { return c.char.split("-")[0] + (c.level ? " (" + c.level + ")" : ""); });
    if (names.length > 3) names = names.slice(0, 3).concat([(chars.length - 3) + " more"]);
    if (names.length < 2) return names.join("");
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  function okHero(st) {
    var head = fill("headline"), last = fill("last"), warn = fill("warn");
    var quiet = st.last_event && Date.now() - Date.parse(st.last_event) > QUIET_MS;
    var chars = st.chars || [];
    if (quiet) {
      head.textContent = "Last data " + ago(st.last_event);
      head.className = "lamp quiet";
      last.textContent = "";
      ["Played since then: in WoW, type ", el("kbd", { text: "/reload" }), " or log out, and it shows up within 5 minutes."]
        .forEach(function (x) { last.appendChild(typeof x === "string" ? document.createTextNode(x) : x); });
    } else {
      head.textContent = "It works";
      head.className = "lamp good";
      last.textContent = (st.last_event ? "Last data " + ago(st.last_event) : "Your data arrives") +
        (chars.length ? ", for " + charsLine(chars) + "." : ".");
    }
    var a = st.addons || {};
    // Installed and not on: off, or never switched on (null: the switch was never saved).
    var off = !!(st.setup && st.setup.addons && "recorder" in st.setup.addons && st.setup.recorder_on !== true);
    var fix = a.recorder === false || off ? [(off ? (st.setup.recorder_on === false ? "Polestar Recorder is off"
                                                   : "Polestar Recorder is installed but not switched on yet")
                                                : "Polestar Recorder sends nothing") + ": in WoW, type ", el("kbd", { text: "/psrec on" }),
                                      ", then ", el("kbd", { text: "/reload" }), "."] :
      a.polestar === false ? ["Polestar sends nothing: tick it in AddOns at character select, then ",
                              el("kbd", { text: "/reload" }), "."] : null;
    warn.textContent = "";
    (fix || []).forEach(function (x) { warn.appendChild(typeof x === "string" ? document.createTextNode(x) : x); });
    warn.hidden = !fix;
    // A shipper from before the auto-update: the update command, once.
    fill("update-once").hidden = !st.update_once;
    // One that should have updated itself by now and didn't: the same command.
    var behind = st.update_by_hand, hand = fill("update-by-hand");
    hand.textContent = "";
    if (behind) {
      hand.appendChild(document.createTextNode("The program on your computer is version " + behind.have +
        " and this site has " + behind.server + ": it didn't update itself (its settings may say " +
        "auto_update = false). "));
      hand.appendChild(el("a", { href: "#f-update", "data-open": "f-update", text: "Run the update command" }));
      hand.appendChild(document.createTextNode(" to catch up: it keeps your settings."));
    }
    hand.hidden = !behind;
  }

  // No address: the friends list gave this person a name that is taken or
  // reserved. Friends never pick one (the owner sets it), so there is
  // nothing for them to do but wait; the page picks the fix up by itself.
  function nameMissing() {
    if (ME.owner) return "Your page has no address: set WOW_OWNER_SLUG and restart wow-stats.";
    return "Your page's address needs a fix from the person who runs this site. " +
      "Nothing to do on your side: this page carries on once it's fixed.";
  }

  // The install command: asked for as the page opens (one code, 15
  // minutes, once), shown for the reader's OS; a new one once it expires.
  var asked = false;
  function heroCommand(box) {
    box.textContent = "";
    ["mac", "windows"].forEach(function (k) {
      var row = copyRow(lastCmd.c[k]);
      row.className = "cmd hero-cmd";
      row.setAttribute("data-os", k);
      box.appendChild(row);
    });
    var t = el("p", { cls: "pending" });
    box.appendChild(t);
    pickOs(OS);
    clearInterval(countdown);
    var tick = function () {
      var left = lastCmd.until - Date.now();
      if (left > 0) {
        var m = Math.ceil(left / 60000);
        t.textContent = "Works once, in the next " + m + (m === 1 ? " minute" : " minutes") + ". No admin rights needed.";
        return;
      }
      clearInterval(countdown);
      box.textContent = "";
      box.appendChild(el("button", { type: "button", text: "Get a new command", onclick: function () { askCode(box); } }));
    };
    tick();
    countdown = setInterval(tick, 5000);
  }
  function askCode(box) {
    asked = true;
    box.textContent = "";
    box.appendChild(el("p", { cls: "pending", text: "Making your command" }));
    post("/api/me/install-code", {}).then(function (c) {
      lastCmd = { c: c, until: Date.now() + c.expires_in * 1000 };
      heroCommand(box);
    }, function (e) { box.textContent = ""; msg(box, e.message, false); });
  }
  function installBox() {
    var box = widget("install");
    if (!box) return;
    if (lastCmd && Date.now() < lastCmd.until) { if (!box.querySelector(".cmd")) heroCommand(box); return; }
    if (!asked) askCode(box);
  }

  // A missing-data line's fix names its addons ("Add Questie or
  // Altoholic."): each name links to the addon's page (as on the pages).
  function fixParts(m) {
    var rest = m.fix || "", out = [];
    for (;;) {
      var hit = null, at = -1;
      (m.addons || []).forEach(function (a) {
        var i = a.url ? rest.indexOf(a.name) : -1;
        if (i >= 0 && (at < 0 || i < at)) { hit = a; at = i; }
      });
      if (!hit) break;
      out.push(rest.slice(0, at), el("a", { href: hit.url, target: "_blank", rel: "noopener noreferrer",
                                            referrerpolicy: "no-referrer", text: hit.name }));
      rest = rest.slice(at + hit.name.length);
    }
    out.push(rest);
    return out;
  }

  // The addon list is drawn once; a later answer only moves its dots, its
  // marks and its counts, so an open fold stays as the reader left it.
  // The marks come from the shipper's setup report (status.setup: which
  // addons are installed, with their version, and the Recorder's switch);
  // the lines under the list from status.missing (what the pages can't
  // show yet, and the fix).
  function addonsBox(st) {
    var box = widget("addons");
    if (!box) return;
    var ul = box.querySelector("ul.addons"), note = box.querySelector("p.pending"), gaps = box.querySelector("ul.gaps");
    if (!ul) {
      ul = el("ul", { cls: "addons" });
      ME.addons.forEach(function (a) {
        ul.appendChild(el("li", { "data-addon": a.id }, [
          a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", referrerpolicy: "no-referrer", text: a.name })
                : el("strong", { text: a.name }),
          a.required ? " (needed)" : "",
          el("span", { cls: "inst", hidden: "" }),
          el("span", { cls: "what", text: a.what }),
          a.step ? el("span", { cls: "step1", text: a.step }) : "",
          el("span", { cls: "note", hidden: "" })]));
      });
      gaps = el("ul", { cls: "gaps", hidden: "" });
      note = el("p", { cls: "pending" });
      box.appendChild(ul);
      box.appendChild(gaps);
      box.appendChild(note);
    }
    var on = st && st.events > 0, n = 0, rep = st && st.setup, have = rep && rep.addons;
    // The summary counts the rows below (owner, 2026-11-20): sending, a
    // needed one not sending, an optional one installed but quiet, an
    // optional one to add. Never one "missing" for both kinds.
    var need = 0, quiet = 0, opt = 0, byId = {};
    ME.addons.forEach(function (a) { byId[a.id] = a; });
    ul.querySelectorAll("li[data-addon]").forEach(function (li) {
      var id = li.getAttribute("data-addon"), a = byId[id] || {}, known = st && st.addons && id in st.addons,
          seen = known && st.addons[id];
      var there = !!have && Object.prototype.hasOwnProperty.call(have, id);
      if (seen) n += 1;
      else if (on && known) {
        if (a.required) need += 1;
        else if (there) quiet += 1;
        else opt += 1;
      }
      li.className = on && known ? (seen ? "seen" : "unseen") : "";
      var inst = li.querySelector(".inst");
      inst.hidden = !have;
      // An optional addon not installed is a choice, not an error.
      inst.className = "inst " + (there ? "yes" : a.required ? "no" : "opt");
      inst.textContent = !have ? "" : there ? "✓ " + (have[id] || "installed") : a.required ? "✗ not installed" : "optional, not installed";
      li.querySelector(".what").hidden = there;
      // The one step left: none once its data arrives; once installed, only
      // what comes after installing (the Recorder's switch has its own line).
      var step = li.querySelector(".step1");
      if (step) {
        step.hidden = !!seen || (there && id === "recorder");
        step.textContent = there && a.then ? a.then : a.step || "";
      }
      var rec = li.querySelector(".note"), off = id === "recorder" && there && rep.recorder_on !== true;
      rec.hidden = !(id === "recorder" && there);
      rec.className = "note " + (off ? "no" : "yes");
      rec.textContent = "";
      if (!rec.hidden) {
        (off ? [(rep.recorder_on === false ? "Recording is off" : "Installed, not switched on yet") + ": in WoW, type ", el("kbd", { text: "/psrec on" }), ", then ", el("kbd", { text: "/reload" }), "."]
             : ["Recording is on."]).forEach(function (x) { rec.appendChild(typeof x === "string" ? document.createTextNode(x) : x); });
      }
    });
    // "Nothing arrived yet" is the hero's own line; the rest, once each.
    var miss = ((st && st.missing) || []).filter(function (m) { return m.id !== "nothing"; });
    gaps.textContent = "";
    miss.forEach(function (m) {
      // Whose gap it is: on the account page a line without a name reads
      // as the main character's (a fresh alt's empty level times did).
      gaps.appendChild(el("li", { "data-kind": m.id }, [el("strong", { text: "Missing" + (m.who ? " for " + m.who : "") + ": " + m.what + "." })]
        .concat([" "], fixParts(m))));
    });
    gaps.hidden = !miss.length;
    note.textContent = (on ? "Green: its data arrives. " : "") +
      "Install them in the CurseForge app (the install command adds Polestar Recorder), then tick them in AddOns at character select, once per character.";
    var sum = fill("addons-sum");
    if (sum) {
      var parts = [n + " sending"];
      if (need) parts.push(need + " needed, not sending");
      if (quiet) parts.push(quiet + " installed, not sending yet");
      if (opt) parts.push(opt + " optional to add");
      sum.textContent = on ? "Your addons: " + parts.join(" · ") : "The addons";
    }
  }

  function resetBox() {
    var box = widget("reset");
    if (!box) return;
    box.textContent = "";
    var out = el("div");
    if (ME.shipper_set_at) {
      box.appendChild(el("p", { text: "New computer, or you think your upload password leaked: reset it, then run the new command on the computer you play on. This install stops sending at once." }));
      box.appendChild(codeButton(box, out, "Reset my upload password", true));
    } else if (!showFresh(out)) {
      box.appendChild(codeButton(box, out, "Get my install command", false));
    }
    box.appendChild(out);
  }

  function aiBox() {
    var apps = [].map.call(document.querySelectorAll("[data-pick-app]"), function (b) { return b.getAttribute("data-pick-app"); });
    if (apps.length) pick("app", remembered("app", apps) || apps[0], false);
  }

  // A state change shows its hero and folds; nothing else is redrawn.
  function show(state) {
    if (state === STATE) return;
    STATE = state;
    document.body.setAttribute("data-state", state);
    document.querySelectorAll("[data-in]").forEach(function (n) {
      n.hidden = n.getAttribute("data-in").split(" ").indexOf(state) < 0;
    });
    if (state === "name" && ME.owner) {
      fill("name-head").textContent = "Your page has no address";
      fill("name-lead").textContent = "Set WOW_OWNER_SLUG and restart wow-stats.";
    }
    if (state === "new") installBox();
    resetBox();
  }

  function render(st) {
    var state = stateOf(st);
    show(state);
    if (state === "ok") okHero(st);
    addonsBox(st);
  }

  // Polling (owner, 2026-11-19: the refresh got in the way): every 10 s
  // while waiting for the first data, every 3 minutes once it arrives,
  // never in a hidden tab, and a redraw only when the answer changed.
  var timer = null, busy = false, lastSig = null;
  function visible() { return document.visibilityState === "visible"; }
  function schedule() {
    clearTimeout(timer);
    if (visible()) timer = setTimeout(poll, STATE === "ok" ? 180000 : 10000);
  }
  function poll() {
    clearTimeout(timer);
    if (!visible() || busy) return;
    busy = true;
    // Not installed yet: re-read the account too, so the page moves on by
    // itself once the command ran.
    var me = ME.shipper_set_at ? Promise.resolve(ME) : get("/api/me");
    me.then(function (m) { ME = m; return get("/api/me/status"); }).then(function (st) {
      var sig = JSON.stringify(st) + "|" + (ME.shipper_set_at || "") + "|" + (ME.slug || "");
      if (sig === lastSig) return;
      lastSig = sig;
      STATUS = st;
      render(st);
    }).catch(function () {
      // The store down: what /api/me alone says; an install waits for the next try.
      if (!ME.shipper_set_at) { render(null); return; }
      var c = document.querySelector(".checking");
      if (c) c.textContent = "Can't check your data right now. This page tries again by itself.";
    }).then(function () { busy = false; schedule(); });
  }
  document.addEventListener("visibilitychange", function () {
    if (!ME || !document.body.classList.contains("start")) return;
    if (visible()) poll(); else clearTimeout(timer);
  });

  function start() {
    if (OS === null) pickOs(remembered("os", ["mac", "windows"]) || guessOs(), false);
    aiBox();
    STATE = null;
    lastSig = null;
    if (STATUS) render(STATUS);
    poll();
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-pick-os], [data-pick-app], [data-copy], [data-open]") : null;
    if (!t) return;
    if (t.hasAttribute("data-pick-os")) { pickOs(t.getAttribute("data-pick-os"), true); return; }
    if (t.hasAttribute("data-pick-app")) { pick("app", t.getAttribute("data-pick-app"), true); return; }
    if (t.hasAttribute("data-open")) {
      e.preventDefault();
      var d = document.getElementById(t.getAttribute("data-open"));
      for (var p = d; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
      if (d) d.scrollIntoView({ block: "start" });
      return;
    }
    var text = t.getAttribute("data-copy");
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
      t.textContent = "Copied"; setTimeout(function () { t.textContent = "Copy"; }, 1500);
    }, function () {
      var code = t.parentElement.querySelector("code");
      var r = document.createRange(); r.selectNodeContents(code);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    });
  });

  // ---------------------------------------------------------------- settings

  function settings() {
    var box = widget("settings");
    box.textContent = "";
    function save(body, note) {
      // Reload first: load() redraws the box, which dropped a message shown before it.
      return post("/api/me/settings", body).then(function (saved) {
        Object.assign(ME, saved);
        return load();
      }).then(function () {
        msg(box, note || "Saved.", true);
      }, function (e) { settings(); msg(box, e.message, false); });
    }
    function toggle(label, help, checked, onchange) {
      var cb = el("input", { type: "checkbox" });
      cb.checked = !!checked;
      cb.addEventListener("change", function () { cb.disabled = true; onchange(cb.checked); });
      return el("label", { cls: "toggle" }, [cb, el("span", {}, [label, el("small", { text: help })])]);
    }
    box.appendChild(toggle("Battle.net data to my AI",
      (ME.bnet_to_model ? "On. Your connected AI can read " : "Off. Turn this on to let your connected AI read ") +
      "your characters' Battle.net profiles (talents, worn gear and guild), the WoW Token price and ruleset status. " +
      "Your chat may keep earlier answers.",
      ME.bnet_to_model, function (v) { save({ bnet_to_model: v }); }));
    if (!ME.slug) {
      box.appendChild(el("p", { text: nameMissing() }));
      return;
    }
    var live = ME.site_live === true ? "live" : ME.site_live === false ?
      "not live yet: the owner adds the address, usually within a day. Nothing to do; this turns to \"live\"" : "";
    box.appendChild(toggle("Public page on", "Anyone with the address sees your characters' card, climb and dungeons, " +
      "with when you cleared each one and your time per level. Nothing else: no gold, no other players, no position." + (ME.site && ME.public_on ? " " + ME.site + (live ? " (" + live + ")" : "") : ""),
      ME.public_on, function (v) { save({ public_on: v }); }));
    box.appendChild(toggle("Classic character models", "The 3D view opens on the original models, like the game's " +
      "Standard Definition option, instead of the updated ones. Anyone can still switch in the view. The face and " +
      "hair are approximate: the game only saves the updated look.", ME.models === "sd",
      function (v) { save({ models: v ? "sd" : "hd" }); }));
    if (ME.chars.length) {
      var t = el("table", { cls: "chars" }, [el("tr", {}, [el("th", { text: "Character" }),
        el("th", { text: "Hide from public page" }), el("th", { text: "Hide from zogzog link" })])]);
      ME.chars.forEach(function (c) {
        function cell(list) {
          var cb = el("input", { type: "checkbox", "aria-label": c + ": " + list });
          cb.checked = ME[list].indexOf(c) >= 0;
          cb.addEventListener("change", function () {
            var cur = ME[list].filter(function (x) { return x !== c; });
            if (cb.checked) cur.push(c);
            var b = {}; b[list] = cur; save(b);
          });
          return el("td", { cls: "c" }, [cb]);
        }
        t.appendChild(el("tr", {}, [el("td", { text: c }), cell("hidden_public"), cell("hidden_zogzog")]));
      });
      box.appendChild(el("h2", { text: "Characters" }));
      box.appendChild(t);
    }
    box.appendChild(el("h2", { text: "Your zogzog link" }));
    box.appendChild(el("p", { cls: "pending", text: "The full content (journal, gear advice, prices) for the friends " +
      "you give it to. Whoever has the link can open it. Never other players or your position." }));
    if (ME.zogzog) box.appendChild(copyRow(ME.zogzog));
    box.appendChild(el("button", { cls: "ghost", type: "button", text: "Make a new link (the old one stops working)",
      onclick: function () { save({ new_zogzog_link: true }, "New link made: the old one stops working at the next update."); } }));
    // The upload password: reset here too, not only on the start page
    // (a friend moving to another computer looks in Settings).
    box.appendChild(el("h2", { text: "Your upload password" }));
    var out = el("div");
    if (ME.shipper_set_at) {
      box.appendChild(el("p", { cls: "pending", text: "Moving to another computer, or you think it leaked: reset it, " +
        "then run the new install command on the computer you play on. The current install stops sending at once." }));
      box.appendChild(codeButton(box, out, "Reset my upload password", true));
    } else if (lastCmd && Date.now() < lastCmd.until) {
      box.appendChild(el("p", { cls: "pending", text: "Reset. Run this on the computer you play on:" }));
      showFresh(out);
    } else {
      box.appendChild(el("p", { cls: "pending" }, ["Not installed yet: your install command is on ",
        el("a", { href: "/start", text: "Start" }), "."]));
    }
    box.appendChild(out);
  }

  function load() {
    return get("/api/me").then(function (me) {
      ME = me;
      if (document.body.classList.contains("start")) start();
      if (document.body.classList.contains("settings")) settings();
    });
  }

  document.addEventListener("DOMContentLoaded", load);
})();
