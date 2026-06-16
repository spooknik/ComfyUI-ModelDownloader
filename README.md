# ComfyUI-ModelDownloader

A ComfyUI custom-node extension that lets WebUI users download models directly into the server's ComfyUI model folders. This solves the problem of users who do not have shell or filesystem access to the machine running ComfyUI.

## Features

- Paste any direct `http`/`https` model URL.
- Choose a destination folder from the common ComfyUI model directories (`checkpoints`, `loras`, `vae`, `controlnet`, etc.).
- Optional custom filename override.
- Live progress bar with bytes downloaded, total size, speed, and ETA.
- List of active and completed downloads, with cancel support.
- Routes are registered directly on ComfyUI's own web server, so remote WebUI users can reach them without exposing a separate port.
- Optional standalone `aiohttp` service still available for development or local-only use.

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

The WebUI panel appears as a **Model Downloader** button in the top-right of the ComfyUI canvas.

## How it works

When ComfyUI loads this custom node:

1. It registers download endpoints under `/api/model-downloader/*` on ComfyUI's existing PromptServer.
2. The browser uses the same origin as the ComfyUI WebUI (`http(s)://your-comfyui-host`), so downloads work for remote users.
3. A standalone service also starts on `127.0.0.1:8189` by default for backward compatibility / local development. Set `COMFY_MODEL_DL_STANDALONE=0` to disable it.

## Configuration

Set environment variables before launching ComfyUI:

| Variable | Default | Description |
| --- | --- | --- |
| `COMFYUI_PATH` | auto-detected | Absolute path to the ComfyUI base directory. |
| `COMFY_MODEL_DL_STANDALONE` | `1` | Whether to start the legacy standalone service on port 8189. |
| `COMFY_MODEL_DL_PORT` | `8189` | Port for the standalone service. |
| `COMFY_MODEL_DL_HOST` | `127.0.0.1` | Host for the standalone service. |

The folder list is hard-coded to common ComfyUI model directories and resolved under `$COMFYUI_PATH/models`.

## Manual standalone service startup

If you prefer not to auto-start the service from ComfyUI, set `COMFY_MODEL_DL_STANDALONE=0` and run it manually:

```bash
python -m ComfyUI-ModelDownloader.download_service
```

## Hugging Face URLs

Paste the repository page link with `/blob/` and the plugin will automatically convert it to the raw `/resolve/` download URL. For example:

```text
https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
```

becomes

```text
https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
```

If a URL returns an HTML page instead of a binary file, the download will fail with a clear error instead of saving a tiny `.safetensors` HTML file.

## API endpoints

### ComfyUI PromptServer routes (recommended)

These are served from the same host/port as ComfyUI:

- `GET /api/model-downloader/folders` — List available model folders.
- `POST /api/model-downloader/download` — Start a download.
- `GET /api/model-downloader/downloads` — List all downloads.
- `GET /api/model-downloader/progress/{download_id}` — Get one download.
- `DELETE /api/model-downloader/download/{download_id}` — Cancel a download.

### Standalone service routes (legacy)

Available when the standalone service is running on its own port:

- `GET /folders`
- `POST /download`
- `GET /downloads`
- `GET /progress/{download_id}`
- `DELETE /download/{download_id}`

All endpoints respond with JSON.

## Usage from the WebUI

After ComfyUI loads the extension, a **Model Downloader** button appears in the top-right of the ComfyUI interface. Click it to open the panel, paste a URL, pick a folder, and start the download.

## Security notes

- Only `http://` and `https://` URLs are accepted.
- Filenames are sanitized to prevent directory traversal.
- The standalone service binds to `127.0.0.1` by default; change `COMFY_MODEL_DL_HOST` only if you understand the network exposure implications.
- Because the new ComfyUI routes run through ComfyUI's own server, they inherit whatever authentication / exposure ComfyUI already has. Add authentication if your ComfyUI instance is exposed to untrusted users.
