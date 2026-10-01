// HTML for the System tab's meters, built from a /system/stats snapshot. Pure functions; exports only.

import { escapeHtml } from "../../lib/util.js";

const KB = 1024;
const MB = KB ** 2;
const GB = KB ** 3;
const TB = KB ** 4;

const esc = (value) => escapeHtml(value == null ? "" : String(value));
const isNum = (value) => typeof value === "number" && Number.isFinite(value);

/** Bytes as a short size: "812 MB", "9.1 GB", "1.82 TB". */
export function formatSize(bytes) {
    if (!isNum(bytes)) return "?";
    if (bytes >= TB) return `${(bytes / TB).toFixed(2)} TB`;
    if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
    if (bytes >= MB) return `${Math.round(bytes / MB)} MB`;
    return `${Math.round(bytes / KB)} KB`;
}

/** "9.1 / 16.0 GB": both numbers in the unit that suits the total. */
export function formatUsedOfTotal(used, total) {
    if (!isNum(used) || !isNum(total)) return isNum(total) ? `? / ${formatSize(total)}` : "?";
    const [unit, label] = total >= TB ? [TB, "TB"] : total >= GB ? [GB, "GB"] : [MB, "MB"];
    const digits = unit === MB ? 0 : unit === TB ? 2 : 1;
    return `${(used / unit).toFixed(digits)} / ${(total / unit).toFixed(digits)} ${label}`;
}

export function formatUptime(seconds) {
    if (!isNum(seconds)) return "?";
    const s = Math.floor(seconds);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
}

const pct = (value) => (isNum(value) ? `${Math.round(value)}%` : "?");
const percentOf = (used, total) => (isNum(used) && isNum(total) && total > 0 ? (100 * used) / total : null);
const level = (percent) => (percent > 90 ? "crit" : percent > 75 ? "warn" : "");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** One labelled bar. `percent` null draws an empty bar (unknown). */
export function meter({ label, value, percent, sub, title }) {
    const known = isNum(percent);
    const p = known ? Math.min(Math.max(percent, 0), 100) : 0;
    const aria = known ? ` aria-valuenow="${Math.round(p)}"` : "";
    return `
        <div class="spk-meter"${title ? ` title="${esc(title)}"` : ""}>
            <div class="spk-meter-head"><span class="spk-meter-label">${esc(label)}</span><span class="spk-meter-value">${esc(value)}</span></div>
            <div class="spk-bar" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100"${aria}>
                <div class="spk-bar-fill ${level(p)}" style="width: ${p.toFixed(1)}%"></div>
            </div>
            ${sub ? `<div class="spk-meter-sub">${esc(sub)}</div>` : ""}
        </div>`;
}

function card(title, meta, body, titleAttr) {
    return `
        <div class="spk-card">
            <div class="spk-card-head">
                <span class="spk-card-title" title="${esc(titleAttr || title)}">${esc(title)}</span>
                ${meta ? `<span class="spk-card-meta">${esc(meta)}</span>` : ""}
            </div>
            ${body}
        </div>`;
}

function gpuCard(gpu) {
    const meta = [
        isNum(gpu.temperature_c) ? `${gpu.temperature_c}°C` : null,
        isNum(gpu.power_w)
            ? `${Math.round(gpu.power_w)}${isNum(gpu.power_limit_w) ? ` / ${Math.round(gpu.power_limit_w)}` : ""} W`
            : null,
        isNum(gpu.fan_percent) ? `fan ${gpu.fan_percent}%` : null,
    ].filter(Boolean);

    const torch = gpu.torch;
    const torchSub =
        torch && (isNum(torch.reserved) || isNum(torch.allocated))
            ? `ComfyUI (PyTorch): ${formatSize(torch.reserved)} reserved · ${formatSize(torch.allocated)} allocated`
            : null;
    const memPercent = percentOf(gpu.mem_used, gpu.mem_total);
    let body = "";
    if (isNum(gpu.util_percent))
        body += meter({ label: "Load", value: pct(gpu.util_percent), percent: gpu.util_percent });
    body += meter({
        label: "VRAM",
        value: `${formatUsedOfTotal(gpu.mem_used, gpu.mem_total)}${isNum(memPercent) ? ` · ${pct(memPercent)}` : ""}`,
        percent: memPercent,
        sub: torchSub,
    });
    return card(`GPU ${gpu.index} · ${gpu.name || "unknown"}`, meta.join(" · "), body);
}

function cpuCard(cpu) {
    const counts = [
        isNum(cpu.physical) && isNum(cpu.logical)
            ? `${cpu.physical}C/${cpu.logical}T`
            : isNum(cpu.logical)
              ? `${cpu.logical} threads`
              : null,
        isNum(cpu.cgroup_limit) ? `limit ${cpu.cgroup_limit} CPUs` : null,
        Array.isArray(cpu.load_avg) ? `load ${cpu.load_avg.map((x) => x.toFixed(2)).join(" ")}` : null,
    ].filter(Boolean);
    const cores = Array.isArray(cpu.per_core) ? cpu.per_core : [];
    const strip = cores.length
        ? `<div class="spk-cores" title="Per-core load">${cores
              .map(
                  (c, i) =>
                      `<span class="${level(c)}" style="height: ${Math.max(0, Math.min(c, 100))}%" title="Core ${i}: ${pct(c)}"></span>`,
              )
              .join("")}</div>`
        : "";
    // In a container, 100% is all of the host's cores; a CPU limit caps how much of that ComfyUI can use.
    const sub =
        isNum(cpu.cgroup_limit) && isNum(cpu.logical)
            ? `Host-wide; the container may use ${cpu.cgroup_limit} of ${cpu.logical} CPUs`
            : null;
    const body = meter({ label: "Usage", value: pct(cpu.percent), percent: cpu.percent, sub }) + strip;
    return card(`CPU · ${cpu.model || "unknown"}`, counts.join(" · "), body);
}

