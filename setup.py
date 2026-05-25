import sysconfig
from pathlib import Path

import zlib
from py2app import build_app as py2app_build_app
from setuptools import setup


# python-build-standalone can expose zlib as a built-in module without
# __file__. py2app assumes that attribute exists and attempts to copy it.
if getattr(zlib, "__file__", None) is None:
    zlib.__file__ = str(Path(sysconfig.get_path("stdlib")) / "encodings" / "zlib_codec.py")


_orig_build_executable = py2app_build_app.py2app.build_executable


def _patched_build_executable(self, *args, **kwargs):
    if getattr(zlib, "__file__", None) is None:
        zlib.__file__ = str(Path(sysconfig.get_path("stdlib")) / "encodings" / "zlib_codec.py")
    return _orig_build_executable(self, *args, **kwargs)


py2app_build_app.py2app.build_executable = _patched_build_executable


APP = ["Amalthea.py"]
DATA_FILES = ["appIcon.icns", "docIcon.icns"]
PLIST = {
    "CFBundleIdentifier": "com.anteprandium.amalthea",
    "CFBundleShortVersionString": "0.9.0",
    "CFBundleVersion": "0.9.0",
    "CFBundleGetInfoString": "A stand-alone Jupyter notebook for SageMath",
    "NSHumanReadableCopyright": "CC0 2020, 2021, 2025, 2026, Anteprandium",
    "CFBundleDocumentTypes": [
        {
            "CFBundleTypeExtensions": ["ipynb"],
            "CFBundleTypeIconFile": "docIcon.icns",
            "CFBundleTypeMIMETypes": ["application/x-ipynb+json"],
            "CFBundleTypeName": "Jupyter Notebook",
            "CFBundleTypeRole": "Editor",
        }
    ],
}

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "appIcon.icns",
    "plist": PLIST,
    "packages": ["objc"],
}


setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
