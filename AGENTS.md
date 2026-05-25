# Amalthea PyObjC Notes

This repository contains a single-file macOS Python application,
`Amalthea.py`. The app is a lightweight document-centric frontend for SageMath
Jupyter notebooks. It starts a local Sage/Jupyter server, opens one notebook
per application window, and embeds the notebook page in `WKWebView` through
PyObjC.

This file is the working record of the codebase, build chain, external
contracts, known failure modes, and the assumptions future work should preserve.

## Current Architecture

`Amalthea.py` is intentionally compact:

- `Amalthea(NSObject)` acts as the `NSApplication` delegate. It owns process
  lifetime, Finder and CLI open-file handling, the shared Sage server helper,
  and the list of open notebook windows.
- `Server` starts, stops, and discovers the Sage/Jupyter server on port `8988`.
- `NotebookWindow(NSObject)` owns a native `NSWindow`, a `WKWebView`, and the
  menu-command bridge into the notebook frontend.

There is no document model layer such as `NSDocument`. The app uses explicit,
manual window management:

- one notebook per window;
- Finder open-file events are queued until the Sage/Jupyter server is ready;
- launch with no documents creates a new untitled notebook file and opens it;
- window size is persisted in `NSUserDefaults` under
  `Anteprandium.Amalthea.MainWindowSize`.

The current UI stack is:

- AppKit for the app lifecycle, menus, and windows;
- WebKit `WKWebView` for notebook rendering;
- PyObjC as the Python bridge into Cocoa and WebKit.

The app is intentionally not trying to expose the full Jupyter shell. The
current product direction is:

- notebook document content in the window body;
- native macOS menus for app and notebook commands;
- as little embedded Jupyter chrome as possible, as long as document title and
  rename affordances remain available.

## Product Invariants

These are the intentional invariants of the app and should not be changed
casually:

- Sage remains an external dependency. The app does not bundle Sage.
- Each notebook opens in its own native window.
- The target UI is the single-document Notebook view, not the full JupyterLab
  workspace.
- Notebook URLs must stay relative to Jupyter's reported `root_dir`.
- The app starts and manages a local Sage/Jupyter server on port `8988`.
- The app owns `.ipynb` documents as an editor and provides both app and
  document icons in the packaged bundle.

## Sage/Jupyter Integration

SageMath is treated as an external dependency and is expected at:

```text
/usr/local/bin/sage
```

The app does not call the `jupyter` wrapper scripts directly. It uses Sage's
Python module entry points:

```text
sage -python -m notebook ...
sage -python -m jupyter_server list --json
sage -python -m jupyter_server stop 8988
```

This is deliberate. In SageMath 10.9, the bundled `jupyter` wrapper may have a
stale shebang, while `sage -python -m ...` works reliably.

The server startup command is currently:

```text
sage -python -m notebook \
  --no-browser \
  --expose-app-in-browser \
  --ip=127.0.0.1 \
  --port=8988 \
  --port-retries=0 \
  --ServerApp.root_dir=$HOME
```

Important details:

- `ServerApp.root_dir` is set to the user's home directory.
- The app strips several Python and virtualenv-related environment variables
  before launching Sage, to avoid leaking packaged Python state into the Sage
  subprocess.
- `Server.is_running()` parses `sage -python -m jupyter_server list --json`
  output and expects useful `url`, `token`, `root_dir`, and `port` fields.
- If a server is already running on port `8988`, Amalthea reuses it instead of
  starting a second one.

## Notebook URL Contract

The application is document-centric. Opening or creating a notebook should load
the document Notebook UI, not a workspace shell. The expected URL shape is:

```text
http://127.0.0.1:8988/notebooks/<path-relative-to-jupyter-root>?token=<token>
```

Do not change this to `/lab/tree/...` unless intentionally moving away from the
document-centric UI.

`Server.url_from_filename()` is a critical function:

- notebook paths are normalized with `Path(...).resolve(strict=False)`;
- the path must be inside the discovered Jupyter `root_dir`;
- the notebook path is converted to a path relative to `root_dir`;
- the relative path is percent-encoded while preserving `/`;
- the final route is `/notebooks/<encoded-relative-path>?token=...`.

