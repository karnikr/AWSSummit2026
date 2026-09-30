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
}

function renderMap(plan) {
  layer.clearLayers();
  const pts = [];
  const p = plan.pickup;
  pickupMarker(p.lat, p.lon, p.name).addTo(layer);
  pts.push([p.lat, p.lon]);

  // Build the ordered route path: pickup -> stop1 -> stop2 ...
  const path = [[p.lat, p.lon]];
  plan.route.ordered_stops.forEach((s, i) => {
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
  const i = plan.impact;
  document.getElementById("m-meals").textContent = i.meals_rescued;
  document.getElementById("m-co2").textContent = i.co2_avoided_kg;
  document.getElementById("m-cost").textContent = i.cost_avoided_units;
  document.getElementById("m-dist").textContent = plan.route.total_km;
  document.getElementById("m-min").textContent = plan.route.total_minutes;

  const d = plan.dispatch;
  const dl = document.getElementById("driver-line");
  if (d.status === "dispatched") {
    dl.textContent = `🚗 ${d.driver_name} (${d.vehicle}) · ${plan.route.ordered_stops.length} stop(s)`;
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
  btn.textContent = "Running agent…";
  document.getElementById("thinking").classList.remove("hidden");
  document.getElementById("narrative").textContent = t("running");

  const donor = donors.find((d) => d.donor_id === document.getElementById("pickup").value);
  const payload = {
    food_type: document.getElementById("food_type").value,
    dietary_info: document.getElementById("dietary_info").value,
    quantity: parseInt(document.getElementById("quantity").value, 10),
    hours_available: parseFloat(document.getElementById("hours").value),
    pickup_name: donor.name,
    pickup_lat: donor.lat,
    pickup_lon: donor.lon,
    lang: (typeof CURRENT_LANG !== 'undefined' ? CURRENT_LANG : 'en'),
  };

  try {
    const res = await fetch("/rescue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const plan = await res.json();
    renderMap(plan);
    renderImpact(plan);
    document.getElementById("narrative").innerHTML =
      window.marked ? marked.parse(plan.narrative) : plan.narrative;
  } catch (err) {
    document.getElementById("narrative").textContent = "Error: " + err;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run rescue →";
    document.getElementById("thinking").classList.add("hidden");
  }
});

loadDonors();
