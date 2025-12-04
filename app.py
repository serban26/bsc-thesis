import os
import subprocess
import threading
import csv
import time

from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "input")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "frontend", "build"),
    static_url_path="/"
)

current_run = {
    "running": False,
    "exit_code": None,
    "output": "",
    "start_ms": None
}


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    index_path = os.path.join(app.static_folder, "index.html")
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return "Frontend build not found. Run npm run build in the frontend directory.", 500


@app.route("/results/<path:filename>")
def serve_results(filename):
    return send_from_directory(RESULTS_DIR, filename)


@app.route("/api/schedule", methods=["GET"])
def api_schedule():
    path = os.path.join(RESULTS_DIR, "schedule.csv")
    if not os.path.exists(path):
        return jsonify({"headers": [], "rows": []})
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [row for row in reader]
    return jsonify({"headers": headers, "rows": rows})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    path = os.path.join(RESULTS_DIR, "stats.csv")
    if not os.path.exists(path):
        return jsonify({"headers": [], "rows": []})
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [row for row in reader]
    return jsonify({"headers": headers, "rows": rows})


@app.route("/api/load-input", methods=["GET"])
def load_input():
    os.makedirs(INPUT_DIR, exist_ok=True)
    input_path = os.path.join(INPUT_DIR, "input.txt")
    if not os.path.exists(input_path):
        return jsonify({"text": ""})
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()
    return jsonify({"text": text})


@app.route("/api/save-input", methods=["POST"])
def save_input():
    data = request.get_json(silent=True)
    if data and "text" in data:
        text = data["text"]
    else:
        text = request.form.get("text", "")
    os.makedirs(INPUT_DIR, exist_ok=True)
    input_path = os.path.join(INPUT_DIR, "input.txt")
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(text)
    return jsonify({"ok": True})


def run_backend_script():
    global current_run
    script_path = os.path.join(SCRIPTS_DIR, "run_all.sh")
    current_run["running"] = True
    current_run["exit_code"] = None
    current_run["output"] = ""
    current_run["start_ms"] = int(time.time() * 1000)
    try:
        if not os.path.exists(script_path):
            current_run["output"] = "scripts/run_all.sh not found"
            current_run["exit_code"] = 1
            return
        process = subprocess.Popen(
            ["bash", script_path],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in process.stdout:
            current_run["output"] += line
        process.wait()
        current_run["exit_code"] = process.returncode
    except Exception as exc:
        current_run["output"] += f"\nERROR: {exc}"
        current_run["exit_code"] = 1
    finally:
        current_run["running"] = False


@app.route("/api/run", methods=["POST"])
def run():
    if current_run["running"]:
        return jsonify({"started": False, "message": "Run already in progress"}), 400
    thread = threading.Thread(target=run_backend_script, daemon=True)
    thread.start()
    return jsonify({"started": True})


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify(
        {
            "running": current_run["running"],
            "exit_code": current_run["exit_code"],
            "output": current_run["output"],
            "start_ms": current_run["start_ms"]
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
