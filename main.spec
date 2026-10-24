# -*- mode: python ; coding: utf-8 -*-
"""main.spec — Sidescan Mosaic Viewer PyInstaller 빌드 스펙 (v4)

v3 대비 변경점
--------------
1. runtime_hooks 에 rthook_geo.py 자동 등록  (v3 누락 — 치명적)
2. 로컬 .py 소스를 datas 로 동봉        → PYZ 수집 실패해도 import 성공
3. PyInstaller 6.x 호환                 → cipher / win_* / zipped_data 제거
4. ONEFILE 기본 False (onedir)          → _internal 내용 직접 확인 가능

빌드
----
    pyinstaller main.spec --clean --noconfirm

검증 (onedir)
-------------
    dir dist\\main\\_internal\\mosaic_builder.py
    dir dist\\main\\_internal\\mosaic_min.py
"""

import os
import sqlite3

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ------------------------------------------------------------------ 설정
APP_NAME = "main"
ENTRY_SCRIPT = "main.py"
CONSOLE = True          # 문제 해결 전까지 True. 완료 후 False.
ONEFILE = False         # 문제 해결 전까지 False(onedir) 권장.
ICON = None             # 예: "app.ico"

HERE = os.path.abspath(SPECPATH)

# 로컬 소스 스캔에서 제외할 모듈 (엔트리 / 훅 / 유틸 스크립트)
SKIP = {
    "main", "rthook_geo", "check_build", "setup", "conf",
}


# ------------------------------------------------------------------ 로컬 모듈
def _local_modules():
    """spec 폴더의 .py 파일명을 모듈명 리스트로 반환."""
    names = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.lower().endswith(".py"):
            continue
        stem = os.path.splitext(fn)[0]
        if stem in SKIP or stem.startswith("_"):
            continue
        names.append(stem)
    return names


LOCAL = _local_modules()
print("[spec] local modules : %s" % ", ".join(LOCAL))


# ------------------------------------------------------------------ 수집
hiddenimports = []
hiddenimports += collect_submodules("rasterio")
hiddenimports += collect_submodules("pyxtf")
hiddenimports += collect_submodules("pyproj")
hiddenimports += collect_submodules("processing")
hiddenimports += LOCAL
hiddenimports += [
    "rasterio.sample",
    "rasterio.vrt",
    "rasterio.control",
    "rasterio.crs",
    "rasterio._features",
    "rasterio._shim",
    "scipy._lib.messagestream",
]

# 설치되지 않은 항목은 사전 제거 (경고 소음 억제)
try:
    from importlib.util import find_spec

    _keep = []
    for name in hiddenimports:
        if name in LOCAL:
            _keep.append(name)
            continue
        try:
            if find_spec(name) is not None:
                _keep.append(name)
        except Exception:
            pass
    hiddenimports = sorted(set(_keep))
except Exception:
    hiddenimports = sorted(set(hiddenimports))

datas = []
datas += collect_data_files("rasterio")     # proj_data / gdal_data
datas += collect_data_files("pyproj")       # proj_dir/share/proj
try:
    datas += collect_data_files("PySide6", includes=["**/*.conf", "**/*.qm"])
except Exception:
    pass

# 핵심: 로컬 소스 자체를 번들 루트에 동봉 (import 폴백)
for _m in LOCAL:
    _src = os.path.join(HERE, _m + ".py")
    if os.path.isfile(_src):
        datas.append((_src, "."))
print("[spec] bundled sources : %d" % len(LOCAL))

# processing 패키지 모듈 동봉
_proc_dir = os.path.join(HERE, "processing")
if os.path.isdir(_proc_dir):
    for _fn in os.listdir(_proc_dir):
        if _fn.lower().endswith(".py"):
            datas.append((os.path.join(_proc_dir, _fn), "processing"))
    print("[spec] bundled processing sources")

# 런타임 훅
runtime_hooks = []
_rt = os.path.join(HERE, "rthook_geo.py")
if os.path.isfile(_rt):
    runtime_hooks.append(_rt)
    print("[spec] runtime hook : rthook_geo.py")
else:
    print("[spec] !! rthook_geo.py 없음 — PROJ_LIB/GDAL_DATA 미설정 위험")

binaries = []


# ------------------------------------------------------------------ Analysis
a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[
        "PyQt5", "PyQt6", "PySide2",
        "tkinter", "matplotlib", "numpy.tests",
        "IPython", "jupyter", "notebook", "pytest",
    ],
    noarchive=False,
)


# ------------------------------------------------------------------ proj.db 통일
def _db_layout(path):
    """proj.db 의 (MAJOR, MINOR) 레이아웃 버전. 실패 시 (-1, -1)."""
    major = minor = -1
    con = None
    try:
        con = sqlite3.connect(path, timeout=2.0)
        cur = con.cursor()
        cur.execute("SELECT key, value FROM metadata "
                    "WHERE key LIKE 'DATABASE.LAYOUT.VERSION.%'")
        for key, val in cur.fetchall():
            try:
                num = int(str(val).strip())
            except (TypeError, ValueError):
                continue
            if key.endswith("MAJOR"):
                major = num
            elif key.endswith("MINOR"):
                minor = num
    except Exception:
        pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return (major, minor)


def _unify_proj_db(toc):
    """TOC 안의 모든 proj.db 항목을 최신 레이아웃 파일로 교체."""
    found = []
    for i, entry in enumerate(toc):
        dest, src = entry[0], entry[1]
        if os.path.basename(str(dest)).lower() != "proj.db":
            continue
        if not src or not os.path.isfile(src):
            continue
        mj, mn = _db_layout(src)
        found.append((mj, mn, i, dest, src))

    print("[spec] 번들 proj.db 항목 %d 개" % len(found))
    for mj, mn, _i, dest, src in found:
        print("[spec]   layout %s.%s  %s" % (mj, mn, dest))

    if not found:
        print("[spec] !! proj.db 미수집 — collect_data_files 확인 필요")
        return toc

    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best = found[0]
    print("[spec] 최신 proj.db : layout %s.%s  %s"
          % (best[0], best[1], best[4]))
    if (best[0], best[1]) < (1, 6):
        print("[spec] !! 최신본도 layout %s.%s — rasterio proj_data 확인"
              % (best[0], best[1]))

    best_src = best[4]
    new_toc = list(toc)
    for mj, mn, i, dest, src in found:
        if src == best_src:
            continue
        entry = list(new_toc[i])
        entry[1] = best_src
        new_toc[i] = tuple(entry)
        print("[spec]   교체: %s  (%s.%s -> %s.%s)"
              % (dest, mj, mn, best[0], best[1]))
    return new_toc


a.datas = _unify_proj_db(a.datas)


# ------------------------------------------------------------------ 중복 제거
def _dedupe(toc):
    seen = set()
    out = []
    for entry in toc:
        key = os.path.normcase(str(entry[0]))
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


a.datas = _dedupe(a.datas)


# ------------------------------------------------------------------ 패키징
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
else:
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
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
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