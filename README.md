# ComfyUI-SpookTools

Server tools for ComfyUI from the browser: model downloads and uploads, and a model file manager, with more coming. Useful when you run ComfyUI on a machine you have no shell or filesystem access to, such as a rented GPU box, a container or a home server.

Formerly **ComfyUI-ModelDownloader**. See [Upgrading from ComfyUI-ModelDownloader](#upgrading-from-comfyui-modeldownloader).

## Features

- A floating **Spook Tools** button opens the tools panel. You can drag the button anywhere with mouse, touch or pen, and each browser remembers where you left it. The panel opens next to the button and always stays on-screen. **Reset button position** in the panel footer puts the button back in the top-right corner.
- **Download** tab: paste any direct `http`/`https` model URL, pick a destination folder from the ComfyUI model directories (`checkpoints`, `loras`, `vae`, `controlnet`, etc.), and optionally override the filename. A live progress bar shows bytes downloaded, total size, speed and ETA. Active and finished downloads are listed, and active ones can be cancelled.
- **Upload** tab: pick one or more model files from your own computer and send them into a model folder, with progress, cancel and an overwrite option. Files go up in 32 MB chunks. If the connection drops or a proxy times out, the chunk is retried automatically, so multi-GB uploads survive flaky links and per-request proxy limits. Uploads are not limited by ComfyUI's `--max-upload-size`.
- **Files** tab: browse each model folder (including subfolders), filter and sort by name, size or date, and delete old models. Delete needs a second click to confirm. You can't delete a file while a download or upload is still writing it.
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

- **GPU**: for each GPU, the load, VRAM used out of total, temperature, power draw and limit, and fan speed. Under the VRAM bar, it shows how much of that PyTorch, and so ComfyUI, holds (reserved and allocated). These figures need the optional `nvidia-ml-py` package (`pip install nvidia-ml-py`). Without it, only PyTorch's VRAM figures are shown.
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
- If ComfyUI is behind a reverse proxy (nginx, Cloudflare, etc.), each 32 MB upload chunk from the browser panel must fit within the proxy's per-request body limit. For nginx this is `client_max_body_size`, which defaults to 1 MB. A single-request `curl` upload must fit the whole file.
- The routes run on ComfyUI's own server, so they have the same authentication and network exposure as ComfyUI itself. If your instance is reachable by untrusted users, put authentication in front of it.
