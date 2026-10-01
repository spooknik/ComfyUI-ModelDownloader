# ComfyUI-SpookTools

Server tools for ComfyUI from the browser: model downloads and uploads, and a model file manager, with more coming. Useful when you run ComfyUI on a machine you have no shell or filesystem access to, such as a rented GPU box, a container or a home server.

Formerly **ComfyUI-ModelDownloader**. See [Upgrading from ComfyUI-ModelDownloader](#upgrading-from-comfyui-modeldownloader).

## Features

- A floating **Spook Tools** button opens the tools panel. You can drag the button anywhere with mouse, touch or pen, and each browser remembers where you left it. The panel opens next to the button and always stays on-screen. **Reset button position** in the panel footer puts the button back in the top-right corner.
- **Download** tab: paste any direct `http`/`https` model URL, pick a destination folder from the ComfyUI model directories (`checkpoints`, `loras`, `vae`, `controlnet`, etc.), and optionally override the filename. A live progress bar shows bytes downloaded, total size, speed and ETA. Active and finished downloads are listed, and active ones can be cancelled.
- **Upload** tab: pick one or more model files from your own computer and send them into a model folder, with progress, cancel and an overwrite option. Files go up in 32 MB chunks. If the connection drops or a proxy times out, the chunk is retried automatically, so multi-GB uploads survive flaky links and per-request proxy limits. Uploads are not limited by ComfyUI's `--max-upload-size`.
- **Files** tab: browse each model folder (including subfolders), filter and sort by name, size or date, and delete old models. Delete needs a second click to confirm. You can't delete a file while a download or upload is still writing it.
- **Gallery** tab: a bulk image manager for ComfyUI's `output` (generated) and `input` (imported) folders. Browse thumbnails, select many files at once, download them as one zip or delete them. See [Gallery](#gallery).
- After an upload or delete, node model dropdowns refresh automatically.
- All routes run on ComfyUI's own web server, so remote users can reach them without you exposing another port.

## Installation

1. Clone this repository into your ComfyUI `custom_nodes` directory:

   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/spooknik/ComfyUI-SpookTools.git
   ```

2. Install the Python dependency. ComfyUI usually already includes aiohttp.

   ```bash
   pip install -r ComfyUI-SpookTools/requirements.txt
   ```

3. Restart ComfyUI.

The **Spook Tools** button appears in the top-right corner of the ComfyUI interface.

## Upgrading from ComfyUI-ModelDownloader

The project was renamed. The old folder name still works, but to follow the new repository:

```bash
cd ComfyUI/custom_nodes/ComfyUI-ModelDownloader
git remote set-url origin https://github.com/spooknik/ComfyUI-SpookTools.git
git pull
cd ..
mv ComfyUI-ModelDownloader ComfyUI-SpookTools
```

Then restart ComfyUI and hard-refresh the browser tab (Ctrl+Shift+R or Cmd+Shift+R) so it loads the new frontend code. The old API prefix `/api/model-downloader` still works as an alias for `/api/spooktools`, so scripts using it, or a tab still running the old page, keep working.

The standalone service on port 8189 and its `COMFY_MODEL_DL_STANDALONE`, `COMFY_MODEL_DL_PORT` and `COMFY_MODEL_DL_HOST` variables were removed. The browser panel never used it. Use the routes on ComfyUI's own port instead.

## Configuration

Set environment variables before launching ComfyUI:

| Variable | Default | Description |
| --- | --- | --- |
| `COMFYUI_PATH` | auto-detected | Absolute path to the ComfyUI base directory. |

Model folders are the common ComfyUI model directories plus any existing subdirectory of `$COMFYUI_PATH/models`.

## Hugging Face URLs

You can paste the repository page link with `/blob/`, and the plugin converts it to the raw `/resolve/` download URL. For example:

```text
https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
```

becomes

```text
https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
```

If a URL returns an HTML page instead of a binary file, the download fails with a clear error. It does not save a tiny `.safetensors` file that is really an HTML page.

## System

The **System** tab is a live resource monitor for the machine ComfyUI runs on. It refreshes every 2 seconds, but only while the tab is visible and the panel is open.

- **GPU**: for each GPU, the load, VRAM used out of total, temperature, power draw and limit, and fan speed. Under the VRAM bar, it shows how much of that PyTorch, and so ComfyUI, holds (reserved and allocated). These figures come from the `nvidia-ml-py` package, which is listed in `requirements.txt` (`pip install -r requirements.txt`). Without it, only PyTorch's VRAM figures are shown.
- **CPU**: the model, core and thread counts, total load with a small per-core strip, and the load average on Linux and macOS.
- **RAM**: used out of total, swap, and ComfyUI's own memory use.
- **Disks**: free space for the models, output, input and temp directories. Directories on the same filesystem share one bar, labelled with all of them. `--base-directory`, `--output-directory` and similar options are respected.
- **Versions** (collapsed): ComfyUI, the frontend, Python, PyTorch, CUDA, cuDNN, xformers, the NVIDIA driver, the OS, and whether ComfyUI runs in a container.
- The top line shows uptime and the queue (running and pending jobs). Bars turn amber above 75% and red above 90%.

In a Docker container, the CPU and RAM figures are for the whole host. If the container has a memory or CPU limit (cgroup v1 or v2), that limit is shown as well.

**Free VRAM** asks ComfyUI to unload all models and free cached memory. It uses ComfyUI's built-in `/api/free` endpoint. If a job is running, the memory is freed when that job finishes.

**Restart ComfyUI** needs a second click to confirm. If jobs are running or queued, it tells you how many and asks again before restarting, because the running job is lost. Once ComfyUI is back, the page reloads by itself. If ComfyUI isn't back within 3 minutes, the tab tells you to check the container or console logs.

How the restart works:

- **By default, ComfyUI is restarted in place.** The Python process replaces itself (`os.execv`) with a fresh ComfyUI started with the same command line. On Linux the process ID stays the same, so a launcher script that waits for ComfyUI to exit never notices. This matters for Docker images such as [mmartial/ComfyUI-Nvidia-Docker](https://github.com/mmartial/ComfyUI-Nvidia-Docker), whose container stops when ComfyUI exits. On Windows, a new process replaces the old one.
- **Supervised setups**: set `SPOOKTOOLS_RESTART_MODE=exit` to make ComfyUI exit with status 0 instead, and let your supervisor start it again. Use a restart policy that also restarts after a clean exit, such as Docker's `always` or `unless-stopped`, or systemd's `Restart=always`.
- **comfy-cli**: when ComfyUI runs under `comfy launch`, the plugin asks comfy-cli to restart it, the same way ComfyUI-Manager does.

**Anyone who can reach your ComfyUI can restart it**, just as they can queue jobs or delete models. See [Security notes](#security-notes).

## Gallery

ComfyUI's own interface handles outputs one file at a time. The **Gallery** tab shows how many files `output` and `input` hold and how much space they use. **Open gallery** opens a full-screen manager:

- Switch between **Output** and **Input**, move through subfolders with the breadcrumb and the subfolder menu, and choose whether files in subfolders are included.
- Search by file name, sort by newest, oldest, name or size, and filter by images, videos or other files (`.json`, `.latent`, `.txt` and so on, so you can clean those up too). A slider sets the thumbnail size.
- Files are shown 200 per page, so folders with tens of thousands of files stay fast. Thumbnails load as they scroll into view.
- Click a file to select it, Shift-click to select a range (this also works across pages), and Ctrl-click or Cmd-click to toggle one file. **Select page** selects the whole page (or press Ctrl+A), and **Select all N matching** selects every file that matches the current folder, search and filter. The number of selected files and their total size are always shown.
- **Download zip** downloads the selection as one archive with its subfolders preserved. **Delete** asks for confirmation with a second click ("Delete 37 files?") and then reports any file it could not delete. Deleting from `input` refreshes the image lists of nodes such as Load Image.
- Double-click a file (or use its ⤢ button) to open a large preview. Use ← and → or the arrow buttons to step through the files, and videos play in the preview. Each press of Esc does one step: it closes the preview if one is open, otherwise clears the selection, otherwise closes the gallery. While the gallery is open, key presses do not reach ComfyUI's shortcuts.

The folders are the ones ComfyUI itself uses (`folder_paths.get_output_directory()` and `get_input_directory()`), so `--base-directory`, `--output-directory` and `--input-directory` are respected. In Docker images such as [mmartial/ComfyUI-Nvidia-Docker](https://github.com/mmartial/ComfyUI-Nvidia-Docker), that is typically `/basedir/output` and `/basedir/input`. The gallery cannot reach any other folder, and hidden files and folders (names starting with `.`) are not shown.

Thumbnails are made with Pillow, which ships with ComfyUI. They are cached in ComfyUI's temp folder under `spooktools-thumbs`, which ComfyUI empties when it starts. At most four thumbnails are rendered at a time, so opening a large folder does not use every CPU core. Animated images show their first frame, and EXIF rotation is applied. Files Pillow cannot read show "No preview". Videos show an icon (there is no ffmpeg dependency) and play in the preview.

Zip downloads are streamed. The archive is written while it downloads, so it is never built in memory or on disk, and multi-GB selections work. Files are stored without recompression (images and videos are already compressed) and zip64 is used when the archive needs it. The browser's own download manager shows the progress. If a file is deleted while its archive is downloading, that file is left out. If you cancel the download, the server stops writing the archive.

## API endpoints

These are served from the same host and port as ComfyUI. Every route is also available under the legacy prefix `/api/model-downloader`.

- `GET /api/spooktools/folders`: list available model folders.
- `POST /api/spooktools/download`: start a download. The JSON body is `{"url", "folder", "filename", "overwrite"}`.
- `GET /api/spooktools/downloads`: list all downloads.
- `GET /api/spooktools/progress/{download_id}`: get one download.
- `DELETE /api/spooktools/download/{download_id}`: cancel a download.
- `GET /api/spooktools/files?folder=loras`: list the files in a model folder, recursively.
- `DELETE /api/spooktools/files?folder=loras&path=sdxl/old.safetensors`: delete a file. `path` is relative to the folder.
- `POST /api/spooktools/upload?folder=loras&filename=my.safetensors&overwrite=0`: upload a file in a single request. The request body is the raw file bytes (`Content-Type: application/octet-stream`). For example: `curl --data-binary @my.safetensors -H "Content-Type: application/octet-stream" "http://host:8188/api/spooktools/upload?folder=loras&filename=my.safetensors"`.
- Chunked uploads (used by the browser panel):
  - `POST /api/spooktools/upload/start` with JSON `{"folder", "filename", "size", "overwrite"}` returns an `upload_id`.
  - `POST /api/spooktools/upload/{upload_id}/chunk?offset=N` sends raw bytes starting at byte `N`. It returns `received`, plus `done: true` after the last chunk. A wrong offset returns 409 with the server's `received` count, so the client can resume.
  - `GET /api/spooktools/upload/{upload_id}` returns how many bytes the server has committed.
  - `DELETE /api/spooktools/upload/{upload_id}` cancels the upload and removes the partial file.
  - Sessions idle for 15 minutes are discarded.
- `GET /api/spooktools/system/stats`: a snapshot of CPU, RAM, GPUs, disks, versions and ComfyUI's state (uptime, queue, `boot_id`). Sections whose data source is missing are `null` or empty, with the reason under `notes`.
- `POST /api/spooktools/system/restart` with JSON `{"force": false}`: restart ComfyUI. If jobs are running or queued and `force` isn't `true`, it returns 409 with the `running` and `pending` counts. Otherwise it returns 202 with the current `boot_id` and restarts half a second later. `boot_id` changes on every start. Send `Content-Type: application/json` or no body: form posts are refused, so another website can't trigger a restart.

All endpoints respond with JSON.

Gallery endpoints. `root` is `output` or `input`, and every path is relative to that folder:

- `GET /api/spooktools/gallery/list?root=output&subfolder=&recursive=1&q=&sort=newest&kind=all&offset=0&limit=200`: one page of files plus totals. The response is `{root, subfolder, directory, subfolders, total, total_size, offset, items: [{path, name, size, modified, kind}]}`, where `subfolders` lists the immediate child folders. `sort` is `newest`, `oldest`, `name` or `size`. `kind` is `all`, `image`, `video` or `other`. `limit` is at most 1000. With `paths_only=1`, the response has `paths` and `sizes` arrays for every matching file instead of `items`.
- `GET /api/spooktools/gallery/thumb?root=output&path=a.png&size=256`: a WebP thumbnail (JPEG if Pillow has no WebP support). It returns 415 if the file can't be turned into a thumbnail.
- `GET /api/spooktools/gallery/file?root=output&path=a.png`: the original file, served inline for images, videos and text, and as a download for anything else.
- `POST /api/spooktools/gallery/delete` with JSON `{"root", "paths": [...]}`: deletes files, never folders, with up to 10,000 paths per request. It returns `{deleted: [...], failed: [{path, error}]}`.
- `POST /api/spooktools/gallery/zip` with JSON `{"root", "paths": [...]}`: checks the selection and returns `{token, count, total_size, filename, missing}`. Then `GET /api/spooktools/gallery/zip/{token}` streams the archive. A token expires after 10 minutes and can be used up to three times.

## Development

Run the tests from the repository root:

```bash
python -m pytest
```

The server code is in the `spooktools/` package. `spooktools/routes.py` registers each feature's route module, such as `routes_models.py`. The browser code is in `js/`: `spooktools.js` is the entry point, `lib/` holds the button, panel and helpers, and `features/` holds the tabs. ComfyUI imports every `.js` file under `js/` as an extension, so every module except `spooktools.js` must only export and have no side effects when imported.

## Security notes

- Only `http://` and `https://` download URLs are accepted.
- Filenames are sanitized to prevent directory traversal.
- The file manager can only list and delete files inside `models/<folder>`. Folder names and file paths that contain `..`, absolute paths or drive letters are rejected.
- **Anyone who can reach your ComfyUI can delete and upload model files.** Only expose ComfyUI to people you trust.
- Other websites you visit can't use your browser to call these endpoints (CSRF). State-changing requests marked `Sec-Fetch-Site: cross-site` are refused, and POSTs must send `Content-Type: application/json` (or `application/octet-stream` for uploads), which a page on another site can't send without a CORS preflight. Scripts and `curl` must set the Content-Type header.
- **Anyone who can reach your ComfyUI can also view, download and delete everything in `output` and `input` through the gallery.** It can't reach files outside those two folders. Paths with `..`, absolute paths, drive letters and hidden names are rejected. Files are served with `X-Content-Type-Options: nosniff` and a sandboxing Content-Security-Policy, and only image, video and plain-text files are shown in the browser, so an uploaded HTML or SVG file can't run script on ComfyUI's origin.
- If ComfyUI is behind a reverse proxy (nginx, Cloudflare, etc.), each 32 MB upload chunk from the browser panel must fit within the proxy's per-request body limit. For nginx this is `client_max_body_size`, which defaults to 1 MB. A single-request `curl` upload must fit the whole file.
- The routes run on ComfyUI's own server, so they have the same authentication and network exposure as ComfyUI itself. If your instance is reachable by untrusted users, put authentication in front of it.
