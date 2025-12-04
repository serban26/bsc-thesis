import React from "react";
import ResultsTable from "./ResultsTable";

function ResultsSection({ title, data }) {
    const headers = data.headers || [];
    const rows = data.rows || [];
    const hasRows = rows.length > 0;
    return (
        <div className="results-section">
            <h2>{title}</h2>
            {hasRows ? (
                <ResultsTable headers={headers} rows={rows} />
            ) : (
                <p className="muted">No {title.toLowerCase()} loaded. Run the algorithm to see results.</p>
            )}
        </div>
    );
}

export default ResultsSection;
