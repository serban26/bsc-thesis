import React from "react";

function ResultsTable({ headers, rows }) {
    if (!rows || rows.length === 0) {
        return null;
    }
    return (
        <div className="table-wrapper">
            <table>
                <thead>
                    <tr>
                        {headers.map((h) => (
                            <th key={h}>{h}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, idx) => (
                        <tr key={idx}>
                            {headers.map((h) => (
                                <td key={h}>{row[h]}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default ResultsTable;
