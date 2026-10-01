// Model folder list shared by the Download, Upload and Files tabs.

import { apiFetch } from "../../lib/api.js";

export class FolderStore {
    constructor() {
        this.folders = [];
        this.selects = [];
    }

    /** Keep a <select> filled with the folder list. `data-existing-only="1"` hides folders not yet created. */
    register(select) {
        this.selects.push(select);
        this.renderSelect(select);
    }

    /** Fetch the folder list. Throws on network/JSON errors so the caller can report them. */
    async load() {
        const response = await apiFetch("/folders");
        const data = await response.json();
        this.folders = data.folders || [];
        for (const select of this.selects) this.renderSelect(select);
    }

    renderSelect(select) {
        const existingOnly = select.dataset.existingOnly === "1";
        const previous = select.value;
        select.innerHTML = "";
        const folders = existingOnly ? this.folders.filter((f) => f.exists) : this.folders;
        for (const folder of folders) {
            const option = document.createElement("option");
            option.value = folder.name;
            option.textContent = existingOnly || folder.exists ? folder.name : `${folder.name} (will create)`;
            select.appendChild(option);
        }
        if (folders.length === 0) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "No model folders found";
            select.appendChild(option);
        }
        if (previous && folders.some((f) => f.name === previous)) select.value = previous;
    }
}
