// Per-browser settings in localStorage. Storage can be unavailable or throw (privacy modes, blocked site
// data, quota), so every access is guarded and failures fall back to "nothing stored".

export function loadSetting(key) {
    try {
        const raw = window.localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

export function saveSetting(key, value) {
    try {
        window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // Not persisted; the setting still applies for this page load.
    }
}

export function removeSetting(key) {
    try {
        window.localStorage.removeItem(key);
    } catch {
        // Nothing to do.
    }
}
