import { app } from "../../scripts/app.js";

const API_PREFIX = "/api/model-downloader";

function getServiceBaseUrl() {
    // Use the same origin as ComfyUI so remote browser users reach the server.
    return window.location.origin + API_PREFIX;
}

async function apiFetch(path, options = {}) {
    const url = `${getServiceBaseUrl()}${path}`;
    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    });
    return response;
}

function formatBytes(bytes) {
    if (bytes === 0 || !bytes) return "0 B";
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
}

function formatDuration(seconds) {
    if (!seconds || seconds < 0) return "--";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}

class ModelDownloaderPanel {
    constructor() {
        this.element = null;
        this.folders = [];
        this.downloads = [];
        this.refreshInterval = null;
    }

    async init() {
        this.element = document.createElement("div");
        this.element.className = "comfy-model-downloader";
        this.element.style.padding = "12px";
        this.element.style.fontFamily = "var(--fg-font-family)";
        this.element.innerHTML = this.renderSkeleton();

        await this.loadFolders();
        this.startRefreshLoop();
        this.bindEvents();
    }

    renderSkeleton() {
        return `
            <style>
                .cmd-section { margin-bottom: 16px; }
                .cmd-section h3 { margin: 0 0 8px; font-size: 14px; }
                .cmd-label { display: block; margin-bottom: 4px; font-size: 12px; color: #aaa; }
                .cmd-input, .cmd-select { width: 100%; padding: 6px; background: #1a1a1a; color: #eee; border: 1px solid #444; border-radius: 4px; box-sizing: border-box; margin-bottom: 8px; }
                .cmd-button { padding: 8px 16px; background: #2d7bf6; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
                .cmd-button:hover { background: #1a5fd4; }
                .cmd-button:disabled { background: #555; cursor: not-allowed; }
                .cmd-error { color: #ff6b6b; font-size: 12px; margin-top: 4px; }
                .cmd-success { color: #51cf66; font-size: 12px; margin-top: 4px; }
                .cmd-download { border: 1px solid #333; border-radius: 4px; padding: 8px; margin-bottom: 8px; background: #161616; }
                .cmd-download-header { display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-bottom: 4px; }
                .cmd-progress-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
                .cmd-progress-fill { height: 100%; background: #2d7bf6; width: 0%; transition: width 0.2s; }
                .cmd-meta { font-size: 11px; color: #888; margin-top: 4px; }
                .cmd-cancel { font-size: 11px; color: #ff6b6b; cursor: pointer; margin-left: 8px; }
                .cmd-empty { color: #888; font-size: 12px; font-style: italic; }
            </style>
            <div class="cmd-section">
                <h3>Download Model</h3>
                <label class="cmd-label" for="cmd-url">Direct URL</label>
                <input id="cmd-url" class="cmd-input" type="text" placeholder="https://huggingface.co/.../model.safetensors" />

                <label class="cmd-label" for="cmd-folder">Destination folder</label>
                <select id="cmd-folder" class="cmd-select"></select>

                <label class="cmd-label" for="cmd-filename">Filename (optional)</label>
                <input id="cmd-filename" class="cmd-input" type="text" placeholder="leave blank to detect from URL" />

                <button id="cmd-download" class="cmd-button">Download</button>
                <div id="cmd-message"></div>
            </div>
            <div class="cmd-section">
                <h3>Downloads</h3>
                <div id="cmd-downloads"></div>
            </div>
        `;
    }

