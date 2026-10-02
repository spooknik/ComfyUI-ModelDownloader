// Frontend side of the "Incognito Preview" node.
//
// The backend sends the image as base64 data URIs in the node's UI payload
// (key `incognito_images`) instead of saving a PNG to the temp folder. To
// render it we have to cover both frontends, which look in slightly different
// places, so we seed all of them:
//
//   - Classic LiteGraph: reads `node.imgs` directly.
//   - Vue: the node image preview reads `app.nodePreviewImages[locator]`
//     *before* falling back to building `/view?...` URLs from
//     `output.images`. Our node emits no files, so any `/view` fallback would
//     point at a non-existent file; writing the data URIs here makes the
//     renderer pick them up instead. This mirrors webcamCapture's out-of-band
//     capture path (`setNodePreviewsByNodeId` writes into this same map).
//
// The `executed` handler stores `detail.output` *before* calling
// `node.onExecuted(output)`, so by the time we run, the store already holds an
// `output.images` entry that would otherwise win. Overwriting the map entries
// reclaims priority for our in-memory previews. No media asset is ever created
// server-side; nothing goes through /view.

import { app } from "../../scripts/app.js";

function setPreviews(node, uris) {
    // Seed every lookup shape the frontends use:
    //   - bare id (classic "app.nodePreviewImages[id]")
    //   - string id
    //   - locator "id:0" used by the current Vue renderer for root-graph nodes
    const map = app?.nodePreviewImages;
    if (map && typeof map === "object") {
        for (const k of [node.id, String(node.id), `${node.id}:0`]) map[k] = uris;
    }

    // Classic LiteGraph path: decoded Image elements on the node.
    const imgs = uris.map((src) => {
        const img = new Image();
        img.onload = () => {
            node.setSizeForImage?.();
            app.graph?.setDirtyCanvas?.(true, true);
        };
        img.src = src;
        return img;
    });
    node.imgs = imgs;

    app.graph?.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: "ComfyUI.SpookTools.IncognitoPreview",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== "SpookIncognitoPreview") return;

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            onExecuted?.apply(this, arguments);

            const uris = output?.incognito_images;
            if (!Array.isArray(uris) || !uris.length) return;

            const valid = uris.filter((u) => typeof u === "string" && u.startsWith("data:image"));
            if (valid.length) setPreviews(this, valid);
        };
    },
});
