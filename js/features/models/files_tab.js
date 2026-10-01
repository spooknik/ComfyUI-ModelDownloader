// Files tab: browse a model folder, filter/sort, and delete files with a two-click confirm.

import { apiFetch } from "../../lib/api.js";
import { escapeHtml, formatBytes, formatDate, showMessage } from "../../lib/util.js";

export class FilesTab {
    /** @param onFilesChanged  called with `null` after a file is deleted. */
    constructor({ folders, onFilesChanged }) {
        this.id = "files";
        this.label = "Files";
        this.onFilesChanged = onFilesChanged;
        this.files = [];

        this.element = document.createElement("div");
        this.element.innerHTML = `
            <div class="spk-section">
                <div class="spk-row">
                    <select id="spk-files-folder" class="spk-select" data-existing-only="1"></select>
                    <button id="spk-files-refresh" class="spk-button spk-button-small">Refresh</button>
                </div>
                <div class="spk-row">
                    <input id="spk-files-filter" class="spk-input" type="text" placeholder="Filter by name" />
                    <select id="spk-files-sort" class="spk-select">
                        <option value="name">Name</option>
                        <option value="size">Largest first</option>
                        <option value="oldest">Oldest first</option>
                        <option value="newest">Newest first</option>
                    </select>
                </div>
                <div id="spk-files-summary" class="spk-meta"></div>
                <div id="spk-files-message"></div>
                <div id="spk-files" style="margin-top: 8px;"></div>
            </div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        folders.register($("#spk-files-folder"));
        $("#spk-files-folder").addEventListener("change", () => this.loadFiles());
        $("#spk-files-refresh").addEventListener("click", () => this.loadFiles());
        $("#spk-files-filter").addEventListener("input", () => this.renderFiles());
        $("#spk-files-sort").addEventListener("change", () => this.renderFiles());
        $("#spk-files").addEventListener("click", (e) => {
            const btn = e.target.closest(".spk-delete");
            if (btn && !btn.disabled) this.onDeleteClick(btn);
        });
    }

    onShow() {
        this.loadFiles();
    }

    get folder() {
        return this.element.querySelector("#spk-files-folder").value;
    }

    showMessage(text, isError) {
        showMessage(this.element.querySelector("#spk-files-message"), text, isError);
    }

    /** Reload the listing if it currently shows `folder` (e.g. after an upload into it). */
    reloadIfShowing(folder) {
        if (folder && folder === this.folder) this.loadFiles();
    }

    async loadFiles() {
        const folder = this.folder;
        if (!folder) {
            this.files = [];
            this.renderFiles();
            return;
        }
        try {
            const response = await apiFetch(`/files?folder=${encodeURIComponent(folder)}`);
            const data = await response.json();
            if (!response.ok) {
                this.showMessage(data.error || "Failed to list files", true);
                return;
            }
            // Ignore stale responses if the folder changed while this request was in flight.
            if (this.folder !== folder) return;
            this.files = data.files || [];
            this.renderFiles();
        } catch (err) {
            this.showMessage(`Request failed: ${err.message}`, true);
        }
    }

    renderFiles() {
        const container = this.element.querySelector("#spk-files");
        const summary = this.element.querySelector("#spk-files-summary");
        const filter = this.element.querySelector("#spk-files-filter").value.trim().toLowerCase();
        const sort = this.element.querySelector("#spk-files-sort").value;

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
            container.innerHTML = `<div class="spk-empty">${this.files.length ? "No files match the filter" : "This folder is empty"}</div>`;
            return;
        }

        container.innerHTML = shown
            .map((f) => {
                const path = escapeHtml(f.path);
                const button = f.active
                    ? `<button class="spk-delete" disabled title="A download or upload is writing this file">in use</button>`
                    : `<button class="spk-delete" data-path="${path}">Delete</button>`;
                return `
                    <div class="spk-file">
                        <div class="spk-file-info">
                            <div class="spk-file-name" title="${path}">${path}</div>
                            <div class="spk-meta">${formatBytes(f.size)} · ${formatDate(f.modified)}</div>
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

        const folder = this.folder;
        const path = btn.dataset.path;
        btn.disabled = true;
        btn.textContent = "Deleting…";
        try {
            const params = new URLSearchParams({ folder, path });
            const response = await apiFetch(`/files?${params}`, { method: "DELETE" });
            const data = await response.json();
            if (!response.ok) {
                this.showMessage(data.error || "Delete failed", true);
                await this.loadFiles();
                return;
            }
            this.showMessage(`Deleted ${path}`, false);
            this.files = this.files.filter((f) => f.path !== path);
            this.renderFiles();
            this.onFilesChanged(null);
        } catch (err) {
            this.showMessage(`Delete failed: ${err.message}`, true);
            btn.disabled = false;
        }
    }
}
