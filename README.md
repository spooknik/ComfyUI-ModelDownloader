# ComfyUI-ModelDownloader

A ComfyUI custom-node extension that lets WebUI users download models directly into the server's ComfyUI model folders. This solves the problem of users who do not have shell or filesystem access to the machine running ComfyUI.

## Features

- Paste any direct `http`/`https` model URL.
- Choose a destination folder from the common ComfyUI model directories (`checkpoints`, `loras`, `vae`, `controlnet`, etc.).
- Optional custom filename override.
- Live progress bar with bytes downloaded, total size, speed, and ETA.
- List of active and completed downloads, with cancel support.
- Small, dedicated `aiohttp` service that runs alongside ComfyUI so the main server stays responsive.

## Installation

1. Clone or copy this folder into your ComfyUI `custom_nodes` directory:

   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/yourusername/ComfyUI-ModelDownloader.git
   ```

2. Install the Python dependency (aiohttp is usually already bundled with ComfyUI):

   ```bash
   pip install -r ComfyUI-ModelDownloader/requirements.txt
   ```

3. Restart ComfyUI.

The service auto-starts on port **8189** when ComfyUI loads the extension.

## Configuration

Set environment variables before launching ComfyUI:

| Variable | Default | Description |
| --- | --- | --- |
| `COMFYUI_PATH` | auto-detected | Absolute path to the ComfyUI base directory. |
| `COMFY_MODEL_DL_PORT` | `8189` | Port for the download service. |
| `COMFY_MODEL_DL_HOST` | `127.0.0.1` | Host to bind the service to. |

The folder list is hard-coded to common ComfyUI model directories and resolved under `$COMFYUI_PATH/models`.

## Manual service startup

If you prefer not to auto-start the service from ComfyUI, set `COMFY_MODEL_DL_NO_AUTO_START=1` and run it manually:

```bash
python -m ComfyUI-ModelDownloader.download_service
```

## API endpoints

All endpoints respond with JSON and support CORS for the ComfyUI WebUI.

- `GET /folders` — List available model folders and their absolute paths.
- `POST /download` — Start a download (`url`, `folder`, optional `filename`, optional `overwrite`).
- `GET /downloads` — List all downloads.
- `GET /progress/{download_id}` — Get the state of a single download.
- `DELETE /download/{download_id}` — Cancel a pending/running download.

## Usage from the WebUI

After ComfyUI loads the extension, a **Model Downloader** button appears in the ComfyUI menu. Click it to open the panel, paste a URL, pick a folder, and start the download.

## Security notes

- Only `http://` and `https://` URLs are accepted.
- Filenames are sanitized to prevent directory traversal.
- The service binds to `127.0.0.1` by default; change `COMFY_MODEL_DL_HOST` only if you understand the network exposure implications.
- The extension trusts the local ComfyUI WebUI caller; add authentication if your ComfyUI instance is exposed to untrusted users.
