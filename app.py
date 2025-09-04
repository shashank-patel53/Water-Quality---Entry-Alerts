# app.py
from flask import Flask, request, redirect, url_for, render_template_string, flash, Response
import sqlite3
from datetime import datetime
import os
import csv

app = Flask(__name__)
app.secret_key = "replace_this_with_random_secret"

DB_PATH = "readings.db"

THRESH = {
    "pH_low": 6.5,
    "pH_high": 8.5,
    "turbidity_high": 1.0,
    "rfc_low": 0.2,
}

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                pH REAL,
                turbidity REAL,
                rfc REAL,
                tds REAL,
                status TEXT
            );
        """)
        conn.commit()
        conn.close()

def save_reading(pH, turbidity, rfc, tds, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO readings (ts, pH, turbidity, rfc, tds, status) VALUES (?, ?, ?, ?, ?, ?)",
              (datetime.utcnow().isoformat()+"Z", pH, turbidity, rfc, tds, status))
    conn.commit()
    conn.close()

def get_last_readings(limit=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ts, pH, turbidity, rfc, tds, status FROM readings ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_readings():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ts, pH, turbidity, rfc, tds, status FROM readings ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def evaluate_alert(pH, turbidity, rfc):
    issues = []
    severity = "OK"
    if pH is not None:
        if pH < THRESH["pH_low"] or pH > THRESH["pH_high"]:
            issues.append(f"pH out of range ({pH})")
            severity = "HIGH"
    if turbidity is not None:
        if turbidity > THRESH["turbidity_high"]:
            issues.append(f"Turbidity high ({turbidity} NTU)")
            if severity != "HIGH":
                severity = "MEDIUM"
    if rfc is not None:
        if rfc < THRESH["rfc_low"]:
            issues.append(f"Low chlorine ({rfc} mg/L)")
            severity = "CRITICAL"
    return severity, issues

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Water Quality - Entry & Alerts</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
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

    .card {
      background: rgba(255, 255, 255, 0.85);
      border-radius: 12px;
      box-shadow: 0 8px 20px rgba(0,0,0,0.15);
      padding: 24px;
      margin-bottom: 24px;
      backdrop-filter: blur(8px);
      transition: transform 0.3s ease;
    }

    .card:hover {
      transform: scale(1.01);
    }

    .ok { border-left: 6px solid #2ecc71; background: #eafaf1; }
    .medium { border-left: 6px solid #f1c40f; background: #fffbe6; }
    .high { border-left: 6px solid #e74c3c; background: #ffecec; }
    .critical { border-left: 6px solid #c0392b; background: #fdecea; }

    label {
      font-weight: 600;
      display: block;
      margin-top: 14px;
      color: #2c3e50;
    }

    input[type="number"] {
      width: 100%;
      padding: 12px;
      margin-top: 6px;
      border: 1px solid #ccc;
      border-radius: 8px;
      background: #fdfdfd;
      transition: box-shadow 0.3s;
    }

    input[type="number"]:focus {
      box-shadow: 0 0 6px #3498db;
      outline: none;
    }

    .btn {
      background: linear-gradient(to right, #3498db, #6dd5fa);
      color: white;
      padding: 12px 20px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      margin-top: 20px;
      font-weight: bold;
      transition: transform 0.3s ease, box-shadow 0.3s ease;
      display: inline-block;
      text-decoration: none;
    }

    .btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      background: #fff;
      border-radius: 8px;
      overflow: hidden;
    }

    th {
      background: #3498db;
      color: white;
      padding: 12px;
      text-align: left;
    }

    td {
      padding: 12px;
      border-bottom: 1px solid #eee;
    }

    tbody tr:nth-child(even) {
      background: #f2f9ff;
    }

    tbody tr:hover {
      background: #d6ecff;
    }

    .small {
      font-size: 0.9em;
      color: #555;
    }

    .alert-icon {
      font-size: 1.3em;
      margin-right: 6px;
    }

    @media screen and (max-width: 600px) {
      .card, table, input, .btn {
        font-size: 0.95em;
      }

      th, td {
        padding: 8px;
      }

      h2, h3 {
        font-size: 1.4em;
      }
    }
  </style>
</head>
<body>
  <h2>💧 Water Quality Data Entry</h2>

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

  <div class="card">
    <form method="post" action="{{ url_for('submit') }}">
      <label>pH (e.g., 7.2)</label>
      <input type="number" step="0.01" name="pH" required>

      <label>Turbidity (NTU) (e.g., 0.8)</label>
      <input type="number" step="0.01" name="turbidity" required>

      <label>Residual Free Chlorine (mg/L) (e.g., 0.3)</label>
      <input type="number" step="0.01" name="rfc" required>

      <label>TDS (ppm) - optional</label>
      <input type="number" step="0.1" name="tds">

      <button class="btn" type="submit">🚀 Submit Reading</button>
    </form>
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
</body>
</html>

"""

@app.route("/")
def index():
    readings = get_last_readings(10)
    alert = None
    alert_level = request.args.get("alert_level")
    if alert_level:
        issues = request.args.getlist("issue")
        css_map = {"OK": "ok", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}
        alert = { "level": alert_level, "issues": issues, "css": css_map.get(alert_level, "ok") }
    return render_template_string(TEMPLATE, readings=readings, alert=alert, thresh=THRESH)

@app.route("/submit", methods=["POST"])
def submit():
    try: pH = float(request.form.get("pH"))
    except: pH = None
    try: turbidity = float(request.form.get("turbidity"))
    except: turbidity = None
    try: rfc = float(request.form.get("rfc"))
    except: rfc = None
    try:
        tds_val = request.form.get("tds")
        tds = float(tds_val) if tds_val not in (None, "") else None
    except: tds = None

    level, issues = evaluate_alert(pH, turbidity, rfc)
    save_reading(pH, turbidity, rfc, tds, level)

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
        data = [["Timestamp", "pH", "Turbidity", "Chlorine", "TDS", "Status"]]
        data += readings
        output = []
        for row in data:
            output.append(",".join([str(x) if x is not None else "" for x in row]))
        return "\n".join(output)
    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=readings.csv"})

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)