Passing absolute filesystem paths directly to `/notebooks/...` causes
"outside root" or page-not-found failures in modern Jupyter Server.

## Native Browser Bridge

The embedded browser is `WKWebView`, configured with injected startup scripts.

### Embedded shell pruning

Amalthea currently hides several pieces of Jupyter shell chrome through an
injected stylesheet:

- the in-page Jupyter menubar;
- the in-page notebook toolbar;
- the notebook logo link in the top header.

Important detail: in the current Notebook 7 frontend, the visible logo is not
the old `#jp-MainLogo` node. The actual clickable logo is:

```text
#jp-NotebookLogo
```

It is rendered as an anchor to:

```text
http://127.0.0.1:8988/tree
```

with `target="_blank"`.

This was discovered by instrumenting the live `WKWebView` DOM. If the logo ever
reappears after a Sage/Jupyter upgrade, inspect the actual rendered top-panel
DOM again before guessing selectors.

### Command bridge

Notebook actions are not hardcoded as scattered raw snippets anymore. The app
injects a single browser bridge:

```text
window.__amalthea.runCommands([...])
```

`NotebookWindow.runNotebookCommands_()` serializes a list of notebook command
IDs and evaluates a short JS call into that bridge.

The injected bridge:

- waits for `window.jupyterapp.commands` to exist;
- polls for up to 10 seconds;
- executes commands sequentially with `await app.commands.execute(command)`;
- emits a specific console error if the app instance was not exposed to the
  browser.

This centralized bridge is less brittle than emitting separate ad hoc
`window.jupyterapp.commands.execute(...)` snippets from every menu handler.

### Root cause of the previous command failure

The original menu actions stopped working because modern Notebook/JupyterLab
frontends do not always expose the global app object in the browser. The
frontend only populates `window.jupyterapp` when the server starts with:

```text
--expose-app-in-browser
```

Without that flag:

- `window.jupyterapp` is absent;
- every `window.jupyterapp.commands.execute(...)` call fails;
- menu actions silently stop working unless debug logging is enabled.

That server-side flag is now part of Amalthea's startup contract. If notebook
commands ever stop working again, check the Sage notebook startup arguments
first.

### Debug console bridge

JavaScript console output is suppressed by default. Set:

```text
AMALTHEA_WEBENGINE_DEBUG=1
```

to mirror browser console messages to stderr through a WebKit script message
handler named `amaltheaConsole`.

This is useful when debugging:

- notebook command bridge failures;
- navigation failures;
- Jupyter frontend changes after a Sage upgrade.

### Navigation policy

Amalthea now applies the same ownership rule to both popup windows and ordinary
main-frame navigations:

- managed notebook URLs stay inside Amalthea;
- non-notebook navigations are sent to the system browser with `NSWorkspace`.

This matters because some notebook header and help links try to leave the
document view for tree or documentation routes. Amalthea should not try to
become a general Jupyter shell host.

## Current Menu Command Surface

The native menu bar is intentionally more important than the embedded page
chrome. The current organization is:

- `File`, `Edit`, `View`, `Insert`, `Cell`, `Kernel`, `Window`, `Help`
- there is no separate `Run` menu anymore
- notebook-specific cell editing lives under `Cell`, not generic `Edit`

High-value notebook-facing actions already surfaced natively include:

- Save
- Insert Cell Above
- Insert Cell Below
- Change Cell to Code
- Change Cell to Markdown
- Change Cell to Raw
- Cut Cell
- Copy Cell
- Paste Cell Below
- Split Cell at Cursor
- Clear Selected Cells
- Clear All Outputs
- Go to Line
- Command Palette
- Toggle Line Numbers
- Interrupt Kernel
- Restart Kernel
- Restart Kernel And Run All Cells
- Change Kernel...
- Show Shortcuts

These map to Jupyter command IDs such as:

