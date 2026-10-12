// Results browser for the Aberdeen Praya Road study. Data come from docs/data/*.json,
// written by hk_corridor/export_dashboard.py.

const COLORS = { observed: "#1d1d1b", simulated: "#2a78d6", east: "#2a78d6", west: "#e0702b" };
const PLAN_LABEL = {
  fixed: "Fixed-time (netconvert)", actuated: "Actuated (SUMO default)", webster: "Webster",
  common_cycle: "Common cycle", coordinated: "Common cycle + offsets",
};
const HELD_OUT = new Set(["AID01101", "AID01102", "AID01223", "TDSAPR20001"]);
const LAYOUT = { margin: { l: 56, r: 16, t: 24, b: 48 }, paper_bgcolor: "#fff", plot_bgcolor: "#fff",
                 font: { size: 12, color: "#1d1d1b" } };

async function load(name) {
  const response = await fetch(`data/${name}.json`);
  if (!response.ok) throw new Error(`could not load data/${name}.json`);
  return response.json();
}

function card(value, label) {
  return `<div class="card"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}

function showCards(summary) {
  const row = (test, params) => summary.validation.find((r) => r.test === test && r.params === params);
  const cal = row("calibration", "calibrated");
  const hold = row("holdout", "calibrated");
  const naive = row("holdout", "naive forecast");
  const t = summary.holdout_tests;
  document.getElementById("cards").innerHTML = [
    card(`${cal.geh_pass_pct.toFixed(1)}%`, "Calibration day: 15-min counts with GEH < 5 (target 85%)"),
    card(`${hold.interior_15min_pct.toFixed(1)}% vs ${naive.interior_15min_pct.toFixed(1)}%`,
         "Holdout, 4 interior detectors: model vs naive scaling (15 min)"),
    card(`${hold.interior_60min_pct.toFixed(1)}% vs ${naive.interior_60min_pct.toFixed(1)}%`,
         "Same at 60 min"),
    card(`${t.perfect_model_ceiling_15min_pct.toFixed(0)}%`,
         "Best a perfect model could score at 15 min, given the missing data"),
    card(`${summary.saturation.sat_flow_veh_h_lane.toLocaleString()}`,
         "Queue discharge in the fitted model, veh/h/lane"),
  ].join("");
}

function showMap(network) {
  const map = L.map("map", { scrollWheelZoom: false });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  // Leaflet needs a view before vector layers are added.
  map.fitBounds(L.latLngBounds(network.corridor.flat()), { padding: [20, 20] });
  network.corridor.forEach((line) => L.polyline(line, { color: "#444", weight: 4 }).addTo(map));
  network.signals.forEach((s) => {
    L.circleMarker([s.lat, s.lon], {
      radius: 6, color: "#333", weight: 1, fillOpacity: 1,
      fillColor: s.kind === "junction" ? "#333" : "#bbb",
    }).bindTooltip(s.kind === "junction" ? "Signalised junction" : "Pedestrian crossing").addTo(map);
  });
  network.detectors.forEach((d) => {
    L.circleMarker([d.lat, d.lon], { radius: 8, color: "#fff", weight: 2, fillOpacity: 1, fillColor: COLORS[d.direction] })
      .bindTooltip(`${d.id} (${d.direction}bound)<br>${d.road}`).addTo(map);
  });
}

function showCounts(counts) {
  const testSel = document.getElementById("test");
  const detSel = document.getElementById("detector");
  const detectors = Object.keys(counts.calibration);
  detSel.innerHTML = detectors.map((d) => `<option value="${d}">${d}</option>`).join("");

  function draw() {
    const test = testSel.value;
    const d = counts[test][detSel.value];
    const low = d.periods.map((p, i) => (p < 8 ? i : -1)).filter((i) => i >= 0);
    const pass = d.geh.filter((g) => g < 5).length;
    const tag = test === "holdout" && HELD_OUT.has(detSel.value) ? "held out, predicted" : "used to build demand";
    Plotly.react("counts", [
      { x: d.clock, y: d.observed, name: "Observed (TD)", mode: "lines", line: { color: COLORS.observed, width: 2 } },
      { x: d.clock, y: d.simulated, name: "Simulated (SUMO)", mode: "lines", line: { color: COLORS.simulated, width: 2, dash: "dash" } },
      { x: low.map((i) => d.clock[i]), y: low.map((i) => d.observed[i]), name: "< 8 of 30 periods", mode: "markers",
        marker: { color: "#fff", size: 8, line: { color: COLORS.observed, width: 1.5 } } },
    ], {
      ...LAYOUT,
      title: { text: `${detSel.value}, ${tag}: GEH < 5 in ${pass} of ${d.geh.length} bins`, font: { size: 13 }, x: 0.02 },
      yaxis: { title: "veh/h", rangemode: "tozero", gridcolor: "#eee" },
      xaxis: { gridcolor: "#fff" }, legend: { orientation: "h", y: -0.2 },
    }, { responsive: true, displayModeBar: false });
  }
  testSel.addEventListener("change", draw);
  detSel.addEventListener("change", draw);
  draw();
}

function showGrid(grid) {
  const taus = [...new Set(grid.map((r) => r.tau))].sort((a, b) => a - b);
  const gaps = [...new Set(grid.map((r) => r.minGap))].sort((a, b) => a - b);
  const cell = (tau, gap) => grid.find((r) => r.tau === tau && r.minGap === gap);
  const z = gaps.map((g) => taus.map((t) => (cell(t, g).all_valid ? cell(t, g).geh_pass_pct : null)));
  const text = gaps.map((g) => taus.map((t) => {
    const c = cell(t, g);
    return `${c.geh_pass_pct.toFixed(1)}%${c.all_valid ? "" : " (invalid)"}`;
  }));
  const chosen = grid.find((r) => r.selected);
  Plotly.react("grid", [{
    type: "heatmap", x: taus.map(String), y: gaps.map(String), z, text, texttemplate: "%{text}",
    colorscale: [[0, "#f3efe6"], [1, "#2a78d6"]], zmin: 60, zmax: 100, showscale: false, hoverongaps: false,
  }], {
    ...LAYOUT,
    xaxis: { title: "tau (s)", type: "category" }, yaxis: { title: "minGap (m)", type: "category" },
    shapes: [{ type: "rect", xref: "x", yref: "y", line: { color: "#1d1d1b", width: 2 },
               x0: taus.indexOf(chosen.tau) - 0.5, x1: taus.indexOf(chosen.tau) + 0.5,
               y0: gaps.indexOf(chosen.minGap) - 0.5, y1: gaps.indexOf(chosen.minGap) + 0.5 }],
  }, { responsive: true, displayModeBar: false });
}

function showSignals(rows) {
  const days = [...new Set(rows.map((r) => r.day))];
  const plans = Object.keys(PLAN_LABEL);
  const traces = days.map((day, k) => {
    const r = plans.map((p) => rows.find((x) => x.day === day && x.plan === p));
    return {
      type: "bar", name: `${day.slice(6)} Sep${k ? " (holdout demand)" : ""}`,
      x: plans.map((p) => PLAN_LABEL[p]), y: r.map((x) => x.mean_delay_s),
      error_y: { type: "data", array: r.map((x) => x.delay_ci95_s), color: "#999" },
      marker: {
        color: k ? "#9cc1ec" : "#2a78d6",
        pattern: { shape: r.map((x) => (x.valid_runs < 5 ? "/" : "")), fillmode: "overlay",
                   fgcolor: "#ffffff", fgopacity: 0.8, size: 8 },
      },
      hovertext: r.map((x) => `${x.valid_runs} of 5 runs valid${x.valid_runs < 5 ? ": not comparable" : ""}`),
    };
  });
  Plotly.react("signals", traces, {
    ...LAYOUT, barmode: "group", yaxis: { title: "s per vehicle", gridcolor: "#eee" },
    legend: { orientation: "h", y: -0.25 },
  }, { responsive: true, displayModeBar: false });
}

async function main() {
  try {
    const [network, counts, grid, signals, summary] = await Promise.all(
      ["network", "counts", "calibration_grid", "signals", "summary"].map(load));
    const sections = [["cards", () => showCards(summary)], ["map", () => showMap(network)],
                      ["counts", () => showCounts(counts)], ["grid", () => showGrid(grid)],
                      ["signals", () => showSignals(signals)]];
    for (const [id, draw] of sections) {
      try {
        draw();
      } catch (err) {
        document.getElementById(id).textContent = `Could not draw this section: ${err.message}`;
      }
    }
    document.getElementById("footer").textContent = `Data exported at commit ${summary.git_commit}.`;
  } catch (err) {
    document.getElementById("cards").textContent = `Could not load the results: ${err.message}`;
  }
}

main();
