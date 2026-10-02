// Frontend side of the "Incognito Preview" node.
//
// The backend sends the image as base64 data URIs in the node's UI payload
// (key `incognito_images`) instead of saving a PNG to the temp folder. This
// extension stores those data URIs on `node.imgs`, which both the classic
// LiteGraph renderer and the Vue frontend already know how to display for
// output nodes — so the image appears on the node exactly like a normal
// preview, without ever touching the server's media assets.

import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "ComfyUI.SpookTools.IncognitoPreview",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== "SpookIncognitoPreview") return;

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            onExecuted?.apply(this, arguments);

            const uris = output?.incognito_images;
            if (!Array.isArray(uris) || uris.length === 0) return;

            const imgs = uris.map((src) => {
                const img = new Image();
                img.src = src;
                return img;
            });

            // Hand the images to the standard node image renderer. This works
            // because output nodes with `node.imgs` set get drawn by ComfyUI
            // itself; we just point it at data URIs instead of /view URLs.
            this.imgs = imgs;
            if (typeof this.setSizeForImage === "function") {
                this.setSizeForImage?.();
            } else if (imgs.length) {
                // Fallback for frontends without setSizeForImage.
                imgs[0].onload = () => this.setSize?.(this.computeSize());
            }
            app.graph?.setDirtyCanvas?.(true, true);
        };
    },
});
