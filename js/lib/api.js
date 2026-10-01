// ComfyUI loads every .js file under js/ as an extension entry point, so this module only exports.

export const API_PREFIX = "/api/spooktools";

export function apiUrl(path) {
    // Use the same origin as ComfyUI so remote browser users reach the server.
    return `${window.location.origin}${API_PREFIX}${path}`;
}

export async function apiFetch(path, options = {}) {
    return fetch(apiUrl(path), {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    });
}
