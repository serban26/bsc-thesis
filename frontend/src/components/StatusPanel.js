import React from "react";
import StatusBadge from "./StatusBadge";

function StatusPanel({ status, runError }) {
    const running = !!status.running;
    const exitCode = status.exit_code;
    const output = status.output || "";
    return (
        <>
            <div className="status-row">
                <StatusBadge running={running} exitCode={exitCode} />
                {running && <div className="spinner" />}
            </div>
            <div className="status-text">
                <strong>Console output</strong>
                <pre className="status-log">
                    {output.trim() ? output.trim() : "No log yet"}
                </pre>
            </div>
            {runError && <div className="error-text">{runError}</div>}
        </>
    );
}

export default StatusPanel;