```text
docmanager:save
notebook:insert-cell-above
notebook:insert-cell-below
notebook:change-cell-to-code
notebook:change-cell-to-markdown
notebook:change-cell-to-raw
apputils:activate-command-palette
notebook:toggle-all-cell-line-numbers
notebook:clear-all-cell-outputs
notebook:split-cell-at-cursor
notebook:interrupt-kernel
notebook:restart-kernel
notebook:restart-run-all
notebook:show-keyboard-shortcuts
```

If future Sage/Jupyter upgrades rename or remove these commands, fix the
command IDs before touching the Cocoa bridge.

Some command-palette actions in the embedded frontend can still crash or lead
to unsupported routes. The current product stance is pragmatic:

- surface the stable, high-value actions natively;
- do not try to preserve the whole command-palette universe;
- prefer hiding or externalizing unstable Jupyter shell actions over emulating
  them in Amalthea.

## Window and File Handling

Notebook creation and opening are intentionally simple:

- `default_notebooks_location()` prefers `~/Documents/Notebooks`, then
  `~/Documents`, then `~`.
- `new_untitled()` writes a small empty notebook JSON file to the first usable
  directory.
- files that do not end in `.ipynb` are rejected as unsupported;
- notebooks outside the active Jupyter `root_dir` are rejected before loading.

`WKWebView` delegates are used for:

- navigation failure fallback;
- renderer termination logging;
- browser new-window requests.

Current behavior:

- navigation failures replace the page with a small local HTML error page;
- content process termination is logged to stderr;
- notebook-triggered new windows create a new Amalthea notebook window with its
  own `WKWebView` only for managed notebook URLs;
- external popup links are sent to the default browser instead of being hosted
  inside Amalthea.

This popup behavior fixed crashes from links such as `Jupyter Reference`.

### Window lifetime policy

`Amalthea.documents` is not just legacy baggage from the earlier Qt versions.
In the current PyObjC app it is also the session-long strong owner of
`NotebookWindow` bridge objects.

Important decision:

- closed notebook windows are not removed from `Amalthea.documents` during the
  session.

Why:

- attempts to release `NotebookWindow` on close caused native AppKit crashes
  during `_NSWindowTransformAnimation` teardown;
- this showed up when closing one of multiple open notebook windows with
  `Cmd-W`;
- keeping the references for the whole session avoids that close-animation
  lifetime bug.

This is an intentional stability tradeoff:

- some stale `NotebookWindow` references accumulate during a session;
- `activeNotebook()` must therefore ignore windows that are no longer visible;
- the app is not expected to manage hundreds of documents per session, so the
  bounded leak is acceptable.

If someone later wants to reintroduce cleanup, they must validate it against
real AppKit close-animation timing and not just Python object ownership theory.

## Kernel Notes

Kernel behavior has a few important constraints that are easy to misread.

### Legacy notebook fallback behavior

Changing the server default kernel to the current Sage kernel is not enough to
make old Sage notebooks open under Sage again.

Why:

- many legacy notebooks record a stale versioned kernelspec name, such as
  `sagemath-9.5`;
- those same notebooks often record `metadata.language_info.name = "python"`;
- Notebook 7 frontend logic prefers exact kernelspec match, then language
  matching, before using the server default;
- with installed kernels like `python3` and `sagemath-10.8`, the language match
  chooses `python3`.

So:

- fresh or exact-match Sage notebooks open correctly under Sage;
- stale legacy Sage notebooks may still open under Python;
- this is a frontend metadata-selection issue, not an Amalthea-only routing
  issue.

This was intentionally not "fixed" with app-side spaghetti logic.

### Untitled notebook creation

Amalthea no longer hardcodes a stale versioned empty notebook template such as
`sagemath-10.5`.

New untitled notebooks are generated at runtime from the currently installed
Sage kernelspec:

- discover current Sage kernelspec name, display name, and language;
- write those into `metadata.kernelspec`;
- do not hardcode `language_info`.

This is the clean future-facing fix for notebooks Amalthea creates itself.

## Build And Packaging

