// Panel shell: a tab bar, the tab contents and a small footer, shown in a fixed dialog anchored to the
// floating button. Features add their UI with `panel.addTab(...)`; the shell knows nothing about them.

import { clamp, viewportSize } from "./util.js";

const PANEL_WIDTH = 420;
const EDGE_MARGIN = 12; // Minimum distance between the panel and the viewport edges.
const BUTTON_GAP = 12; // Distance between the button and the panel.
const COMFORTABLE_HEIGHT = 360; // Below the button "has room" if this much (or the whole panel) fits there.
const MIN_HEIGHT = 160; // If neither side has this much room, use the whole viewport height instead.

export class SpookPanel {
    /** @param anchor  the FloatingButton the panel opens from (needs getRect() and onMove()). */
    constructor(anchor) {
        this.anchor = anchor;
        this.tabs = new Map();
        this.activeTab = null;

        this.element = document.createElement("div");
        this.element.className = "spk-dialog";
        this.element.hidden = true;

        const root = document.createElement("div");
        root.className = "spk-root";
        this.tabBar = document.createElement("div");
        this.tabBar.className = "spk-tabs";
        this.body = document.createElement("div");
        this.footer = document.createElement("div");
        this.footer.className = "spk-footer";
        root.append(this.tabBar, this.body, this.footer);
        this.element.appendChild(root);

        anchor.onMove(() => this.reposition());
    }

    /**
     * Add a tab. `tab` provides `id`, `label` and `element` (its content), and optionally `onShow()`, called
     * whenever the tab becomes visible (switched to, or the panel opened while it is the active tab), and
     * `onHide()`, called when it stops being visible (another tab chosen, or the panel closed).
     * The first tab added is the initially active one.
     */
    addTab(tab) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "spk-tab";
        button.dataset.tab = tab.id;
        button.textContent = tab.label;
        button.addEventListener("click", () => this.switchTab(tab.id));
        this.tabBar.appendChild(button);

        tab.element.classList.add("spk-tab-panel");
        tab.element.dataset.panel = tab.id;
        this.body.appendChild(tab.element);
        this.tabs.set(tab.id, { tab, button });

        if (!this.activeTab) this.activeTab = tab.id;
        this.updateTabVisibility();
    }

    /** Add a small text link to the footer. */
    addFooterAction(label, onClick) {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "spk-footer-link";
        link.textContent = label;
        link.addEventListener("click", onClick);
        this.footer.appendChild(link);
        return link;
    }

    switchTab(id) {
        if (!this.tabs.has(id)) return;
        const previous = this.activeTab;
        this.activeTab = id;
        this.updateTabVisibility();
        if (previous !== id && this.isOpen()) this.tabs.get(previous)?.tab.onHide?.();
        this.tabs.get(id).tab.onShow?.();
    }

    updateTabVisibility() {
        for (const [id, { tab, button }] of this.tabs) {
            button.classList.toggle("active", id === this.activeTab);
            tab.element.hidden = id !== this.activeTab;
        }
    }

    isOpen() {
        return !this.element.hidden;
    }

    open() {
        this.element.hidden = false;
        this.reposition();
        // Reopening should show current data (e.g. the file list), not a stale view.
        this.tabs.get(this.activeTab)?.tab.onShow?.();
    }

    close() {
        if (!this.isOpen()) return;
        this.element.hidden = true;
        this.tabs.get(this.activeTab)?.tab.onHide?.();
    }

    toggle() {
        if (this.isOpen()) this.close();
        else this.open();
    }

    /**
     * Anchor the panel to the button: below it if there is room, otherwise above; aligned with the button's
     * left or right edge, whichever side has more room; always fully inside the viewport.
     */
    reposition() {
        if (!this.isOpen()) return;
        const el = this.element;
        const button = this.anchor.getRect();
        const { width: vw, height: vh } = viewportSize();

        const width = Math.max(0, Math.min(PANEL_WIDTH, vw - 2 * EDGE_MARGIN));
        const alignRight = button.left + button.width / 2 > vw / 2;
        const left = clamp(alignRight ? button.right - width : button.left, EDGE_MARGIN, vw - EDGE_MARGIN - width);

        const roomBelow = vh - button.bottom - BUTTON_GAP - EDGE_MARGIN;
        const roomAbove = button.top - BUTTON_GAP - EDGE_MARGIN;
        // scrollHeight is the full content height even while max-height clips it; add the borders.
        const wanted = Math.min(el.scrollHeight + el.offsetHeight - el.clientHeight, COMFORTABLE_HEIGHT);

        let top = "auto";
        let bottom = "auto";
        let maxHeight;
        if (Math.max(roomBelow, roomAbove) < MIN_HEIGHT) {
            // Tiny viewport: use all of it (the button stays on top of the panel, so it can still close it).
            top = `${EDGE_MARGIN}px`;
            maxHeight = Math.max(0, vh - 2 * EDGE_MARGIN);
        } else if (roomBelow >= wanted || roomBelow >= roomAbove) {
            top = `${button.bottom + BUTTON_GAP}px`;
            maxHeight = roomBelow;
        } else {
            // Anchored by its bottom edge, so content changes grow the panel upwards, away from the button.
            bottom = `${vh - button.top + BUTTON_GAP}px`;
            maxHeight = roomAbove;
        }
        Object.assign(el.style, {
            left: `${left}px`,
            right: "auto",
            top,
            bottom,
            width: `${width}px`,
            maxHeight: `${maxHeight}px`,
        });
    }
}
