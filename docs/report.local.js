/* limitorderbook report: latency chart wiring and the order book replay.
   Reads <script id="report-data"> written by scripts/build_report.py.
   Loaded after kit/report.js and kit/charts.js (all deferred, so in order). */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var r = Math.round;

  function latency(D) {
    if (!D.latency || !$("#lat-plot")) return;
    var books = [
      { key: "FlatBook", name: "FlatBook", color: "var(--series-1)" },
      { key: "MapBook", name: "MapBook", color: "var(--series-2)" }
    ];
    var rows = D.ops.map(function (op) {
      var s = {};
      books.forEach(function (b) {
        var p = D.latency[b.key][op.key];
        s[b.key] = { lo: p.p50, mid: p.p50, hi: p["p99.9"], mark: p.p99,
          tip: "p50 " + r(p.p50) + " ns · p99 " + r(p.p99) + " ns · p99.9 " + r(p["p99.9"]) + " ns" };
      });
      return { label: op.label, series: s };
    });
    var h2 = $("#latency");
    RKCharts.dotRange("#lat-plot", {
      rows: rows, series: books, xmin: 0,
      ticks: D.gridStep ? { step: D.gridStep } : null,
      format: function (v) { return String(r(v)); },
      xLabel: "Latency (ns)" + (D.gridStep ? ", gridlines on clock ticks" : ""),
      title: (h2 ? h2.textContent + ". " : "") + "Dot-and-range chart of p50, p99 and p99.9 latency per operation for FlatBook and MapBook; values are in the table view."
    });
    var trows = [];
    D.ops.forEach(function (op) {
      books.forEach(function (b) {
        var p = D.latency[b.key][op.key];
        trows.push({ op: op.label, book: b.name, p50: p.p50, p90: p.p90, p99: p.p99, p999: p["p99.9"], max: p.max, n: p.count });
      });
    });
    var n = function (v) { return r(v).toLocaleString("en-US"); };
    RKCharts.table("#lat-table", {
      caption: "Per-message latency in ns, one run per book. Max is a single sample and mostly reflects scheduling noise.",
      columns: [
        { key: "op", label: "Operation" }, { key: "book", label: "Book", align: "left" },
        { key: "p50", label: "p50", format: n }, { key: "p90", label: "p90", format: n },
        { key: "p99", label: "p99", format: n }, { key: "p999", label: "p99.9", format: n },
        { key: "max", label: "max", format: n }, { key: "n", label: "Messages", format: n }
      ],
      rows: trows
    });
  }

  /* ------------------------------------------------------------ replay */
  var TYPES = { LIMIT: "Limit", IOC: "IOC", FOK: "FOK", POST: "Post-only", MARKET: "Market" };
  var REASONS = {
    unknown_id: "no resting order has that id (already filled or cancelled)",
    would_cross: "a post-only order would have traded on arrival",
    zero_quantity: "quantity is zero", invalid_price: "invalid price", duplicate_id: "that id is already resting"
  };

  function parse(cmd) {
    var t = cmd.trim().split(/\s+/);
    if (t[0] === "ADD") return { kind: "ADD", id: t[1], side: t[2] === "B" ? "buy" : "sell", price: +t[3], qty: +t[4], type: t[5] || "LIMIT" };
    if (t[0] === "CXL") return { kind: "CXL", id: t[1] };
    if (t[0] === "MOD") return { kind: "MOD", id: t[1], qty: +t[2], price: +t[3] };
    return { kind: t[0] };
  }

  function describeOp(cmd) {
    var c = parse(cmd);
    if (c.kind === "ADD") return TYPES[c.type] + " " + c.side + " " + c.qty + (c.type === "MARKET" ? "" : " @ " + c.price) + " · order #" + c.id;
    if (c.kind === "CXL") return "Cancel order #" + c.id;
    if (c.kind === "MOD") return "Modify order #" + c.id + " to " + c.qty + " @ " + c.price;
    return cmd;
  }

  function describeResult(step) {
    var c = parse(step.cmd), ev = step.events, parts = [];
    var fills = ev.filter(function (e) { return e.type === "fill"; });
    var traded = fills.reduce(function (a, f) { return a + f.qty; }, 0);
    if (fills.length) parts.push("Traded " + traded + ": " + fills.map(function (f) { return f.qty + " @ " + f.price + " with #" + f.resting_id; }).join(", ") + ".");
    ev.forEach(function (e) {
      if (e.type === "rejected") parts.push("Rejected: " + (REASONS[e.reason] || e.reason.replace(/_/g, " ")) + ".");
      if (e.type === "cancelled") parts.push(c.kind === "CXL" ? "Removed " + e.qty + " @ " + e.price + " from the book." : "Cancelled the unfilled " + e.qty + ".");
    });
    var acc = ev.filter(function (e) { return e.type === "accepted"; })[0];
    var cancelled = ev.some(function (e) { return e.type === "cancelled"; });
    if (acc && !cancelled) {
      var rest = acc.qty - traded;
      if (c.kind === "MOD" && !fills.length) parts.push("Order #" + c.id + " now rests " + acc.qty + " @ " + acc.price + ".");
      else if (rest > 0) parts.push("Rested " + rest + " @ " + acc.price + ".");
    }
    return parts.join(" ") || "No change to the book.";
  }

  function replay(D) {
    var steps = D.replay, demo = $("#demo");
    if (!steps || !demo) return;
    var ladder = $("#ladder"), scr = $("#scrubber"), play = $("#play"), prevB = $("#prev"), nextB = $("#next"),
      tradeB = $("#next-trade"), resetB = $("#reset"), cnt = $("#step-count"), opT = $("#op-text"), resT = $("#res-text"),
      hist = $("#history"), chips = document.querySelectorAll(".otypes li");
    var start = D.replayStart || 0, cur = start, timer = null, delay = 700, maxQty = 1, last = steps.length - 1;
    steps.forEach(function (s) { s.bids.concat(s.asks).forEach(function (l) { maxQty = Math.max(maxQty, l[1]); }); });
    var isTrade = function (s) { return s.events.some(function (e) { return e.type === "fill"; }); };
    var nextTrade = function () { for (var i = cur + 1; i <= last; i++) if (isTrade(steps[i])) return i; return -1; };

    function cell(side, lvl) {
      if (!lvl) return '<td class="' + side + '"></td>';
      var w = Math.max(2, lvl[1] / maxQty * 100);
      return '<td class="' + side + '"><div><span class="bar" style="width:calc((100% - 52px) * ' + (w / 100).toFixed(3) + ')"></span><span class="q">' + lvl[1] + " (" + lvl[2] + ")</span></div></td>";
    }

    function render() {
      var step = steps[cur], prev = cur > 0 ? steps[cur - 1] : { bids: [], asks: [] };
      var bids = {}, asks = {}, old = {}, traded = {};
      step.bids.forEach(function (l) { bids[l[0]] = l; });
      step.asks.forEach(function (l) { asks[l[0]] = l; });
      prev.bids.forEach(function (l) { old["b" + l[0]] = l[1]; });
      prev.asks.forEach(function (l) { old["a" + l[0]] = l[1]; });
      step.events.forEach(function (e) { if (e.type === "fill") traded[e.price] = 1; });
      var prices = step.bids.concat(step.asks).map(function (l) { return l[0]; }).concat(Object.keys(traded).map(Number));
      if (!prices.length) {
        ladder.innerHTML = '<p class="ladder-empty">The book is empty. Press Step to submit the next order.</p>';
      } else {
        var hi = Math.max.apply(null, prices), lo = Math.min.apply(null, prices);
        var bb = step.bids.length ? step.bids[0][0] : null, ba = step.asks.length ? step.asks[0][0] : null;
        var h = '<table class="ladder"><caption class="vh">Book depth after step ' + (cur + 1) + ', one row per price tick</caption>' +
          '<colgroup><col><col class="px"><col></colgroup><thead><tr><th scope="col" class="bid-h">▲ Bids · qty (orders)</th>' +
          '<th scope="col">Price</th><th scope="col" class="ask-h">Asks · qty (orders) ▼</th></tr></thead><tbody>';
        for (var p = hi; p >= lo; p--) {
          var b = bids[p], a = asks[p], cls = [];
          cls.push(a ? "ask" : b ? "bid" : "empty");
          if (bb !== null && ba !== null && p > bb && p < ba) cls.push("gap");
          if (p === bb || p === ba) cls.push("best");
          if (traded[p]) cls.push("traded");
          if ((b ? b[1] : 0) !== (old["b" + p] || 0) || (a ? a[1] : 0) !== (old["a" + p] || 0)) cls.push("chg");
          h += '<tr class="' + cls.join(" ") + '">' + cell("bid", b) + '<th scope="row">' + p + "</th>" + cell("ask", a) + "</tr>";
          if (p === ba && bb !== null) {
            var gap = ba - bb;
            h += '<tr class="spread"><td colspan="3">Spread · ' + gap + " tick" + (gap === 1 ? "" : "s") + "</td></tr>";
          }
        }
        ladder.innerHTML = h + "</tbody></table>";
      }
      var c = parse(step.cmd);
      opT.innerHTML = "";
      opT.textContent = describeOp(step.cmd);
      var raw = document.createElement("span"); raw.className = "raw"; raw.textContent = step.cmd; opT.appendChild(raw);
      resT.textContent = describeResult(step);
      chips.forEach(function (li) { li.classList.toggle("on", c.kind === "ADD" && li.dataset.type === c.type); });
      var items = "";
      for (var i = cur; i >= Math.max(0, cur - 4); i--) items += '<li><span class="n">' + (i + 1) + "</span><span>" + describeOp(steps[i].cmd) + "</span></li>";
      hist.innerHTML = items;
      cnt.textContent = "Step " + (cur + 1) + " of " + steps.length;
      scr.value = String(cur);
      scr.setAttribute("aria-valuetext", "Step " + (cur + 1) + " of " + steps.length);
      prevB.disabled = cur === 0;
      nextB.disabled = cur === last;
      tradeB.disabled = nextTrade() < 0;
      if (!timer) play.textContent = cur === last ? "Replay" : "Play";
      ladder.setAttribute("data-step", String(cur));
    }

    function go(i) { cur = Math.min(last, Math.max(0, i)); render(); }
    function pause() { if (timer) { clearTimeout(timer); timer = null; } play.setAttribute("aria-pressed", "false"); render(); }
    function tick() {
      if (cur >= last) { pause(); return; }
      go(cur + 1);
      timer = setTimeout(tick, delay);
    }
    function start_() {
      if (cur >= last) go(start);
      play.setAttribute("aria-pressed", "true"); play.textContent = "Pause";
      timer = setTimeout(tick, delay);
    }
    var toggle = function () { if (timer) pause(); else start_(); };
    var manual = function (i) { if (timer) pause(); go(i); };

    play.addEventListener("click", toggle);
    prevB.addEventListener("click", function () { manual(cur - 1); });
    nextB.addEventListener("click", function () { manual(cur + 1); });
    resetB.addEventListener("click", function () { manual(start); });
    tradeB.addEventListener("click", function () { var i = nextTrade(); if (i >= 0) manual(i); });
    scr.addEventListener("input", function () { manual(Number(scr.value)); });
    if (window.RK && RK.segmented) RK.segmented("#speed", function (v) { delay = Number(v); });

    /* Shortcuts only while focus is inside the demo (WCAG 2.1.4). Native
       controls keep their own keys: buttons take Space, the slider takes arrows. */
    demo.addEventListener("keydown", function (e) {
      var tag = e.target.tagName;
      if (tag === "INPUT" || (tag === "BUTTON" && (e.key === " " || e.key === "Enter"))) return;
      if (e.target.closest && e.target.closest("#speed")) return;
      var k = e.key;
      if (k === " ") toggle();
      else if (k === "ArrowRight") manual(cur + 1);
      else if (k === "ArrowLeft") manual(cur - 1);
      else if (k === "Home") manual(start);
      else if (k === "End") manual(last);
      else return;
      e.preventDefault();
    });

    if ("IntersectionObserver" in window)
      new IntersectionObserver(function (en) { if (!en[0].isIntersecting && timer) pause(); }, { threshold: 0.25 }).observe(demo);
    render();
  }

  function init() {
    var D = window.RK && RK.data("report-data");
    if (!D) return;
    latency(D);
    replay(D);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
