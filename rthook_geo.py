
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
