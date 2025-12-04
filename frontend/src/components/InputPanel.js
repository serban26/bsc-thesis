import React from "react";
import StatusPanel from "./StatusPanel";

function InputPanel({
    inputText,
    onInputChange,
    onReload,
    onSave,
    onRunAndSave,
    onRunExisting,
    loadingInput,
    saving,
    status,
    runError
}) {
    const running = !!status.running;
    return (
        <>
            <div className="panel-header">
                <h2>Input file</h2>
                <button
                    className="secondary"
                    onClick={onReload}
                    disabled={loadingInput || saving || running}
                >
                    {loadingInput ? "Loading..." : "Reload from file"}
                </button>
            </div>
            <textarea
                className="input-textarea"
                value={inputText}
                onChange={(e) => onInputChange(e.target.value)}
                placeholder="Paste the contents of input/input.txt here"
                spellCheck={false}
            />
            <div className="toolbar">
                <div className="toolbar-left">
                    <button
                        className="secondary"
                        onClick={onSave}
                        disabled={saving || running}
                    >
                        {saving ? "Saving..." : "Save input"}
                    </button>
                    <button
                        className="primary"
                        onClick={onRunAndSave}
                        disabled={running}
                    >
                        {running ? "Running..." : "Save and run"}
                    </button>
                    <button
                        className="ghost"
                        onClick={onRunExisting}
                        disabled={running}
                    >
                        Run with existing file
                    </button>
                </div>
                <div className="toolbar-right">
                    <StatusPanel status={status} runError={runError} />
                </div>
            </div>
        </>
    );
}

export default InputPanel;
