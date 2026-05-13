import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { StatPill } from "./ui/StatPill";
const MS_MINUTE = 60000;
const MS_HOUR = 3600000;
const MS_DAY = 86400000;
function fmtRelative(value) {
    if (!value)
        return "";
    const when = Date.parse(value);
    if (Number.isNaN(when))
        return String(value);
    const delta = Date.now() - when;
    if (delta < MS_MINUTE)
        return "just now";
    if (delta < MS_HOUR)
        return `${Math.round(delta / MS_MINUTE)}m ago`;
    if (delta < MS_DAY)
        return `${Math.round(delta / MS_HOUR)}h ago`;
    return `${Math.round(delta / MS_DAY)}d ago`;
}
function fmtSize(bytes) {
    if (!bytes || bytes <= 0)
        return "—";
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024)
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
function extensionLabel(asset) {
    const name = asset.name || "";
    const dot = name.lastIndexOf(".");
    if (dot <= 0 || dot === name.length - 1)
        return "file";
    return name.slice(dot + 1).toLowerCase().slice(0, 5);
}
function runtimeTone(runtime) {
    const key = runtime.toLowerCase();
    if (key.includes("claude"))
        return "#e06b3f";
    if (key.includes("codex"))
        return "#1a1a1a";
    if (key.includes("cursor"))
        return "#4d6cff";
    if (key.includes("browser"))
        return "#1fa971";
    return "var(--ink3)";
}
function ExtensionBadge({ asset }) {
    const ext = extensionLabel(asset);
    return (_jsx("div", { style: {
            width: 38,
            height: 44,
            flexShrink: 0,
            borderRadius: 4,
            background: "rgba(20,16,10,0.04)",
            border: "1px solid rgba(20,16,10,0.12)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--mono)",
            fontSize: 9,
            fontWeight: 600,
            color: "var(--ink2)",
            letterSpacing: 0.4,
            textTransform: "uppercase",
        }, children: ext }));
}
function AssetResultRow({ result }) {
    const runtime = String(result.harness || "dhee");
    const tool = String(result.tool_name || "");
    const kind = String(result.packet_kind || "").replace(/^routed_/, "");
    return (_jsxs("div", { style: {
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink2)",
            padding: "4px 0",
        }, children: [_jsx("span", { style: {
                    width: 5,
                    height: 5,
                    borderRadius: "50%",
                    background: runtimeTone(runtime),
                    flexShrink: 0,
                } }), _jsx("span", { style: { color: runtimeTone(runtime), minWidth: 60 }, children: runtime }), _jsx("span", { style: { color: "var(--ink3)" }, children: tool.toLowerCase() || kind }), _jsx("span", { style: { marginLeft: "auto", color: "var(--ink3)" }, children: fmtRelative(result.updated_at || result.created_at) })] }));
}
function AssetCard({ asset, onDelete, busyDelete, }) {
    const [showResults, setShowResults] = useState(false);
    const results = asset.results || [];
    const processors = new Set();
    for (const r of results)
        processors.add(String(r.harness || "dhee"));
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "white",
            padding: 12,
            borderRadius: 6,
            display: "flex",
            flexDirection: "column",
            gap: 8,
        }, children: [_jsxs("div", { style: { display: "flex", gap: 10, alignItems: "flex-start" }, children: [_jsx(ExtensionBadge, { asset: asset }), _jsxs("div", { style: { flex: 1, minWidth: 0 }, children: [_jsx("div", { style: {
                                    fontSize: 13,
                                    fontWeight: 600,
                                    lineHeight: 1.3,
                                    whiteSpace: "nowrap",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                }, title: asset.name, children: asset.name }), _jsxs("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    letterSpacing: 0.3,
                                    marginTop: 4,
                                    display: "flex",
                                    gap: 8,
                                }, children: [_jsx("span", { children: fmtSize(asset.size_bytes) }), asset.updated_at ? _jsxs("span", { children: ["uploaded ", fmtRelative(asset.updated_at)] }) : null] })] }), _jsx("button", { onClick: () => void onDelete(asset), disabled: busyDelete, title: "Remove asset", "aria-label": "Remove asset", style: {
                            width: 22,
                            height: 22,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            border: "1px solid transparent",
                            borderRadius: 3,
                            background: "transparent",
                            color: "var(--ink3)",
                            cursor: busyDelete ? "not-allowed" : "pointer",
                            opacity: busyDelete ? 0.5 : 1,
                            padding: 0,
                            flexShrink: 0,
                        }, onMouseEnter: (e) => {
                            if (!busyDelete) {
                                e.currentTarget.style.background = "rgba(203,63,78,0.08)";
                                e.currentTarget.style.color = "var(--rose)";
                            }
                        }, onMouseLeave: (e) => {
                            e.currentTarget.style.background = "transparent";
                            e.currentTarget.style.color = "var(--ink3)";
                        }, children: _jsx("svg", { width: 12, height: 12, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M6 6l12 12M18 6L6 18", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" }) }) })] }), _jsxs("div", { style: { display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }, children: [processors.size > 0 ? (_jsx(StatPill, { label: `${results.length} processed`, tone: "var(--green)" })) : (_jsx(StatPill, { label: "not yet processed" })), Array.from(processors)
                        .slice(0, 3)
                        .map((runtime) => (_jsx(StatPill, { label: runtime, tone: runtimeTone(runtime) }, runtime)))] }), results.length > 0 && (_jsxs(_Fragment, { children: [_jsx("button", { onClick: () => setShowResults((v) => !v), style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            background: "transparent",
                            border: 0,
                            padding: 0,
                            textAlign: "left",
                            cursor: "pointer",
                            letterSpacing: 0.4,
                        }, children: showResults ? "▾ hide processing feed" : `▸ show processing feed (${results.length})` }), showResults && (_jsx("div", { style: {
                            display: "flex",
                            flexDirection: "column",
                            borderTop: "1px dashed var(--border)",
                            paddingTop: 6,
                        }, children: results.slice(0, 8).map((result) => (_jsx(AssetResultRow, { result: result }, result.id))) }))] }))] }));
}
export function AssetDrawer({ workspace, project, onActivity, }) {
    const [assets, setAssets] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [status, setStatus] = useState("idle");
    const [statusMessage, setStatusMessage] = useState("");
    const [dragHover, setDragHover] = useState(false);
    const [busyDeleteId, setBusyDeleteId] = useState(null);
    const fileInputRef = useRef(null);
    const scopeLabel = useMemo(() => {
        if (project)
            return project.name;
        if (workspace)
            return `${workspace.label || workspace.name} (workspace)`;
        return "—";
    }, [project, workspace]);
    const refresh = useCallback(async () => {
        if (!workspace) {
            setAssets([]);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const res = project
                ? await api.listProjectAssets(project.id)
                : await api.listWorkspaceAssets(workspace.id, false);
            setAssets(res.assets || []);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setLoading(false);
        }
    }, [project, workspace]);
    useEffect(() => {
        void refresh();
    }, [refresh]);
    // Light polling so the processing feed stays fresh without a full SSE
    // implementation (PR 4).
    useEffect(() => {
        if (!workspace)
            return;
        const timer = window.setInterval(() => void refresh(), 5000);
        return () => window.clearInterval(timer);
    }, [refresh, workspace]);
    const uploadFiles = useCallback(async (files) => {
        if (!workspace)
            return;
        const list = Array.from(files);
        if (list.length === 0)
            return;
        setStatus("uploading");
        setStatusMessage(`uploading ${list.length} file${list.length === 1 ? "" : "s"}…`);
        setError(null);
        try {
            for (const file of list) {
                if (project) {
                    await api.uploadProjectAsset(project.id, file);
                }
                else {
                    await api.uploadWorkspaceAsset(workspace.id, file);
                }
            }
            setStatus("success");
            setStatusMessage(list.length === 1
                ? `uploaded ${list[0].name}`
                : `uploaded ${list.length} files`);
            await refresh();
            onActivity?.();
        }
        catch (e) {
            setStatus("error");
            setStatusMessage(String(e));
        }
        finally {
            window.setTimeout(() => {
                setStatus("idle");
                setStatusMessage("");
            }, 2200);
        }
    }, [project, workspace, refresh, onActivity]);
    const onDrop = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragHover(false);
        const dt = e.dataTransfer;
        if (dt?.files?.length) {
            void uploadFiles(dt.files);
        }
    };
    const onDragOver = (e) => {
        if (!workspace)
            return;
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer.types?.includes("Files") && !dragHover)
            setDragHover(true);
    };
    const onDragLeave = (e) => {
        if (e.currentTarget === e.target)
            setDragHover(false);
    };
    const onFilePick = (e) => {
        const files = e.target.files;
        if (files?.length)
            void uploadFiles(files);
        e.target.value = "";
    };
    const deleteAsset = async (asset) => {
        if (!window.confirm(`Remove "${asset.name}"?`))
            return;
        setBusyDeleteId(asset.id);
        try {
            await api.deleteProjectAsset(asset.id);
            setAssets((current) => current.filter((a) => a.id !== asset.id));
            onActivity?.();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusyDeleteId(null);
        }
    };
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 10 }, onDrop: onDrop, onDragOver: onDragOver, onDragLeave: onDragLeave, children: [_jsxs("div", { style: {
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    letterSpacing: 0.5,
                    color: "var(--ink3)",
                    textTransform: "uppercase",
                }, children: [_jsxs("span", { children: ["Assets \u00B7 ", scopeLabel] }), _jsx("span", { children: assets.length })] }), _jsxs("div", { onClick: () => workspace && fileInputRef.current?.click(), style: {
                    padding: "16px 14px",
                    border: `1px dashed ${dragHover ? "var(--accent)" : "var(--border)"}`,
                    background: dragHover ? "rgba(224,107,63,0.06)" : "white",
                    borderRadius: 6,
                    textAlign: "center",
                    cursor: workspace ? "pointer" : "not-allowed",
                    opacity: workspace ? 1 : 0.55,
                    transition: "background 0.18s ease, border-color 0.18s ease",
                }, children: [_jsx("input", { ref: fileInputRef, type: "file", multiple: true, style: { display: "none" }, onChange: onFilePick }), _jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            color: dragHover ? "var(--accent)" : "var(--ink2)",
                            fontWeight: 500,
                            marginBottom: 4,
                        }, children: status === "uploading"
                            ? statusMessage
                            : dragHover
                                ? "release to upload"
                                : "drop files here or click to upload" }), _jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 0.4,
                        }, children: project
                            ? "visible to every agent working on this project"
                            : workspace
                                ? "workspace-wide — every project sees it"
                                : "select a workspace first" })] }), status === "success" && (_jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--green)",
                    lineHeight: 1.4,
                }, children: statusMessage })), status === "error" && (_jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--rose)",
                    lineHeight: 1.4,
                }, children: statusMessage })), error && status !== "error" ? (_jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--rose)",
                    lineHeight: 1.4,
                }, children: error })) : null, loading && assets.length === 0 ? (_jsx("div", { style: { display: "grid", gap: 8 }, children: [0, 1, 2].map((i) => (_jsx("div", { style: {
                        height: 66,
                        borderRadius: 6,
                        background: "linear-gradient(90deg, rgba(20,16,10,0.04) 0%, rgba(20,16,10,0.08) 50%, rgba(20,16,10,0.04) 100%)",
                        backgroundSize: "200% 100%",
                        animation: `dhee-shimmer 1.4s linear ${i * 140}ms infinite`,
                        border: "1px solid rgba(20,16,10,0.06)",
                    } }, i))) })) : assets.length === 0 ? (_jsxs("div", { style: {
                    padding: 14,
                    border: "1px dashed var(--border)",
                    borderRadius: 6,
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink3)",
                    lineHeight: 1.55,
                    background: "white",
                }, children: ["No assets yet. Drop a spec PDF, design export, or schema doc here \u2014 every agent in this", project ? " project" : " workspace", " will see it."] })) : (_jsx("div", { style: { display: "grid", gap: 8 }, children: assets.map((asset) => (_jsx(AssetCard, { asset: asset, onDelete: deleteAsset, busyDelete: busyDeleteId === asset.id }, asset.id))) }))] }));
}
