import React from "react";
import { parseRequestsFromInput } from "../utils/requests";

const columns = [
    { key: "name", label: "Request" },
    { key: "server", label: "Assigned server" },
    { key: "arrival_ms", label: "Arrival (ms)" },
    { key: "start_ms", label: "Start (ms)" },
    { key: "finish_ms", label: "Finish (ms)" },
    { key: "status", label: "Status" }
];

function getStatusInfo(rawStatus) {
    if (rawStatus === "pending") {
        return { label: "Pending", className: "pending" };
    }
    if (
        rawStatus === "too_consuming" ||
        rawStatus === "unassigned" ||
        rawStatus === "no_server"
    ) {
        return { label: "Unassigned", className: "unassigned" };
    }
    return { label: "Done", className: "assigned" };
}

function buildRows(inputText, schedule) {
    const scheduleRows = (schedule && schedule.rows) || [];
    const byName = new Map();
    scheduleRows.forEach((row) => {
        if (row.name) {
            byName.set(row.name, row);
        }
    });

    const inputRequests = parseRequestsFromInput(inputText);
    const rows = [];

    if (inputRequests.length) {
        inputRequests.forEach((req) => {
            const s = byName.get(req.name);
            if (s) {
                const status =
                    s.status ||
                    (s.server && s.server.trim() ? "assigned" : "unassigned");
                rows.push({
                    name: req.name,
                    server: s.server || "",
                    arrival_ms: s.arrival_ms || req.arrival_ms || "",
                    start_ms: s.start_ms || "",
                    finish_ms: s.finish_ms || "",
                    status
                });
            } else {
                rows.push({
                    name: req.name,
                    server: "",
                    arrival_ms: req.arrival_ms || "",
                    start_ms: "",
                    finish_ms: "",
                    status: "pending"
                });
            }
        });
    } else {
        scheduleRows.forEach((s) => {
            const status =
                s.status ||
                (s.server && s.server.trim() ? "assigned" : "unassigned");
            rows.push({
                name: s.name,
                server: s.server || "",
                arrival_ms: s.arrival_ms || "",
                start_ms: s.start_ms || "",
                finish_ms: s.finish_ms || "",
                status
            });
        });
    }

    return rows;
}

function filterByTime(rows, status) {
    if (!status || !status.start_ms) {
        return rows;
    }
    const running = !!status.running;
    const startMs = status.start_ms;
    if (!running && status.exit_code != null) {
        return rows;
    }
    const now = Date.now();
    const elapsed = now - startMs;
    if (elapsed <= 0) {
        return [];
    }
    return rows.filter((row) => {
        const a = parseInt(row.arrival_ms, 10);
        if (Number.isNaN(a)) {
            return true;
        }
        return a <= elapsed;
    });
}

function ScheduleSection({ inputText, schedule, status }) {
    const allRows = buildRows(inputText, schedule);
    const rows = filterByTime(allRows, status);

    return (
        <div className="results-section">
            <h2>Schedule</h2>
            {rows.length === 0 ? (
                <p className="muted">
                    No schedule loaded. Run the algorithm to see results.
                </p>
            ) : (
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
                            {rows.map((row) => {
                                const statusInfo = getStatusInfo(row.status);
                                return (
                                    <tr
                                        key={row.name}
                                        className={
                                            statusInfo.className === "assigned"
                                                ? "row-assigned"
                                                : statusInfo.className === "pending"
                                                    ? "row-pending"
                                                    : "row-unassigned"
                                        }
                                    >
                                        <td>{row.name}</td>
                                        <td>{row.server || "—"}</td>
                                        <td>{row.arrival_ms || "—"}</td>
                                        <td>{row.start_ms || "—"}</td>
                                        <td>{row.finish_ms || "—"}</td>
                                        <td>
                                            <span
                                                className={
                                                    "status-chip status-" + statusInfo.className
                                                }
                                            >
                                                {statusInfo.label}
                                            </span>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

export default ScheduleSection;
