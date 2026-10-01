// Formatting and small DOM helpers. Exports only (no side effects on import).

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function formatBytes(bytes) {
    if (!bytes || bytes < 1) return "0 B";
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), sizes.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
}

export function formatDuration(seconds) {
    if (!seconds || seconds < 0) return "--";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}

export function formatDate(epochSeconds) {
    if (!epochSeconds) return "";
    return new Date(epochSeconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export function showMessage(el, text, isError) {
    el.textContent = text;
    el.className = isError ? "spk-error" : "spk-success";
}

export function clamp(value, min, max) {
    return Math.min(Math.max(value, min), Math.max(min, max));
}

// Size of the area fixed-position elements live in (excludes scrollbars, unlike innerWidth/innerHeight).
export function viewportSize() {
    const root = document.documentElement;
    return { width: root.clientWidth || window.innerWidth, height: root.clientHeight || window.innerHeight };
}
