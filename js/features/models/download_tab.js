// Download tab: start URL downloads into a model folder and follow their progress.

import { apiFetch } from "../../lib/api.js";
import { escapeHtml, formatBytes, formatDuration, showMessage } from "../../lib/util.js";

export class DownloadTab {
    constructor({ folders }) {
        this.id = "download";
        this.label = "Download";
        this.downloads = [];
        this.refreshInterval = null;

        this.element = document.createElement("div");
        this.element.innerHTML = `
            <div class="spk-section">
                <h3>Download Model</h3>
                <label class="spk-label" for="spk-url">Direct URL</label>
                <input id="spk-url" class="spk-input" type="text" placeholder="https://huggingface.co/.../model.safetensors" />

                <label class="spk-label" for="spk-folder">Destination folder</label>
                <select id="spk-folder" class="spk-select"></select>

                <label class="spk-label" for="spk-filename">Filename (optional)</label>
                <input id="spk-filename" class="spk-input" type="text" placeholder="leave blank to detect from URL" />

                <button id="spk-download" class="spk-button">Download</button>
                <div id="spk-message"></div>
            </div>
            <div class="spk-section">
                <h3>Downloads</h3>
                <div id="spk-downloads"></div>
            </div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        folders.register($("#spk-folder"));
        $("#spk-download").addEventListener("click", () => this.onDownloadClick());
        $("#spk-url").addEventListener("keydown", (e) => {
            if (e.key === "Enter") this.onDownloadClick();
        });
    }

    showMessage(text, isError) {
        showMessage(this.element.querySelector("#spk-message"), text, isError);
    }

    async onDownloadClick() {
        const url = this.element.querySelector("#spk-url").value.trim();
        const folder = this.element.querySelector("#spk-folder").value;
        const filename = this.element.querySelector("#spk-filename").value.trim();

        if (!url) {
            this.showMessage("Please enter a URL", true);
            return;
        }
        if (!folder) {
            this.showMessage("Please select a folder", true);
            return;
        }

        const downloadBtn = this.element.querySelector("#spk-download");
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
                this.element.querySelector("#spk-url").value = "";
                this.element.querySelector("#spk-filename").value = "";
                await this.refreshDownloads();
            }
        } catch (err) {
            this.showMessage(`Request failed: ${err.message}`, true);
        } finally {
            downloadBtn.disabled = false;
        }
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
        const container = this.element.querySelector("#spk-downloads");
        if (this.downloads.length === 0) {
            container.innerHTML = `<div class="spk-empty">No downloads yet</div>`;
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
            div.className = "spk-download";

            const percent = dl.bytes_total ? Math.round((dl.bytes_downloaded / dl.bytes_total) * 100) : 0;
            const cancelHtml = ["pending", "running"].includes(dl.status)
                ? `<span class="spk-cancel" data-id="${dl.download_id}">cancel</span>`
                : "";

            div.innerHTML = `
                <div class="spk-download-header">
                    <span>${escapeHtml(dl.filename || "model")}</span>
                    <span>${dl.status}${cancelHtml}</span>
                </div>
                <div class="spk-progress-bar"><div class="spk-progress-fill" style="width: ${percent}%"></div></div>
                <div class="spk-meta">
                    ${formatBytes(dl.bytes_downloaded)} / ${formatBytes(dl.bytes_total)}
                    · ${formatBytes(dl.speed_bps)}/s
                    · ETA ${formatDuration(dl.eta_seconds)}
                    ${dl.error ? `· Error: ${escapeHtml(dl.error)}` : ""}
                </div>
            `;
            container.appendChild(div);
        }

        container.querySelectorAll(".spk-cancel").forEach((el) => {
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
}
