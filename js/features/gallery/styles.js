// Gallery stylesheet (tab summary, full-screen overlay, grid and lightbox). Injected once by registerGalleryTab.
// Kept in its own <style> element next to lib/styles.js; same spk- prefix and dark theme.

const STYLE_ID = "spk-gallery-styles";

const CSS = `
    .spk-gal-summary { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
    .spk-gal-summary-row { display: flex; justify-content: space-between; gap: 8px; width: 100%; padding: 8px 10px; background: #161616; color: #ddd; border: 1px solid #333; border-radius: 4px; cursor: pointer; font-size: 12px; text-align: left; }
    .spk-gal-summary-row:hover { background: #202020; border-color: #2d7bf6; }
    .spk-gal-summary-row > span:last-child { color: #999; white-space: nowrap; }

    .spk-gallery { position: fixed; inset: 0; z-index: 10002; display: flex; align-items: stretch; justify-content: center; padding: 16px; box-sizing: border-box; background: rgba(0,0,0,0.6); font-family: var(--fg-font-family, system-ui, sans-serif); color: #ddd; }
    .spk-gallery[hidden] { display: none; }
    .spk-gal-window { position: relative; display: flex; flex-direction: column; width: 100%; max-width: 1800px; min-height: 0; background: #1a1a1a; border: 1px solid #444; border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.6); overflow: hidden; outline: none; }
    .spk-gal-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #2a2a2a; }
    .spk-gal-bar > * { margin: 0; }
    .spk-gal-title { font-size: 15px; font-weight: 600; margin-right: 4px; }
    .spk-gal-seg { display: inline-flex; border: 1px solid #444; border-radius: 4px; overflow: hidden; }
    .spk-gal-seg button { padding: 5px 12px; background: #222; color: #aaa; border: none; cursor: pointer; font-size: 12px; }
    .spk-gal-seg button + button { border-left: 1px solid #444; }
    .spk-gal-seg button.active { background: #2d7bf6; color: #fff; }
    .spk-gal-crumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; min-width: 0; font-size: 12px; color: #777; }
    .spk-gal-crumbs button { padding: 2px 4px; background: none; border: none; color: #8ab4ff; cursor: pointer; font-size: 12px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-gal-crumbs button:disabled { color: #ddd; cursor: default; }
    .spk-gal-spacer { flex: 1; }
    .spk-gal-close { padding: 2px 10px; background: none; color: #aaa; border: 1px solid #444; border-radius: 4px; cursor: pointer; font-size: 18px; line-height: 1.2; }
    .spk-gal-close:hover { color: #fff; border-color: #888; }
    .spk-gallery .spk-input, .spk-gallery .spk-select { width: auto; margin: 0; padding: 5px 6px; font-size: 12px; }
    .spk-gal-search { flex: 1 1 180px; min-width: 120px; }
    .spk-gal-subfolders { max-width: 200px; }
    .spk-gallery .spk-check { margin: 0; }
    .spk-gal-size { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #aaa; }
    .spk-gal-size input { width: 110px; }
    .spk-gal-btn { padding: 5px 10px; background: #2a2a2a; color: #ddd; border: 1px solid #444; border-radius: 4px; cursor: pointer; font-size: 12px; white-space: nowrap; }
    .spk-gal-btn:hover:not(:disabled) { background: #333; border-color: #666; }
    .spk-gal-btn:disabled { color: #666; cursor: not-allowed; }
    .spk-gal-btn.primary { background: #2d7bf6; border-color: #2d7bf6; color: #fff; }
    .spk-gal-btn.primary:hover:not(:disabled) { background: #1a5fd4; }
    .spk-gal-btn.primary:disabled { background: #333; border-color: #444; color: #777; }
    .spk-gallery .spk-delete { padding: 5px 10px; font-size: 12px; }
    .spk-gal-selinfo { font-size: 12px; color: #ccc; white-space: nowrap; }
    .spk-gal-selinfo.has { color: #8ab4ff; font-weight: 600; }
    .spk-gal-message { padding: 0 12px; min-height: 0; font-size: 12px; }
    .spk-gal-message > div { margin: 6px 0; }
    .spk-gal-message > div:empty { display: none; }
    .spk-gal-scroll { position: relative; flex: 1; min-height: 0; overflow-y: auto; padding: 10px 12px; }
    .spk-gal-grid { --spk-gal-tile: 160px; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(var(--spk-gal-tile), 100%), 1fr)); gap: 8px; user-select: none; -webkit-user-select: none; transition: opacity 0.15s; }
    .spk-gal-grid.loading { opacity: 0.45; }
    .spk-gal-tile { position: relative; min-width: 0; border: 2px solid transparent; border-radius: 6px; background: #141414; cursor: pointer; }
    .spk-gal-tile:hover { border-color: #555; }
    .spk-gal-tile.selected { border-color: #2d7bf6; background: #12233f; }
    .spk-gal-thumb { position: relative; aspect-ratio: 1 / 1; display: flex; align-items: center; justify-content: center; overflow: hidden; border-radius: 4px 4px 0 0; background: #1e1e1e; }
    .spk-gal-thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .spk-gal-icon { display: flex; flex-direction: column; align-items: center; gap: 4px; color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; text-align: center; padding: 4px; }
    .spk-gal-icon svg { width: 40%; max-width: 64px; height: auto; opacity: 0.8; }
    .spk-gal-tile.broken .spk-gal-thumb img { display: none; }
    .spk-gal-tile.broken .spk-gal-thumb::after { content: "No preview"; color: #777; font-size: 11px; }
    .spk-gal-check { position: absolute; top: 6px; left: 6px; width: 20px; height: 20px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.7); background: rgba(0,0,0,0.35); box-sizing: border-box; color: transparent; font-size: 12px; line-height: 16px; text-align: center; opacity: 0; transition: opacity 0.1s; }
    .spk-gal-tile:hover .spk-gal-check, .spk-gal-grid.has-selection .spk-gal-check { opacity: 1; }
    .spk-gal-tile.selected .spk-gal-check { opacity: 1; background: #2d7bf6; border-color: #2d7bf6; color: #fff; }
    .spk-gal-zoom { position: absolute; top: 4px; right: 4px; width: 26px; height: 26px; padding: 0; border: none; border-radius: 4px; background: rgba(0,0,0,0.55); color: #fff; font-size: 14px; cursor: zoom-in; opacity: 0; transition: opacity 0.1s; }
    .spk-gal-tile:hover .spk-gal-zoom, .spk-gal-zoom:focus-visible { opacity: 1; }
    .spk-gal-badge { position: absolute; bottom: 4px; left: 4px; padding: 1px 5px; border-radius: 3px; background: rgba(0,0,0,0.65); color: #ddd; font-size: 10px; text-transform: uppercase; }
    .spk-gal-caption { padding: 4px 6px 5px; min-width: 0; }
    .spk-gal-caption .spk-gal-name { font-size: 11px; color: #ddd; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-gal-caption .spk-gal-meta { font-size: 10px; color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spk-gal-empty { padding: 40px 0; text-align: center; color: #888; font-size: 13px; }
    .spk-gal-pager { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 8px; padding: 8px 12px; border-top: 1px solid #2a2a2a; font-size: 12px; color: #aaa; }

    .spk-lightbox { position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column; background: rgba(8,8,8,0.97); }
    .spk-lightbox[hidden] { display: none; }
    .spk-lb-stage { position: relative; flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 12px 56px; }
    .spk-lb-stage img, .spk-lb-stage video { max-width: 100%; max-height: 100%; object-fit: contain; box-shadow: 0 4px 24px rgba(0,0,0,0.6); }
    .spk-lb-status { position: absolute; color: #888; font-size: 13px; pointer-events: none; }
    .spk-lb-other { display: flex; flex-direction: column; align-items: center; gap: 12px; color: #aaa; font-size: 13px; }
    .spk-lb-nav { position: absolute; top: 50%; transform: translateY(-50%); width: 44px; height: 72px; border: none; border-radius: 6px; background: rgba(255,255,255,0.08); color: #fff; font-size: 30px; cursor: pointer; }
    .spk-lb-nav:hover:not(:disabled) { background: rgba(255,255,255,0.18); }
    .spk-lb-nav:disabled { opacity: 0.2; cursor: default; }
    .spk-lb-prev { left: 6px; }
    .spk-lb-next { right: 6px; }
    .spk-lb-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 8px 12px; border-top: 1px solid #2a2a2a; font-size: 12px; }
    .spk-lb-name { font-weight: 600; color: #eee; overflow-wrap: anywhere; }
    .spk-lb-meta { color: #888; }
    .spk-lb-bar a { color: #8ab4ff; }

    @media (max-width: 600px) {
        .spk-gallery { padding: 0; }
        .spk-gal-window { border-radius: 0; border: none; }
        .spk-gal-bar { padding: 6px 8px; gap: 6px; }
        .spk-gal-scroll { padding: 8px; }
        .spk-gal-size input { width: 80px; }
        .spk-lb-stage { padding: 8px 4px; }
        .spk-lb-nav { width: 34px; height: 56px; font-size: 24px; }
    }
`;

export function injectGalleryStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}
