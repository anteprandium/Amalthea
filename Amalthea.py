#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import objc
from AppKit import (
    NSAlert,
    NSAlertStyleCritical,
    NSAlertStyleInformational,
    NSAlertStyleWarning,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSApplicationDelegateReplyFailure,
    NSApplicationDelegateReplySuccess,
    NSBackingStoreBuffered,
    NSBeep,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSObject,
    NSOpenPanel,
    NSRunningApplication,
    NSWorkspace,
    NSUserDefaults,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import (
    NSAutoreleasePool,
    NSBundle,
    NSMakeRect,
    NSMutableArray,
    NSURL,
    NSURLRequest,
)
from WebKit import (
    WKNavigationActionPolicyAllow,
    WKNavigationActionPolicyCancel,
    WKUserContentController,
    WKUserScript,
    WKUserScriptInjectionTimeAtDocumentStart,
    WKWebView,
    WKWebViewConfiguration,
)


WINDOW_STYLE_MASK = (
    NSWindowStyleMaskTitled
    | NSWindowStyleMaskClosable
    | NSWindowStyleMaskMiniaturizable
    | NSWindowStyleMaskResizable
)

SAGE_LOCATION = "/usr/local/bin/sage"
WINDOW_DEFAULTS_KEY = "Anteprandium.Amalthea.MainWindowSize"
PAGE_ZOOM_DEFAULTS_KEY = "Anteprandium.Amalthea.PageZoom"
PAGE_ZOOM_DEFAULT = 1.15
PAGE_ZOOM_MIN = 0.8
PAGE_ZOOM_MAX = 1.6
PAGE_ZOOM_STEP = 0.1
CONSOLE_HANDLER_NAME = "amaltheaConsole"


def normalise(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def default_notebooks_location() -> str:
    home = Path.home()
    documents = home / "Documents"
    for location in (documents / "Notebooks", documents, home):
        if location.exists():
            return str(location)
    return "/"


def looks_like_notebook(path: str) -> bool:
    return path.lower().endswith(".ipynb")


def make_name(index: int) -> str:
    return "Untitled.ipynb" if index == 0 else f"Untitled{index}.ipynb"


def clamp_page_zoom(value: float) -> float:
    return max(PAGE_ZOOM_MIN, min(PAGE_ZOOM_MAX, value))


def load_page_zoom() -> float:
    raw = NSUserDefaults.standardUserDefaults().stringForKey_(PAGE_ZOOM_DEFAULTS_KEY)
    if not raw:
        return PAGE_ZOOM_DEFAULT
    try:
        return clamp_page_zoom(float(raw))
    except ValueError:
        return PAGE_ZOOM_DEFAULT


def save_page_zoom(value: float) -> None:
    NSUserDefaults.standardUserDefaults().setObject_forKey_(
        f"{clamp_page_zoom(value):.2f}", PAGE_ZOOM_DEFAULTS_KEY
    )


def discover_sage_kernel_spec() -> dict[str, str]:
    if not (os.path.isfile(SAGE_LOCATION) and os.access(SAGE_LOCATION, os.X_OK)):
        return {}

    completed = subprocess.run(
        [
            SAGE_LOCATION,
            "-python",
            "-c",
            (
                "import json; "
                "from jupyter_client.kernelspec import KernelSpecManager; "
                "specs = KernelSpecManager().get_all_specs(); "
                "candidates = ["
                "(name, record.get('spec', {})) for name, record in specs.items() "
                "if record.get('spec', {}).get('language', '').lower() == 'sage' "
                "or 'sage' in record.get('spec', {}).get('display_name', '').lower() "
                "or name.startswith('sagemath-')"
                "]; "
                "name, spec = next(iter(candidates), ('', {})); "
                "print(json.dumps({"
                "'name': name, "
                "'display_name': spec.get('display_name', ''), "
                "'language': spec.get('language', '')"
                "}))"
            ),
        ],
        cwd="/",
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {}

    try:
        spec = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {}

    if not spec.get("name"):
        return {}
    return spec


def build_empty_notebook() -> str:
    spec = discover_sage_kernel_spec()
    metadata = {}
    if spec:
        metadata["kernelspec"] = {
            "display_name": spec.get("display_name") or spec["name"],
            "language": spec.get("language") or "sage",
            "name": spec["name"],
        }

    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [],
            }
        ],
        "metadata": metadata,
        "nbformat": 4,
        "nbformat_minor": 4,
    }
    return json.dumps(notebook, indent=1) + "\n"


def new_untitled(locations: Iterable[str] | None = None) -> str:
    search_locations = list(locations or [default_notebooks_location()])
    target_dir: Path | None = None
    for location in search_locations:
        candidate = Path(normalise(location))
        if candidate.exists() and candidate.is_dir():
            target_dir = candidate
            break

    if target_dir is None:
        raise NotADirectoryError("No readable notebook directory is available.")

    for index in range(10000):
        path = target_dir / make_name(index)
        if not path.exists():
            path.write_text(build_empty_notebook(), encoding="utf-8")
            return str(path)

    raise FileExistsError("Could not find a free Untitled notebook name.")


