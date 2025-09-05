# app.py
from flask import Flask, request, redirect, url_for, render_template_string, Response, jsonify
import sqlite3
from datetime import datetime
import os
from twilio.rest import Client  # Twilio SMS

app = Flask(__name__)
app.secret_key = "replace_this_with_random_secret"

DB_PATH = "readings.db"

DEFAULT_THRESH = {
    "pH_low": 6.5,
    "pH_high": 8.5,
    "turbidity_high": 1.0,
    "rfc_low": 0.2,
}

# --- Twilio Config ---
TWILIO_ACCOUNT_SID = "your_account_sid"
TWILIO_AUTH_TOKEN = "your_auth_token"
TWILIO_PHONE_NUMBER = "+1234567890"  # Twilio sender
TARGET_PHONE_NUMBER = "+91xxxxxxxxxx"  # Your number

def send_sms_alert(level, issues, timestamp):
    if level in ["CRITICAL", "HIGH"]:
        body = f"🚨 Water Alert [{level}] at {timestamp}\n" + "\n".join(f"• {i}" for i in issues)
        try:
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=body,
                from_=TWILIO_PHONE_NUMBER,
                to=TARGET_PHONE_NUMBER
            )
        except Exception as e:
            print("SMS failed:", e)

# --- DB helpers ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            pH REAL,
            turbidity REAL,
            rfc REAL,
            tds REAL,
            status TEXT
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS thresholds (
            key TEXT PRIMARY KEY,
            value REAL
        );
    """)
    for k, v in DEFAULT_THRESH.items():
        c.execute("INSERT OR IGNORE INTO thresholds (key, value) VALUES (?, ?)", (k, v))
    # --- Simple migration: add lat/lon if missing ---
    c.execute("PRAGMA table_info(readings)")
    cols = {row[1] for row in c.fetchall()}
    if "lat" not in cols:
        c.execute("ALTER TABLE readings ADD COLUMN lat REAL")
    if "lon" not in cols:
        c.execute("ALTER TABLE readings ADD COLUMN lon REAL")
    conn.commit()
    conn.close()

def get_thresholds():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, value FROM thresholds")
    rows = c.fetchall()
    conn.close()
    return {k: v for k, v in rows}

def update_thresholds(new_values):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for k, v in new_values.items():
        c.execute("UPDATE thresholds SET value = ? WHERE key = ?", (v, k))
    conn.commit()
    conn.close()

def save_reading(pH, turbidity, rfc, tds, status, lat, lon):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ts = datetime.utcnow().isoformat() + "Z"
    c.execute("""INSERT INTO readings (ts, pH, turbidity, rfc, tds, status, lat, lon)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (ts, pH, turbidity, rfc, tds, status, lat, lon))
    conn.commit()
    conn.close()
    return ts  # return timestamp for alert

