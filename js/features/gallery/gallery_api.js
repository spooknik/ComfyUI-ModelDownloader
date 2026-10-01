// HTTP helpers for the gallery endpoints. Exports only (no side effects on import).

import { apiFetch, apiUrl } from "../../lib/api.js";

export const ROOTS = [
    { id: "output", label: "Output" },
    { id: "input", label: "Input" },
];
export const PAGE_SIZE = 200;
// The server accepts up to 10,000 paths per delete request; smaller batches keep each request short.
const DELETE_BATCH = 5000;
// Thumbnail sizes the server renders; the client asks for one of them so URLs (and browser caching) stay stable
// while the size slider moves.
const THUMB_SIZES = [128, 256, 384, 512];

async function jsonOrThrow(response) {
    let data = null;
    try {
        data = await response.json();
    } catch {
        // Not JSON (proxy error page etc.); reported below by status.
    }
    if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
    return data;
}

/** GET /gallery/list. `params`: root, subfolder, recursive, q, sort, kind, offset, limit, paths_only. */
export async function listFiles(params, options = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null || value === "") continue;
        query.set(key, typeof value === "boolean" ? (value ? "1" : "0") : String(value));
    }
    return jsonOrThrow(await apiFetch(`/gallery/list?${query}`, options));
}

export function thumbSizeFor(pixels) {
    return THUMB_SIZES.find((s) => s >= pixels) ?? THUMB_SIZES[THUMB_SIZES.length - 1];
}

export function thumbUrl(root, item, size) {
    // `v` names the file version, so the browser may cache the thumbnail for good.
    const query = new URLSearchParams({ root, path: item.path, size: String(size), v: String(item.modified) });
    return apiUrl(`/gallery/thumb?${query}`);
}

export function fileUrl(root, item) {
    return apiUrl(`/gallery/file?${new URLSearchParams({ root, path: item.path, v: String(item.modified) })}`);
}

/** Delete in batches. Resolves to { deleted: [...], failed: [{path, error}] } over all batches. */
export async function deletePaths(root, paths, onProgress) {
    const result = { deleted: [], failed: [] };
    for (let i = 0; i < paths.length; i += DELETE_BATCH) {
        const batch = paths.slice(i, i + DELETE_BATCH);
        const response = await apiFetch("/gallery/delete", {
            method: "POST",
            body: JSON.stringify({ root, paths: batch }),
        });
        const data = await jsonOrThrow(response);
        result.deleted.push(...data.deleted);
        result.failed.push(...data.failed);
        onProgress?.(Math.min(i + batch.length, paths.length), paths.length);
    }
    return result;
}

/** POST /gallery/zip. Resolves to { token, count, total_size, filename, missing }. */
export async function prepareZip(root, paths) {
    return jsonOrThrow(await apiFetch("/gallery/zip", { method: "POST", body: JSON.stringify({ root, paths }) }));
}

/** Let the browser download the prepared archive natively (its own progress UI, no buffering in the page). */
export function startZipDownload(token, filename) {
    const link = document.createElement("a");
    link.href = apiUrl(`/gallery/zip/${encodeURIComponent(token)}`);
    link.download = filename;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
}
