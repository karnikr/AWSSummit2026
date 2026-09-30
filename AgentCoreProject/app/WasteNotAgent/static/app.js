// WasteNot demo frontend: form -> /rescue -> map (ordered multi-stop route) +
// impact + prominent agent reasoning narrative.
const map = L.map("map").setView([24.72, 46.68], 14);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);

let layer = L.layerGroup().addTo(map);
let donors = [];

function pickupMarker(lat, lon, name) {
  const icon = L.divIcon({ html: `<div class="pickup-marker">🍽️</div>`, className: "", iconSize: [30, 30] });
  return L.marker([lat, lon], { icon }).bindPopup(`<b>Pickup:</b> ${name}`);
}
function stopMarker(lat, lon, n, popup) {
  const icon = L.divIcon({ html: `<div class="stop-marker">${n}</div>`, className: "", iconSize: [26, 26] });
  return L.marker([lat, lon], { icon }).bindPopup(popup);
}

async function loadDonors() {
  try {
    const res = await fetch("/donors");
    donors = await res.json();
    const sel = document.getElementById("pickup");
    sel.innerHTML = "";
    donors.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.donor_id;
      opt.textContent = `${d.name} (${d.type})`;
      sel.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to load donors:", err);
  }
}

function renderMap(plan) {
  layer.clearLayers();
  const pts = [];
  const p = plan.pickup;
  if (p) {
    pickupMarker(p.lat, p.lon, p.name).addTo(layer);
    pts.push([p.lat, p.lon]);
  }

  // Guard: route/ordered_stops may be missing or empty (e.g. no eligible match).
  const stops = (plan.route && plan.route.ordered_stops) || [];
  const path = p ? [[p.lat, p.lon]] : [];
  stops.forEach((s, i) => {
    const popup =
      `<b>${t("stop")} ${i + 1}: ${s.name}</b><br/>${s.allocated}<br/>` +
      `diet: ${s.dietary_needs || "any"}<br/>leg ${s.leg_km} km`;
    stopMarker(s.latitude, s.longitude, i + 1, popup).addTo(layer);
    path.push([s.latitude, s.longitude]);
    pts.push([s.latitude, s.longitude]);
  });

  if (path.length > 1) {
    L.polyline(path, { color: "#1f6f54", weight: 4, opacity: 0.75 }).addTo(layer);
  }
  if (pts.length) map.fitBounds(pts, { padding: [50, 50] });
}

function renderImpact(plan) {
  const box = document.getElementById("impact");
  box.classList.remove("hidden");
  const i = plan.impact || {};
  const route = plan.route || { ordered_stops: [], total_km: 0, total_minutes: 0 };
  const setNum = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = (val === undefined || val === null) ? 0 : val;
  };
  setNum("m-meals", i.meals_rescued);
  setNum("m-co2", i.co2_avoided_kg);
  setNum("m-cost", i.cost_avoided_units);
  setNum("m-dist", route.total_km);
  setNum("m-min", route.total_minutes);

  const d = plan.dispatch || {};
  const dl = document.getElementById("driver-line");
  if (d.status === "dispatched") {
    dl.textContent = `🚗 ${d.driver_name} (${d.vehicle}) · ${route.ordered_stops.length} stop(s)`;
  } else {
    dl.textContent = t("noDriver");
  }

  const sl = document.getElementById("split-line");
  const parts = [];
  if (plan.split_across > 1) parts.push(t("splitAcross", plan.split_across));
  if (plan.unallocated) parts.push(t("unallocated", plan.unallocated));
  if (plan.excluded && plan.excluded.length)
    parts.push(t("excluded", plan.excluded.length));
  sl.textContent = parts.join(" · ");
}

document.getElementById("rescue-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  btn.textContent = t("runningBtn");
  document.getElementById("thinking").classList.remove("hidden");
  document.getElementById("narrative").textContent = t("running");

  const donor = donors.find((d) => d.donor_id === document.getElementById("pickup").value);
  if (!donor) {
    document.getElementById("narrative").textContent = "No pickup location selected.";
    btn.disabled = false;
    btn.textContent = t("run");
    document.getElementById("thinking").classList.add("hidden");
    return;
  }

  const payload = {
    food_type: document.getElementById("food_type").value,
    dietary_info: document.getElementById("dietary_info").value,
    quantity: parseInt(document.getElementById("quantity").value, 10),
    hours_available: parseFloat(document.getElementById("hours").value),
    pickup_name: donor.name,
    pickup_lat: donor.lat,
    pickup_lon: donor.lon,
    lang: (typeof CURRENT_LANG !== "undefined" ? CURRENT_LANG : "en"),
  };

  try {
    const res = await fetch("/rescue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`Server ${res.status}: ${errText}`);
    }
    const plan = await res.json();
    renderMap(plan);
    renderImpact(plan);
    const narrative = plan.narrative || "(No narrative returned.)";
    document.getElementById("narrative").innerHTML =
      window.marked ? marked.parse(narrative) : narrative;
  } catch (err) {
    console.error(err);
    document.getElementById("narrative").textContent = "Error: " + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = t("run");
    document.getElementById("thinking").classList.add("hidden");
  }
});

loadDonors();
