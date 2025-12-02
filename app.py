import os
import subprocess
from flask import Flask, send_from_directory, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(BASE_DIR, "input", "input.txt")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
SCHEDULE_PATH = os.path.join(RESULTS_DIR, "schedule.csv")
RUN_SCRIPT = os.path.join(BASE_DIR, "scripts", "run_all.sh")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/input", methods=["GET", "POST"])
def api_input():
    if request.method == "GET":
        if not os.path.exists(INPUT_PATH):
            return "", 200, {"Content-Type": "text/plain; charset=utf-8"}
        with open(INPUT_PATH, "r", encoding="utf-8") as f:
            data = f.read()
        return data, 200, {"Content-Type": "text/plain; charset=utf-8"}
    body = request.get_data(as_text=True) or ""
    os.makedirs(os.path.dirname(INPUT_PATH), exist_ok=True)
    with open(INPUT_PATH, "w", encoding="utf-8") as f:
        f.write(body)
    return jsonify({"status": "ok"})


@app.route("/api/run", methods=["POST"])
def api_run():
    env = os.environ.copy()
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        ["bash", RUN_SCRIPT],
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    out, _ = proc.communicate()
    return jsonify({"status": "ok", "exit_code": proc.returncode, "output": out})


@app.route("/results/<path:filename>")
def results_file(filename):
    return send_from_directory(RESULTS_DIR, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
