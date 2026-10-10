
"""proj_env.py — PROJ / GDAL 데이터 경로 정리 (PyInstaller 대응)

배경
----
pyproj 와 rasterio 는 **각자 다른 PROJ 버전**을 내장한다.
    pyproj  1.4 레이아웃 (PROJ 9.2 계열)
    rasterio(GDAL 3.12) 1.6 레이아웃 (PROJ 9.6 계열)
개발 환경에서는 두 패키지가 각자 자기 proj.db 를 알아서 쓰므로 문제가 없다.
따라서 **개발 환경에서는 PROJ_DATA 를 건드리면 안 된다.**
(강제로 pyproj 쪽을 지정하면 rasterio 가
 "DATABASE.LAYOUT.VERSION.MINOR = 4 whereas a number >= 6 is expected" 로 죽는다.)

PyInstaller onefile 번들에서는 사정이 다르다.
런타임 훅이 임의의 proj.db 하나를 PROJ_DATA 로 잡아 버리므로,
번들 안의 여러 proj.db 중 **레이아웃 버전이 가장 높은 것**을 골라 줘야 한다.
(PROJ 는 minor >= 기대값이면 통과하므로, 높은 쪽이 양쪽 모두에서 동작한다.)

동작
----
    frozen(EXE)  : 번들 내 proj.db 중 최신 레이아웃을 PROJ_DATA/PROJ_LIB 로 지정
    개발(스크립트): 아무것도 설정하지 않음 (진단 정보만 수집)

사용법
------
    main.py 의 **맨 첫 줄** (pyproj / rasterio import 보다 먼저)

        import proj_env
        proj_env.setup()

단독 실행
---------
    python proj_env.py            진단 출력
    python proj_env.py --force    개발 환경에서도 강제 설정하여 테스트
"""

import os
import sqlite3
import sys

__all__ = ["setup", "get_proj_data_dir", "get_gdal_data_dir",
           "list_candidates", "report", "self_test"]

# rasterio(GDAL 3.10+) 가 요구하는 최소 레이아웃
REQUIRED_LAYOUT = (1, 6)

_DONE = False
_APPLIED = False
_CHOSEN_PROJ = None
_CHOSEN_GDAL = None
_CANDIDATES = []
_NOTES = []