    bindEvents() {
        const downloadBtn = this.element.querySelector("#cmd-download");
        downloadBtn.addEventListener("click", () => this.onDownloadClick());

        const urlInput = this.element.querySelector("#cmd-url");
        urlInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") this.onDownloadClick();
        });
    }

    async loadFolders() {
        try {
            const response = await apiFetch("/folders");
            const data = await response.json();
            this.folders = data.folders || [];
            this.renderFolderOptions();
        } catch (err) {
            this.showMessage(`Cannot reach download service: ${err.message}`, true);
        }
    }

    renderFolderOptions() {
        const select = this.element.querySelector("#cmd-folder");
        select.innerHTML = "";
        for (const folder of this.folders) {
            const option = document.createElement("option");
            option.value = folder.name;
            option.textContent = `${folder.name} ${folder.exists ? "" : "(will create)"}`;
            select.appendChild(option);
        }
        if (this.folders.length === 0) {
            const option = document.createElement("option");
            option.textContent = "No model folders found";
            select.appendChild(option);
        }
    }

    async onDownloadClick() {
        const url = this.element.querySelector("#cmd-url").value.trim();
        const folder = this.element.querySelector("#cmd-folder").value;
        const filename = this.element.querySelector("#cmd-filename").value.trim();

        if (!url) {
            this.showMessage("Please enter a URL", true);
            return;
        }
        if (!folder) {
            this.showMessage("Please select a folder", true);
            return;
        }

        const downloadBtn = this.element.querySelector("#cmd-download");
        downloadBtn.disabled = true;
        this.showMessage("Starting download...", false);

        try {
            const payload = { url, folder };
            if (filename) payload.filename = filename;
            const response = await apiFetch("/download", {
                method: "POST",
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok) {
                this.showMessage(data.error || "Download failed", true);
            } else {
                this.showMessage(`Download started: ${data.download_id}`, false);
                this.element.querySelector("#cmd-url").value = "";
                this.element.querySelector("#cmd-filename").value = "";
                await this.refreshDownloads();
            }
        } catch (err) {
            this.showMessage(`Request failed: ${err.message}`, true);
        } finally {
            downloadBtn.disabled = false;
        }
    }

    showMessage(text, isError) {
        const el = this.element.querySelector("#cmd-message");
        el.textContent = text;
        el.className = isError ? "cmd-error" : "cmd-success";
    }

    startRefreshLoop() {
        this.refreshInterval = setInterval(() => this.refreshDownloads(), 2000);
        this.refreshDownloads();
    }

    stopRefreshLoop() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    async refreshDownloads() {
        try {
            const response = await apiFetch("/downloads");
            if (!response.ok) return;
            this.downloads = await response.json();
            this.renderDownloads();
        } catch (err) {
            // Fail silently on refresh to avoid spam.
        }
    }

    renderDownloads() {
        const container = this.element.querySelector("#cmd-downloads");
        if (this.downloads.length === 0) {
            container.innerHTML = `<div class="cmd-empty">No downloads yet</div>`;
            return;
        }
        container.innerHTML = "";

        // Sort: running first, then pending, then completed/failed.
        const statusOrder = { running: 0, pending: 1, completed: 2, failed: 3, cancelled: 4 };
        const sorted = [...this.downloads].sort((a, b) => {
            const diff = (statusOrder[a.status] ?? 5) - (statusOrder[b.status] ?? 5);
            return diff || b.created_at - a.created_at;
        });

        for (const dl of sorted) {
            const div = document.createElement("div");
            div.className = "cmd-download";

            const percent = dl.bytes_total ? Math.round((dl.bytes_downloaded / dl.bytes_total) * 100) : 0;
            const cancelHtml = ["pending", "running"].includes(dl.status)
                ? `<span class="cmd-cancel" data-id="${dl.download_id}">cancel</span>`
                : "";

            div.innerHTML = `
                <div class="cmd-download-header">
                    <span>${this.escapeHtml(dl.filename || "model")}</span>
                    <span>${dl.status}${cancelHtml}</span>
                </div>
                <div class="cmd-progress-bar"><div class="cmd-progress-fill" style="width: ${percent}%"></div></div>
                <div class="cmd-meta">
                    ${formatBytes(dl.bytes_downloaded)} / ${formatBytes(dl.bytes_total)}
                    · ${formatBytes(dl.speed_bps)}/s
                    · ETA ${formatDuration(dl.eta_seconds)}
                    ${dl.error ? `· Error: ${this.escapeHtml(dl.error)}` : ""}
                </div>
            `;
            container.appendChild(div);
        }

        container.querySelectorAll(".cmd-cancel").forEach((el) => {
            el.addEventListener("click", () => this.cancelDownload(el.dataset.id));
        });
    }

    async cancelDownload(downloadId) {
        try {
            await apiFetch(`/download/${downloadId}`, { method: "DELETE" });
            await this.refreshDownloads();
        } catch (err) {
            this.showMessage(`Cancel failed: ${err.message}`, true);
        }
    }

    escapeHtml(text) {
        if (!text) return "";
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

// Register the panel with ComfyUI.
app.registerExtension({
    name: "ComfyUI.ModelDownloader",
    async setup() {
        console.log("[ComfyUI-ModelDownloader] extension setup starting");
        const panel = new ModelDownloaderPanel();
        await panel.init();

        // Create a floating toggle button that works with both legacy and modern ComfyUI frontends.
        const toggle = document.createElement("button");
        toggle.id = "cmd-model-downloader-toggle";
        toggle.textContent = "Model Downloader";
        toggle.title = "Download models into ComfyUI folders";
        toggle.style.position = "fixed";
        toggle.style.top = "12px";
        toggle.style.right = "12px";
        toggle.style.zIndex = "10001";
        toggle.style.padding = "8px 14px";
        toggle.style.background = "#2d7bf6";
        toggle.style.color = "#fff";
        toggle.style.border = "none";
        toggle.style.borderRadius = "6px";
        toggle.style.cursor = "pointer";
        toggle.style.fontFamily = "sans-serif";
        toggle.style.fontSize = "13px";
        toggle.style.boxShadow = "0 2px 8px rgba(0,0,0,0.4)";

        toggle.addEventListener("click", () => {
            const existing = document.getElementById("cmd-dialog");
            if (existing) {
                existing.remove();
                return;
            }
            const dialog = document.createElement("div");
            dialog.id = "cmd-dialog";
            dialog.style.position = "fixed";
            dialog.style.top = "52px";
            dialog.style.right = "12px";
            dialog.style.width = "380px";
            dialog.style.maxHeight = "calc(100vh - 72px)";
            dialog.style.overflow = "auto";
            dialog.style.background = "#1a1a1a";
            dialog.style.border = "1px solid #444";
            dialog.style.borderRadius = "8px";
            dialog.style.zIndex = "10000";
            dialog.style.boxShadow = "0 4px 16px rgba(0,0,0,0.5)";
            dialog.appendChild(panel.element);
            document.body.appendChild(dialog);
        });

        document.body.appendChild(toggle);
        console.log("[ComfyUI-ModelDownloader] toggle button added to body");
    },
});
