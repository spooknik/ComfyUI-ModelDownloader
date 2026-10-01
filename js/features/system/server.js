// Talking to the server for the System tab: stats, restart, free VRAM, and waiting for a restart to finish.
// Exports only (no side effects on import).

import { apiFetch } from "../../lib/api.js";
import { sleep } from "../../lib/util.js";

/** fetch() that gives up after `ms` (a server that is going down can leave a request hanging). */
async function fetchWithTimeout(doFetch, ms) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    try {
        return await doFetch(controller.signal);
    } finally {
        clearTimeout(timer);
    }
}

/** GET /system/stats. Throws on network errors, timeouts and non-2xx answers. */
export async function fetchStats(timeoutMs = 5000) {
    const response = await fetchWithTimeout(
        (signal) => apiFetch("/system/stats", { signal, cache: "no-store" }),
        timeoutMs,
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

/** POST /system/restart. Resolves to { status, data } (202 restarting, 409 busy with counts, or an error). */
export async function requestRestart(force) {
    const response = await apiFetch("/system/restart", { method: "POST", body: JSON.stringify({ force }) });
    let data = {};
    try {
        data = await response.json();
    } catch (err) {
        // Non-JSON error page (e.g. a proxy): the status code is enough.
    }
    return { status: response.status, data };
}

/** ComfyUI's own POST /api/free: unload models and free cached memory (done by the prompt worker when idle). */
export async function freeVram() {
    const response = await fetch(`${window.location.origin}/api/free`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unload_models: true, free_memory: true }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
}

/**
 * Poll /system/stats until it answers with a boot_id other than `oldBootId` (the restarted process).
 * Connection errors while ComfyUI is down are expected and ignored. Resolves true once it is back, or false after
 * `timeoutMs`. `onProgress(seconds, serverDown)` is called each round.
 */
export async function waitForNewBoot(oldBootId, { timeoutMs = 180_000, intervalMs = 1000, onProgress } = {}) {
    const started = Date.now();
    let down = false;
    while (Date.now() - started < timeoutMs) {
        onProgress?.(Math.round((Date.now() - started) / 1000), down);
        try {
            const data = await fetchStats(4000);
            const bootId = data?.comfyui?.boot_id;
            if (bootId && bootId !== oldBootId) return true;
        } catch (err) {
            down = true;
        }
        await sleep(intervalMs);
    }
    return false;
}
