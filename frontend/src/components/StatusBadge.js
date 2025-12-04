import React from "react";

function StatusBadge({ running, exitCode }) {
    let label = "Idle";
    let className = "status-badge idle";
    if (running) {
        label = "Running";
        className = "status-badge running";
    } else if (exitCode === 0) {
        label = "Completed";
        className = "status-badge success";
    } else if (exitCode != null) {
        label = "Failed";
        className = "status-badge failed";
    }
    return <span className={className}>{label}</span>;
}

export default StatusBadge;