def get_last_readings(limit=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT ts, pH, turbidity, rfc, tds, status, lat, lon
                 FROM readings ORDER BY id DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_readings():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT ts, pH, turbidity, rfc, tds, status, lat, lon
                 FROM readings ORDER BY id DESC""")
    rows = c.fetchall()
    conn.close()
    return rows

def evaluate_alert(pH, turbidity, rfc, thresh):
    issues = []
    severity = "OK"
    if pH is not None:
        if pH < thresh["pH_low"] or pH > thresh["pH_high"]:
            issues.append(f"pH out of range ({pH})")
            severity = "HIGH"
    if turbidity is not None:
        if turbidity > thresh["turbidity_high"]:
            issues.append(f"Turbidity high ({turbidity} NTU)")
            if severity != "HIGH":
                severity = "MEDIUM"
    if rfc is not None:
        if rfc < thresh["rfc_low"]:
            issues.append(f"Low chlorine ({rfc} mg/L)")
            severity = "CRITICAL"
    return severity, issues

# --- Templates ---
TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Water Quality - Entry, Alerts & Map</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <!-- Leaflet CSS -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <style>
    body {
      font-family: 'Segoe UI', sans-serif;
      margin: 0;
      padding: 20px;
      background: linear-gradient(to right, #74ebd5, #ACB6E5);
      color: #333;
    }
    h2, h3 {
      color: #fff;
      text-shadow: 1px 1px 2px #444;
    }
    .layout {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
    .card {
      background: rgba(255, 255, 255, 0.85);
      border-radius: 12px;
      box-shadow: 0 8px 20px rgba(0,0,0,0.15);
      padding: 24px;
      margin-bottom: 24px;
      backdrop-filter: blur(8px);
      transition: transform 0.3s ease;
    }
    .card:hover { transform: scale(1.01); }
    .ok { border-left: 6px solid #2ecc71; background: #eafaf1; }
    .medium { border-left: 6px solid #f1c40f; background: #fffbe6; }
    .high { border-left: 6px solid #e74c3c; background: #ffecec; }
    .critical { border-left: 6px solid #c0392b; background: #fdecea; }
    label { font-weight: 600; display: block; margin-top: 14px; color: #2c3e50; }
    input[type="number"], input[type="text"] {
      width: 100%; padding: 12px; margin-top: 6px; border: 1px solid #ccc; border-radius: 8px; background: #fdfdfd; transition: box-shadow 0.3s;
    }
    input[type="number"]:focus, input[type="text"]:focus { box-shadow: 0 0 6px #3498db; outline: none; }
    .btn {
      background: linear-gradient(to right, #3498db, #6dd5fa);
      color: white; padding: 12px 20px; border: none; border-radius: 8px; cursor: pointer; margin-top: 20px; font-weight: bold;
      transition: transform 0.3s ease, box-shadow 0.3s ease; display: inline-block; text-decoration: none;
    }
    .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
    .btn.secondary { background: linear-gradient(to right, #7f8c8d, #95a5a6); }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; border-radius: 8px; overflow: hidden; }
    th { background: #3498db; color: white; padding: 12px; text-align: left; }
    td { padding: 12px; border-bottom: 1px solid #eee; }
    tbody tr:nth-child(even) { background: #f2f9ff; }
    tbody tr:hover { background: #d6ecff; }
    .small { font-size: 0.9em; color: #555; }
    .alert-icon { font-size: 1.3em; margin-right: 6px; }
    #map { width: 100%; height: 460px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0,0,0,0.15); }
    .row-inline { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; align-items: end; }
    @media (max-width: 600px) { .row-inline { grid-template-columns: 1fr; } }
    .legend {
      background: rgba(255,255,255,0.9); padding: 8px 10px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); font-size: 13px;
    }
    .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  </style>
</head>
<body>
  <h2>💧 Water Quality — Entry, Alerts & Geo Map</h2>

  {% if alert %}
    <div class="card {{ alert.css }}">
      <strong><span class="alert-icon">🚨</span>ALERT — {{ alert.level }}</strong>
      <div class="small" style="margin-top:6px;">
        {% for issue in alert.issues %}
          • {{ issue }}<br>
        {% endfor %}
      </div>
    </div>
  {% endif %}

  <div class="layout">
    <div class="card">
      <form method="post" action="{{ url_for('submit') }}">
        <label>pH (e.g., 7.2)</label>
        <input type="number" step="0.01" name="pH" required>

        <label>Turbidity (NTU) (e.g., 0.8)</label>
        <input type="number" step="0.01" name="turbidity" required>

        <label>Residual Free Chlorine (mg/L) (e.g., 0.3)</label>
        <input type="number" step="0.01" name="rfc" required>

        <label>TDS (ppm) — optional</label>
        <input type="number" step="0.1" name="tds">

        <div class="row-inline">
          <div>
            <label>Latitude (optional)</label>
            <input type="number" step="0.000001" name="lat" id="lat">
          </div>
          <div>
            <label>Longitude (optional)</label>
            <input type="number" step="0.000001" name="lon" id="lon">
          </div>
          <button class="btn secondary" type="button" id="btnGeo">📍 Use my location</button>
        </div>

        <button class="btn" type="submit">🚀 Submit Reading</button>
      </form>
      <div class="small" style="margin-top:10px;">
        Tip: Click “Use my location” to auto-fill lat/lon. You can also drop a pin on the map—fields will update.
      </div>
    </div>

    <div class="card">
      <div id="map"></div>
      <div class="small" style="margin-top:8px;">
        <span class="legend">
          <span class="dot" style="background:#2ecc71;"></span>OK
          <span class="dot" style="background:#f1c40f; margin-left:10px;"></span>MEDIUM
          <span class="dot" style="background:#e67e22; margin-left:10px;"></span>HIGH
          <span class="dot" style="background:#e74c3c; margin-left:10px;"></span>CRITICAL
        </span>
      </div>
    </div>
  </div>

  <h3>📊 Last {{ readings|length }} Readings</h3>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Time (UTC)</th>
          <th>pH</th>
          <th>Turbidity</th>
          <th>Chlorine</th>
          <th>TDS</th>
          <th>Status</th>
          <th>Lat</th>
          <th>Lon</th>
        </tr>
      </thead>
      <tbody>
        {% for r in readings %}
        <tr>
          <td class="small">{{ r[0] }}</td>
          <td>{{ r[1] }}</td>
          <td>{{ r[2] }}</td>
          <td>{{ r[3] }}</td>
          <td>{{ r[4] if r[4] is not none else '-' }}</td>
          <td>{{ r[5] }}</td>
          <td>{{ '%.6f'|format(r[6]) if r[6] is not none else '-' }}</td>
          <td>{{ '%.6f'|format(r[7]) if r[7] is not none else '-' }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <div style="margin-top:10px;">
      <a href="{{ url_for('export_csv') }}" class="btn">📥 Export CSV</a>
    </div>
  </div>

  <div class="small">
    ⚙️ Thresholds: pH [{{ thresh.pH_low }} - {{ thresh.pH_high }}], Turbidity > {{ thresh.turbidity_high }} NTU, Chlorine &lt; {{ thresh.rfc_low }} mg/L → alert
  </div>

  <h3>⚙️ Threshold Settings</h3>
  <div class="card">
    <form method="post" action="{{ url_for('update_thresh') }}">
      <label>pH Low</label><input type="number" step="0.01" name="pH_low" value="{{ thresh.pH_low }}" required>
      <label>pH High</label><input type="number" step="0.01" name="pH_high" value="{{ thresh.pH_high }}" required>
      <label>Turbidity High</label><input type="number" step="0.01" name="turbidity_high" value="{{ thresh.turbidity_high }}" required>
      <label>Chlorine Low</label><input type="number" step="0.01" name="rfc_low" value="{{ thresh.rfc_low }}" required>
      <button class="btn" type="submit">💾 Update Thresholds</button>
    </form>
  </div>

  <!-- Leaflet JS -->
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    // --- Geolocation autofill ---
    document.getElementById('btnGeo').addEventListener('click', function() {
      if (!navigator.geolocation) { alert('Geolocation not supported'); return; }
      navigator.geolocation.getCurrentPosition(function(pos) {
        document.getElementById('lat').value = pos.coords.latitude.toFixed(6);
        document.getElementById('lon').value = pos.coords.longitude.toFixed(6);
        if (window._map) { window._map.setView([pos.coords.latitude, pos.coords.longitude], 15); }
      }, function(err){ alert('Location error: ' + err.message); }, { enableHighAccuracy: true, timeout: 10000 });
    });

    // --- Leaflet map ---
    const map = L.map('map');
    window._map = map;
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    map.setView([22.9734, 78.6569], 5); // India view default

    // Click to set lat/lon in form
    map.on('click', function(e) {
      const { lat, lng } = e.latlng;
      document.getElementById('lat').value = lat.toFixed(6);
      document.getElementById('lon').value = lng.toFixed(6);
      if (window._dropPin) { map.removeLayer(window._dropPin); }
      window._dropPin = L.marker([lat, lng]).addTo(map).bindPopup('Selected Location').openPopup();
    });

    function colorFor(status) {
      if (status === 'CRITICAL') return '#e74c3c';
      if (status === 'HIGH') return '#e67e22';
      if (status === 'MEDIUM') return '#f1c40f';
      return '#2ecc71';
    }

    // Load markers
    fetch('{{ url_for("geojson") }}')
      .then(r => r.json())
      .then(geo => {
        const markers = [];
        L.geoJSON(geo, {
          pointToLayer: function (feature, latlng) {
            const st = feature.properties.status || 'OK';
            const marker = L.circleMarker(latlng, {
              radius: 8, weight: 2, fillOpacity: 0.9, color: '#333', fillColor: colorFor(st)
            });
            const p = feature.properties;
            marker.bindPopup(
              `<b>Status:</b> ${p.status}<br>
               <b>Time (UTC):</b> ${p.ts}<br>
               <b>pH:</b> ${p.pH}<br>
               <b>Turbidity:</b> ${p.turbidity} NTU<br>
               <b>Chlorine:</b> ${p.rfc} mg/L<br>
               <b>TDS:</b> ${p.tds ?? '-'}`
            );
            markers.push(marker);
            return marker;
          }
        }).addTo(map);
        // Fit bounds if we have markers
        if (markers.length) {
          const group = L.featureGroup(markers);
          map.fitBounds(group.getBounds().pad(0.2));
        }
      })
      .catch(err => console.error('Map load error:', err));
  </script>
</body>
</html>
"""

@app.route("/")
def index():
    readings = get_last_readings(10)
    thresh = get_thresholds()
    alert = None
    alert_level = request.args.get("alert_level")
    if alert_level:
        issues = request.args.getlist("issue")
        css_map = {"OK": "ok", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}
        alert = { "level": alert_level, "issues": issues, "css": css_map.get(alert_level, "ok") }
    return render_template_string(TEMPLATE, readings=readings, alert=alert, thresh=thresh)

@app.route("/submit", methods=["POST"])
def submit():
    def to_float(x):
        try:
            return float(x)
        except:
            return None
    pH = to_float(request.form.get("pH"))
    turbidity = to_float(request.form.get("turbidity"))
    rfc = to_float(request.form.get("rfc"))
    tds_val = request.form.get("tds")
    tds = to_float(tds_val) if tds_val not in (None, "") else None
    lat_val = request.form.get("lat")
    lon_val = request.form.get("lon")
    lat = to_float(lat_val) if lat_val not in (None, "") else None
    lon = to_float(lon_val) if lon_val not in (None, "") else None

    thresh = get_thresholds()
    level, issues = evaluate_alert(pH, turbidity, rfc, thresh)
    ts = save_reading(pH, turbidity, rfc, tds, level, lat, lon)

    if level in ["CRITICAL", "HIGH"]:
        send_sms_alert(level, issues, ts)

    if issues:
        query = [("alert_level", level)] + [("issue", it) for it in issues]
        from urllib.parse import urlencode
        return redirect(url_for("index") + "?" + urlencode(query, doseq=True))
    else:
        return redirect(url_for("index"))

@app.route("/export")
def export_csv():
    readings = get_all_readings()
    def generate():
        data = [["Timestamp", "pH", "Turbidity", "Chlorine", "TDS", "Status", "Lat", "Lon"]]
        data += readings
        output = []
        for row in data:
            output.append(",".join([str(x) if x is not None else "" for x in row]))
        return "\n".join(output)
    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=readings.csv"})

@app.route("/update_thresholds", methods=["POST"])
def update_thresh():
    try:
        new_values = {
            "pH_low": float(request.form.get("pH_low")),
            "pH_high": float(request.form.get("pH_high")),
            "turbidity_high": float(request.form.get("turbidity_high")),
            "rfc_low": float(request.form.get("rfc_low")),
        }
        update_thresholds(new_values)
    except Exception as e:
        print("Threshold update failed:", e)
    return redirect(url_for("index"))

# --- GeoJSON API for map ---
@app.route("/api/geojson")
def geojson():
    rows = get_all_readings()
    features = []
    for ts, pH, turbidity, rfc, tds, status, lat, lon in rows:
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "ts": ts, "pH": pH, "turbidity": turbidity, "rfc": rfc,
                "tds": tds, "status": status
            }
        })
    return jsonify({"type": "FeatureCollection", "features": features})

# Backward-compatible alias used by the template
@app.route("/api/readings.geojson")
def geojson_alias():
    return geojson()

# Jinja url_for reference used above
@app.context_processor
def inject_urls():
    return {"geojson": "geojson"}

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)