// ComfyUI-SpookTools frontend entry point.
//
// ComfyUI imports every .js file under js/ as an extension, so this is the only module with side effects
// and the only one that imports ComfyUI's app (it is served at /extensions/<folder>/spooktools.js, so
// ../../scripts/app.js resolves to /scripts/app.js whatever the plugin folder is called). Everything under
// lib/ and features/ only exports, and receives `app` as an argument where needed.

import { app } from "../../scripts/app.js";
import { registerModelTabs } from "./features/models/index.js";
import { registerSystemTab } from "./features/system/index.js";
import { FloatingButton } from "./lib/button.js";
import { SpookPanel } from "./lib/panel.js";
import { injectStyles } from "./lib/styles.js";

const LOG_PREFIX = "[SpookTools]";

app.registerExtension({
    name: "ComfyUI.SpookTools",
    async setup() {
        console.log(`${LOG_PREFIX} extension setup starting`);
        injectStyles();

        const button = new FloatingButton({
            label: "Spook Tools",
            title: "Spook Tools: download, upload and manage models on the ComfyUI server (drag to move)",
            storageKey: "spooktools.button.position",
        });
        const panel = new SpookPanel(button);
        button.onClick = () => panel.toggle();

        // Features add their tabs here: one line per feature.
        registerModelTabs(panel, app);
        registerSystemTab(panel, app);

        panel.addFooterAction("Reset button position", () => button.resetPosition());
        document.body.appendChild(panel.element);
        button.mount();
        console.log(`${LOG_PREFIX} toggle button added to body`);
    },
});
