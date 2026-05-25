# Amalthea

Amalthea is a lightweight macOS frontend for SageMath Jupyter notebooks.

It is intentionally document-centric:

- one notebook per native window
- embedded notebook view, not a full JupyterLab workspace
- native macOS menus for the important actions
- minimal embedded Jupyter chrome

## What It Is

Amalthea is for people who want to work with Sage notebooks as native macOS
documents instead of as tabs inside a general-purpose browser or JupyterLab
workspace.

It keeps the app small by relying on:

- AppKit for windows and menus
- `WKWebView` for notebook rendering
- an external SageMath installation for the kernel and Jupyter server

## What It Is Not

- not a bundled Sage distribution
- not a full JupyterLab desktop shell
- not a cross-platform application
- not intended for large-scale multi-document session management

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

For direct development launch:

```sh
.venv/bin/python Amalthea.py
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
- The active packaging route is PyInstaller.

## Authorship

Amalthea was originally designed and developed in PyQt and PySide by its
repository author.

The current native macOS PyObjC/AppKit/`WKWebView` port, packaging, and
integration work were co-developed with OpenAI Codex.
