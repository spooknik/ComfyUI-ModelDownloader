// Frontend side of the "Incognito Preview" node.
//
// The backend pushes PNG data URIs over the websocket as a custom
// `spooktools.incognito_preview` event ({ node: <execution id>, images: [...] })
// instead of saving a file or putting them in the node's `ui` output.
//
// We draw them in our own DOM widget rather than feeding ComfyUI's built-in
// preview (`node.imgs` / `app.nodePreviewImages`): the frontend rebuilds those
// from `output.images` (/view URLs of saved files) and from transient latent
// previews, so anything we write there is ignored or wiped on the next draw.
// A DOM widget is rendered as-is by both the LiteGraph canvas and Vue nodes.

import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

const NODE_TYPE = "SpookIncognitoPreview";
const EVENT = "spooktools.incognito_preview";
const MIN_HEIGHT = 120;
const MAX_AUTO_HEIGHT = 512;

function nodeForExecutionId(id) {
    // Execution ids are "<node>" for the root graph and "<subgraph node>:<inner node>…" inside subgraphs.
    let graph = app.graph;
    let node = null;
    for (const part of String(id).split(":")) {
        node = graph?.getNodeById?.(Number(part)) ?? null;
        if (!node) return null;
        graph = node.subgraph;
    }
    return node;
}

function createPreviewWidget(node) {
    const root = document.createElement("div");
    Object.assign(root.style, {
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        overflow: "hidden",
    });
    const grid = document.createElement("div");
    Object.assign(grid.style, { flex: "1", minHeight: "0", display: "grid", gap: "2px" });
    const caption = document.createElement("div");
    Object.assign(caption.style, {
        flex: "none",
        font: "11px sans-serif",
        color: "#999",
        textAlign: "center",
        padding: "2px 0",
    });
    caption.textContent = "Run the workflow to preview (nothing is saved)";
    root.append(grid, caption);

    const widget = node.addDOMWidget("incognito_preview", "spk_incognito_preview", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => MIN_HEIGHT,
    });
    node.spkIncognito = { widget, grid, caption, grown: false };
}

function showImages(node, uris) {
    if (!node.spkIncognito) createPreviewWidget(node);
    const state = node.spkIncognito;

    const cols = Math.ceil(Math.sqrt(uris.length));
    state.grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    state.grid.style.gridAutoRows = "minmax(0, 1fr)";

    const imgs = uris.map((src) => {
        const img = document.createElement("img");
        Object.assign(img.style, { width: "100%", height: "100%", objectFit: "contain", minHeight: "0" });
        img.src = src;
        return img;
    });
    state.grid.replaceChildren(...imgs);

    imgs[0].addEventListener(
        "load",
        () => {
            const { naturalWidth: w, naturalHeight: h } = imgs[0];
            state.caption.textContent = uris.length > 1 ? `${uris.length} images · ${w}×${h}` : `${w}×${h}`;
            // Grow the node to fit the first preview once; after that, respect the user's sizing.
            if (!state.grown && w && h) {
                state.grown = true;
                const rows = Math.ceil(uris.length / cols);
                const want = Math.min(((node.size[0] - 20) / cols) * (h / w) * rows, MAX_AUTO_HEIGHT);
                const height = node.computeSize()[1] - MIN_HEIGHT + want;
                if (height > node.size[1]) node.setSize([node.size[0], height]);
            }
            app.graph?.setDirtyCanvas?.(true, true);
        },
        { once: true },
    );
}

app.registerExtension({
    name: "ComfyUI.SpookTools.IncognitoPreview",

    async setup() {
        api.addEventListener(EVENT, ({ detail }) => {
            const uris = (detail?.images || []).filter((u) => typeof u === "string" && u.startsWith("data:image/"));
            if (!uris.length) return;
            const node = nodeForExecutionId(detail.node);
            if (!node) {
                console.warn(`[SpookTools] Incognito preview: node ${detail.node} not found in the open graph`);
                return;
            }
            showImages(node, uris);
        });
    },

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== NODE_TYPE) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            createPreviewWidget(this);
            return result;
        };
    },
});
