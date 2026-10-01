// Stylesheet for the button, the panel shell and all tabs. Injected once by the entry module's setup().

const STYLE_ID = "spk-styles";

const CSS = `
    .spk-toggle { position: fixed; z-index: 10001; padding: 8px 14px; background: #2d7bf6; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-family: sans-serif; font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); touch-action: none; user-select: none; -webkit-user-select: none; white-space: nowrap; }
    .spk-toggle.spk-dragging { cursor: grabbing; opacity: 0.85; }
    .spk-dialog { position: fixed; z-index: 10000; overflow: auto; box-sizing: border-box; background: #1a1a1a; color: #ddd; border: 1px solid #444; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.5); }
    .spk-root { padding: 12px; font-family: var(--fg-font-family, system-ui, sans-serif); }
    .spk-footer { display: flex; justify-content: flex-end; gap: 12px; padding-top: 8px; border-top: 1px solid #2a2a2a; }
    .spk-footer-link { padding: 0; background: none; border: none; color: #777; font-size: 11px; cursor: pointer; }
    .spk-footer-link:hover { color: #ccc; text-decoration: underline; }
    .spk-section { margin-bottom: 16px; }
    .spk-section h3 { margin: 0 0 8px; font-size: 14px; }
    .spk-section-header { display: flex; justify-content: space-between; align-items: center; }
    .spk-label { display: block; margin-bottom: 4px; font-size: 12px; color: #aaa; }
    .spk-input, .spk-select { width: 100%; padding: 6px; background: #1a1a1a; color: #eee; border: 1px solid #444; border-radius: 4px; box-sizing: border-box; margin-bottom: 8px; }
    .spk-check { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #aaa; margin-bottom: 8px; cursor: pointer; }
    .spk-row { display: flex; gap: 6px; }
    .spk-row > .spk-input, .spk-row > .spk-select { flex: 1; min-width: 0; }
    .spk-button { padding: 8px 16px; background: #2d7bf6; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
    .spk-button:hover { background: #1a5fd4; }
    .spk-button:disabled { background: #555; cursor: not-allowed; }
    .spk-button-small { padding: 6px 10px; margin-bottom: 8px; font-size: 12px; }
    .spk-error { color: #ff6b6b; font-size: 12px; margin-top: 4px; }
    .spk-success { color: #51cf66; font-size: 12px; margin-top: 4px; }
    .spk-download { border: 1px solid #333; border-radius: 4px; padding: 8px; margin-bottom: 8px; background: #161616; }
    .spk-download-header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 4px; }
    .spk-download-header > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-progress-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
    .spk-progress-fill { height: 100%; background: #2d7bf6; width: 0%; transition: width 0.2s; }
    .spk-meta { font-size: 11px; color: #888; margin-top: 4px; }
    .spk-cancel, .spk-link { font-size: 11px; color: #ff6b6b; cursor: pointer; margin-left: 8px; }
    .spk-link { color: #888; font-weight: normal; }
    .spk-empty { color: #888; font-size: 12px; font-style: italic; }
    .spk-tabs { display: flex; gap: 2px; margin-bottom: 12px; border-bottom: 1px solid #333; overflow-x: auto; scrollbar-width: none; }
    .spk-tabs::-webkit-scrollbar { display: none; }
    .spk-tab { flex: 1 0 auto; padding: 8px 6px; background: none; color: #aaa; border: none; border-bottom: 2px solid transparent; cursor: pointer; font-size: 13px; white-space: nowrap; }
    @media (max-width: 400px) { .spk-tab { padding: 8px 3px; font-size: 12px; } }
    .spk-tab:hover { color: #eee; }
    .spk-tab.active { color: #fff; border-bottom-color: #2d7bf6; }
    .spk-file { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #2a2a2a; }
    .spk-file:hover { background: #202020; }
    .spk-file-info { flex: 1; min-width: 0; }
    .spk-file-name { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-file .spk-meta { margin-top: 2px; }
    .spk-delete { flex: none; padding: 4px 10px; font-size: 11px; background: #2a2a2a; color: #ff6b6b; border: 1px solid #553333; border-radius: 4px; cursor: pointer; }
    .spk-delete:hover { background: #3a2020; }
    .spk-delete.armed { background: #c92a2a; color: #fff; border-color: #c92a2a; }
    .spk-delete:disabled { color: #666; border-color: #333; background: #1f1f1f; cursor: not-allowed; }
`;

export function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}
