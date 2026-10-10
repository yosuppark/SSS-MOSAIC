
# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for XTF Sidescan Mosaic
  build:  pyinstaller xtf_mosaic.spec --noconfirm --clean
"""

import os
import sys
import glob
import importlib.util

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
)

# ----------------------------------------------------------------- settings
APP_NAME      = "XtfMosaic"
ENTRY_SCRIPT  = "main.py"
SHOW_CONSOLE  = True      # first build: keep True to read tracebacks
EXCLUDE_SCIPY = False

try:
    PROJ_ROOT = os.path.abspath(SPECPATH)
except NameError:
    PROJ_ROOT = os.path.abspath(os.getcwd())


def _p(*parts):
    return os.path.join(PROJ_ROOT, *parts)


def has_mod(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


# ------------------------------------------------------- Qt binding detect
QT_ALL = ["PySide6", "PyQt5", "PyQt6", "PySide2"]

_src = ""
try:
    with open(_p(ENTRY_SCRIPT), "r", encoding="utf-8", errors="ignore") as fh:
        _src = fh.read()
except Exception:
    _src = ""

QT_BINDING = None
for _name in QT_ALL:
    if _name in _src and has_mod(_name):
        QT_BINDING = _name
        break
if QT_BINDING is None:
    for _name in QT_ALL:
        if has_mod(_name):
            QT_BINDING = _name
            break
if QT_BINDING is None:
    raise SystemExit(
        "[spec] FATAL: no Qt binding installed.\n"
        "        pip install PySide6-Essentials PySide6-Addons PySide6"
    )

HAS_WEBENGINE = has_mod(QT_BINDING + ".QtWebEngineWidgets")

print("[spec] Qt binding   :", QT_BINDING)
print("[spec] WebEngine    :", HAS_WEBENGINE)


# --------------------------------------------------------- PROJ / GDAL data
proj_data_dir = None
try:
    import pyproj
    _d = pyproj.datadir.get_data_dir()
    if _d and os.path.isfile(os.path.join(_d, "proj.db")):
        proj_data_dir = _d
except Exception:
    proj_data_dir = None

if proj_data_dir is None:
    _bases = [sys.prefix, os.path.dirname(sys.executable)]
    _pats = [
        ("Lib", "site-packages", "pyproj", "proj_dir", "share", "proj"),
        ("Library", "share", "proj"),
        ("share", "proj"),
    ]
    for _b in _bases:
        for _pt in _pats:
            _c = os.path.join(_b, *_pt)
            if os.path.isfile(os.path.join(_c, "proj.db")):
                proj_data_dir = _c
                break
        if proj_data_dir:
            break

gdal_data_dir = None
try:
    import rasterio
    _g = os.path.join(os.path.dirname(rasterio.__file__), "gdal_data")
    if os.path.isdir(_g):
        gdal_data_dir = _g
except Exception:
    gdal_data_dir = None

print("[spec] pyproj data  :", proj_data_dir)
print("[spec] gdal  data   :", gdal_data_dir)

if proj_data_dir is None:
    print("[spec] WARNING: proj.db not found -- CRS transforms may fail")


# ------------------------------------------------------------- runtime hook
RTHOOK = _p("rthook_geo.py")

_RTHOOK_SRC = '''
import os
import sys

if getattr(sys, "frozen", False):
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

    for _v in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "GDAL_DRIVER_PATH",
               "PYTHONPATH", "PYTHONHOME", "GDAL_FILENAME_IS_UTF8"):
        os.environ.pop(_v, None)

    os.environ.setdefault("PROJ_NETWORK", "OFF")
    os.environ.setdefault("QT_OPENGL", "software")

    def _find(root, target, depth=6):
        root = os.path.abspath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)
        for cur, dirs, files in os.walk(root):
            if cur.count(os.sep) - base_depth > depth:
                dirs[:] = []
                continue
            if target in files:
                return cur
        return None

    _proj = _find(base, "proj.db")
    if _proj:
        os.environ["PROJ_DATA"] = _proj
        os.environ["PROJ_LIB"] = _proj
        try:
            import pyproj
            pyproj.datadir.set_data_dir(_proj)
        except Exception:
            pass

    _gdal = _find(base, "gt_datum.csv") or _find(base, "header.dxf")
    if _gdal:
        os.environ["GDAL_DATA"] = _gdal
'''

with open(RTHOOK, "w", encoding="utf-8") as fh:
    fh.write(_RTHOOK_SRC)


# -------------------------------------------------------------------- datas
datas = []

for _pkg in ("pyproj", "rasterio", "pyxtf", "certifi"):
    if has_mod(_pkg):
        try:
            datas += collect_data_files(_pkg)
        except Exception:
            pass

if proj_data_dir:
    datas.append((proj_data_dir, os.path.join("pyproj", "proj_dir", "share", "proj")))
    datas.append((proj_data_dir, os.path.join("share", "proj")))

if gdal_data_dir:
    datas.append((gdal_data_dir, os.path.join("rasterio", "gdal_data")))

for _res in ("resources", "icons", "assets", "html", "web", "templates"):
    if os.path.isdir(_p(_res)):
        datas.append((_p(_res), _res))

for _pat in ("*.png", "*.ico", "*.svg", "*.qss", "*.html", "*.js", "*.json"):
    for _f in glob.glob(_p(_pat)):
        datas.append((_f, "."))


# ----------------------------------------------------------------- binaries
binaries = []
for _pkg in ("rasterio", "pyproj"):
    if has_mod(_pkg):
        try:
            binaries += collect_dynamic_libs(_pkg)
        except Exception:
            pass


# ----------------------------------------------------------- hiddenimports
_hidden = []

for _pkg in ("rasterio", "pyproj", "pyxtf", "affine"):
    if has_mod(_pkg):
        try:
            _hidden += collect_submodules(_pkg)
        except Exception:
            pass

_qt_mods = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtPrintSupport", "QtSvg"]
if HAS_WEBENGINE:
    _qt_mods += ["QtWebEngineWidgets", "QtWebEngineCore", "QtWebChannel", "QtQuick", "QtQml"]
for _m in _qt_mods:
    _hidden.append(QT_BINDING + "." + _m)

if QT_BINDING.startswith("PyQt"):
    _hidden.append(QT_BINDING + ".sip")
    _hidden.append("sip")

_hidden += [
    "numpy",
    "numpy.core.multiarray",
    "bottom_loader",
    "mosaic_builder",
    "xtf_processor",
    "mosaic_overlay",
    "mosaic_dialog",
    "mosaic_params_dialog",
    "map_view",
    "xtf_loader",
]

if not EXCLUDE_SCIPY and has_mod("scipy"):
    _hidden += ["scipy.ndimage", "scipy.interpolate", "scipy.signal", "scipy.spatial"]

# drop names that do not exist -> removes "Hidden import not found" errors
hiddenimports = []
_seen = set()
for _h in _hidden:
    if _h in _seen:
        continue
    _seen.add(_h)
    if has_mod(_h):
        hiddenimports.append(_h)


# ------------------------------------------------------------------ excludes
excludes = ["tkinter", "matplotlib", "IPython", "notebook", "pytest",
            "sphinx", "setuptools", "pip", "wheel", "test", "unittest"]

for _other in QT_ALL:
    if _other != QT_BINDING:
        excludes.append(_other)

if EXCLUDE_SCIPY:
    excludes.append("scipy")


# ------------------------------------------------------------------ analysis
a = Analysis(
    [_p(ENTRY_SCRIPT)],
    pathex=[PROJ_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[RTHOOK],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=SHOW_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
