export function parseCsv(text) {
    const trimmed = (text || "").trim();
    if (!trimmed) {
        return { headers: [], rows: [] };
    }
    const lines = trimmed
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.length > 0);
    if (lines.length === 0) {
        return { headers: [], rows: [] };
    }
    const headers = lines[0].split(",").map((h) => h.trim());
    const rows = lines.slice(1).map((line) => {
        const parts = line.split(",");
        const obj = {};
        headers.forEach((h, idx) => {
            obj[h] = (parts[idx] || "").trim();
        });
        return obj;
    });
    return { headers, rows };
}
