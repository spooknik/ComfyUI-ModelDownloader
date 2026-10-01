// Gallery feature: a panel tab summarising output/ and input/, opening a full-screen bulk image manager.

import { GalleryTab } from "./gallery_tab.js";
import { GalleryOverlay } from "./overlay.js";
import { injectGalleryStyles } from "./styles.js";

export function registerGalleryTab(panel, app) {
    injectGalleryStyles();
    let overlay = null; // Built on first use; most sessions never open the gallery.

    const onFilesChanged = (root) => {
        tab.refresh();
        if (root !== "input") return;
        // LoadImage & co. list input/ files in their dropdowns: refresh them like the models feature does.
        try {
            Promise.resolve(app.refreshComboInNodes?.()).catch(() => {});
        } catch (err) {
            // Older/newer frontends may not expose this; not critical.
        }
    };

    const tab = new GalleryTab({
        openGallery: (root) => {
            overlay ??= new GalleryOverlay({ onFilesChanged });
            overlay.open(root);
        },
    });
    panel.addTab(tab);
}
