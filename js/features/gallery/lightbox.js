// Large preview over the gallery grid: images, inline video, prev/next across pages. Exports only.

import { formatBytes, formatDate } from "../../lib/util.js";
import { fileUrl } from "./gallery_api.js";

export class Lightbox {
    /**
     * @param source  provides `count()` (all matching files), `itemAt(index)` (async; may switch the grid page,
     *                resolves to {root, item} or null), `isSelected(path)` and `toggle(index)`.
     */
    constructor(source) {
        this.source = source;
        this.index = -1;
        this.current = null;
        this.navSeq = 0;

        this.element = document.createElement("div");
        this.element.className = "spk-lightbox";
        this.element.hidden = true;
        this.element.innerHTML = `
            <div class="spk-lb-stage">
                <div class="spk-lb-status"></div>
                <button type="button" class="spk-lb-nav spk-lb-prev" title="Previous (←)" aria-label="Previous">‹</button>
                <button type="button" class="spk-lb-nav spk-lb-next" title="Next (→)" aria-label="Next">›</button>
            </div>
            <div class="spk-lb-bar">
                <span class="spk-lb-name"></span>
                <span class="spk-lb-meta"></span>
                <span class="spk-gal-spacer"></span>
                <button type="button" class="spk-gal-btn spk-lb-select"></button>
                <a class="spk-lb-open" target="_blank" rel="noopener">Open in new tab</a>
                <button type="button" class="spk-gal-close spk-lb-close" title="Close preview (Esc)" aria-label="Close preview">✕</button>
            </div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        this.stage = $(".spk-lb-stage");
        this.status = $(".spk-lb-status");
        this.prevButton = $(".spk-lb-prev");
        this.nextButton = $(".spk-lb-next");
        this.selectButton = $(".spk-lb-select");
        this.prevButton.addEventListener("click", () => this.step(-1));
        this.nextButton.addEventListener("click", () => this.step(1));
        $(".spk-lb-close").addEventListener("click", () => this.close());
        this.selectButton.addEventListener("click", () => {
            this.source.toggle(this.index);
            this.updateSelectButton();
        });
        // Clicking the dark backdrop (not the media or the arrows) closes the preview.
        this.stage.addEventListener("click", (e) => {
            if (e.target === this.stage) this.close();
        });
    }

    isOpen() {
        return !this.element.hidden;
    }

    async show(index) {
        const count = this.source.count();
        if (index < 0 || index >= count) return;
        const seq = ++this.navSeq;
        this.element.hidden = false;
        this.index = index;
        this.clearMedia();
        this.status.textContent = "Loading…";
        const entry = await this.source.itemAt(index);
        if (seq !== this.navSeq || !this.isOpen()) return; // Navigated again (or closed) meanwhile.
        if (!entry) {
            this.status.textContent = "This file is no longer listed";
            return;
        }
        this.current = entry;
        this.render(entry);
    }

    step(delta) {
        const next = this.index + delta;
        if (next >= 0 && next < this.source.count()) this.show(next);
    }

    close() {
        this.navSeq++;
        this.clearMedia();
        this.element.hidden = true;
        this.current = null;
    }

    clearMedia() {
        for (const el of this.stage.querySelectorAll("img, video, .spk-lb-other")) {
            if (el.tagName === "VIDEO") {
                el.pause();
                el.removeAttribute("src");
                el.load(); // Stops the download of the old video.
            }
            el.remove();
        }
    }

    render({ root, item }) {
        const url = fileUrl(root, item);
        const count = this.source.count();
        this.element.querySelector(".spk-lb-name").textContent = item.path;
        const position = `${(this.index + 1).toLocaleString()} / ${count.toLocaleString()}`;
        this.element.querySelector(".spk-lb-meta").textContent =
            `${position} · ${formatBytes(item.size)} · ${formatDate(item.modified)}`;
        this.element.querySelector(".spk-lb-open").href = url;
        this.prevButton.disabled = this.index <= 0;
        this.nextButton.disabled = this.index >= count - 1;
        this.updateSelectButton();

        let media;
        if (item.kind === "image") {
            media = document.createElement("img");
            media.alt = item.name;
            media.decoding = "async";
            media.addEventListener("load", () => (this.status.textContent = ""));
            media.addEventListener("error", () => (this.status.textContent = "The browser cannot display this image"));
            media.src = url;
        } else if (item.kind === "video") {
            media = document.createElement("video");
            media.controls = true;
            media.autoplay = true;
            media.loop = true;
            media.playsInline = true;
            media.addEventListener("loadeddata", () => (this.status.textContent = ""));
            media.addEventListener("error", () => (this.status.textContent = "The browser cannot play this video"));
            media.src = url;
        } else {
            media = document.createElement("div");
            media.className = "spk-lb-other";
            media.textContent = "No preview for this file type.";
            this.status.textContent = "";
        }
        this.stage.insertBefore(media, this.prevButton);
    }

    updateSelectButton() {
        const selected = this.current ? this.source.isSelected(this.current.item.path) : false;
        this.selectButton.textContent = selected ? "✓ Selected" : "Select";
        this.selectButton.classList.toggle("primary", selected);
    }
}
