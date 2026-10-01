// Full-screen gallery: browse output/ or input/ as a paged thumbnail grid, multi-select, zip-download, delete.
//
// The grid shows one server page (PAGE_SIZE files) at a time, so even folders with tens of thousands of files
// only ever have a few hundred tiles in the DOM. Selection is a Map of root-relative path -> size and survives
// paging, searching and filtering (it is cleared when switching between output and input).

import { loadSetting, saveSetting } from "../../lib/storage.js";
import { clamp, formatBytes, formatDate, showMessage } from "../../lib/util.js";
import {
    PAGE_SIZE,
    ROOTS,
    deletePaths,
    listFiles,
    prepareZip,
    startZipDownload,
    thumbSizeFor,
    thumbUrl,
} from "./gallery_api.js";
import { Lightbox } from "./lightbox.js";

const PREFS_KEY = "spooktools.gallery.prefs";
const SORTS = [
    ["newest", "Newest first"],
    ["oldest", "Oldest first"],
    ["name", "Name"],
    ["size", "Largest first"],
];
const KINDS = [
    ["all", "All files"],
    ["image", "Images"],
    ["video", "Videos"],
    ["other", "Other files"],
];
const TILE_MIN = 96;
const TILE_MAX = 320;
const DEFAULT_PREFS = { root: "output", recursive: true, sort: "newest", kind: "all", tile: 160 };
const SEARCH_DEBOUNCE_MS = 250;
const ARM_TIMEOUT_MS = 4000;
// While the gallery is open these events stop at the window so ComfyUI never sees them (canvas shortcuts,
// pasting images/nodes into the workflow). Default actions such as typing into the search box still happen.
const SWALLOWED_EVENTS = ["keydown", "keyup", "keypress", "paste", "copy", "cut"];