class Server:
    def __init__(self, port: int = 8988, timeout: float = 6.0) -> None:
        self.token = ""
        self.url = ""
        self.root_dir = normalise(str(Path.home()))
        self.port = port
        self.timeout = timeout
        self._program = SAGE_LOCATION
        self._notebook_args = ["-python", "-m", "notebook"]
        self._server_args = ["-python", "-m", "jupyter_server"]
        self.default_kernel_name = ""
        self.proc: subprocess.Popen[str] | None = None
        self.last_error = ""

    def reset_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for var in [
            "PYTHONHOME",
            "_PY2APP_LAUNCHED_",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
            "EXECUTABLEPATH",
            "RESOURCEPATH",
            "__PYVENV_LAUNCHER__",
            "PYTHONPATH",
            "ARGVZERO",
            "VIRTUAL_ENV",
        ]:
            env.pop(var, None)
        return env

    def update_info(self, url: str, token: str | None, root_dir: str | None = None) -> None:
        if not url:
            return

        base = url
        if "?token=" in base:
            base, _ = base.split("?token=", 1)
        if base.endswith("tree"):
            base = base[:-4]
        if not base.endswith("/"):
            base += "/"

        self.url = base
        self.token = token or ""
        if root_dir:
            self.root_dir = normalise(root_dir)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._program, *args],
            env=self.reset_environment(),
            cwd="/",
            text=True,
            capture_output=True,
            check=False,
        )

    def discover_default_kernel_name(self) -> str:
        if self.default_kernel_name:
            return self.default_kernel_name

        completed = self._run(
            [
                "-python",
                "-c",
                (
                    "import json; "
                    "from jupyter_client.kernelspec import KernelSpecManager; "
                    "specs = KernelSpecManager().get_all_specs(); "
                    "candidates = ["
                    "name for name, record in specs.items() "
                    "if record.get('spec', {}).get('language', '').lower() == 'sage' "
                    "or 'sage' in record.get('spec', {}).get('display_name', '').lower() "
                    "or name.startswith('sagemath-')"
                    "]; "
                    "print(json.dumps(candidates))"
                ),
            ]
        )
        if completed.returncode != 0:
            return ""

        try:
            candidates = json.loads(completed.stdout.strip() or "[]")
        except json.JSONDecodeError:
            return ""

        if not candidates:
            return ""

        preferred = next((name for name in candidates if name.startswith("sagemath-")), None)
        self.default_kernel_name = preferred or candidates[0]
        return self.default_kernel_name

    def stop(self) -> None:
        self._run([*self._server_args, "stop", str(self.port)])
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1)
        self.proc = None

    def is_running(self) -> bool:
        completed = self._run([*self._server_args, "list", "--json"])
        if completed.returncode != 0:
            return False

        for line in completed.stdout.splitlines():
            if "{" not in line:
                continue
            try:
                record = json.loads(line[line.index("{"):])
            except json.JSONDecodeError:
                continue
            if record.get("port") == self.port and record.get("url"):
                self.update_info(record["url"], record.get("token"), record.get("root_dir"))
                self.last_error = ""
                return True
        return False

    def start(self) -> int:
        if self.is_running():
            self.last_error = ""
            return 0

        default_kernel_name = self.discover_default_kernel_name()
        notebook_args = [
            self._program,
            *self._notebook_args,
            "--no-browser",
            "--expose-app-in-browser",
            "--ip=127.0.0.1",
            f"--port={self.port}",
            "--port-retries=0",
            f"--ServerApp.root_dir={self.root_dir}",
        ]
        if default_kernel_name:
            notebook_args.append(
                f"--MultiKernelManager.default_kernel_name={default_kernel_name}"
            )

        self.proc = subprocess.Popen(
            notebook_args,
            env=self.reset_environment(),
            cwd="/",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                self.last_error = stderr.strip() or "Sage/Jupyter exited before reporting URL."
                self.proc = None
                return 1
            if self.is_running():
                self.last_error = ""
                return 0
            time.sleep(0.2)

        self.last_error = f"Timed out waiting for Sage/Jupyter to start on port {self.port}."
        return 1

    def url_from_filename(self, filename: str) -> str:
        resolved_file = normalise(filename)
        resolved_root = normalise(self.root_dir)
        root_prefix = resolved_root if resolved_root.endswith("/") else f"{resolved_root}/"

        if resolved_file == resolved_root or not resolved_file.startswith(root_prefix):
            raise ValueError(
                f"'{resolved_file}' is outside the Jupyter contents root '{self.root_dir}'."
            )

        relative_path = resolved_file[len(root_prefix):]
        if relative_path in {".", ".."} or relative_path.startswith("../"):
            raise ValueError(
                f"'{resolved_file}' is outside the Jupyter contents root '{self.root_dir}'."
            )

        encoded_path = quote(relative_path, safe="/")
        query = f"?token={self.token}" if self.token else ""
        return f"{self.url}notebooks/{encoded_path}{query}"


def alert(title: str, message: str, style: int) -> None:
    panel = NSAlert.alloc().init()
    panel.setMessageText_(title)
    panel.setInformativeText_(message)
    panel.setAlertStyle_(style)
    panel.runModal()


class NotebookWindow(NSObject):
    web_view = objc.ivar()
    window = objc.ivar()
    app = objc.ivar()

    def initWithApp_(self, app):
        self = objc.super(NotebookWindow, self).init()
        if self is None:
            return None

        self.app = app
        width, height = self._load_window_size()
        frame = NSMakeRect(0.0, 0.0, width, height)

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, WINDOW_STYLE_MASK, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Amalthea")
        self.window.setDelegate_(self)
        self.window.center()

        configuration = WKWebViewConfiguration.alloc().init()
        controller = WKUserContentController.alloc().init()
        controller.addUserScript_(
            WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                """
                (function () {
                  if (window.__amaltheaBridgeInstalled) {
                    return;
                  }
                  window.__amaltheaBridgeInstalled = true;

                  function installNotebookChromeOverrides() {
                    const styleId = 'amalthea-hide-notebook-menu';
                    function ensureStyle() {
                      if (document.getElementById(styleId)) {
                        return;
                      }
                      const style = document.createElement('style');
                      style.id = styleId;
                      style.textContent = [
                        '#jp-NotebookLogo {',
                          '  display: none !important;',
                          '  width: 0 !important;',
                          '  min-width: 0 !important;',
                          '  margin: 0 !important;',
                          '  padding: 0 !important;',
                        '  overflow: hidden !important;',
                        '}',
                        '#menu-panel-wrapper {',
                        '  display: none !important;',
                        '  height: 0 !important;',
                        '  min-height: 0 !important;',
                        '  margin: 0 !important;',
                        '  padding: 0 !important;',
                        '}',
                        '#menu-panel-wrapper:empty {',
                        '  border: 0 !important;',
                        '}',
                        '.jp-NotebookPanel-toolbar,',
                        '.jp-NotebookPanel-toolbar.jp-Toolbar,',
                        '.jp-Toolbar.jp-NotebookPanel-toolbar {',
                        '  display: none !important;',
                        '  height: 0 !important;',
                        '  min-height: 0 !important;',
                        '  margin: 0 !important;',
                        '  padding: 0 !important;',
                        '  border: 0 !important;',
                        '}',
                        '#main {',
                        '  top: 0 !important;',
                        '}'
                      ].join('\\n');
                      (document.head || document.documentElement).appendChild(style);
                    }

                    ensureStyle();

                    const observer = new MutationObserver(function() {
                      ensureStyle();
                    });
                    observer.observe(document.documentElement, {
                      childList: true,
                      subtree: true
                    });
                  }

                  installNotebookChromeOverrides();

                  function readPageConfig() {
                    const node = document.getElementById('jupyter-config-data');
                    if (!node || !node.textContent) {
                      return null;
                    }
                    try {
                      return JSON.parse(node.textContent);
                    } catch (error) {
                      return null;
                    }
                  }

                  function waitForApp(timeoutMs) {
                    return new Promise((resolve, reject) => {
                      const started = Date.now();
                      function poll() {
                        if (window.jupyterapp && window.jupyterapp.commands) {
                          resolve(window.jupyterapp);
                          return;
                        }
                        if (Date.now() - started >= timeoutMs) {
                          const pageConfig = readPageConfig();
                          const expose = pageConfig && pageConfig.exposeAppInBrowser;
                          if (expose === false) {
                            reject(new Error(
                              'Jupyter app instance is not exposed to the browser. ' +
                              'The notebook server must start with --expose-app-in-browser.'
                            ));
                            return;
                          }
                          reject(new Error('Timed out waiting for the notebook command registry.'));
                          return;
                        }
                        window.setTimeout(poll, 50);
                      }
                      poll();
                    });
                  }

                  window.__amalthea = window.__amalthea || {};
                  window.__amalthea.runCommands = function(commands) {
                    Promise.resolve()
                      .then(function() { return waitForApp(10000); })
                      .then(async function(app) {
                        for (const command of commands) {
                          await app.commands.execute(command);
                        }
                      })
                      .catch(function(error) {
                        console.error('Amalthea command bridge failed:', error);
                      });
                    return true;
                  };
                })();
                """,
                WKUserScriptInjectionTimeAtDocumentStart,
                False,
            )
        )
        if os.environ.get("AMALTHEA_WEBENGINE_DEBUG"):
            controller.addUserScript_(
                WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                    """
                    (function () {
                      if (window.__amaltheaConsoleHookInstalled) { return; }
                      window.__amaltheaConsoleHookInstalled = true;
                      ['log', 'info', 'warn', 'error', 'debug'].forEach(function(level) {
                        var original = console[level];
                        console[level] = function() {
                          try {
                            window.webkit.messageHandlers.amaltheaConsole.postMessage({
                              level: level,
                              message: Array.prototype.slice.call(arguments).join(' ')
                            });
                          } catch (e) {}
                          if (original) { original.apply(console, arguments); }
                        };
                      });
                    })();
                    """,
                    WKUserScriptInjectionTimeAtDocumentStart,
                    False,
                )
            )
            controller.addScriptMessageHandler_name_(self, CONSOLE_HANDLER_NAME)
        configuration.setUserContentController_(controller)

        self.web_view = WKWebView.alloc().initWithFrame_configuration_(frame, configuration)
        self.web_view.setNavigationDelegate_(self)
        self.web_view.setUIDelegate_(self)
        self.web_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.applyPageZoom_(load_page_zoom())
        self.window.setContentView_(self.web_view)
        return self

    def _load_window_size(self) -> tuple[float, float]:
        raw = NSUserDefaults.standardUserDefaults().stringForKey_(WINDOW_DEFAULTS_KEY)
        if not raw:
            return (1024.0, 700.0)
        try:
            width_text, height_text = str(raw).split("x", 1)
            return (float(width_text), float(height_text))
        except ValueError:
            return (1024.0, 700.0)

    def _save_window_size(self) -> None:
        size = self.window.frame().size
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            f"{size.width}x{size.height}", WINDOW_DEFAULTS_KEY
        )

    def show(self) -> None:
        self.window.makeKeyAndOrderFront_(None)

    def loadURL_filePath_(self, url: str, file_path: str | None) -> None:
        if file_path:
            self.window.setRepresentedFilename_(file_path)
            self.window.setTitle_(Path(file_path).name)
        request = NSURLRequest.requestWithURL_(NSURL.URLWithString_(url))
        self.web_view.loadRequest_(request)

    def _url_string(self, url) -> str:
        return str(url) if url is not None else ""

    def _is_managed_notebook_url(self, url) -> bool:
        if self.app is None or self.app.server is None:
            return False
        base_url = self.app.server.url or ""
        candidate = self._url_string(url)
        return bool(base_url) and candidate.startswith(base_url)

    def applyPageZoom_(self, factor: float) -> None:
        if self.web_view.respondsToSelector_(b"setPageZoom:"):
            self.web_view.setPageZoom_(clamp_page_zoom(factor))
        else:
            print("WKWebView pageZoom is not available in this WebKit bridge.", file=sys.stderr)

    def runNotebookJavaScript_(self, script: str) -> None:
        self.web_view.evaluateJavaScript_completionHandler_(script, None)

    def runNotebookCommands_(self, *commands: str) -> None:
        if not commands:
            return
        payload = json.dumps(list(commands))
        self.runNotebookJavaScript_(
            f"if (window.__amalthea && window.__amalthea.runCommands) "
            f"{{ window.__amalthea.runCommands({payload}); }}"
        )

    def runFrontendCommand_(self, command: str) -> None:
        self.runNotebookCommands_(command)

    def windowWillClose_(self, notification) -> None:
        self._save_window_size()
        self.window.setDelegate_(None)
        self.web_view.setNavigationDelegate_(None)
        self.web_view.setUIDelegate_(None)

    def windowDidResize_(self, notification) -> None:
        self._save_window_size()

    def userContentController_didReceiveScriptMessage_(self, controller, message) -> None:
        if not os.environ.get("AMALTHEA_WEBENGINE_DEBUG"):
            return
        body = message.body()
        if isinstance(body, dict):
            level = body.get("level", "log")
            text = body.get("message", "")
            print(f"JS[{level}] {text}", file=sys.stderr)
        else:
            print(f"JS {body}", file=sys.stderr)

    def webView_didFailNavigation_withError_(self, webview, navigation, error) -> None:
        failed_url = str(webview.URL()) if webview.URL() else ""
        print(f"WKWebView failed to load: {failed_url}", file=sys.stderr)
        self.web_view.loadHTMLString_baseURL_(
            "<html><body style='font-family:-apple-system; padding:2em'>"
            "<h2>Amalthea could not load the notebook page.</h2>"
            f"<p>{failed_url}</p>"
            "</body></html>",
            None,
        )

    def webView_didFailProvisionalNavigation_withError_(self, webview, navigation, error) -> None:
        self.webView_didFailNavigation_withError_(webview, navigation, error)

    def webViewWebContentProcessDidTerminate_(self, webview) -> None:
        current_url = str(webview.URL()) if webview.URL() else ""
        print(f"WKWebView content process terminated: {current_url}", file=sys.stderr)

    def webView_decidePolicyForNavigationAction_decisionHandler_(
        self,
        webview,
        navigation_action,
        decision_handler,
    ) -> None:
        request = navigation_action.request()
        target_url = request.URL() if request is not None else None
        target_frame = navigation_action.targetFrame()

        if target_url is None:
            decision_handler(WKNavigationActionPolicyAllow)
            return

        if target_frame is None or not target_frame.isMainFrame():
            decision_handler(WKNavigationActionPolicyAllow)
            return

        if self._is_managed_notebook_url(target_url):
            decision_handler(WKNavigationActionPolicyAllow)
            return

        NSWorkspace.sharedWorkspace().openURL_(target_url)
        decision_handler(WKNavigationActionPolicyCancel)

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
        self,
        webview,
        configuration,
        navigation_action,
        window_features,
    ):
        request = navigation_action.request()
        target_url = request.URL() if request is not None else None
        target_frame = navigation_action.targetFrame()
        if target_frame is not None and target_frame.isMainFrame():
            return None

        if target_url is not None and not self._is_managed_notebook_url(target_url):
            NSWorkspace.sharedWorkspace().openURL_(target_url)
            return None

        notebook = self.app.createNotebookWindow()
        notebook.show()
        return notebook.web_view

    def saveDocument_(self, sender) -> None:
        self.runNotebookCommands_("docmanager:save")

    def insertCellAbove_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:insert-cell-above",
        )

    def insertCellBelow_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:insert-cell-below",
        )

    def changeCellToCode_(self, sender) -> None:
        self.runNotebookCommands_("notebook:change-cell-to-code")

    def changeCellToMarkdown_(self, sender) -> None:
        self.runNotebookCommands_("notebook:change-cell-to-markdown")

    def changeCellToRaw_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:change-cell-to-raw",
        )

    def showCommandPalette_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "apputils:activate-command-palette",
        )

    def toggleLineNumbers_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:toggle-all-cell-line-numbers",
        )

    def clearAllOutputs_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:clear-all-cell-outputs",
        )

    def splitCellAtCursor_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-edit-mode",
            "notebook:split-cell-at-cursor",
        )

    def interruptKernel_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-edit-mode",
            "notebook:interrupt-kernel",
        )

    def restartKernel_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-edit-mode",
            "notebook:restart-kernel",
        )

    def showNotebookShortcuts_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-edit-mode",
            "notebook:show-keyboard-shortcuts",
        )

    def cutCell_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:cut-cell",
        )

    def copyCell_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:copy-cell",
        )

    def pasteCellBelow_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-command-mode",
            "notebook:paste-cell-below",
        )

    def undo_(self, sender) -> None:
        self.runFrontendCommand_("editmenu:undo")

    def redo_(self, sender) -> None:
        self.runFrontendCommand_("editmenu:redo")

    def clearCurrentCell_(self, sender) -> None:
        self.runFrontendCommand_("editmenu:clear-current")

    def goToLine_(self, sender) -> None:
        self.runFrontendCommand_("editmenu:go-to-line")

    def runSelectedCells_(self, sender) -> None:
        self.runFrontendCommand_("runmenu:run")

    def runCellAndSelectNext_(self, sender) -> None:
        self.runNotebookCommands_(
            "notebook:enter-edit-mode",
            "notebook:run-cell-and-select-next",
        )

    def runAllCells_(self, sender) -> None:
        self.runFrontendCommand_("runmenu:run-all")

    def restartKernelAndRunAllCells_(self, sender) -> None:
        self.runFrontendCommand_("runmenu:restart-and-run-all")

    def restartKernelAndClearOutputs_(self, sender) -> None:
        self.runFrontendCommand_("kernelmenu:restart-and-clear")

    def reconnectToKernel_(self, sender) -> None:
        self.runFrontendCommand_("kernelmenu:reconnect-to-kernel")

    def shutdownKernel_(self, sender) -> None:
        self.runFrontendCommand_("kernelmenu:shutdown")

    def changeKernel_(self, sender) -> None:
        self.runFrontendCommand_("kernelmenu:change")


