// System tab: live CPU/RAM/GPU/disk meters (polled only while the tab is visible), versions, Free VRAM, and
// Restart ComfyUI with a two-click confirm and a wait for the restarted server.

import { showMessage, sleep } from "../../lib/util.js";
import { queueBusyText, renderStats, renderVersions, statusText, totalVramUsed } from "./render.js";
import { fetchStats, freeVram, requestRestart, waitForNewBoot } from "./server.js";

const POLL_MS = 2000;
const CONFIRM_MS = 4000; // How long an armed (confirm?) restart button stays armed.
const RESTART_TIMEOUT_MS = 180_000;

export class SystemTab {
    constructor() {
        this.id = "system";
        this.label = "System";
        this.stats = null;
        this.polling = false;
        this.generation = 0; // Bumped on stop, so a poll loop from before a stop/start pair ends itself.
        this.timer = null;
        this.restarting = false;
        this.versionsHtml = "";

        this.element = document.createElement("div");
        this.element.innerHTML = `
            <div class="spk-sys-main">
                <div class="spk-sys-status">
                    <span class="spk-sys-status-main"><span class="spk-dot loading"></span><span class="spk-sys-status-text">Loading…</span></span>
                    <span class="spk-pill" hidden>offline</span>
                </div>
                <div class="spk-sys-stats"></div>
                <details class="spk-details">
                    <summary>Versions</summary>
                    <dl class="spk-kv"></dl>
                </details>
                <div class="spk-sys-actions">
                    <button type="button" class="spk-button spk-sys-free" title="Unload models and free cached VRAM (ComfyUI's /api/free)">Free VRAM</button>
                    <button type="button" class="spk-button spk-danger spk-sys-restart-btn">Restart ComfyUI</button>
                </div>
                <div class="spk-sys-message"><div></div></div>
            </div>
            <div class="spk-sys-restart" hidden></div>
        `;
        const $ = (sel) => this.element.querySelector(sel);
        this.mainView = $(".spk-sys-main");
        this.restartView = $(".spk-sys-restart");
        this.statsEl = $(".spk-sys-stats");
        this.statusDot = $(".spk-dot");
        this.statusTextEl = $(".spk-sys-status-text");
        this.offlinePill = $(".spk-pill");
        this.versionsEl = $(".spk-kv");
        this.freeButton = $(".spk-sys-free");
        this.restartButton = $(".spk-sys-restart-btn");
        this.messageEl = $(".spk-sys-message > div"); // showMessage() replaces its className.

        this.freeButton.addEventListener("click", () => this.onFreeClick());
        this.restartButton.addEventListener("click", () => this.onRestartClick());
        this.resetRestartButton();
    }

    onShow() {
        this.startPolling();
    }

    onHide() {
        this.stopPolling();
    }

    showMessage(text, isError) {
        showMessage(this.messageEl, text, isError);
    }

    // ---- Polling ----------------------------------------------------------------------------------------

    startPolling() {
        if (this.polling) return;
        this.polling = true;
        const generation = ++this.generation;
        const tick = async () => {
            if (generation !== this.generation) return;
            // A hidden browser tab has nobody looking: skip the request but keep the loop alive.
            if (!document.hidden && !this.restarting) await this.refresh();
            if (generation === this.generation) this.timer = setTimeout(tick, POLL_MS);
        };
        tick();
    }

    stopPolling() {
        this.polling = false;
        this.generation++;
        clearTimeout(this.timer);
        this.timer = null;
    }

    async refresh() {
        try {
            const stats = await fetchStats();
            if (this.restarting) return;
            this.stats = stats;
            this.render();
            this.setOnline(true, statusText(stats));
        } catch (err) {
            // Polls repeat every 2s, so no message spam: just flag the meters as stale.
            if (!this.restarting) this.setOnline(false, this.stats ? statusText(this.stats) : "No data");
        }
    }

    setOnline(online, text) {
        this.statusDot.className = `spk-dot${online ? "" : " offline"}`;
        this.statusTextEl.textContent = text;
        this.offlinePill.hidden = online;
        this.statsEl.classList.toggle("spk-stale", !online);
    }

    render() {
        this.statsEl.innerHTML = renderStats(this.stats);
        // Versions only change on restart; rewriting them every poll would fight text selection.
        const versions = renderVersions(this.stats);
        if (versions !== this.versionsHtml) {
            this.versionsHtml = versions;
            this.versionsEl.innerHTML = versions;
        }
    }

    // ---- Free VRAM --------------------------------------------------------------------------------------