The active packaging route is `PyInstaller`. The old `py2app` path is no longer
the supported build chain.

Use:

```sh
./build_macos_app.sh
```

For local install/deploy convenience there is also:

```sh
./deploy.sh
```

`deploy.sh` is a wrapper for personal-machine deployment. It:

1. stops running Amalthea processes;
2. tries to stop the Sage/Jupyter server on port `8988`;
3. kills any leftover listener on `8988`;
4. runs `build_macos_app.sh`;
5. removes `/Applications/Amalthea.app` with `sudo`;
6. moves the fresh `dist/Amalthea.app` into `/Applications` with `sudo`;
7. cleans local `build/`, `dist/`, and `Amalthea.spec`.

It is intentionally machine-oriented and not part of a generalized distribution
story.

The script currently:

1. Ensures Sage exists at `/usr/local/bin/sage` unless `SAGE_BIN` overrides it.
2. Ensures a local standalone CPython 3.12 exists in `.python-build/`.
3. Recreates `.venv` if its Python minor version does not match the build
   Python.
4. Bootstraps `pip` in the virtualenv if needed.
5. Installs `requirements.txt`.
6. Removes prior `build/` and `dist/` outputs unless `CLEAN=0`.
7. Runs `PyInstaller` in windowed mode against `Amalthea.py`.
8. Deletes the generated `Amalthea.spec`.
9. Rewrites `Info.plist` metadata with `/usr/libexec/PlistBuddy`.
10. Copies `appIcon.icns` and `docIcon.icns` into the bundle resources.
11. Recreates `.ipynb` document ownership metadata in `Info.plist`.
12. Removes any stale code signature.
13. Re-signs the final bundle with ad hoc signing.
14. Clears quarantine attributes on the built app.

The final built app is:

```text
dist/Amalthea.app
```

## Build Python

The current script defaults to a local standalone CPython 3.12 build downloaded
from:

```text
https://github.com/astral-sh/python-build-standalone/releases/download/20251120/cpython-3.12.12%2B20251120-aarch64-apple-darwin-install_only.tar.gz
```

This is deliberate.

Known packaging constraints:

- Sage's Python is not the build Python.
- Apple's Command Line Tools Python is not assumed to be sufficient.
- the build script validates the cached build Python major/minor version before
  reusing it.

If you change the build Python version, update both the standalone download URL
and any assumptions about third-party packaging tool compatibility.

## Bundle Metadata And Document Ownership

The build script explicitly reasserts bundle metadata after PyInstaller
completes. This is important and should not be removed casually.

Current post-build metadata includes:

- `CFBundleIdentifier = com.anteprandium.amalthea`
- `CFBundleShortVersionString = 0.9.0`
- `CFBundleVersion = 0.9.0`
- `CFBundleIconFile = appIcon.icns`
- `LSMinimumSystemVersion = 13.0`
- `CFBundleDocumentTypes` registration for `.ipynb`
- document icon `docIcon.icns`
- MIME type `application/x-ipynb+json`
- role `Editor`

If the bundle builds but Finder integration regresses, inspect the generated
`Info.plist` first.

## Runtime Verification That Has Already Happened

The current PyObjC/PyInstaller app has been verified to this extent:

- the packaged app launches and works when opened through Finder;
- the app owns `.ipynb` documents in the bundle metadata;
- the command bridge issue was fixed by adding
  `--expose-app-in-browser` and centralizing notebook command dispatch;
- the final bundle passes `codesign --verify --verbose=2`.

A previous Finder launch failure was a packaging/runtime issue during
development; the current bundle state is working.

Also note:

- sandboxed GUI launch tests can be misleading;
- `open dist/Amalthea.app` inside a restricted environment may fail in ways that
  do not reproduce in Finder or outside the sandbox.

## Known Historical Failure Modes

Several similar symptoms had different causes. Keep them distinct.

### Jupyter path and routing failures

- Page not found: Amalthea built notebook URLs from absolute filesystem paths
  instead of paths relative to Jupyter `root_dir`.