class Amalthea(NSObject):
    documents = objc.ivar()
    pending_files = objc.ivar()
    server = objc.ivar()

    def init(self):
        self = objc.super(Amalthea, self).init()
        if self is None:
            return None
        self.documents = NSMutableArray.array()
        self.pending_files = NSMutableArray.array()
        self.server = None
        return self

    def applicationDidFinishLaunching_(self, notification) -> None:
        self.buildMenus()
        if not self.ensureSagePresent():
            NSApp.terminate_(None)
            return

        self.server = Server()
        rc = self.server.start()
        if rc != 0 or not self.server.url:
            message = (
                "Amalthea could not start the SageMath Jupyter server.\n\n"
                "Please check that SageMath is correctly installed and that no other "
                "Jupyter server is already using port 8988, then try again."
            )
            if self.server.last_error:
                message += f"\n\nDetails from Sage/Jupyter:\n{self.server.last_error}"
            alert("Amalthea error", message, NSAlertStyleCritical)
            NSApp.terminate_(None)
            return

        while self.pending_files.count():
            filename = str(self.pending_files.objectAtIndex_(0))
            self.pending_files.removeObjectAtIndex_(0)
            self.openFile_(filename)

        if len(sys.argv) > 1:
            for path in sys.argv[1:]:
                self.openFile_(path)
        else:
            self.performSelector_withObject_afterDelay_(b"newFileIfNoDocuments", None, 1.5)

        icon_path = NSBundle.mainBundle().pathForResource_ofType_("appIcon", "icns")
        if icon_path:
            NSApp.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon_path))

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:
        return True

    def applicationWillTerminate_(self, notification) -> None:
        if self.server is not None:
            self.server.stop()

    def application_openFile_(self, app, filename) -> bool:
        if self.server and self.server.url:
            return self.openFile_(str(filename)) is not None
        self.pending_files.addObject_(str(filename))
        return True

    def application_openFiles_(self, app, filenames) -> None:
        opened_any = False
        for filename in filenames:
            if self.server and self.server.url:
                opened_any = self.openFile_(str(filename)) is not None or opened_any
            else:
                self.pending_files.addObject_(str(filename))
                opened_any = True
        NSApp.replyToOpenOrPrint_(
            NSApplicationDelegateReplySuccess if opened_any else NSApplicationDelegateReplyFailure
        )

    def ensureSagePresent(self) -> bool:
        if os.path.isfile(SAGE_LOCATION) and os.access(SAGE_LOCATION, os.X_OK):
            return True
        alert(
            "SageMath not available",
            (
                f"Amalthea could not find SageMath at:\n\n{SAGE_LOCATION}\n\n"
                "Please install SageMath and make sure the 'sage' command is available at that location.\n\n"
                "After installation, launch SageMath at least once so that it can initialise its environment, then start Amalthea again."
            ),
            NSAlertStyleCritical,
        )
        return False

    def createNotebookWindow(self) -> NotebookWindow:
        notebook = NotebookWindow.alloc().initWithApp_(self)
        self.documents.addObject_(notebook)
        return notebook

    def activeNotebook(self) -> NotebookWindow | None:
        key_window = NSApp.keyWindow() or NSApp.mainWindow()
        for notebook in self.documents:
            if notebook.window is None or notebook.window.isVisible() is False:
                continue
            if notebook.window == key_window:
                return notebook
        visible_notebooks = [
            notebook
            for notebook in self.documents
            if notebook.window is not None and notebook.window.isVisible()
        ]
        if visible_notebooks:
            return visible_notebooks[-1]
        return None

    def _dispatch_notebook_action(self, selector: bytes, sender) -> None:
        notebook = self.activeNotebook()
        if notebook is None:
            NSBeep()
            return
        notebook.performSelector_withObject_(selector, sender)

    def openFile_(self, filename: str | None):
        if not self.server or not self.server.url:
            return None

        if filename and looks_like_notebook(filename):
            try:
                url = self.server.url_from_filename(filename)
            except ValueError as err:
                alert("Notebook outside Jupyter root", str(err), NSAlertStyleWarning)
                return None
            notebook = self.createNotebookWindow()
            notebook.loadURL_filePath_(url, normalise(filename))
            notebook.show()
            return notebook
        if filename:
            alert(
                "Unsupported file",
                f"'{filename}' does not look like a Jupyter notebook (.ipynb).",
                NSAlertStyleWarning,
            )
        return None

    def selectFile(self):
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Open Jupyter Notebook")
        panel.setCanChooseDirectories_(False)
        panel.setCanChooseFiles_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["ipynb"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(default_notebooks_location()))
        if panel.runModal() == NSModalResponseOK:
            url = panel.URL()
            if url is not None:
                return self.openFile_(str(url.path()))
        return None

    def newFile(self):
        return self.openFile_(new_untitled())

    def newFileIfNoDocuments(self) -> None:
        if not self.documents.count():
            self.newFile()

    def showAbout_(self, sender) -> None:
        alert(
            "About Amalthea",
            "A lightweight frontend for Sage Jupyter notebooks.",
            NSAlertStyleInformational,
        )

    def openDocument_(self, sender) -> None:
        self.selectFile()

    def newDocument_(self, sender) -> None:
        self.newFile()

    def saveDocument_(self, sender) -> None:
        self._dispatch_notebook_action(b"saveDocument:", sender)

    def insertCellAbove_(self, sender) -> None:
        self._dispatch_notebook_action(b"insertCellAbove:", sender)

    def insertCellBelow_(self, sender) -> None:
        self._dispatch_notebook_action(b"insertCellBelow:", sender)

    def changeCellToCode_(self, sender) -> None:
        self._dispatch_notebook_action(b"changeCellToCode:", sender)

    def changeCellToMarkdown_(self, sender) -> None:
        self._dispatch_notebook_action(b"changeCellToMarkdown:", sender)

    def changeCellToRaw_(self, sender) -> None:
        self._dispatch_notebook_action(b"changeCellToRaw:", sender)

    def showCommandPalette_(self, sender) -> None:
        self._dispatch_notebook_action(b"showCommandPalette:", sender)

    def toggleLineNumbers_(self, sender) -> None:
        self._dispatch_notebook_action(b"toggleLineNumbers:", sender)

    def clearAllOutputs_(self, sender) -> None:
        self._dispatch_notebook_action(b"clearAllOutputs:", sender)

    def splitCellAtCursor_(self, sender) -> None:
        self._dispatch_notebook_action(b"splitCellAtCursor:", sender)

    def interruptKernel_(self, sender) -> None:
        self._dispatch_notebook_action(b"interruptKernel:", sender)

    def restartKernel_(self, sender) -> None:
        self._dispatch_notebook_action(b"restartKernel:", sender)

    def showNotebookShortcuts_(self, sender) -> None:
        self._dispatch_notebook_action(b"showNotebookShortcuts:", sender)

    def set_page_zoom(self, factor: float) -> None:
        factor = clamp_page_zoom(factor)
        save_page_zoom(factor)
        for notebook in self.documents:
            if notebook.window is not None and notebook.window.isVisible():
                notebook.applyPageZoom_(factor)

    def zoomIn_(self, sender) -> None:
        self.set_page_zoom(load_page_zoom() + PAGE_ZOOM_STEP)

    def zoomOut_(self, sender) -> None:
        self.set_page_zoom(load_page_zoom() - PAGE_ZOOM_STEP)

    def actualSize_(self, sender) -> None:
        self.set_page_zoom(1.0)

    def cutCell_(self, sender) -> None:
        self._dispatch_notebook_action(b"cutCell:", sender)

    def copyCell_(self, sender) -> None:
        self._dispatch_notebook_action(b"copyCell:", sender)

    def pasteCellBelow_(self, sender) -> None:
        self._dispatch_notebook_action(b"pasteCellBelow:", sender)

    def undo_(self, sender) -> None:
        self._dispatch_notebook_action(b"undo:", sender)

    def redo_(self, sender) -> None:
        self._dispatch_notebook_action(b"redo:", sender)

    def clearCurrentCell_(self, sender) -> None:
        self._dispatch_notebook_action(b"clearCurrentCell:", sender)

    def goToLine_(self, sender) -> None:
        self._dispatch_notebook_action(b"goToLine:", sender)

    def runSelectedCells_(self, sender) -> None:
        self._dispatch_notebook_action(b"runSelectedCells:", sender)

    def runCellAndSelectNext_(self, sender) -> None:
        self._dispatch_notebook_action(b"runCellAndSelectNext:", sender)

    def runAllCells_(self, sender) -> None:
        self._dispatch_notebook_action(b"runAllCells:", sender)

    def restartKernelAndRunAllCells_(self, sender) -> None:
        self._dispatch_notebook_action(b"restartKernelAndRunAllCells:", sender)

    def restartKernelAndClearOutputs_(self, sender) -> None:
        self._dispatch_notebook_action(b"restartKernelAndClearOutputs:", sender)

    def reconnectToKernel_(self, sender) -> None:
        self._dispatch_notebook_action(b"reconnectToKernel:", sender)

    def shutdownKernel_(self, sender) -> None:
        self._dispatch_notebook_action(b"shutdownKernel:", sender)

    def changeKernel_(self, sender) -> None:
        self._dispatch_notebook_action(b"changeKernel:", sender)

    def buildMenus(self) -> None:
        main_menu = NSMenu.alloc().initWithTitle_("MainMenu")
        NSApp.setMainMenu_(main_menu)

        app_menu = self.addTopLevelMenu_title_(main_menu, "Amalthea")
        self.addMenuItem_title_action_key_modifiers_target_(
            app_menu, "About Amalthea", b"showAbout:", "", 0, self
        )
        app_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            app_menu, "Hide Amalthea", b"hide:", "h", NSEventModifierFlagCommand, NSApp
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            app_menu,
            "Hide Others",
            b"hideOtherApplications:",
            "h",
            NSEventModifierFlagCommand | NSEventModifierFlagOption,
            NSApp,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            app_menu, "Show All", b"unhideAllApplications:", "", 0, NSApp
        )
        app_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            app_menu, "Quit Amalthea", b"terminate:", "q", NSEventModifierFlagCommand, NSApp
        )

        file_menu = self.addTopLevelMenu_title_(main_menu, "File")
        self.addMenuItem_title_action_key_modifiers_target_(
            file_menu, "Open", b"openDocument:", "o", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            file_menu, "Close", b"performClose:", "w", NSEventModifierFlagCommand, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            file_menu, "New", b"newDocument:", "n", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            file_menu, "Save", b"saveDocument:", "s", NSEventModifierFlagCommand, self
        )

        edit_menu = self.addTopLevelMenu_title_(main_menu, "Edit")
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Undo", b"undo:", "z", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu,
            "Redo",
            b"redo:",
            "z",
            NSEventModifierFlagCommand | NSEventModifierFlagShift,
            self,
        )
        edit_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Cut", b"cut:", "x", NSEventModifierFlagCommand, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Copy", b"copy:", "c", NSEventModifierFlagCommand, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Paste", b"paste:", "v", NSEventModifierFlagCommand, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Delete", b"delete:", "", 0, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            edit_menu, "Select All", b"selectAll:", "a", NSEventModifierFlagCommand, None
        )

        view_menu = self.addTopLevelMenu_title_(main_menu, "View")
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu,
            "Command Palette",
            b"showCommandPalette:",
            "P",
            NSEventModifierFlagCommand | NSEventModifierFlagShift,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu,
            "Toggle Line Numbers",
            b"toggleLineNumbers:",
            "l",
            NSEventModifierFlagCommand | NSEventModifierFlagOption,
            self,
        )
        view_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu, "Zoom In", b"zoomIn:", "=", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu, "Zoom Out", b"zoomOut:", "-", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu, "Actual Size", b"actualSize:", "0", NSEventModifierFlagCommand, self
        )
        view_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            view_menu,
            "Enter Full Screen",
            b"toggleFullScreen:",
            "f",
            NSEventModifierFlagCommand | NSEventModifierFlagControl,
            None,
        )

        insert_menu = self.addTopLevelMenu_title_(main_menu, "Insert")
        self.addMenuItem_title_action_key_modifiers_target_(
            insert_menu,
            "Insert Cell Above",
            b"insertCellAbove:",
            "A",
            NSEventModifierFlagCommand | NSEventModifierFlagShift,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            insert_menu,
            "Insert Cell Below",
            b"insertCellBelow:",
            "B",
            NSEventModifierFlagCommand | NSEventModifierFlagShift,
            self,
        )

        cell_menu = self.addTopLevelMenu_title_(main_menu, "Cell")
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu, "Code Cell", b"changeCellToCode:", "y", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Markdown Cell",
            b"changeCellToMarkdown:",
            "m",
            NSEventModifierFlagCommand,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu, "Raw Cell", b"changeCellToRaw:", "r", NSEventModifierFlagCommand, self
        )
        cell_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Cut Cell",
            b"cutCell:",
            "x",
            NSEventModifierFlagCommand | NSEventModifierFlagOption,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Copy Cell",
            b"copyCell:",
            "c",
            NSEventModifierFlagCommand | NSEventModifierFlagOption,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Paste Cell Below",
            b"pasteCellBelow:",
            "v",
            NSEventModifierFlagCommand | NSEventModifierFlagOption,
            self,
        )
        cell_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Split Cell at Cursor",
            b"splitCellAtCursor:",
            "-",
            NSEventModifierFlagCommand | NSEventModifierFlagShift,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Run Cell and Select Next",
            b"runCellAndSelectNext:",
            "\r",
            NSEventModifierFlagShift,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu, "Run Selected Cells", b"runSelectedCells:", "", 0, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu, "Run All Cells", b"runAllCells:", "", 0, self
        )
        cell_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Clear Selected Cells",
            b"clearCurrentCell:",
            "k",
            NSEventModifierFlagCommand,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu, "Clear All Outputs", b"clearAllOutputs:", "o", NSEventModifierFlagControl, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            cell_menu,
            "Go to Line",
            b"goToLine:",
            "l",
            NSEventModifierFlagCommand,
            self,
        )

        kernel_menu = self.addTopLevelMenu_title_(main_menu, "Kernel")
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu, "Interrupt", b"interruptKernel:", "i", NSEventModifierFlagCommand, self
        )
        kernel_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu, "Restart", b"restartKernel:", "0", NSEventModifierFlagCommand, self
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu,
            "Restart and Clear All Outputs",
            b"restartKernelAndClearOutputs:",
            "",
            0,
            self,
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu,
            "Restart Kernel and Run All Cells",
            b"restartKernelAndRunAllCells:",
            "",
            0,
            self,
        )
        kernel_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu, "Reconnect to Kernel", b"reconnectToKernel:", "", 0, self
        )
        kernel_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu, "Shut Down Kernel", b"shutdownKernel:", "", 0, self
        )
        kernel_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            kernel_menu, "Change Kernel...", b"changeKernel:", "", 0, self
        )

        window_menu = self.addTopLevelMenu_title_(main_menu, "Window")
        self.addMenuItem_title_action_key_modifiers_target_(
            window_menu, "Minimize", b"performMiniaturize:", "m", NSEventModifierFlagCommand, None
        )
        self.addMenuItem_title_action_key_modifiers_target_(
            window_menu, "Zoom", b"zoom:", "", 0, None
        )
        window_menu.addItem_(NSMenuItem.separatorItem())
        self.addMenuItem_title_action_key_modifiers_target_(
            window_menu, "Bring All to Front", b"arrangeInFront:", "", 0, NSApp
        )
        NSApp.setWindowsMenu_(window_menu)

        help_menu = self.addTopLevelMenu_title_(main_menu, "Help")
        self.addMenuItem_title_action_key_modifiers_target_(
            help_menu, "Shortcuts", b"showNotebookShortcuts:", "", 0, self
        )
        NSApp.setHelpMenu_(help_menu)

    def addTopLevelMenu_title_(self, main_menu, title: str):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        menu = NSMenu.alloc().initWithTitle_(title)
        item.setSubmenu_(menu)
        main_menu.addItem_(item)
        return menu

    def addMenuItem_title_action_key_modifiers_target_(
        self,
        menu,
        title: str,
        action: bytes,
        key: str,
        modifiers: int,
        target,
    ) -> None:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        item.setKeyEquivalentModifierMask_(modifiers)
        if target is not None:
            item.setTarget_(target)
        menu.addItem_(item)


def main() -> int:
    pool = NSAutoreleasePool.alloc().init()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Amalthea.alloc().init()
    app.setDelegate_(delegate)
    NSRunningApplication.currentApplication().activateWithOptions_(1 << 1)
    app.run()
    del pool
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
