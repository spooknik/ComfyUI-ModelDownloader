// Models feature: the Download, Upload and Files tabs, sharing one folder list.

import { DownloadTab } from "./download_tab.js";
import { FilesTab } from "./files_tab.js";
import { FolderStore } from "./folders.js";
import { UploadTab } from "./upload_tab.js";

export function registerModelTabs(panel, app) {
    const folders = new FolderStore();

    // After an upload (folder given) or a delete (null): refresh the matching file listing and the model dropdowns.
    const onFilesChanged = (folder) => {
        files.reloadIfShowing(folder);
        // Refresh model dropdowns on nodes so new/removed files show up without a page reload.
        try {
            Promise.resolve(app.refreshComboInNodes?.()).catch(() => {});
        } catch (err) {
            // Older/newer frontends may not expose this; not critical.
        }
    };

    const download = new DownloadTab({ folders });
    const upload = new UploadTab({ folders, onFilesChanged });
    const files = new FilesTab({ folders, onFilesChanged });
    panel.addTab(download);
    panel.addTab(upload);
    panel.addTab(files);

    // Leaving the page aborts in-flight uploads, so ask first.
    window.addEventListener("beforeunload", (e) => {
        if (upload.hasActiveUploads()) {
            e.preventDefault();
            e.returnValue = "";
        }
    });

    folders.load().catch((err) => download.showMessage(`Cannot reach download service: ${err.message}`, true));
    download.startRefreshLoop();
}
