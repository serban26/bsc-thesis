export function parseRequestsFromInput(inputText) {
    const lines = (inputText || "")
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.length > 0);

    if (!lines.length) {
        return [];
    }

    let pos = 0;
    const firstParts = lines[0].split(/\s+/);
    const numServers = parseInt(firstParts[0], 10);
    if (Number.isNaN(numServers)) {
        return [];
    }

    pos = 1 + numServers;
    if (pos >= lines.length) {
        return [];
    }

    const maybeConfigLine = lines[pos].split(/\s+/);
    if (maybeConfigLine.length === 1 && !Number.isNaN(parseInt(maybeConfigLine[0], 10))) {
        pos += 1;
    }

    const requests = [];
    for (; pos < lines.length; pos += 1) {
        const parts = lines[pos].split(/\s+/);
        if (parts.length < 4) {
            continue;
        }
        const [name, , , arrival] = parts;
        const arrivalMs = parseInt(arrival, 10);
        requests.push({
            name,
            arrival_ms: Number.isNaN(arrivalMs) ? "" : arrivalMs.toString()
        });
    }

    return requests;
}
