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
- If ComfyUI is behind a reverse proxy (nginx, Cloudflare, etc.), each 32 MB upload chunk from the browser panel must fit within the proxy's per-request body limit. For nginx this is `client_max_body_size`, which defaults to 1 MB. A single-request `curl` upload must fit the whole file.
- The routes run on ComfyUI's own server, so they have the same authentication and network exposure as ComfyUI itself. If your instance is reachable by untrusted users, put authentication in front of it.
