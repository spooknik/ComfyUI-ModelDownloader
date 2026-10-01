// Floating toggle button that can be dragged anywhere (mouse, touch, pen) and remembers where it was left.
//
// The position is stored relative to the nearest horizontal and vertical viewport edges, e.g.
// { x: "right", dx: 12, y: "top", dy: 3 }, so a button parked in a corner stays in that corner when the window
// is resized. It is clamped fully on-screen whenever it is applied; the stored offsets are left untouched by
// clamping, so the button returns to its spot when the window grows again.

import { loadSetting, removeSetting, saveSetting } from "./storage.js";
import { clamp, viewportSize } from "./util.js";

// Pointer travel (px) after which a press becomes a drag instead of a click.
const DRAG_THRESHOLD = 4;

export const DEFAULT_BUTTON_POSITION = Object.freeze({ x: "right", dx: 12, y: "top", dy: 3 });

function isValidPosition(p) {
    return (
        !!p &&
        (p.x === "left" || p.x === "right") &&
        (p.y === "top" || p.y === "bottom") &&
        Number.isFinite(p.dx) &&
        Number.isFinite(p.dy)
    );
}

export class FloatingButton {
    constructor({ label, title, storageKey, onClick }) {
        this.storageKey = storageKey;
        this.onClick = onClick;
        this.moveListeners = new Set();
        this.drag = null;
        this.suppressClick = false;

        const saved = loadSetting(storageKey);
        this.position = isValidPosition(saved) ? { ...saved } : { ...DEFAULT_BUTTON_POSITION };

        this.element = document.createElement("button");
        this.element.type = "button";
        this.element.className = "spk-toggle";
        this.element.textContent = label;
        this.element.title = title;
        this.bindEvents();
    }

    mount(parent = document.body) {
        parent.appendChild(this.element);
        this.applyPosition();
        window.addEventListener("resize", () => this.applyPosition());
    }

    /** Called with the button's DOMRect whenever it moves (drag, resize, reset). */
    onMove(listener) {
        this.moveListeners.add(listener);
    }

    getRect() {
        return this.element.getBoundingClientRect();
    }

    resetPosition() {
        removeSetting(this.storageKey);
        this.position = { ...DEFAULT_BUTTON_POSITION };
        this.applyPosition();
    }

    /** Place the button at its stored edge-relative position, clamped on-screen. */
    applyPosition() {
        const { width, height } = viewportSize();
        const rect = this.getRect();
        const p = this.position;
        const dx = clamp(p.dx, 0, width - rect.width);
        const dy = clamp(p.dy, 0, height - rect.height);
        Object.assign(this.element.style, {
            left: p.x === "left" ? `${dx}px` : "auto",
            right: p.x === "right" ? `${dx}px` : "auto",
            top: p.y === "top" ? `${dy}px` : "auto",
            bottom: p.y === "bottom" ? `${dy}px` : "auto",
        });
        this.notifyMove();
    }

    /** Place the button's top-left corner at (left, top), clamped on-screen. Used while dragging. */
    moveTo(left, top) {
        const { width, height } = viewportSize();
        const rect = this.getRect();
        Object.assign(this.element.style, {
            left: `${clamp(left, 0, width - rect.width)}px`,
            top: `${clamp(top, 0, height - rect.height)}px`,
            right: "auto",
            bottom: "auto",
        });
        this.notifyMove();
    }

    /** Describe the current on-screen spot relative to the nearest horizontal and vertical edges. */
    edgePosition() {
        const { width, height } = viewportSize();
        const rect = this.getRect();
        const x = rect.left + rect.width / 2 <= width / 2 ? "left" : "right";
        const y = rect.top + rect.height / 2 <= height / 2 ? "top" : "bottom";
        return {
            x,
            dx: Math.max(0, Math.round(x === "left" ? rect.left : width - rect.right)),
            y,
            dy: Math.max(0, Math.round(y === "top" ? rect.top : height - rect.bottom)),
        };
    }

    notifyMove() {
        const rect = this.getRect();
        for (const listener of this.moveListeners) listener(rect);
    }

    bindEvents() {
        const el = this.element;

        el.addEventListener("pointerdown", (e) => {
            if (e.pointerType === "mouse" && e.button !== 0) return;
            const rect = this.getRect();
            this.suppressClick = false;
            this.drag = {
                pointerId: e.pointerId,
                startX: e.clientX,
                startY: e.clientY,
                left: rect.left,
                top: rect.top,
                moved: false,
            };
            try {
                el.setPointerCapture(e.pointerId);
            } catch {
                // Pointer already gone; the drag simply ends on the next pointerup/cancel we see.
            }
        });

        el.addEventListener("pointermove", (e) => {
            const drag = this.drag;
            if (!drag || e.pointerId !== drag.pointerId) return;
            const dx = e.clientX - drag.startX;
            const dy = e.clientY - drag.startY;
            if (!drag.moved) {
                if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
                drag.moved = true;
                el.classList.add("spk-dragging");
            }
            this.moveTo(drag.left + dx, drag.top + dy);
        });

        const endDrag = (e) => {
            const drag = this.drag;
            if (!drag || e.pointerId !== drag.pointerId) return;
            this.drag = null;
            el.classList.remove("spk-dragging");
            if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
            if (!drag.moved) return;
            // The click that follows releasing a drag must not toggle the panel.
            this.suppressClick = true;
            this.position = this.edgePosition();
            saveSetting(this.storageKey, this.position);
            this.applyPosition();
        };
        el.addEventListener("pointerup", endDrag);
        el.addEventListener("pointercancel", endDrag);
        el.addEventListener("lostpointercapture", endDrag);

        el.addEventListener("click", (e) => {
            // detail === 0 means keyboard activation, which never follows a drag.
            const suppressed = this.suppressClick && e.detail !== 0;
            this.suppressClick = false;
            if (suppressed) {
                e.preventDefault();
                e.stopPropagation();
                return;
            }
            this.onClick?.(e);
        });
    }
}