const VIDEO_ICON = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
const FILE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/></svg>`;

function loadPrefs() {
    const saved = loadSetting(PREFS_KEY) || {};
    const prefs = { ...DEFAULT_PREFS };
    if (ROOTS.some((r) => r.id === saved.root)) prefs.root = saved.root;
    if (typeof saved.recursive === "boolean") prefs.recursive = saved.recursive;
    if (SORTS.some(([id]) => id === saved.sort)) prefs.sort = saved.sort;
    if (KINDS.some(([id]) => id === saved.kind)) prefs.kind = saved.kind;
    if (Number.isFinite(saved.tile)) prefs.tile = clamp(saved.tile, TILE_MIN, TILE_MAX);
    return prefs;
}

function plural(count, word) {
    return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
}

function extensionOf(name) {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1) : "";
}

function isTextField(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
    return el.tagName === "INPUT" && !["checkbox", "radio", "button", "range"].includes(el.type);
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

export class GalleryOverlay {
    /** @param onFilesChanged  called with the root after files were deleted from it. */
    constructor({ onFilesChanged }) {
        this.onFilesChanged = onFilesChanged;
        this.prefs = loadPrefs();
        this.subfolder = "";
        this.query = "";
        this.page = 0;
        this.items = [];
        this.total = 0;
        this.totalSize = 0;
        this.subfolders = [];
        this.directory = "";
        this.selection = new Map();
        this.selectionSize = 0;
        this.anchor = null; // Index (over all matches) of the last plain/ctrl click, for shift-click ranges.
        this.loadSeq = 0;
        this.matches = null; // { key, promise } of the paths_only listing for the current query.
        this.brokenThumbs = new Set(); // Thumbnail URLs that failed (URLs include the file's mtime).
        this.busy = false;
        this.deleteArmed = false;
        this.armTimer = null;
        this.searchTimer = null;
        this.restoreFocus = null;
        this.onCapturedEvent = (e) => this.handleCapturedEvent(e);
        this.build();
    }

    // ---- DOM -----------------------------------------------------------------------------------------------

    build() {
        this.element = el("div", "spk-gallery");
        this.element.hidden = true;
        this.element.innerHTML = `
            <div class="spk-gal-window" role="dialog" aria-modal="true" aria-label="Gallery" tabindex="-1">
                <div class="spk-gal-bar">
                    <span class="spk-gal-title">Gallery</span>
                    <div class="spk-gal-seg spk-gal-roots" role="group" aria-label="Folder"></div>
                    <nav class="spk-gal-crumbs" aria-label="Current folder"></nav>
                    <select class="spk-select spk-gal-subfolders" aria-label="Open a subfolder"></select>
                    <span class="spk-gal-spacer"></span>
                    <button type="button" class="spk-gal-close" title="Close (Esc)" aria-label="Close gallery">✕</button>
                </div>
                <div class="spk-gal-bar">
                    <input type="search" class="spk-input spk-gal-search" placeholder="Search file names" aria-label="Search file names" />
                    <select class="spk-select spk-gal-sort" aria-label="Sort"></select>
                    <select class="spk-select spk-gal-kind" aria-label="File type"></select>
                    <label class="spk-check" title="Also list files in subfolders"><input type="checkbox" class="spk-gal-recursive" /> Subfolders</label>
                    <label class="spk-gal-size">Size <input type="range" class="spk-gal-tilesize" min="${TILE_MIN}" max="${TILE_MAX}" step="8" aria-label="Thumbnail size" /></label>
                    <button type="button" class="spk-gal-btn spk-gal-refresh" title="Reload the listing">Refresh</button>
                </div>
                <div class="spk-gal-bar">
                    <span class="spk-gal-selinfo" aria-live="polite"></span>
                    <button type="button" class="spk-gal-btn spk-gal-selpage" title="Select every file on this page (Ctrl+A)">Select page</button>
                    <button type="button" class="spk-gal-btn spk-gal-selall"></button>
                    <button type="button" class="spk-gal-btn spk-gal-clear" title="Clear the selection (Esc)">Clear</button>
                    <span class="spk-gal-spacer"></span>
                    <button type="button" class="spk-gal-btn primary spk-gal-zip">Download zip</button>
                    <button type="button" class="spk-delete spk-gal-delete">Delete</button>
                </div>
                <div class="spk-gal-message"><div></div></div>
                <div class="spk-gal-scroll">
                    <div class="spk-gal-grid"></div>
                    <div class="spk-gal-empty" hidden></div>
                </div>
                <div class="spk-gal-pager">
                    <button type="button" class="spk-gal-btn spk-gal-prev">‹ Prev</button>
                    <label>Page <select class="spk-select spk-gal-page" aria-label="Page"></select></label>
                    <span class="spk-gal-range"></span>
                    <button type="button" class="spk-gal-btn spk-gal-next">Next ›</button>
                </div>
            </div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        this.window = $(".spk-gal-window");
        this.rootsEl = $(".spk-gal-roots");
        this.crumbs = $(".spk-gal-crumbs");
        this.subfolderSelect = $(".spk-gal-subfolders");
        this.searchInput = $(".spk-gal-search");
        this.sortSelect = $(".spk-gal-sort");
        this.kindSelect = $(".spk-gal-kind");
        this.recursiveInput = $(".spk-gal-recursive");
        this.tileInput = $(".spk-gal-tilesize");
        this.selInfo = $(".spk-gal-selinfo");
        this.selPageButton = $(".spk-gal-selpage");
        this.selAllButton = $(".spk-gal-selall");
        this.clearButton = $(".spk-gal-clear");
        this.zipButton = $(".spk-gal-zip");
        this.deleteButton = $(".spk-gal-delete");
        this.messageEl = $(".spk-gal-message > div");
        this.scroller = $(".spk-gal-scroll");
        this.grid = $(".spk-gal-grid");
        this.emptyEl = $(".spk-gal-empty");
        this.prevButton = $(".spk-gal-prev");
        this.nextButton = $(".spk-gal-next");
        this.pageSelect = $(".spk-gal-page");
        this.rangeEl = $(".spk-gal-range");

        for (const root of ROOTS) {
            const button = el("button", "", root.label);
            button.type = "button";
            button.dataset.root = root.id;
            button.addEventListener("click", () => this.switchRoot(root.id));
            this.rootsEl.appendChild(button);
        }
        for (const [select, options] of [
            [this.sortSelect, SORTS],
            [this.kindSelect, KINDS],
        ]) {
            for (const [value, label] of options) select.appendChild(new Option(label, value));
        }
        this.sortSelect.value = this.prefs.sort;
        this.kindSelect.value = this.prefs.kind;
        this.recursiveInput.checked = this.prefs.recursive;
        this.tileInput.value = String(this.prefs.tile);
        this.grid.style.setProperty("--spk-gal-tile", `${this.prefs.tile}px`);

        this.lightbox = new Lightbox({
            count: () => this.total,
            itemAt: (index) => this.itemAt(index),
            isSelected: (path) => this.selection.has(path),
            toggle: (index) => this.toggleIndex(index),
        });
        this.window.appendChild(this.lightbox.element);

        this.bindEvents();
    }

    bindEvents() {
        this.element.querySelector(".spk-gal-close").addEventListener("click", () => this.close());
        // Clicking the dimmed backdrop around the window closes the gallery.
        this.element.addEventListener("click", (e) => {
            if (e.target === this.element) this.close();
        });
        // Keep wheel, drag-and-drop and context menus inside the gallery (ComfyUI listens on the document; a file
        // dropped here must not be loaded as a workflow).
        this.element.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });
        for (const type of ["dragover", "drop", "contextmenu"]) {
            this.element.addEventListener(type, (e) => {
                e.stopPropagation();
                if (type !== "contextmenu") e.preventDefault();
            });
        }

        this.subfolderSelect.addEventListener("change", () => {
            const name = this.subfolderSelect.value;
            this.subfolderSelect.value = "";
            if (name) this.navigate(this.subfolder ? `${this.subfolder}/${name}` : name);
        });
        this.crumbs.addEventListener("click", (e) => {
            const button = e.target.closest("button[data-path]");
            if (button && !button.disabled) this.navigate(button.dataset.path);
        });
        this.searchInput.addEventListener("input", () => {
            clearTimeout(this.searchTimer);
            this.searchTimer = setTimeout(() => {
                const query = this.searchInput.value.trim();
                if (query !== this.query) {
                    this.query = query;
                    this.resetAndLoad();
                }
            }, SEARCH_DEBOUNCE_MS);
        });
        this.sortSelect.addEventListener("change", () => this.setPref("sort", this.sortSelect.value));
        this.kindSelect.addEventListener("change", () => this.setPref("kind", this.kindSelect.value));
        this.recursiveInput.addEventListener("change", () => this.setPref("recursive", this.recursiveInput.checked));
        this.tileInput.addEventListener("input", () => {
            this.grid.style.setProperty("--spk-gal-tile", `${this.tileInput.value}px`);
        });
        this.tileInput.addEventListener("change", () => {
            const before = this.thumbPixels();
            this.prefs.tile = Number(this.tileInput.value);
            saveSetting(PREFS_KEY, this.prefs);
            if (this.thumbPixels() !== before) this.renderGrid(); // Bigger tiles: sharper thumbnails.
        });
        this.element.querySelector(".spk-gal-refresh").addEventListener("click", () => {
            this.matches = null;
            this.reload();
        });

        this.selPageButton.addEventListener("click", () => this.selectPage());
        this.selAllButton.addEventListener("click", () => this.selectAllMatching());
        this.clearButton.addEventListener("click", () => this.clearSelection());
        this.zipButton.addEventListener("click", () => this.downloadZip());
        this.deleteButton.addEventListener("click", () => this.onDeleteClick());

        this.prevButton.addEventListener("click", () => this.goToPage(this.page - 1));
        this.nextButton.addEventListener("click", () => this.goToPage(this.page + 1));
        this.pageSelect.addEventListener("change", () => this.goToPage(Number(this.pageSelect.value)));

        // Shift-click must extend the selection, not select text.
        this.grid.addEventListener("mousedown", (e) => {
            if (e.shiftKey) e.preventDefault();
        });
        this.grid.addEventListener("click", (e) => this.onGridClick(e));
        this.grid.addEventListener("dblclick", (e) => {
            const tile = e.target.closest(".spk-gal-tile");
            if (tile && !e.target.closest(".spk-gal-zoom")) this.openLightbox(Number(tile.dataset.index));
        });
    }

    // ---- open / close / keyboard ---------------------------------------------------------------------------

    isOpen() {
        return !this.element.hidden;
    }

    open(root) {
        if (root && root !== this.prefs.root) this.switchRoot(root, { load: false });
        if (!this.element.isConnected) document.body.appendChild(this.element);
        if (!this.isOpen()) {
            this.restoreFocus = document.activeElement;
            this.element.hidden = false;
            for (const type of SWALLOWED_EVENTS) window.addEventListener(type, this.onCapturedEvent, true);
            this.window.focus({ preventScroll: true });
        }
        this.matches = null;
        this.reload(); // Show files generated since the gallery was last open.
    }

    close() {
        this.lightbox.close();
        this.disarmDelete();
        this.element.hidden = true;
        for (const type of SWALLOWED_EVENTS) window.removeEventListener(type, this.onCapturedEvent, true);
        const previous = this.restoreFocus;
        this.restoreFocus = null;
        if (previous?.isConnected && typeof previous.focus === "function") previous.focus({ preventScroll: true });
    }

    handleCapturedEvent(e) {
        if (!this.isOpen()) return;
        e.stopImmediatePropagation();
        if (e.type !== "keydown") return;
        if (e.key === "Escape") {
            e.preventDefault();
            if (this.lightbox.isOpen()) this.lightbox.close();
            else if (this.selection.size) this.clearSelection();
            else this.close();
            return;
        }
        if (this.lightbox.isOpen()) {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                e.preventDefault();
                this.lightbox.step(e.key === "ArrowLeft" ? -1 : 1);
            }
            return;
        }
        if (isTextField(e.target)) return;
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
            e.preventDefault();
            this.selectPage();
        }
    }

    // ---- query state ---------------------------------------------------------------------------------------

    get root() {
        return this.prefs.root;
    }

    setPref(key, value) {
        this.prefs[key] = value;
        saveSetting(PREFS_KEY, this.prefs);
        this.resetAndLoad();
    }

    switchRoot(root, { load = true } = {}) {
        if (root === this.prefs.root) return;
        const hadSelection = this.selection.size > 0;
        this.prefs.root = root;
        saveSetting(PREFS_KEY, this.prefs);
        this.subfolder = "";
        this.clearSelection();
        if (hadSelection) this.showMessage("Selection cleared (it belonged to the other folder).", false);
        if (load) this.resetAndLoad();
    }

    navigate(subfolder) {
        this.subfolder = subfolder;
        this.resetAndLoad();
    }

    resetAndLoad() {
        this.page = 0;
        this.anchor = null;
        this.matches = null;
        this.reload({ scrollTop: true });
    }

    queryParams() {
        return {
            root: this.prefs.root,
            subfolder: this.subfolder,
            recursive: this.prefs.recursive,
            q: this.query,
            sort: this.prefs.sort,
            kind: this.prefs.kind,
        };
    }

    async reload({ scrollTop = false } = {}) {
        const seq = ++this.loadSeq;
        this.grid.classList.add("loading");
        this.renderHeader();
        try {
            const data = await listFiles({ ...this.queryParams(), offset: this.page * PAGE_SIZE, limit: PAGE_SIZE });
            if (seq !== this.loadSeq) return; // A newer request (folder/filter/page change) superseded this one.
            const lastPage = Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1);
            if (this.page > lastPage) {
                // Files were removed (here or elsewhere) and this page no longer exists.
                this.page = lastPage;
                return this.reload({ scrollTop });
            }
            this.items = data.items;
            this.total = data.total;
            this.totalSize = data.total_size;
            this.subfolders = data.subfolders;
            this.directory = data.directory;
            this.renderHeader();
            this.renderGrid();
            this.renderPager();
            this.renderSelection();
            if (scrollTop) this.scroller.scrollTop = 0;
        } catch (err) {
            if (seq !== this.loadSeq) return;
            if (this.subfolder && /not found/i.test(err.message)) {
                // The folder was deleted or renamed outside the gallery: go back to the top.
                this.showMessage(`Folder "${this.subfolder}" no longer exists.`, true);
                this.subfolder = "";
                return this.resetAndLoad();
            }
            this.showMessage(`Could not list files: ${err.message}`, true);
        } finally {
            if (seq === this.loadSeq) this.grid.classList.remove("loading");
        }
    }

    goToPage(page) {
        const lastPage = Math.max(0, Math.ceil(this.total / PAGE_SIZE) - 1);
        page = clamp(page, 0, lastPage);
        if (page === this.page) return;
        this.page = page;
        this.reload({ scrollTop: true });
    }

    /** For the lightbox: the file at `index` over all matches, switching the grid to its page if needed. */
    async itemAt(index) {
        const page = Math.floor(index / PAGE_SIZE);
        if (page !== this.page) {
            this.page = page;
            await this.reload({ scrollTop: true });
        }
        const local = index - this.page * PAGE_SIZE;
        const item = this.items[local];
        this.grid.children[local]?.scrollIntoView({ block: "nearest" });
        return item ? { root: this.prefs.root, item } : null;
    }

    /** All matching paths and sizes in listing order (cached per query until a reload or change). */
    matchingPaths() {
        const params = this.queryParams();
        const key = JSON.stringify(params);
        if (this.matches?.key !== key) {
            const promise = listFiles({ ...params, paths_only: true });
            this.matches = { key, promise };
            promise.catch(() => {
                if (this.matches?.promise === promise) this.matches = null;
            });
        }
        return this.matches.promise;
    }

    // ---- rendering -----------------------------------------------------------------------------------------

    showMessage(text, isError) {
        showMessage(this.messageEl, text, isError);
    }

    clearMessage() {
        this.messageEl.textContent = "";
    }

    thumbPixels() {
        return thumbSizeFor(this.prefs.tile * (window.devicePixelRatio || 1));
    }

    renderHeader() {
        for (const button of this.rootsEl.children) {
            button.classList.toggle("active", button.dataset.root === this.prefs.root);
        }
        const rootLabel = ROOTS.find((r) => r.id === this.prefs.root)?.label ?? this.prefs.root;
        const crumbs = [[rootLabel, ""]];
        let path = "";
        for (const part of this.subfolder ? this.subfolder.split("/") : []) {
            path = path ? `${path}/${part}` : part;
            crumbs.push([part, path]);
        }
        this.crumbs.replaceChildren();
        crumbs.forEach(([label, target], i) => {
            if (i > 0) this.crumbs.appendChild(el("span", "", "/"));
            const button = el("button", "", label);
            button.type = "button";
            button.dataset.path = target;
            button.disabled = i === crumbs.length - 1;
            if (i === 0 && this.directory) button.title = this.directory;
            this.crumbs.appendChild(button);
        });
        this.subfolderSelect.replaceChildren(
            new Option(this.subfolders.length ? `Subfolders (${this.subfolders.length})…` : "No subfolders", "")
        );
        for (const name of this.subfolders) this.subfolderSelect.appendChild(new Option(name, name));
        this.subfolderSelect.disabled = this.subfolders.length === 0;
    }

    renderGrid() {
        const thumbPx = this.thumbPixels();
        const fragment = document.createDocumentFragment();
        this.items.forEach((item, index) => fragment.appendChild(this.buildTile(item, index, thumbPx)));
        this.grid.replaceChildren(fragment);
        this.emptyEl.hidden = this.items.length > 0;
        if (!this.items.length) {
            const filtered = this.query || this.prefs.kind !== "all";
            this.emptyEl.textContent = filtered ? "No files match the search or filter." : "No files here yet.";
        }
    }

    buildTile(item, index, thumbPx) {
        const tile = el("div", "spk-gal-tile");
        tile.dataset.index = String(index);
        tile.classList.toggle("selected", this.selection.has(item.path));
        tile.title = `${item.path}\n${formatBytes(item.size)} · ${formatDate(item.modified)}`;

        const thumb = el("div", "spk-gal-thumb");
        const url = item.kind === "image" ? thumbUrl(this.prefs.root, item, thumbPx) : null;
        if (url && this.brokenThumbs.has(url)) {
            tile.classList.add("broken"); // Failed before (corrupt file): don't ask the server again.
        } else if (url) {
            const img = document.createElement("img");
            img.loading = "lazy";
            img.decoding = "async";
            img.alt = "";
            img.draggable = false;
            img.addEventListener(
                "error",
                () => {
                    this.brokenThumbs.add(url);
                    tile.classList.add("broken");
                },
                { once: true }
            );
            img.src = url;
            thumb.appendChild(img);
        } else {
            const icon = el("div", "spk-gal-icon");
            icon.innerHTML = item.kind === "video" ? VIDEO_ICON : FILE_ICON;
            icon.appendChild(el("span", "", extensionOf(item.name) || "file"));
            thumb.appendChild(icon);
            if (item.kind === "video") thumb.appendChild(el("span", "spk-gal-badge", "video"));
        }
        thumb.appendChild(el("span", "spk-gal-check", "✓"));
        const zoom = el("button", "spk-gal-zoom", "⤢");
        zoom.type = "button";
        zoom.title = "Preview (or double-click)";
        zoom.setAttribute("aria-label", `Preview ${item.name}`);
        thumb.appendChild(zoom);

        const caption = el("div", "spk-gal-caption");
        caption.appendChild(el("div", "spk-gal-name", item.name));
        caption.appendChild(el("div", "spk-gal-meta", `${formatBytes(item.size)} · ${formatDate(item.modified)}`));
        tile.append(thumb, caption);
        return tile;
    }

    renderPager() {
        const pages = Math.max(1, Math.ceil(this.total / PAGE_SIZE));
        if (this.pageSelect.options.length !== pages) {
            this.pageSelect.replaceChildren();
            for (let i = 0; i < pages; i++) this.pageSelect.appendChild(new Option(`${i + 1} of ${pages}`, String(i)));
        }
        this.pageSelect.value = String(this.page);
        this.pageSelect.disabled = pages <= 1;
        this.prevButton.disabled = this.page <= 0;
        this.nextButton.disabled = this.page >= pages - 1;
        if (!this.total) {
            this.rangeEl.textContent = "No files";
            return;
        }
        const first = this.page * PAGE_SIZE + 1;
        const last = this.page * PAGE_SIZE + this.items.length;
        const range = `${first.toLocaleString()}–${last.toLocaleString()}`;
        this.rangeEl.textContent = `${range} of ${plural(this.total, "file")} · ${formatBytes(this.totalSize)}`;
    }

    renderSelection() {
        const count = this.selection.size;
        this.selInfo.textContent = count
            ? `${plural(count, "file")} selected · ${formatBytes(this.selectionSize)}`
            : "Nothing selected";
        this.selInfo.classList.toggle("has", count > 0);
        this.grid.classList.toggle("has-selection", count > 0);
        this.selAllButton.textContent = `Select all ${this.total.toLocaleString()} matching`;
        this.selAllButton.disabled = this.busy || this.total === 0;
        this.selPageButton.disabled = this.busy || this.items.length === 0;
        this.clearButton.disabled = this.busy || count === 0;
        this.zipButton.disabled = this.busy || count === 0;
        this.deleteButton.disabled = this.busy || count === 0;
        if (this.deleteArmed) this.deleteButton.textContent = `Delete ${plural(count, "file")}?`;
    }

    updateTiles() {
        for (const tile of this.grid.children) {
            const item = this.items[Number(tile.dataset.index)];
            tile.classList.toggle("selected", !!item && this.selection.has(item.path));
        }
    }

    // ---- selection -----------------------------------------------------------------------------------------

    select(path, size) {
        if (this.selection.has(path)) return;
        this.selection.set(path, size);
        this.selectionSize += size;
    }

    deselect(path) {
        if (!this.selection.has(path)) return;
        this.selectionSize -= this.selection.get(path);
        this.selection.delete(path);
    }

    selectionChanged() {
        this.disarmDelete();
        this.updateTiles();
        this.renderSelection();
    }

    onGridClick(e) {
        const tile = e.target.closest(".spk-gal-tile");
        if (!tile) return;
        const local = Number(tile.dataset.index);
        if (e.target.closest(".spk-gal-zoom")) {
            this.openLightbox(local);
            return;
        }
        const index = this.page * PAGE_SIZE + local;
        if (e.shiftKey && this.anchor !== null) {
            this.selectRange(this.anchor, index);
            return;
        }
        // Plain and Ctrl/Cmd clicks both toggle (this is a multi-select grid, not a file explorer).
        this.toggleIndex(index);
        this.anchor = index;
    }

    toggleIndex(index) {
        const item = this.items[index - this.page * PAGE_SIZE];
        if (!item) return;
        if (this.selection.has(item.path)) this.deselect(item.path);
        else this.select(item.path, item.size);
        this.selectionChanged();
    }

    async selectRange(from, to) {
        const [lo, hi] = from <= to ? [from, to] : [to, from];
        const start = this.page * PAGE_SIZE;
        if (lo >= start && hi < start + this.items.length) {
            for (let i = lo; i <= hi; i++) this.select(this.items[i - start].path, this.items[i - start].size);
            this.selectionChanged();
            return;
        }
        // The anchor is on another page: take the range from the full listing.
        try {
            const all = await this.matchingPaths();
            for (let i = lo; i <= Math.min(hi, all.paths.length - 1); i++) this.select(all.paths[i], all.sizes[i]);
            this.selectionChanged();
        } catch (err) {
            this.showMessage(`Could not select the range: ${err.message}`, true);
        }
    }

    selectPage() {
        for (const item of this.items) this.select(item.path, item.size);
        this.selectionChanged();
    }

    async selectAllMatching() {
        this.setBusy(true);
        try {
            const all = await this.matchingPaths();
            all.paths.forEach((path, i) => this.select(path, all.sizes[i]));
            this.showMessage(`Selected all ${plural(all.paths.length, "matching file")}.`, false);
        } catch (err) {
            this.showMessage(`Could not select all: ${err.message}`, true);
        } finally {
            this.setBusy(false);
            this.selectionChanged();
        }
    }

    clearSelection() {
        this.selection.clear();
        this.selectionSize = 0;
        this.anchor = null;
        this.selectionChanged();
    }

    setBusy(busy) {
        this.busy = busy;
        this.renderSelection();
    }

    openLightbox(local) {
        this.lightbox.show(this.page * PAGE_SIZE + local);
    }

    // ---- actions -------------------------------------------------------------------------------------------

    async downloadZip() {
        const paths = [...this.selection.keys()];
        if (!paths.length) return;
        this.setBusy(true);
        this.showMessage(`Preparing a zip of ${plural(paths.length, "file")}…`, false);
        try {
            const zip = await prepareZip(this.prefs.root, paths);
            startZipDownload(zip.token, zip.filename);
            let text = `Downloading ${plural(zip.count, "file")} (${formatBytes(zip.total_size)}) as ${zip.filename}.`;
            const missing = zip.missing.length;
            if (missing === 1) text += " 1 selected file no longer exists and was left out.";
            else if (missing) text += ` ${plural(missing, "selected file")} no longer exist and were left out.`;
            this.showMessage(text, false);
        } catch (err) {
            this.showMessage(`Zip download failed: ${err.message}`, true);
        } finally {
            this.setBusy(false);
        }
    }

    disarmDelete() {
        clearTimeout(this.armTimer);
        if (!this.deleteArmed) return;
        this.deleteArmed = false;
        this.deleteButton.textContent = "Delete";
        this.deleteButton.classList.remove("armed");
    }

    async onDeleteClick() {
        const count = this.selection.size;
        if (!count || this.busy) return;
        // Two-step confirm: the first click arms the button, a second click within a few seconds deletes.
        if (!this.deleteArmed) {
            this.deleteArmed = true;
            this.deleteButton.classList.add("armed");
            this.deleteButton.textContent = `Delete ${plural(count, "file")}?`;
            this.armTimer = setTimeout(() => this.disarmDelete(), ARM_TIMEOUT_MS);
            return;
        }
        this.disarmDelete();
        const root = this.prefs.root;
        const paths = [...this.selection.keys()];
        this.setBusy(true);
        this.showMessage(`Deleting ${plural(paths.length, "file")}…`, false);
        let result = null;
        try {
            result = await deletePaths(root, paths, (done, total) =>
                this.showMessage(`Deleting… ${done.toLocaleString()} of ${total.toLocaleString()}`, false)
            );
        } catch (err) {
            this.showMessage(`Delete failed: ${err.message}`, true);
        }
        if (result) {
            for (const path of result.deleted) this.deselect(path);
            // Already gone (deleted elsewhere): nothing left to select either.
            for (const f of result.failed) if (f.error === "File not found") this.deselect(f.path);
            let text = `Deleted ${plural(result.deleted.length, "file")}.`;
            if (result.failed.length) {
                const shown = result.failed.slice(0, 5).map((f) => `${f.path} (${f.error})`).join(", ");
                const more = result.failed.length > 5 ? ` and ${result.failed.length - 5} more` : "";
                text += ` ${plural(result.failed.length, "file")} could not be deleted: ${shown}${more}.`;
            }
            this.showMessage(text, result.failed.length > 0);
        }
        this.anchor = null;
        this.matches = null;
        this.setBusy(false);
        await this.reload();
        if (result?.deleted.length) this.onFilesChanged?.(root);
    }
}
