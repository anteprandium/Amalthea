# Amalthea

Amalthea is a lightweight macOS frontend for SageMath Jupyter notebooks.

It is intentionally document-centric:

- one notebook per native window
- embedded notebook view, not a full JupyterLab workspace
- native macOS menus for the important actions
- minimal embedded Jupyter chrome

## Requirements

- macOS on Apple Silicon
- SageMath installed, with `sage` available at `/usr/local/bin/sage`

Amalthea does not bundle Sage. It starts and manages a local Sage/Jupyter
server on port `8988`.

## Build

To build a local app bundle:

```sh
./build_macos_app.sh
```

This produces:

```text
dist/Amalthea.app
```

To deploy the freshly built app to `/Applications` on a personal machine:

```sh
./deploy.sh
```

`deploy.sh` stops stale Amalthea and Sage/Jupyter processes, rebuilds the app,
installs it into `/Applications` using `sudo`, and cleans local build
artifacts afterward.

## Notes

- Amalthea opens notebook URLs under `/notebooks/<relative-path>?token=...`
  rather than JupyterLab workspace routes.
- Non-notebook navigations are handed off to the system browser.
- The app owns `.ipynb` documents in the packaged bundle metadata.

## Authorship

Amalthea was originally designed and developed in PyQt and PySide by its repository author.

The current native macOS PyObjC/AppKit/`WKWebView` port, packaging, and
integration work were co-developed with OpenAI Codex.