    async onFreeClick() {
        const button = this.freeButton;
        const before = totalVramUsed(this.stats);
        const busy = this.stats?.comfyui?.queue?.remaining > 0;
        button.disabled = true;
        button.textContent = "Freeing…";
        try {
            await freeVram();
            if (busy) {
                this.showMessage("Requested: ComfyUI frees VRAM when the current job finishes", false);
                return;
            }
            // The prompt worker unloads asynchronously; give it a moment, then measure.
            await sleep(1500);
            let after = null;
            try {
                this.stats = await fetchStats();
                this.render();
                this.setOnline(true, statusText(this.stats));
                after = totalVramUsed(this.stats);
            } catch (err) {
                // Measuring is a bonus; the request itself succeeded.
            }
            const freed = before != null && after != null ? before - after : null;
            if (freed != null && freed > 64 * 1024 * 1024) {
                this.showMessage(`Freed ${(freed / 1024 ** 3).toFixed(2)} GB of VRAM`, false);
            } else if (freed != null) {
                this.showMessage("Done: nothing more to free (no models were loaded)", false);
            } else {
                this.showMessage("Models unloaded and cached memory freed", false);
            }
        } catch (err) {
            this.showMessage(`Free VRAM failed: ${err.message}`, true);
        } finally {
            button.disabled = false;
            button.textContent = "Free VRAM";
        }
    }

    // ---- Restart ----------------------------------------------------------------------------------------

    resetRestartButton() {
        const button = this.restartButton;
        clearTimeout(this.disarmTimer);
        button.dataset.state = "idle";
        button.textContent = "Restart ComfyUI";
        button.classList.remove("armed");
        button.disabled = false;
    }

    /** Arm the button: the next click within `ms` does `state`. */
    armRestartButton(state, label, ms) {
        const button = this.restartButton;
        clearTimeout(this.disarmTimer);
        button.dataset.state = state;
        button.textContent = label;
        button.classList.add("armed");
        this.disarmTimer = setTimeout(() => {
            if (button.dataset.state === state) {
                this.resetRestartButton();
                if (state === "force") this.showMessage("", false);
            }
        }, ms);
    }

    async onRestartClick() {
        const state = this.restartButton.dataset.state;
        if (state === "idle") {
            this.armRestartButton("confirm", "Confirm restart?", CONFIRM_MS);
            return;
        }
        if (state !== "confirm" && state !== "force") return;
        const force = state === "force";
        clearTimeout(this.disarmTimer);
        this.restartButton.disabled = true;
        this.restartButton.textContent = "Restarting…";
        let result;
        try {
            result = await requestRestart(force);
        } catch (err) {
            this.resetRestartButton();
            this.showMessage(`Restart failed: ${err.message}`, true);
            return;
        }
        if (result.status === 409) {
            this.restartButton.disabled = false;
            this.showMessage(`${queueBusyText(result.data)}: restart anyway? The running job will be lost.`, true);
            this.armRestartButton("force", "Restart anyway", 2 * CONFIRM_MS);
            return;
        }
        if (result.status !== 202) {
            this.resetRestartButton();
            this.showMessage(result.data.error || `Restart failed (HTTP ${result.status})`, true);
            return;
        }
        this.waitForRestart(result.data.boot_id || this.stats?.comfyui?.boot_id);
    }

    /** Show the "restarting" state until the server answers with a new boot_id, then reload the page. */
    async waitForRestart(oldBootId) {
        this.restarting = true;
        this.mainView.hidden = true;
        this.restartView.hidden = false;
        this.restartView.innerHTML = `
            <div class="spk-spinner"></div>
            <div>Restarting… waiting for ComfyUI</div>
            <div class="spk-meta spk-sys-restart-progress">Stopping the old process</div>
        `;
        const progress = this.restartView.querySelector(".spk-sys-restart-progress");
        const back = await waitForNewBoot(oldBootId, {
            timeoutMs: RESTART_TIMEOUT_MS,
            onProgress: (seconds, down) => {
                progress.textContent = down
                    ? `Server is down, waiting for it to come back (${seconds}s)`
                    : `Stopping the old process (${seconds}s)`;
            },
        });
        if (back) {
            progress.textContent = "ComfyUI is back. Reloading the page…";
            window.location.reload();
            return;
        }
        this.restartView.innerHTML = `
            <div class="spk-error">ComfyUI didn't come back within ${RESTART_TIMEOUT_MS / 60000} minutes. Check the container (or console) logs.</div>
            <div class="spk-row">
                <button type="button" class="spk-button spk-button-small spk-sys-wait">Keep waiting</button>
                <button type="button" class="spk-button spk-button-small spk-sys-reload">Reload page</button>
            </div>
        `;
        this.restartView.querySelector(".spk-sys-wait").addEventListener("click", () => this.waitForRestart(oldBootId));
        this.restartView.querySelector(".spk-sys-reload").addEventListener("click", () => window.location.reload());
    }
}