# ------------------------------------------------------------------ 탐색 기준
def _base_dirs():
    """proj 데이터가 있을 만한 루트 디렉터리."""
    dirs = []

    def _add(path):
        if not path:
            return
        p = os.path.abspath(path)
        if p not in dirs and os.path.isdir(p):
            dirs.append(p)

    _add(getattr(sys, "_MEIPASS", None))
    _add(os.path.dirname(os.path.abspath(sys.executable)))
    try:
        _add(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        _add(os.getcwd())

    # site-packages (개발 환경에서 rasterio / fiona / osgeo 번들 데이터 탐색용)
    for name in ("pyproj", "rasterio", "fiona", "osgeo"):
        try:
            import importlib.util
            spec = importlib.util.find_spec(name)
        except Exception:
            spec = None
        if spec is None:
            continue
        locs = list(getattr(spec, "submodule_search_locations", None) or [])
        if spec.origin:
            locs.append(os.path.dirname(spec.origin))
        for loc in locs:
            _add(loc)                       # 패키지 자체
            _add(os.path.dirname(loc))      # site-packages

    _add(os.path.join(sys.prefix, "Library"))
    _add(sys.prefix)
    return dirs


_REL_PROJ = (
    os.path.join("pyproj", "proj_dir", "share", "proj"),
    os.path.join("proj_dir", "share", "proj"),
    os.path.join("rasterio", "proj_data"),
    os.path.join("fiona", "proj_data"),
    os.path.join("osgeo", "data", "proj"),
    os.path.join("data", "proj"),
    os.path.join("share", "proj"),
    os.path.join("Library", "share", "proj"),
    "proj_data",
    "proj",
    "",                                     # base 자체에 proj.db 가 있는 경우
)

_REL_GDAL = (
    os.path.join("rasterio", "gdal_data"),
    os.path.join("fiona", "gdal_data"),
    os.path.join("osgeo", "data", "gdal"),
    os.path.join("share", "gdal"),
    os.path.join("Library", "share", "gdal"),
    "gdal_data",
)


# ------------------------------------------------------------------ 버전 확인
def _db_layout(db_path):
    """proj.db 의 (MAJOR, MINOR) 레이아웃 버전. 실패 시 (-1, -1)."""
    major = minor = -1
    con = None
    try:
        con = sqlite3.connect(db_path, timeout=2.0)
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


def _collect_proj():
    """[(major, minor, dir)] 중복 제거된 후보 목록."""
    out = []
    seen = set()

    def _try(d):
        if not d:
            return
        d = os.path.abspath(d)
        key = os.path.normcase(d)
        if key in seen:
            return
        db = os.path.join(d, "proj.db")
        if not os.path.isfile(db):
            return
        seen.add(key)
        mj, mn = _db_layout(db)
        out.append((mj, mn, d))

    for env_key in ("PROJ_DATA", "PROJ_LIB"):
        val = os.environ.get(env_key)
        if val:
            for part in val.split(os.pathsep):
                _try(part.strip())

    for base in _base_dirs():
        for rel in _REL_PROJ:
            _try(os.path.join(base, rel) if rel else base)

    return out


def _collect_gdal():
    out = []
    seen = set()

    def _try(d):
        if not d or not os.path.isdir(d):
            return
        d = os.path.abspath(d)
        key = os.path.normcase(d)
        if key in seen:
            return
        marker = any(os.path.isfile(os.path.join(d, f))
                     for f in ("gdalvrt.xsd", "header.dxf", "pcs.csv",
                               "gt_datum.csv", "stateplane.csv"))
        if marker:
            seen.add(key)
            out.append(d)

    _try(os.environ.get("GDAL_DATA"))
    for base in _base_dirs():
        for rel in _REL_GDAL:
            _try(os.path.join(base, rel))
    return out


# ------------------------------------------------------------------ setup
def setup(verbose=False, force=None):
    """PROJ 경로 진단 및 (필요 시) 환경변수 설정.

    force=None  : frozen 일 때만 설정  (권장 / 기본값)
    force=True  : 항상 설정
    force=False : 절대 설정하지 않음 (진단만)
    """
    global _DONE, _APPLIED, _CHOSEN_PROJ, _CHOSEN_GDAL, _CANDIDATES, _NOTES

    if _DONE and force is None:
        return _CHOSEN_PROJ

    _NOTES = []
    _CANDIDATES = _collect_proj()
    frozen = bool(getattr(sys, "frozen", False))

    if force is None:
        apply_env = frozen
    else:
        apply_env = bool(force)

    if _CANDIDATES:
        best = sorted(_CANDIDATES, key=lambda t: (t[0], t[1]), reverse=True)[0]
        _CHOSEN_PROJ = best[2]
        if (best[0], best[1]) < REQUIRED_LAYOUT:
            _NOTES.append(
                "최신 proj.db 도 레이아웃 %s.%s 로 낮습니다 "
                "(rasterio 는 %s.%s 이상 요구). "
                "번들에 rasterio/proj_data 가 포함됐는지 확인하세요."
                % (best[0], best[1], REQUIRED_LAYOUT[0], REQUIRED_LAYOUT[1]))
    else:
        _CHOSEN_PROJ = None
        _NOTES.append("proj.db 를 찾지 못했습니다.")

    gdal_dirs = _collect_gdal()
    _CHOSEN_GDAL = gdal_dirs[0] if gdal_dirs else None

    if apply_env and _CHOSEN_PROJ:
        os.environ["PROJ_DATA"] = _CHOSEN_PROJ
        os.environ["PROJ_LIB"] = _CHOSEN_PROJ
        os.environ.setdefault("PROJ_NETWORK", "OFF")
        _APPLIED = True
        mod = sys.modules.get("pyproj.datadir")
        if mod is not None:
            try:
                mod.set_data_dir(_CHOSEN_PROJ)
            except Exception:
                pass
    elif not apply_env:
        _NOTES.append("개발 환경이므로 PROJ_DATA 를 설정하지 않습니다 "
                      "(각 패키지가 자체 proj.db 사용).")

    if apply_env and _CHOSEN_GDAL:
        os.environ["GDAL_DATA"] = _CHOSEN_GDAL

    _DONE = True
    if verbose:
        report()
    return _CHOSEN_PROJ


def get_proj_data_dir():
    setup()
    return _CHOSEN_PROJ


def get_gdal_data_dir():
    setup()
    return _CHOSEN_GDAL


def list_candidates():
    setup()
    return list(_CANDIDATES)


# ------------------------------------------------------------------ 진단
def report():
    print("frozen        :", bool(getattr(sys, "frozen", False)))
    print("_MEIPASS      :", getattr(sys, "_MEIPASS", None))
    print("환경변수 적용 :", _APPLIED)
    print("PROJ_DATA     :", os.environ.get("PROJ_DATA"))
    print("GDAL_DATA     :", os.environ.get("GDAL_DATA"))
    print("proj.db 후보  :", len(_CANDIDATES))
    for mj, mn, d in sorted(_CANDIDATES,
                            key=lambda t: (t[0], t[1]), reverse=True):
        mark = "   <== 최신" if d == _CHOSEN_PROJ else ""
        print("   layout %s.%s  %s%s" % (mj, mn, d, mark))
    for note in _NOTES:
        print("   * " + note)


def self_test():
    print("-" * 62)
    try:
        from pyproj import CRS, Transformer
        print("pyproj   EPSG:32652 ->", CRS.from_epsg(32652).name)
        tr = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)
        print("pyproj   변환       ->", tr.transform(126.57439, 33.23511))
    except Exception as exc:
        print("pyproj   실패:", exc)
    print("-" * 62)
    try:
        import rasterio
        from rasterio.crs import CRS as RCRS
        print("rasterio", rasterio.__version__,
              "GDAL", rasterio.__gdal_version__)
        print("rasterio EPSG:32652 ->", RCRS.from_epsg(32652).to_string())
    except Exception as exc:
        print("rasterio 실패:", exc)
    print("-" * 62)


if __name__ == "__main__":
    forced = "--force" in sys.argv
    setup(force=True if forced else None)
    report()
    self_test()
else:
    setup()
