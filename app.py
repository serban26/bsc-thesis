import os
import subprocess
import threading

from flask import Flask, send_from_directory, request, jsonify

app = Flask(__name__, static_folder=".", static_url_path="")

current_run = {
    "running": False,
    "exit_code": None,
    "output": "",
}


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/results/<path:filename>")
def results_file(filename):
    return send_from_directory("results", filename)


@app.route("/api/load-input", methods=["GET"])
def load_input():
    path = os.path.join("input", "input.txt")
    if not os.path.exists(path):
        os.makedirs("input", exist_ok=True)
        with open(path, "w") as f:
            f.write("")
    with open(path, "r") as f:
        content = f.read()
    return content, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/save-input", methods=["POST"])
def save_input():
    os.makedirs("input", exist_ok=True)
    path = os.path.join("input", "input.txt")
    data = request.get_data(as_text=True) or ""
    with open(path, "w") as f:
        f.write(data)
    return "", 204


def _find_run_script():
    candidates = [
        "run_all.sh",
        os.path.join("scripts", "run_all.sh"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _run_worker(script):
    global current_run
    proc = subprocess.Popen(
        ["bash", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    current_run["running"] = True
    current_run["exit_code"] = None
    output_lines = []
    for line in proc.stdout:
        output_lines.append(line)
        current_run["output"] = "".join(output_lines)
    proc.wait()
    current_run["exit_code"] = proc.returncode
    current_run["running"] = False


@app.route("/api/run", methods=["POST"])
def run_experiment():
    global current_run
    if current_run["running"]:
        return jsonify({"status": "busy"}), 409
    script = _find_run_script()
    if script is None:
        current_run = {
            "running": False,
            "exit_code": 127,
            "output": "ERROR: run_all.sh not found (looked in ./ and ./scripts/)\n",
        }
        return jsonify({"status": "error"}), 500
    current_run = {"running": True, "exit_code": None, "output": ""}
    t = threading.Thread(target=_run_worker, args=(script,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify(
        {
            "running": current_run["running"],
            "exit_code": current_run["exit_code"],
            "output": current_run["output"],
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
