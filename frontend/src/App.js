import React, { useState, useEffect, useCallback } from "react";
import { parseCsv } from "./utils/csv";
import InputPanel from "./components/InputPanel";
import ResultsSection from "./components/ResultsSection";
import ScheduleSection from "./components/ScheduleSection";

function App() {
    const [inputText, setInputText] = useState("");
    const [loadingInput, setLoadingInput] = useState(false);
    const [saving, setSaving] = useState(false);
    const [runError, setRunError] = useState("");
    const [status, setStatus] = useState({
        running: false,
        exit_code: null,
        output: "",
        start_ms: null
    });
    const [schedule, setSchedule] = useState({ headers: [], rows: [] });
    const [stats, setStats] = useState({ headers: [], rows: [] });

    const loadInput = useCallback(async () => {
        setLoadingInput(true);
        try {
            const res = await fetch("/api/load-input");
            if (res.ok) {
                const data = await res.json();
                setInputText(data.text || "");
            }
        } catch (e) { }
        setLoadingInput(false);
    }, []);

    useEffect(() => {
        loadInput();
    }, [loadInput]);

    const saveInput = useCallback(async () => {
        setSaving(true);
        try {
            await fetch("/api/save-input", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ text: inputText })
            });
        } catch (e) { }
        setSaving(false);
    }, [inputText]);

    const startRun = useCallback(
        async (saveBeforeRun) => {
            setRunError("");
            setSchedule({ headers: [], rows: [] });
            setStats({ headers: [], rows: [] });
            if (saveBeforeRun) {
                await saveInput();
            }
            try {
                const res = await fetch("/api/run", { method: "POST" });
                if (!res.ok) {
                    const data = await res.json().catch(() => null);
                    setRunError((data && data.message) || "Failed to start run");
                }
            } catch (e) {
                setRunError("Failed to start run");
            }
        },
        [saveInput]
    );

    useEffect(() => {
        let cancelled = false;

        async function poll() {
            try {
                const [statusRes, scheduleRes, statsRes] = await Promise.all([
                    fetch("/api/status"),
                    fetch("/results/schedule.csv?ts=" + Date.now()),
                    fetch("/results/stats.csv?ts=" + Date.now())
                ]);

                if (!statusRes.ok) {
                    return;
                }

                const statusData = await statusRes.json();
                if (cancelled) {
                    return;
                }
                setStatus(statusData);

                if (scheduleRes.ok) {
                    const scheduleText = await scheduleRes.text();
                    const scheduleData = parseCsv(scheduleText);
                    setSchedule(scheduleData);
                } else {
                    setSchedule({ headers: [], rows: [] });
                }

                if (statsRes.ok) {
                    const statsText = await statsRes.text();
                    const statsData = parseCsv(statsText);
                    setStats(statsData);
                } else {
                    setStats({ headers: [], rows: [] });
                }
            } catch (e) { }
        }

        poll();
        const id = setInterval(poll, 1000);

        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, []);

    return (
        <div className="page">
            <header className="page-header">
                <h1>SDN Round-Robin Load Balancer</h1>
                <p className="subtitle">Offline scheduler and live results</p>
            </header>
            <main className="layout">
                <section className="panel left">
                    <InputPanel
                        inputText={inputText}
                        onInputChange={setInputText}
                        onReload={loadInput}
                        onSave={saveInput}
                        onRunAndSave={() => startRun(true)}
                        onRunExisting={() => startRun(false)}
                        loadingInput={loadingInput}
                        saving={saving}
                        status={status}
                        runError={runError}
                    />
                </section>
                <section className="panel right">
                    <ScheduleSection
                        inputText={inputText}
                        schedule={schedule}
                        status={status}
                    />
                    <ResultsSection title="Stats" data={stats} />
                </section>
            </main>
        </div>
    );
}

export default App;
