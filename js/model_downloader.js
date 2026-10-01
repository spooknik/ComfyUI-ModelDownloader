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
    if (!bytes || bytes < 1) return "0 B";
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), sizes.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
}

function formatDuration(seconds) {
    if (!seconds || seconds < 0) return "--";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}

function formatDate(epochSeconds) {
    if (!epochSeconds) return "";
    return new Date(epochSeconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

class ModelDownloaderPanel {
    constructor() {
        this.element = null;
        this.folders = [];
        this.downloads = [];
        this.uploads = [];
        this.files = [];
        this.refreshInterval = null;
        this.nextUploadId = 1;
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
        this.renderUploads();
    }

    renderSkeleton() {
        return `
            <style>
                .cmd-section { margin-bottom: 16px; }
                .cmd-section h3 { margin: 0 0 8px; font-size: 14px; }
                .cmd-section-header { display: flex; justify-content: space-between; align-items: center; }
                .cmd-label { display: block; margin-bottom: 4px; font-size: 12px; color: #aaa; }
                .cmd-input, .cmd-select { width: 100%; padding: 6px; background: #1a1a1a; color: #eee; border: 1px solid #444; border-radius: 4px; box-sizing: border-box; margin-bottom: 8px; }
                .cmd-check { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #aaa; margin-bottom: 8px; cursor: pointer; }
                .cmd-row { display: flex; gap: 6px; }
                .cmd-row > .cmd-input, .cmd-row > .cmd-select { flex: 1; min-width: 0; }
                .cmd-button { padding: 8px 16px; background: #2d7bf6; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
                .cmd-button:hover { background: #1a5fd4; }
                .cmd-button:disabled { background: #555; cursor: not-allowed; }
                .cmd-button-small { padding: 6px 10px; margin-bottom: 8px; font-size: 12px; }
                .cmd-error { color: #ff6b6b; font-size: 12px; margin-top: 4px; }
                .cmd-success { color: #51cf66; font-size: 12px; margin-top: 4px; }
                .cmd-download { border: 1px solid #333; border-radius: 4px; padding: 8px; margin-bottom: 8px; background: #161616; }
                .cmd-download-header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 4px; }
                .cmd-download-header > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .cmd-progress-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
                .cmd-progress-fill { height: 100%; background: #2d7bf6; width: 0%; transition: width 0.2s; }
                .cmd-meta { font-size: 11px; color: #888; margin-top: 4px; }
                .cmd-cancel, .cmd-link { font-size: 11px; color: #ff6b6b; cursor: pointer; margin-left: 8px; }
                .cmd-link { color: #888; font-weight: normal; }
                .cmd-empty { color: #888; font-size: 12px; font-style: italic; }
                .cmd-tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid #333; }
                .cmd-tab { flex: 1; padding: 8px; background: none; color: #aaa; border: none; border-bottom: 2px solid transparent; cursor: pointer; font-size: 13px; }
                .cmd-tab:hover { color: #eee; }
                .cmd-tab.active { color: #fff; border-bottom-color: #2d7bf6; }
                .cmd-file { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #2a2a2a; }
                .cmd-file:hover { background: #202020; }
                .cmd-file-info { flex: 1; min-width: 0; }
                .cmd-file-name { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .cmd-file .cmd-meta { margin-top: 2px; }
                .cmd-delete { flex: none; padding: 4px 10px; font-size: 11px; background: #2a2a2a; color: #ff6b6b; border: 1px solid #553333; border-radius: 4px; cursor: pointer; }
                .cmd-delete:hover { background: #3a2020; }
                .cmd-delete.armed { background: #c92a2a; color: #fff; border-color: #c92a2a; }
                .cmd-delete:disabled { color: #666; border-color: #333; background: #1f1f1f; cursor: not-allowed; }
            </style>
            <div class="cmd-tabs">
                <button class="cmd-tab active" data-tab="download">Download</button>
                <button class="cmd-tab" data-tab="upload">Upload</button>
                <button class="cmd-tab" data-tab="files">Files</button>
            </div>

            <div class="cmd-panel" data-panel="download">
                <div class="cmd-section">
                    <h3>Download Model</h3>
                    <label class="cmd-label" for="cmd-url">Direct URL</label>
                    <input id="cmd-url" class="cmd-input" type="text" placeholder="https://huggingface.co/.../model.safetensors" />

                    <label class="cmd-label" for="cmd-folder">Destination folder</label>
                    <select id="cmd-folder" class="cmd-select cmd-folder-select"></select>

                    <label class="cmd-label" for="cmd-filename">Filename (optional)</label>
                    <input id="cmd-filename" class="cmd-input" type="text" placeholder="leave blank to detect from URL" />

                    <button id="cmd-download" class="cmd-button">Download</button>
                    <div id="cmd-message"></div>
                </div>
                <div class="cmd-section">
                    <h3>Downloads</h3>
                    <div id="cmd-downloads"></div>
                </div>
            </div>

            <div class="cmd-panel" data-panel="upload" hidden>
                <div class="cmd-section">
                    <h3>Upload From This Computer</h3>
                    <label class="cmd-label" for="cmd-upload-file">Model file(s)</label>
                    <input id="cmd-upload-file" class="cmd-input" type="file" multiple />

                    <label class="cmd-label" for="cmd-upload-folder">Destination folder</label>
                    <select id="cmd-upload-folder" class="cmd-select cmd-folder-select"></select>

                    <label class="cmd-label" for="cmd-upload-filename">Filename (optional, single file only)</label>
                    <input id="cmd-upload-filename" class="cmd-input" type="text" placeholder="leave blank to keep the original name" />

                    <label class="cmd-check"><input id="cmd-upload-overwrite" type="checkbox" /> Overwrite existing files</label>

                    <button id="cmd-upload" class="cmd-button">Upload</button>
                    <div id="cmd-upload-message"></div>
                </div>
                <div class="cmd-section">
                    <h3 class="cmd-section-header">Uploads <span id="cmd-uploads-clear" class="cmd-link">clear finished</span></h3>
                    <div id="cmd-uploads"></div>
                </div>
            </div>

            <div class="cmd-panel" data-panel="files" hidden>
                <div class="cmd-section">
                    <div class="cmd-row">
                        <select id="cmd-files-folder" class="cmd-select cmd-folder-select" data-existing-only="1"></select>
                        <button id="cmd-files-refresh" class="cmd-button cmd-button-small">Refresh</button>
                    </div>
                    <div class="cmd-row">
                        <input id="cmd-files-filter" class="cmd-input" type="text" placeholder="Filter by name" />
                        <select id="cmd-files-sort" class="cmd-select">
                            <option value="name">Name</option>
                            <option value="size">Largest first</option>
                            <option value="oldest">Oldest first</option>
                            <option value="newest">Newest first</option>
                        </select>
                    </div>
                    <div id="cmd-files-summary" class="cmd-meta"></div>
                    <div id="cmd-files-message"></div>
                    <div id="cmd-files" style="margin-top: 8px;"></div>
                </div>
            </div>
        `;
    }

    bindEvents() {
        const $ = (sel) => this.element.querySelector(sel);

        this.element.querySelectorAll(".cmd-tab").forEach((tab) => {
            tab.addEventListener("click", () => this.switchTab(tab.dataset.tab));
        });

        $("#cmd-download").addEventListener("click", () => this.onDownloadClick());
        $("#cmd-url").addEventListener("keydown", (e) => {
            if (e.key === "Enter") this.onDownloadClick();
        });

        $("#cmd-upload").addEventListener("click", () => this.onUploadClick());
        $("#cmd-uploads-clear").addEventListener("click", () => {
            this.uploads = this.uploads.filter((u) => ["queued", "running"].includes(u.status));
            this.renderUploads();
        });
        $("#cmd-uploads").addEventListener("click", (e) => {
            const el = e.target.closest(".cmd-cancel");
            if (el) this.cancelUpload(Number(el.dataset.id));
        });

        $("#cmd-files-folder").addEventListener("change", () => this.loadFiles());
        $("#cmd-files-refresh").addEventListener("click", () => this.loadFiles());
        $("#cmd-files-filter").addEventListener("input", () => this.renderFiles());
        $("#cmd-files-sort").addEventListener("change", () => this.renderFiles());
        $("#cmd-files").addEventListener("click", (e) => {
            const btn = e.target.closest(".cmd-delete");
            if (btn && !btn.disabled) this.onDeleteClick(btn);
        });
    }

    switchTab(name) {
        this.element.querySelectorAll(".cmd-tab").forEach((tab) => {
            tab.classList.toggle("active", tab.dataset.tab === name);
        });
        this.element.querySelectorAll(".cmd-panel").forEach((panel) => {
            panel.hidden = panel.dataset.panel !== name;
        });
        if (name === "files") this.loadFiles();
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
        for (const select of this.element.querySelectorAll(".cmd-folder-select")) {
            const existingOnly = select.dataset.existingOnly === "1";
            const previous = select.value;
            select.innerHTML = "";
            const folders = existingOnly ? this.folders.filter((f) => f.exists) : this.folders;
            for (const folder of folders) {
                const option = document.createElement("option");
                option.value = folder.name;
                option.textContent = existingOnly || folder.exists ? folder.name : `${folder.name} (will create)`;
                select.appendChild(option);
            }
            if (folders.length === 0) {
                const option = document.createElement("option");
                option.value = "";
                option.textContent = "No model folders found";
                select.appendChild(option);
            }
            if (previous && folders.some((f) => f.name === previous)) select.value = previous;
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

    showMessage(text, isError, targetId = "cmd-message") {
        const el = this.element.querySelector(`#${targetId}`);
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

    // ---- Uploads -------------------------------------------------------------------------------

    onUploadClick() {
        const fileInput = this.element.querySelector("#cmd-upload-file");
        const filenameInput = this.element.querySelector("#cmd-upload-filename");
        const files = [...fileInput.files];
        const folder = this.element.querySelector("#cmd-upload-folder").value;
        const customName = filenameInput.value.trim();
        const overwrite = this.element.querySelector("#cmd-upload-overwrite").checked;

        if (files.length === 0) {
            this.showMessage("Please choose a file", true, "cmd-upload-message");
            return;
        }
        if (!folder) {
            this.showMessage("Please select a folder", true, "cmd-upload-message");
            return;
        }
        if (customName && files.length > 1) {
            this.showMessage("A custom filename only works when uploading a single file", true, "cmd-upload-message");
            return;
        }

        for (const file of files) {
            this.uploads.push({
                id: this.nextUploadId++,
                file,
                folder,
                filename: customName || file.name,
                overwrite,
                status: "queued",
                loaded: 0,
                total: file.size,
                startedAt: 0,
                error: null,
                xhr: null,
            });
        }
        fileInput.value = "";
        filenameInput.value = "";
        this.showMessage(`Queued ${files.length} file(s) for upload to ${folder}`, false, "cmd-upload-message");
        this.renderUploads();
        this.processUploadQueue();
    }

    hasActiveUploads() {
        return this.uploads.some((u) => ["queued", "running"].includes(u.status));
    }

    processUploadQueue() {
        // One upload at a time so a batch of large files doesn't split the bandwidth.
        if (this.uploads.some((u) => u.status === "running")) return;
        const next = this.uploads.find((u) => u.status === "queued");
        if (next) this.startUpload(next);
    }

    startUpload(up) {
        const params = new URLSearchParams({
            folder: up.folder,
            filename: up.filename,
            overwrite: up.overwrite ? "1" : "0",
        });
        const xhr = new XMLHttpRequest();
        up.xhr = xhr;
        up.status = "running";
        up.startedAt = performance.now();

        const finish = (status, error = null) => {
            up.status = status;
            up.error = error;
            up.xhr = null;
            up.file = null;
            this.renderUploads();
            if (status === "completed") this.onModelFilesChanged(up.folder);
            this.processUploadQueue();
        };

        xhr.open("POST", `${getServiceBaseUrl()}/upload?${params}`);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        xhr.upload.onprogress = (e) => {
            up.loaded = e.loaded;
            if (e.lengthComputable) up.total = e.total;
            this.renderUploads();
        };
        xhr.onload = () => {
            let data = {};
            try {
                data = JSON.parse(xhr.responseText);
            } catch (err) {
                // Non-JSON error page (e.g. from a reverse proxy).
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                up.loaded = up.total;
                finish("completed");
            } else {
                finish("failed", data.error || `HTTP ${xhr.status}`);
            }
        };
        xhr.onerror = () => finish("failed", "Network error (connection lost, or a proxy rejected the upload size)");
        xhr.onabort = () => finish("cancelled");
        xhr.send(up.file);
        this.renderUploads();
    }

    cancelUpload(id) {
        const up = this.uploads.find((u) => u.id === id);
        if (!up) return;
        if (up.status === "running" && up.xhr) {
            up.xhr.abort();
        } else if (up.status === "queued") {
            up.status = "cancelled";
            up.file = null;
            this.renderUploads();
        }
    }

    renderUploads() {
        const container = this.element.querySelector("#cmd-uploads");
        if (this.uploads.length === 0) {
            container.innerHTML = `<div class="cmd-empty">No uploads yet</div>`;
            return;
        }

        const statusOrder = { running: 0, queued: 1, completed: 2, failed: 3, cancelled: 4 };
        const sorted = [...this.uploads].sort((a, b) => {
            const diff = (statusOrder[a.status] ?? 5) - (statusOrder[b.status] ?? 5);
            return diff || b.id - a.id;
        });

        container.innerHTML = sorted
            .map((up) => {
                const percent = up.total ? Math.round((up.loaded / up.total) * 100) : 0;
                const elapsed = up.startedAt ? (performance.now() - up.startedAt) / 1000 : 0;
                const speed = up.status === "running" && elapsed > 0 ? up.loaded / elapsed : 0;
                const eta = speed > 0 ? (up.total - up.loaded) / speed : null;
                const cancelHtml = ["queued", "running"].includes(up.status)
                    ? `<span class="cmd-cancel" data-id="${up.id}">cancel</span>`
                    : "";
                const speedHtml = up.status === "running" ? `· ${formatBytes(speed)}/s · ETA ${formatDuration(eta)}` : "";
                return `
                    <div class="cmd-download">
                        <div class="cmd-download-header">
                            <span title="${this.escapeHtml(up.folder)}/${this.escapeHtml(up.filename)}">${this.escapeHtml(up.filename)}</span>
                            <span>${up.status}${cancelHtml}</span>
                        </div>
                        <div class="cmd-progress-bar"><div class="cmd-progress-fill" style="width: ${percent}%"></div></div>
                        <div class="cmd-meta">
                            ${this.escapeHtml(up.folder)} · ${formatBytes(up.loaded)} / ${formatBytes(up.total)}
                            ${speedHtml}
                            ${up.error ? `· Error: ${this.escapeHtml(up.error)}` : ""}
                        </div>
                    </div>
                `;
            })
            .join("");
    }

    // ---- File manager --------------------------------------------------------------------------

    async loadFiles() {
        const folder = this.element.querySelector("#cmd-files-folder").value;
        if (!folder) {
            this.files = [];
            this.renderFiles();
            return;
        }
        try {
            const response = await apiFetch(`/files?folder=${encodeURIComponent(folder)}`);
            const data = await response.json();
            if (!response.ok) {
                this.showMessage(data.error || "Failed to list files", true, "cmd-files-message");
                return;
            }
            // Ignore stale responses if the folder changed while this request was in flight.
            if (this.element.querySelector("#cmd-files-folder").value !== folder) return;
            this.files = data.files || [];
            this.renderFiles();
        } catch (err) {
            this.showMessage(`Request failed: ${err.message}`, true, "cmd-files-message");
        }
    }

    renderFiles() {
        const container = this.element.querySelector("#cmd-files");
        const summary = this.element.querySelector("#cmd-files-summary");
        const filter = this.element.querySelector("#cmd-files-filter").value.trim().toLowerCase();
        const sort = this.element.querySelector("#cmd-files-sort").value;

        const shown = this.files.filter((f) => !filter || f.path.toLowerCase().includes(filter));
        const sorters = {
            name: (a, b) => a.path.localeCompare(b.path, undefined, { sensitivity: "base", numeric: true }),
            size: (a, b) => b.size - a.size,
            oldest: (a, b) => a.modified - b.modified,
            newest: (a, b) => b.modified - a.modified,
        };
        shown.sort(sorters[sort] || sorters.name);

        const totalSize = this.files.reduce((sum, f) => sum + f.size, 0);
        const shownSize = shown.reduce((sum, f) => sum + f.size, 0);
        summary.textContent = filter
            ? `${shown.length} of ${this.files.length} files · ${formatBytes(shownSize)} of ${formatBytes(totalSize)}`
            : `${this.files.length} files · ${formatBytes(totalSize)}`;

        if (shown.length === 0) {
            container.innerHTML = `<div class="cmd-empty">${this.files.length ? "No files match the filter" : "This folder is empty"}</div>`;
            return;
        }

        container.innerHTML = shown
            .map((f) => {
                const path = this.escapeHtml(f.path);
                const button = f.active
                    ? `<button class="cmd-delete" disabled title="A download or upload is writing this file">in use</button>`
                    : `<button class="cmd-delete" data-path="${path}">Delete</button>`;
                return `
                    <div class="cmd-file">
                        <div class="cmd-file-info">
                            <div class="cmd-file-name" title="${path}">${path}</div>
                            <div class="cmd-meta">${formatBytes(f.size)} · ${formatDate(f.modified)}</div>
                        </div>
                        ${button}
                    </div>
                `;
            })
            .join("");
    }

    async onDeleteClick(btn) {
        // Two-step confirm: first click arms the button, second click within 4s deletes.
        if (btn.dataset.armed !== "1") {
            btn.dataset.armed = "1";
            btn.textContent = "Confirm?";
            btn.classList.add("armed");
            setTimeout(() => {
                if (btn.isConnected && btn.dataset.armed === "1") {
                    btn.dataset.armed = "";
                    btn.textContent = "Delete";
                    btn.classList.remove("armed");
                }
            }, 4000);
            return;
        }

        const folder = this.element.querySelector("#cmd-files-folder").value;
        const path = btn.dataset.path;
        btn.disabled = true;
        btn.textContent = "Deleting…";
        try {
            const params = new URLSearchParams({ folder, path });
            const response = await apiFetch(`/files?${params}`, { method: "DELETE" });
            const data = await response.json();
            if (!response.ok) {
                this.showMessage(data.error || "Delete failed", true, "cmd-files-message");
                await this.loadFiles();
                return;
            }
            this.showMessage(`Deleted ${path}`, false, "cmd-files-message");
            this.files = this.files.filter((f) => f.path !== path);
            this.renderFiles();
            this.onModelFilesChanged(null);
        } catch (err) {
            this.showMessage(`Delete failed: ${err.message}`, true, "cmd-files-message");
            btn.disabled = false;
        }
    }

    onModelFilesChanged(folder) {
        if (folder && folder === this.element.querySelector("#cmd-files-folder").value) {
            this.loadFiles();
        }
        // Refresh model dropdowns on nodes so new/removed files show up without a page reload.
        try {
            Promise.resolve(app.refreshComboInNodes?.()).catch(() => {});
        } catch (err) {
            // Older/newer frontends may not expose this; not critical.
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

        // Leaving the page aborts in-flight uploads, so ask first.
        window.addEventListener("beforeunload", (e) => {
            if (panel.hasActiveUploads()) {
                e.preventDefault();
                e.returnValue = "";
            }
        });

        // Create a floating toggle button that works with both legacy and modern ComfyUI frontends.
        const toggle = document.createElement("button");
        toggle.id = "cmd-model-downloader-toggle";
        toggle.textContent = "Model Downloader";
        toggle.title = "Download, upload and manage models in ComfyUI folders";
        toggle.style.position = "fixed";
        toggle.style.top = "3px";
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
            dialog.style.width = "420px";
            dialog.style.maxWidth = "calc(100vw - 24px)";
            dialog.style.maxHeight = "calc(100vh - 72px)";
            dialog.style.overflow = "auto";
            dialog.style.background = "#1a1a1a";
            dialog.style.border = "1px solid #444";
            dialog.style.borderRadius = "8px";
            dialog.style.zIndex = "10000";
            dialog.style.boxShadow = "0 4px 16px rgba(0,0,0,0.5)";
            dialog.appendChild(panel.element);
            document.body.appendChild(dialog);
            // Reopening the dialog should show current files, not a stale listing.
            if (!panel.element.querySelector('[data-panel="files"]').hidden) panel.loadFiles();
        });

        document.body.appendChild(toggle);
        console.log("[ComfyUI-ModelDownloader] toggle button added to body");
    },
});
