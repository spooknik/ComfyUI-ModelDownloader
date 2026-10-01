// Styles for the System tab. Injected once by registerSystemTab(); exports only (no side effects on import).

const STYLE_ID = "spk-system-styles";

const CSS = `
    .spk-sys-status { display: flex; align-items: center; justify-content: space-between; gap: 4px 8px; flex-wrap: wrap; font-size: 11px; color: #999; margin-bottom: 8px; }
    .spk-sys-status-main { display: flex; align-items: center; gap: 6px; min-width: 0; }
    .spk-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #51cf66; }
    .spk-dot.offline { background: #ff6b6b; }
    .spk-dot.loading { background: #777; }
    .spk-pill { padding: 0 6px; border-radius: 8px; border: 1px solid #553333; color: #ff6b6b; font-size: 10px; line-height: 16px; }
    .spk-sys-stats.spk-stale { opacity: 0.45; }
    .spk-card { border: 1px solid #2e2e2e; border-radius: 6px; padding: 8px; margin-bottom: 8px; background: #161616; }
    .spk-card-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 2px 8px; font-size: 12px; }
    .spk-card-title { color: #eee; font-weight: 600; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-card-meta { color: #999; font-size: 11px; font-variant-numeric: tabular-nums; }
    .spk-meter { margin-top: 6px; }
    .spk-meter-head { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: #aaa; margin-bottom: 3px; }
    .spk-meter-label { min-width: 0; overflow-wrap: anywhere; }
    .spk-meter-value { flex: none; color: #ddd; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .spk-bar { height: 6px; background: #2c2c2c; border-radius: 3px; overflow: hidden; }
    .spk-bar-fill { height: 100%; background: #2d7bf6; }
    .spk-bar-fill.warn, .spk-cores > span.warn { background: #f59f00; }
    .spk-bar-fill.crit, .spk-cores > span.crit { background: #e03131; }
    .spk-meter-sub { font-size: 11px; color: #888; margin-top: 3px; white-space: pre-line; }
    .spk-cores { display: flex; align-items: flex-end; gap: 1px; height: 16px; margin-top: 6px; padding: 1px; background: #202020; border-radius: 2px; }
    .spk-cores > span { flex: 1; min-width: 1px; min-height: 1px; background: #2d7bf6; }
    .spk-sys-note { font-size: 11px; color: #888; font-style: italic; margin: 0 0 8px; }
    .spk-details { margin: 4px 0 0; font-size: 12px; }
    .spk-details > summary { cursor: pointer; color: #aaa; }
    .spk-details > summary:hover { color: #eee; }
    .spk-kv { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 6px 0 0; font-size: 11px; }
    .spk-kv dt { color: #888; }
    .spk-kv dd { margin: 0; color: #ddd; overflow-wrap: anywhere; }
    .spk-sys-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .spk-sys-actions .spk-button { flex: 1 1 120px; padding: 7px 10px; font-size: 12px; white-space: nowrap; }
    .spk-button.spk-danger { background: #2a2a2a; color: #ff8787; border: 1px solid #553333; }
    .spk-button.spk-danger:hover { background: #3a2020; }
    .spk-button.spk-danger.armed { background: #c92a2a; color: #fff; border-color: #c92a2a; }
    .spk-button.spk-danger:disabled { background: #1f1f1f; color: #666; border-color: #333; }
    .spk-sys-restart { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 32px 12px; text-align: center; font-size: 13px; }
    .spk-sys-restart[hidden] { display: none; }
    .spk-sys-restart .spk-meta { margin: 0; }
    .spk-sys-restart .spk-row { justify-content: center; }
    .spk-spinner { width: 22px; height: 22px; border: 3px solid #333; border-top-color: #2d7bf6; border-radius: 50%; animation: spk-spin 0.9s linear infinite; }
    @keyframes spk-spin { to { transform: rotate(360deg); } }
`;

export function injectSystemStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}