- Outside root: the notebook file was not under the reported contents root.
- Browser and app both failed on the same URL: the problem was in Sage/Jupyter
  routing or path construction, not the native app shell.

### Browser command bridge failure

- Menu actions stopped working because `window.jupyterapp` was not exposed.
- Root cause: the notebook server was not started with
  `--expose-app-in-browser`.
- Fix: add the server flag and centralize the injected command bridge.

### Earlier PyObjC packaging failures

The first PyObjC packaging route used `py2app`. That route is now historical
only and should be treated as abandoned unless someone re-validates it from
scratch.

Observed problems included:

- `py2app` crashing against built-in `zlib` on standalone Python because it
  expected a `__file__` attribute;
- a monkeypatch in `setup.py` partially worked around that packaging bug;
- despite that, the resulting app bundles still had launch/runtime issues and
  were not the final successful route.

### Sandboxed launch false positives

- Some `open` and LaunchServices failures observed during development were from
  sandbox restrictions rather than from the bundle itself.
- External or Finder-based launch tests were more trustworthy than sandboxed
  GUI-launch attempts.

## `setup.py` Status

`setup.py` still exists in the repository, but it is a historical artifact from
the abandoned `py2app` route. It contains a `zlib` monkeypatch for `py2app`.

Current status:

- it is not used by `build_macos_app.sh`;
- it is not part of the active build chain;
- it is only useful as historical context for the earlier failed packaging
  attempt.

Do not assume `setup.py` describes the current app packaging. If it becomes
confusing, removing it in a future cleanup would be reasonable, but that has not
been done yet.

## Development Notes

The source app can be run from the development environment:

```sh
.venv/bin/python Amalthea.py
```

The packaged app may be run directly from:

```sh
dist/Amalthea.app/Contents/MacOS/Amalthea
```

For WebKit and notebook frontend diagnostics:

```sh
AMALTHEA_WEBENGINE_DEBUG=1 dist/Amalthea.app/Contents/MacOS/Amalthea
```

If the app launches but notebook commands fail, inspect:

1. whether the notebook server is actually started with
   `--expose-app-in-browser`;
2. whether `window.jupyterapp` exists in the page;
3. whether the command IDs still exist in the current notebook frontend;
4. the stderr output with `AMALTHEA_WEBENGINE_DEBUG=1`.

## Clean Repository Shape

This folder is intended to sync across machines. Keep only lightweight source
and build recipe files under version or sync control:

```text
AGENTS.md
Amalthea.py
appIcon.icns
build_macos_app.sh
deploy.sh
docIcon.icns
requirements.txt
setup.py
```

Generated or machine-local files should not be preserved in the shared folder:

```text
.python-build/
.venv/
build/
dist/
__pycache__/
.DS_Store
Amalthea.spec
```

`Amalthea.spec` is a generated PyInstaller file. The build script removes it
after the build and it should not be kept.

## Build Prerequisites

A normal rebuild currently requires:

- macOS on Apple Silicon;
- command line tools providing `curl`, `tar`, `codesign`, `xattr`, and
  `/usr/libexec/PlistBuddy`;
- internet access for the first build, so the script can download standalone
  CPython and Python packages;
- SageMath installed with `/usr/local/bin/sage` available.

The script is designed to be run from the repository root:

```sh
./build_macos_app.sh
```

Useful environment overrides:

```sh
SAGE_BIN=/path/to/sage ./build_macos_app.sh
PYTHON_BIN=/path/to/python3 ./build_macos_app.sh
PYTHON_VERSION=3.12 ./build_macos_app.sh
PYTHON_BUILD_URL=https://...tar.gz ./build_macos_app.sh
CLEAN=0 ./build_macos_app.sh
```

`CLEAN=0` is useful while iterating on bundle metadata or signing fixes, but a
normal reproducible build should use the default clean behavior.

## Troubleshooting Order

When the app fails, investigate in this order:

1. Confirm Sage is reachable:

   ```sh
   /usr/local/bin/sage --version
   ```

