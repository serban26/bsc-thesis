import React from "react";

const columns = [
    { key: "name", label: "Request" },
    { key: "server", label: "Assigned server" },
    { key: "arrival_ms", label: "Arrival (ms)" },
    { key: "start_ms", label: "Start (ms)" },
    { key: "finish_ms", label: "Finish (ms)" },
    { key: "status", label: "Status" }
];

function formatStatus(status) {
    if (!status) {
        return "";
    }
    if (status === "assigned") {
        return "Assigned";
    }
    if (status === "too_consuming") {
        return "Too consuming (unassigned)";
    }
    if (status === "no_server") {
        return "No server available";
    }
    return status;
}

function formatCell(row, col) {
    const value = row[col.key];
    if (col.key === "server") {
        return value && value.trim() ? value : "Unassigned";
    }
    if (col.key === "status") {
        return formatStatus(value);
    }
    return value === undefined || value === null || value === "" ? "-" : value;
}

function RequestsTable({ title, rows }) {
    if (!rows || rows.length === 0) {
        return null;
    }
    return (
        <div className="results-subsection">
            <h3 className="results-subtitle">{title}</h3>
            <div className="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            {columns.map((c) => (
                                <th key={c.key}>{c.label}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, idx) => (
                            <tr key={idx}>
                                {columns.map((c) => (
                                    <td key={c.key}>{formatCell(row, c)}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function RequestsSection({ schedule }) {
    const rows = schedule.rows || [];
    if (!rows.length) {
        return (
            <div className="results-section">
                <h2>Requests</h2>
                <p className="muted">No schedule loaded. Run the algorithm to see results.</p>
            </div>
        );
    }
    const assigned = rows.filter((r) => r.server && r.server.trim());
    const unassigned = rows.filter(
        (r) => !r.server || !r.server.trim() || r.status === "too_consuming" || r.status === "no_server"
    );
    return (
        <div className="results-section">
            <h2>Requests</h2>
            {assigned.length > 0 && <RequestsTable title="Assigned requests" rows={assigned} />}
            {unassigned.length > 0 && <RequestsTable title="Unassigned or failed requests" rows={unassigned} />}
        </div>
    );
}

export default RequestsSection;
