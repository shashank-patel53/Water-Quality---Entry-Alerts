from flask import Flask, request, redirect, url_for, render_template_string, Response, jsonify, flash
import sqlite3, csv
from datetime import datetime
from twilio.rest import Client  # Twilio SMS
from geopy.geocoders import Nominatim

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
TWILIO_PHONE_NUMBER = "+1234567890"
TARGET_PHONE_NUMBER = "+91xxxxxxxxxx"

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

def get_lat_lon_from_city(city_name):
    try:
        geolocator = Nominatim(user_agent="water_quality_app")
        location = geolocator.geocode(city_name)
        if location:
            return location.latitude, location.longitude
    except Exception as e:
        print("Geocoding failed:", e)
    return None, None

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
            status TEXT,
            lat REAL,
            lon REAL
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
    return ts

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

# --- Template with multi-page navbar + colored markers ---
TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Water Quality - Map & Alerts</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
  <style>
    body { font-family:'Segoe UI',sans-serif; margin:0; padding:20px; background:linear-gradient(135deg,#00c6ff,#0072ff); color:#333;}
    nav { background:#0072ff; padding:12px 20px; display:flex; gap:20px;}
    nav a { color:#fff; text-decoration:none; font-weight:bold;}
    nav a:hover { text-decoration:underline;}
    h2,h3 { color:#fff; text-shadow:1px 1px 2px #222; }
    .container { padding:20px; }
    .card { background:rgba(255,255,255,0.92); border-radius:14px; box-shadow:0 8px 22px rgba(0,0,0,0.25); padding:20px; margin-bottom:20px; transition:transform .3s; }
    .card:hover { transform:scale(1.01);}
    label { font-weight:600; display:block; margin-top:12px;}
    input { width:100%; padding:10px; margin-top:6px; border:1px solid #ccc; border-radius:8px;}
    .btn { background:linear-gradient(to right,#ff512f,#dd2476); color:white; border:none; border-radius:8px; padding:12px 18px; cursor:pointer; font-weight:bold; margin-top:16px;}
    .btn:hover { opacity:0.9;}
    table { width:100%; border-collapse:collapse; margin-top:10px; background:#fff; border-radius:8px; overflow:hidden;}
    th { background:#0072ff; color:white; padding:10px; text-align:left;}
    td { padding:10px; border-bottom:1px solid #eee;}
    #map { width:100%; height:450px; border-radius:12px; margin-top:10px; }
    .flash { padding:12px; border-radius:8px; margin-bottom:20px; font-weight:bold;}
    .OK { background:#d4edda; color:#155724; }
    .MEDIUM { background:#fff3cd; color:#856404; }
    .HIGH { background:#f8d7da; color:#721c24; }
    .CRITICAL { background:#721c24; color:#fff; }
  </style>
</head>
<body>
<nav>
    <a href="{{ url_for('index') }}">🏠 Dashboard</a>
    <a href="{{ url_for('index') }}#add">➕ Add Reading</a>
    <a href="{{ url_for('index') }}#threshold">⚙️ Update Thresholds</a>
    <a href="{{ url_for('export_csv') }}">📥 Export CSV</a>
</nav>
<div class="container">

{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    {% for category, msg in messages %}
      <div class="flash {{ category }}">⚠️ {{ msg }}</div>
    {% endfor %}
  {% endif %}
{% endwith %}

<h2 id="add">💧 Add Water Reading</h2>
<div class="card">
  <form method="post" action="{{ url_for('submit') }}">
    <label>City Name</label><input type="text" name="city" placeholder="Enter city e.g. Delhi" required>
    <label>pH</label><input type="number" step="0.01" name="pH" required>
    <label>Turbidity (NTU)</label><input type="number" step="0.01" name="turbidity" required>
    <label>Residual Free Chlorine (mg/L)</label><input type="number" step="0.01" name="rfc" required>
    <label>TDS (ppm) - optional</label><input type="number" step="0.1" name="tds">
    <button class="btn" type="submit">🚀 Submit</button>
  </form>
</div>

<h3 id="threshold">⚙️ Update Thresholds</h3>
<div class="card">
  <form method="post" action="{{ url_for('update_thresholds_route') }}">
    <label>pH Low</label><input type="number" step="0.1" name="pH_low" value="{{ thresh['pH_low'] }}" required>
    <label>pH High</label><input type="number" step="0.1" name="pH_high" value="{{ thresh['pH_high'] }}" required>
    <label>Turbidity High</label><input type="number" step="0.1" name="turbidity_high" value="{{ thresh['turbidity_high'] }}" required>
    <label>Chlorine Low</label><input type="number" step="0.1" name="rfc_low" value="{{ thresh['rfc_low'] }}" required>
    <button class="btn" type="submit">💾 Update</button>
  </form>
</div>

<div class="card">
  <div id="map"></div>
</div>

<h3>📊 Recent Readings</h3>
<div class="card">
  <table>
    <thead><tr><th>Time</th><th>pH</th><th>Turbidity</th><th>Chlorine</th><th>TDS</th><th>Status</th></tr></thead>
    <tbody>
    {% for r in readings %}
      <tr>
        <td>{{ r[0] }}</td>
        <td>{{ r[1] }}</td><td>{{ r[2] }}</td><td>{{ r[3] }}</td>
        <td>{{ r[4] if r[4] else '-' }}</td><td>{{ r[5] }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map=L.map('map').setView([22.9734,78.6569],5);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);

fetch('{{ url_for("geojson") }}').then(r=>r.json()).then(g=>{
  L.geoJSON(g,{
    pointToLayer:(f,latlng)=>{
        let color = 'green';
        if(f.properties.status=='MEDIUM') color='yellow';
        else if(f.properties.status=='HIGH') color='orange';
        else if(f.properties.status=='CRITICAL') color='red';
        return L.circleMarker(latlng,{
            radius:8, fillColor: color, color:'#000', weight:1, fillOpacity:0.9
        }).bindPopup(`<b>Status:</b> ${f.properties.status}<br>
                      <b>pH:</b> ${f.properties.pH}<br>
                      <b>Turbidity:</b> ${f.properties.turbidity}<br>
                      <b>Chlorine:</b> ${f.properties.rfc}`);
    }
  }).addTo(map);
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    readings = get_last_readings(10)
    thresh = get_thresholds()
    return render_template_string(TEMPLATE, readings=readings, thresh=thresh)

@app.route("/submit", methods=["POST"])
def submit():
    def to_float(x):
        try: return float(x)
        except: return None

    pH = to_float(request.form.get("pH"))
    turbidity = to_float(request.form.get("turbidity"))
    rfc = to_float(request.form.get("rfc"))
    tds_val = request.form.get("tds")
    tds = to_float(tds_val) if tds_val not in (None, "") else None

    city = request.form.get("city")
    lat, lon = get_lat_lon_from_city(city)

    thresh = get_thresholds()
    level, issues = evaluate_alert(pH, turbidity, rfc, thresh)
    ts = save_reading(pH, turbidity, rfc, tds, level, lat, lon)

    if level in ["CRITICAL","HIGH"]:
        send_sms_alert(level, issues, ts)

    if level == "OK":
        flash("Water quality is safe ✅", "OK")
    else:
        flash(f"{level} Alert! Issues: {', '.join(issues)}", level)

    return redirect(url_for("index"))

@app.route("/update_thresholds", methods=["POST"])
def update_thresholds_route():
    new_vals={}
    for key in ["pH_low","pH_high","turbidity_high","rfc_low"]:
        try: new_vals[key]=float(request.form.get(key))
        except: pass
    update_thresholds(new_vals)
    flash("Thresholds updated ✅", "OK")
    return redirect(url_for("index"))

@app.route("/export_csv")
def export_csv():
    rows=get_all_readings()
    def generate():
        data=[["Timestamp","pH","Turbidity","Chlorine","TDS","Status","Lat","Lon"]]+list(rows)
        for row in data:
            yield ",".join([str(x) if x is not None else "" for x in row])+"\n"
    return Response(generate(),mimetype="text/csv",headers={"Content-Disposition":"attachment;filename=readings.csv"})

@app.route("/api/geojson")
def geojson():
    rows = get_all_readings()
    features=[]
    for ts,pH,turbidity,rfc,tds,status,lat,lon in rows:
        if lat is None or lon is None: continue
        features.append({
          "type":"Feature",
          "geometry":{"type":"Point","coordinates":[lon,lat]},
          "properties":{"ts":ts,"pH":pH,"turbidity":turbidity,"rfc":rfc,"tds":tds,"status":status}
        })
    return jsonify({"type":"FeatureCollection","features":features})

if __name__=="__main__":
    init_db()
    app.run(debug=True,host="0.0.0.0",port=5000)







                         



                         