2. Confirm Jupyter server discovery works:

   ```sh
   /usr/local/bin/sage -python -m jupyter_server list --json
   ```

3. Confirm Amalthea builds the expected document URL:

   ```text
   <server-url>/notebooks/<path-relative-to-root_dir>?token=<token>
   ```

4. Paste that URL into a normal browser. If it fails there, debug Sage/Jupyter
   paths and routes before touching the PyObjC app shell.

5. If the URL works in a browser but menu actions do not work, verify
   `--expose-app-in-browser`, `window.jupyterapp`, and the current Jupyter
   command IDs.

6. If the URL works in a browser but the app shows a failed page, inspect the
   `WKWebView` navigation callbacks and stderr logging.

7. If the packaged executable fails before opening a window, inspect the build
   Python, PyObjC packages, PyInstaller output, bundle metadata, and code
   signature before changing application logic.

## Guidance For Future Development

Do not begin by redesigning the app. The current architecture is deliberately
small and document-centric. Prefer narrow changes that preserve these
invariants:

- Sage remains an external dependency.
- Each notebook still opens in its own `NotebookWindow`.
- The default target remains the single-document Notebook UI.
- Paths sent to Jupyter remain relative to the discovered `root_dir`.
- `PyInstaller` is the validated packaging route.
- The browser command bridge depends on `--expose-app-in-browser`.

Recent product decisions that should be preserved unless there is a strong
reason otherwise:

- keep the notebook title/checkpoint header, because the title area currently
  serves as a document rename interface;
- hide the embedded menubar, toolbar, and notebook logo rather than duplicating
  Jupyter shell chrome inside Amalthea;
- prefer native menus over embedded Jupyter controls for stable, high-value
  notebook actions;
- externalize non-notebook routes instead of trying to host tree/home/workspace
  Jupyter surfaces;
- do not add a Preferences window unless several real per-user settings appear;
- avoid app-side special-case logic for legacy kernelspec migration unless
  there is no cleaner place to solve the problem.

Before changing server startup or URL logic:

- inspect live Jupyter metadata from
  `sage -python -m jupyter_server list --json`;
- verify the actual browser route in a normal browser;
- confirm whether the breakage is in server metadata, notebook routing, or UI
  integration.

Before changing packaging:

- inspect the generated bundle layout in `dist/Amalthea.app`;
- inspect `Contents/Info.plist`;
- verify the final signature with `codesign --verify --verbose=2`.

Prefer recording any new external-contract assumptions in this file as they are
discovered.

## Dependency Version Policy

The current active dependencies are listed in `requirements.txt`:

```text
pyobjc-core>=10,<11
pyobjc-framework-Cocoa>=10,<11
pyobjc-framework-WebKit>=10,<11
pyinstaller>=6,<7
```

The ranges are intentionally narrow at the major-version level because:

- PyObjC must match the installed macOS framework bridge expectations;
- PyInstaller app layout changes can affect bundle post-processing and signing.

If you widen these ranges or move to a new major version, re-check:

- build success from a clean environment;
- Finder launch behavior;
- `.ipynb` document ownership in `Info.plist`;
- command bridge behavior inside a real notebook page.

## Future Weak Points

The most likely future breakage is still at the Sage/Jupyter contract boundary,
not in the native Cocoa shell.

The app depends on these external contracts:

1. Sage launcher behavior:

   ```text
   /usr/local/bin/sage
   sage -python -m notebook ...
   sage -python -m jupyter_server ...
   ```

2. Jupyter server metadata from:

   ```sh
   /usr/local/bin/sage -python -m jupyter_server list --json
   ```

   Amalthea currently expects usable `url`, `token`, `root_dir`, and `port`
   fields.

3. Notebook frontend routing:

   ```text
   <url>/notebooks/<path-relative-to-root_dir>?token=<token>
   ```

4. Notebook frontend command exposure:

   ```text
   --expose-app-in-browser
   window.jupyterapp.commands
   ```

If the app starts but cannot display or control a notebook after a Sage or
Jupyter upgrade, inspect these contracts before changing the AppKit or WebKit
code.
