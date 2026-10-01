// Upload tab: send files from this computer into a model folder in retried, resumable chunks.

import { apiFetch, apiUrl } from "../../lib/api.js";
import { escapeHtml, formatBytes, formatDuration, showMessage, sleep } from "../../lib/util.js";

// Uploads are sent in chunks so a dropped connection or a proxy timeout/body limit only costs one chunk.
const UPLOAD_CHUNK_SIZE = 32 * 1024 * 1024;
// Backoff 1, 2, 4, 8, then 15s: ~2 minutes in total, enough to outlast the server's 60s stalled-chunk takeover.
const UPLOAD_MAX_RETRIES = 10;

export class UploadTab {
    /** @param onFilesChanged  called with the folder name after an upload completes. */
    constructor({ folders, onFilesChanged }) {
        this.id = "upload";
        this.label = "Upload";
        this.onFilesChanged = onFilesChanged;
        this.uploads = [];
        this.nextUploadId = 1;

        this.element = document.createElement("div");
        this.element.innerHTML = `
            <div class="spk-section">
                <h3>Upload From This Computer</h3>
                <label class="spk-label" for="spk-upload-file">Model file(s)</label>
                <input id="spk-upload-file" class="spk-input" type="file" multiple />

                <label class="spk-label" for="spk-upload-folder">Destination folder</label>
                <select id="spk-upload-folder" class="spk-select"></select>

                <label class="spk-label" for="spk-upload-filename">Filename (optional, single file only)</label>
                <input id="spk-upload-filename" class="spk-input" type="text" placeholder="leave blank to keep the original name" />

                <label class="spk-check"><input id="spk-upload-overwrite" type="checkbox" /> Overwrite existing files</label>

                <button id="spk-upload" class="spk-button">Upload</button>
                <div id="spk-upload-message"></div>
            </div>
            <div class="spk-section">
                <h3 class="spk-section-header">Uploads <span id="spk-uploads-clear" class="spk-link">clear finished</span></h3>
                <div id="spk-uploads"></div>
            </div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        folders.register($("#spk-upload-folder"));
        $("#spk-upload").addEventListener("click", () => this.onUploadClick());
        $("#spk-uploads-clear").addEventListener("click", () => {
            this.uploads = this.uploads.filter((u) => ["queued", "running"].includes(u.status));
            this.renderUploads();
        });
        $("#spk-uploads").addEventListener("click", (e) => {
            const el = e.target.closest(".spk-cancel");
            if (el) this.cancelUpload(Number(el.dataset.id));
        });
        this.renderUploads();
    }

    showMessage(text, isError) {
        showMessage(this.element.querySelector("#spk-upload-message"), text, isError);
    }

    onUploadClick() {
        const fileInput = this.element.querySelector("#spk-upload-file");
        const filenameInput = this.element.querySelector("#spk-upload-filename");
        const files = [...fileInput.files];
        const folder = this.element.querySelector("#spk-upload-folder").value;
        const customName = filenameInput.value.trim();
        const overwrite = this.element.querySelector("#spk-upload-overwrite").checked;

        if (files.length === 0) {
            this.showMessage("Please choose a file", true);
            return;
        }
        if (!folder) {
            this.showMessage("Please select a folder", true);
            return;
        }
        if (customName && files.length > 1) {
            this.showMessage("A custom filename only works when uploading a single file", true);
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
        this.showMessage(`Queued ${files.length} file(s) for upload to ${folder}`, false);
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

    async startUpload(up) {
        up.status = "running";
        up.startedAt = performance.now();
        this.renderUploads();

        let status = "completed";
        let error = null;
        try {
            const response = await apiFetch("/upload/start", {
                method: "POST",
                body: JSON.stringify({ folder: up.folder, filename: up.filename, overwrite: up.overwrite, size: up.total }),
            });
            const start = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(start.error || `HTTP ${response.status}`);
            up.uploadId = start.upload_id;

            let offset = 0;
            let retries = 0;
            while (true) {
                if (up.cancelRequested) throw new Error("cancelled");
                const end = Math.min(offset + UPLOAD_CHUNK_SIZE, up.total);
                const result = await this.sendChunk(up, offset, up.file.slice(offset, end));
                if (result.ok) {
                    offset = result.data.received;
                    up.loaded = offset;
                    retries = 0;
                    up.retryNote = null;
                    if (result.data.done) break;
                    continue;
                }
                if (up.cancelRequested) throw new Error("cancelled");
                if (!result.retryable || retries >= UPLOAD_MAX_RETRIES) throw new Error(result.error);

                retries += 1;
                up.retryNote = `${result.error} — retrying (${retries}/${UPLOAD_MAX_RETRIES})`;
                this.renderUploads();
                await sleep(Math.min(1000 * 2 ** (retries - 1), 15000));
                // Resume from whatever the server actually committed (a lost response may hide a completed chunk).
                const synced = await this.fetchUploadOffset(up.uploadId);
                if (synced !== null) offset = synced;
                up.loaded = offset;
                up.retryNote = null;
            }
        } catch (err) {
            status = up.cancelRequested ? "cancelled" : "failed";
            error = up.cancelRequested ? null : err.message;
            if (up.uploadId) apiFetch(`/upload/${up.uploadId}`, { method: "DELETE" }).catch(() => {});
        }

        up.status = status;
        up.error = error;
        up.retryNote = null;
        up.xhr = null;
        up.file = null;
        this.renderUploads();
        if (status === "completed") this.onFilesChanged(up.folder);
        this.processUploadQueue();
    }

    sendChunk(up, offset, blob) {
        // XHR (not fetch) so we get upload progress events.
        return new Promise((resolve) => {
            const xhr = new XMLHttpRequest();
            up.xhr = xhr;
            xhr.open("POST", apiUrl(`/upload/${up.uploadId}/chunk?offset=${offset}`));
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            xhr.upload.onprogress = (e) => {
                up.loaded = offset + e.loaded;
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
                    resolve({ ok: true, data });
                } else {
                    // 409 = offset mismatch / chunk still being written; 408/429/5xx = transient proxy or server trouble.
                    const retryable = [408, 409, 429].includes(xhr.status) || xhr.status >= 500;
                    resolve({ ok: false, retryable, error: data.error || `HTTP ${xhr.status}` });
                }
            };
            xhr.onerror = () => resolve({ ok: false, retryable: true, error: "Network error" });
            xhr.onabort = () => resolve({ ok: false, retryable: false, error: "cancelled" });
            xhr.send(blob);
        });
    }

    async fetchUploadOffset(uploadId) {
        try {
            const response = await apiFetch(`/upload/${uploadId}`);
            if (!response.ok) return null;
            return (await response.json()).received;
        } catch (err) {
            return null;
        }
    }

    cancelUpload(id) {
        const up = this.uploads.find((u) => u.id === id);
        if (!up) return;
        if (up.status === "running") {
            up.cancelRequested = true;
            up.xhr?.abort();
        } else if (up.status === "queued") {
            up.status = "cancelled";
            up.file = null;
            this.renderUploads();
        }
    }

    renderUploads() {
        const container = this.element.querySelector("#spk-uploads");
        if (this.uploads.length === 0) {
            container.innerHTML = `<div class="spk-empty">No uploads yet</div>`;
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
                    ? `<span class="spk-cancel" data-id="${up.id}">cancel</span>`
                    : "";
                const speedHtml =
                    up.status !== "running"
                        ? ""
                        : up.retryNote
                          ? `· ${escapeHtml(up.retryNote)}`
                          : `· ${formatBytes(speed)}/s · ETA ${formatDuration(eta)}`;
                return `
                    <div class="spk-download">
                        <div class="spk-download-header">
                            <span title="${escapeHtml(up.folder)}/${escapeHtml(up.filename)}">${escapeHtml(up.filename)}</span>
                            <span>${up.status}${cancelHtml}</span>
                        </div>
                        <div class="spk-progress-bar"><div class="spk-progress-fill" style="width: ${percent}%"></div></div>
                        <div class="spk-meta">
                            ${escapeHtml(up.folder)} · ${formatBytes(up.loaded)} / ${formatBytes(up.total)}
                            ${speedHtml}
                            ${up.error ? `· Error: ${escapeHtml(up.error)}` : ""}
                        </div>
                    </div>
                `;
            })
            .join("");
    }
}
