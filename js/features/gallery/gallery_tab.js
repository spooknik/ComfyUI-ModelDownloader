// Gallery tab in the panel: a short summary of output/ and input/ and the button that opens the full gallery.

import { formatBytes, showMessage } from "../../lib/util.js";
import { ROOTS, listFiles } from "./gallery_api.js";

export class GalleryTab {
    /** @param openGallery  called with a root id ("output" / "input") to open the full-screen gallery. */
    constructor({ openGallery }) {
        this.id = "gallery";
        this.label = "Gallery";
        this.refreshSeq = 0;

        this.element = document.createElement("div");
        this.element.innerHTML = `
            <div class="spk-section">
                <div class="spk-meta" style="margin: 0 0 10px;">Browse, download (as a zip) and delete generated and imported images and videos.</div>
                <div class="spk-gal-summary"></div>
                <button type="button" class="spk-button spk-gal-open">Open gallery</button>
                <div class="spk-gal-tab-message"></div>
            </div>
        `;
        this.summary = this.element.querySelector(".spk-gal-summary");
        this.rows = new Map();
        for (const root of ROOTS) {
            const row = document.createElement("button");
            row.type = "button";
            row.className = "spk-gal-summary-row";
            row.dataset.root = root.id;
            row.innerHTML = `<span></span><span>…</span>`;
            row.firstElementChild.textContent = root.label;
            row.addEventListener("click", () => openGallery(root.id));
            this.summary.appendChild(row);
            this.rows.set(root.id, row);
        }
        this.element.querySelector(".spk-gal-open").addEventListener("click", () => openGallery());
    }

    onShow() {
        this.refresh();
    }

    /** Recount both folders (recursive totals; limit=0 returns no items). */
    async refresh() {
        const seq = ++this.refreshSeq;
        const message = this.element.querySelector(".spk-gal-tab-message");
        try {
            const results = await Promise.all(
                ROOTS.map((root) => listFiles({ root: root.id, recursive: true, limit: 0 }))
            );
            if (seq !== this.refreshSeq) return;
            message.textContent = "";
            results.forEach((data, i) => {
                const row = this.rows.get(ROOTS[i].id);
                const files = `${data.total.toLocaleString()} file${data.total === 1 ? "" : "s"}`;
                row.lastElementChild.textContent = `${files} · ${formatBytes(data.total_size)}`;
                row.title = `${data.directory}\nClick to open in the gallery`;
            });
        } catch (err) {
            if (seq === this.refreshSeq) showMessage(message, `Cannot read the gallery folders: ${err.message}`, true);
        }
    }
}