function ramCard(ram) {
    const extras = [
        `${formatSize(ram.available)} available`,
        isNum(ram.swap_total) && ram.swap_total > 0 ? `swap ${formatUsedOfTotal(ram.swap_used, ram.swap_total)}` : null,
    ].filter(Boolean);
    let body = meter({
        label: ram.cgroup ? "Host" : "Used",
        value: `${formatUsedOfTotal(ram.used, ram.total)} · ${pct(ram.percent)}`,
        percent: ram.percent,
        sub: extras.join(" · "),
    });
    if (ram.cgroup) {
        const c = ram.cgroup;
        body += meter({
            label: "Container limit",
            value: `${formatUsedOfTotal(c.memory_used, c.memory_limit)} · ${pct(c.memory_percent)}`,
            percent: c.memory_percent,
        });
    }
    const meta = isNum(ram.process_rss) ? `ComfyUI ${formatSize(ram.process_rss)}` : "";
    return card("RAM", meta, body);
}

function disksCard(disks) {
    const body = disks
        .map((disk) => {
            const paths = Object.entries(disk.paths || {})
                .map(([role, path]) => `${role}: ${path}`)
                .join("\n");
            return meter({
                label: disk.label,
                value: `${formatSize(disk.free)} free / ${formatSize(disk.total)}`,
                percent: disk.percent,
                title: paths,
            });
        })
        .join("");
    return card("Disks", disks.length > 1 ? `${disks.length} filesystems` : "", body);
}

const note = (text) => (text ? `<div class="spk-sys-note">${esc(text)}</div>` : "");

/** All meters for one snapshot. */
export function renderStats(stats) {
    const notes = stats.notes || {};
    const gpus = Array.isArray(stats.gpus) ? stats.gpus : [];
    let html = gpus.map(gpuCard).join("");
    if (!gpus.length) html += note(notes.gpus ? `No GPU stats: ${notes.gpus}` : "No GPU found");
    else html += note(notes.gpus);
    html += stats.cpu ? cpuCard(stats.cpu) : note(`CPU: ${notes.cpu || "unavailable"}`);
    html += stats.ram ? ramCard(stats.ram) : note(`RAM: ${notes.ram || "unavailable"}`);
    if (Array.isArray(stats.disks) && stats.disks.length) html += disksCard(stats.disks);
    else html += note(`Disks: ${notes.disks || "unavailable"}`);
    return html;
}

/** "Up 2h 13m · Queue: 1 running, 3 pending". */
export function statusText(stats) {
    const comfy = stats.comfyui || {};
    const queue = comfy.queue;
    let queueText = "";
    if (queue) {
        if (isNum(queue.running) && isNum(queue.pending)) {
            queueText = queue.remaining ? `${queue.running} running, ${queue.pending} pending` : "idle";
        } else {
            queueText = queue.remaining ? `${plural(queue.remaining, "job")} queued` : "idle";
        }
    }
    return [`Up ${formatUptime(comfy.uptime_s)}`, queueText && `Queue: ${queueText}`].filter(Boolean).join(" · ");
}

const VERSION_LABELS = [
    ["comfyui", "ComfyUI"],
    ["frontend", "Frontend"],
    ["python", "Python"],
    ["pytorch", "PyTorch"],
    ["cuda", "CUDA"],
    ["hip", "ROCm (HIP)"],
    ["cudnn", "cuDNN"],
    ["xformers", "xformers"],
    ["nvidia_driver", "NVIDIA driver"],
    ["os", "OS"],
    ["platform", "Platform"],
    ["arch", "Arch"],
];

const RESTART_MODES = {
    exec: "re-exec in place",
    exit: "exit (supervisor restarts)",
    "comfy-cli": "comfy-cli",
};

/** The Versions list (plus process facts that don't change while it runs). */
export function renderVersions(stats) {
    const versions = stats.versions || {};
    const comfy = stats.comfyui || {};
    const rows = VERSION_LABELS.filter(([key]) => versions[key]).map(([key, label]) => [label, versions[key]]);
    rows.push(["Container", versions.container || "no"]);
    if (comfy.pid) rows.push(["PID", comfy.pid]);
    if (comfy.restart_mode) rows.push(["Restart mode", RESTART_MODES[comfy.restart_mode] || comfy.restart_mode]);
    if (!stats.versions && stats.notes?.versions) rows.push(["Error", stats.notes.versions]);
    return rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("");
}

/** Total VRAM in use across GPUs (for the Free VRAM feedback), or null if unknown. */
export function totalVramUsed(stats) {
    const values = (stats?.gpus || []).map((g) => g.mem_used).filter(isNum);
    return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

export function queueBusyText(data) {
    if (isNum(data.running) && isNum(data.pending)) return `${data.running} running, ${data.pending} pending`;
    return isNum(data.remaining) ? `${plural(data.remaining, "job")} queued` : "jobs queued";
}
