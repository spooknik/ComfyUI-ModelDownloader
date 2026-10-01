// System feature: the System tab (resource monitor, Free VRAM, Restart ComfyUI). Exports only.

import { injectSystemStyles } from "./styles.js";
import { SystemTab } from "./system_tab.js";

export function registerSystemTab(panel, app) {
    injectSystemStyles();
    panel.addTab(new SystemTab());
}
