
"""map_view.py — Leaflet 기반 지도 위젯 (PySide6 QtWebEngine)

기능:
  - add_track(idx, points, color)      : 파일별 색상 항적 추가
  - add_coverage(idx, polygon, color)  : swath coverage 폴리곤 추가
  - set_active_track(idx)              : 활성 항적 강조
  - highlight_ping(ping_idx)           : hover 핑 위치 마커
  - clear_ping_marker()                : 핑 마커 제거
  - add_contact(lon, lat, label)       : contact 마커 + 라벨
  - add_image_overlay(url, bounds)     : GeoTIFF/PNG 오버레이 (믹스인 제공)
  - clear_image_overlays()             : 오버레이 제거 (믹스인 제공)
  - fit_all()                          : 전체 항적에 맞춰 zoom

v3 호환 API (단일 파일 뷰어용 wrapper):
  - set_track(points, name)            : 항적 1개 설정 + 활성화 + zoom
  - set_swath(polygon)                 : swath 폴리곤 1개 설정
  - clear()                            : 전체 초기화

좌표 규약: Python 측 points는 (lat, lon) 순서.
           loader meta는 x=lon, y=lat 이므로 main.py에서
           [(m["y"], m["x"]) for m in meta] 로 변환해 넘길 것.

JS 주입 규칙: 모든 값은 반드시 json.dumps() 를 거친다.
           (역슬래시·따옴표·개행으로 인한 SyntaxError 원천 차단)
"""

import json

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from overlay_mixin import ImageOverlayMixin


# ==================================================== HTML (Leaflet CDN)
_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; padding: 0; }
  .contact-label {
    background: rgba(255,255,255,0.85);
    border: 1px solid #333;
    border-radius: 3px;
    padding: 0 3px;
    font-size: 11px;
    font-weight: bold;
    color: #000;
  }
</style>
</head>
<body>
<div id="map"></div>
<script>
  var map = L.map('map').setView([35.0, 129.0], 8);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  // 전역 저장소
  window.tracks     = {};   // idx -> L.polyline
  window.coverages  = {};   // idx -> L.polygon
  window.contacts   = [];   // L.circleMarker 목록
  window.overlays   = [];   // L.imageOverlay 목록
  window.pingMarker = null;
</script>
</body>
</html>
"""


class _DebugPage(QWebEnginePage):
    """JS 콘솔 메시지를 파이썬 stdout 으로 중계."""

    def javaScriptConsoleMessage(self, level, msg, line, source):
        print("[JS:%s] %s" % (line, msg))


class MapView(ImageOverlayMixin, QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks = {}        # idx -> [(lat, lon), ...] 원본(인덱스 보존)
        self._active = None      # 활성 파일 idx
        self._ready = False
        self._pending_js = []

        self.setPage(_DebugPage(self))
        self.loadFinished.connect(self._on_load_finished)
        self.setHtml(_HTML, QUrl("https://localhost/"))

    # ---------------------------------------------- JS 실행 헬퍼
    def _run_js(self, js):
        """페이지 로드 전 호출은 큐에 보관 후 로드 완료 시 일괄 실행."""
        if self._ready:
            self.page().runJavaScript(js)
        else:
            self._pending_js.append(js)

    def _on_load_finished(self, ok):
        self._ready = bool(ok)
        if self._ready:
            for js in self._pending_js:
                self.page().runJavaScript(js)
        self._pending_js = []

    @staticmethod
    def _j(value):
        """JS 리터럴로 안전하게 직렬화."""
        return json.dumps(value)

    @staticmethod
    def _clean(points):
        """(0, 0) 무효 좌표 및 NaN 제거."""
        out = []
        for p in points or ():
            try:
                la = float(p[0])
                lo = float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            if la != la or lo != lo:
                continue
            if la == 0.0 and lo == 0.0:
                continue
            out.append([la, lo])
        return out

    # ---------------------------------------------- 항적
    def add_track(self, idx, points, color="#e6194b"):
        """points: [(lat, lon), ...] 핑 순서대로."""
        pts = self._clean(points)
        if not pts:
            return False
        self._tracks[idx] = list(points)   # 인덱스 1:1 보존용 원본

        js = (
            "(function(){"
            "  var k=%s;"
            "  if(window.tracks[k]){map.removeLayer(window.tracks[k]);}"
            "  window.tracks[k]=L.polyline(%s,"
            "      {color:%s,weight:2,opacity:0.6}).addTo(map);"
            "})();"
        ) % (self._j(str(idx)), self._j(pts), self._j(color))
        self._run_js(js)
        return True

    def remove_track(self, idx):
        """단일 항적 제거."""
        self._tracks.pop(idx, None)
        js = (
            "(function(){"
            "  var k=%s;"
            "  if(window.tracks[k]){map.removeLayer(window.tracks[k]);"
            "    delete window.tracks[k];}"
            "  if(window.coverages[k]){map.removeLayer(window.coverages[k]);"
            "    delete window.coverages[k];}"
            "})();"
        ) % (self._j(str(idx)),)
        self._run_js(js)
        return True

    def set_active_track(self, idx):
        """활성 항적 강조(굵게), 나머지는 흐리게."""
        self._active = idx
        js = (
            "(function(){"
            "  var act=%s;"
            "  for(var k in window.tracks){"
            "    var on=(k===act);"
            "    window.tracks[k].setStyle({weight:on?4:2,"
            "                               opacity:on?1.0:0.4});"
            "    if(on){window.tracks[k].bringToFront();}"
            "  }"
            "})();"
        ) % (self._j(str(idx)),)
        self._run_js(js)
        return True

    def highlight_ping(self, ping_idx):
        """waterfall hover 위치를 지도에 원형 마커로 표시."""
        pts = self._tracks.get(self._active)
        if not pts or not (0 <= ping_idx < len(pts)):
            return False
        lat, lon = float(pts[ping_idx][0]), float(pts[ping_idx][1])
        if lat != lat or lon != lon:
            return False
        if lat == 0.0 and lon == 0.0:
            return False

        js = (
            "(function(){"
            "  var ll=[%s,%s];"
            "  if(!window.pingMarker){"
            "    window.pingMarker=L.circleMarker(ll,{radius:7,"
            "      color:'#ff0000',weight:2,fillColor:'#ffff00',"
            "      fillOpacity:0.8}).addTo(map);"
            "  }else{window.pingMarker.setLatLng(ll);}"
            "})();"
        ) % (self._j(lat), self._j(lon))
        self._run_js(js)
        return True

    def clear_ping_marker(self):
        """핑 하이라이트 마커 제거."""
        js = (
            "(function(){"
            "  if(window.pingMarker){map.removeLayer(window.pingMarker);"
            "    window.pingMarker=null;}"
            "})();"
        )
        self._run_js(js)
        return True

    # ---------------------------------------------- coverage
    def add_coverage(self, idx, polygon, color="#3cb44b"):
        """polygon: [(lat, lon), ...] 외곽선. swath coverage 반투명 표시."""
        pts = self._clean(polygon)
        if len(pts) < 3:
            return False
        js = (
            "(function(){"
            "  var k=%s,c=%s;"
            "  if(window.coverages[k]){map.removeLayer(window.coverages[k]);}"
            "  window.coverages[k]=L.polygon(%s,"
            "      {color:c,weight:1,fillColor:c,fillOpacity:0.15})"
            "      .addTo(map);"
            "})();"
        ) % (self._j(str(idx)), self._j(color), self._j(pts))
        self._run_js(js)
        return True

    # ---------------------------------------------- contact
    def add_contact(self, lon, lat, label, color="#ffe119"):
        """contact 마커 + 상시 라벨 표시."""
        try:
            lon = float(lon)
            lat = float(lat)
        except (TypeError, ValueError):
            return False
        if lat != lat or lon != lon:
            return False
        if lon == 0.0 and lat == 0.0:
            return False

        js = (
            "(function(){"
            "  var m=L.circleMarker([%s,%s],{radius:6,color:'#000000',"
            "      weight:1.5,fillColor:%s,fillOpacity:0.9}).addTo(map);"
            "  m.bindTooltip(%s,{permanent:true,direction:'top',"
            "      offset:[0,-6],className:'contact-label'});"
            "  window.contacts.push(m);"
            "})();"
        ) % (self._j(lat), self._j(lon), self._j(color),
             self._j("" if label is None else str(label)))
        self._run_js(js)
        return True

    def clear_contacts(self):
        """contact 마커 전부 제거."""
        js = (
            "(function(){"
            "  window.contacts.forEach(function(m){"
            "    try{map.removeLayer(m);}catch(e){}});"
            "  window.contacts=[];"
            "})();"
        )
        self._run_js(js)
        return True

    # ---------------------------------------------- 화면 맞춤 / 초기화
    def fit_all(self):
        """등록된 모든 항적/오버레이가 보이도록 zoom 조정."""
        js = (
            "(function(){"
            "  var g=[];"
            "  for(var k in window.tracks){g.push(window.tracks[k]);}"
            "  for(var k in window.coverages){g.push(window.coverages[k]);}"
            "  window.overlays.forEach(function(o){g.push(o);});"
            "  if(g.length===0){return;}"
            "  try{map.fitBounds(L.featureGroup(g).getBounds().pad(0.1));}"
            "  catch(e){}"
            "})();"
        )
        self._run_js(js)
        return True

    def fit_track(self, idx):
        """특정 항적에 맞춰 zoom."""
        js = (
            "(function(){"
            "  var t=window.tracks[%s];"
            "  if(!t){return;}"
            "  try{map.fitBounds(t.getBounds().pad(0.1));}catch(e){}"
            "})();"
        ) % (self._j(str(idx)),)
        self._run_js(js)
        return True

    def clear_all(self):
        """항적/coverage/contact/오버레이 전부 제거."""
        self._tracks = {}
        self._active = None
        js = (
            "(function(){"
            "  for(var k in window.tracks){"
            "    try{map.removeLayer(window.tracks[k]);}catch(e){}}"
            "  for(var k in window.coverages){"
            "    try{map.removeLayer(window.coverages[k]);}catch(e){}}"
            "  window.contacts.forEach(function(m){"
            "    try{map.removeLayer(m);}catch(e){}});"
            "  window.overlays.forEach(function(o){"
            "    try{map.removeLayer(o);}catch(e){}});"
            "  if(window.pingMarker){"
            "    try{map.removeLayer(window.pingMarker);}catch(e){}}"
            "  window.tracks={};window.coverages={};"
            "  window.contacts=[];window.overlays=[];"
            "  window.pingMarker=null;"
            "})();"
        )
        self._run_js(js)
        return True

    # ================================================ v3 호환 API
    def set_track(self, points, name="", color="#e6194b"):
        """단일 항적 설정 + 활성화 + 전체 화면 맞춤.
        points: [(lat, lon), ...] 핑 순서대로 (인덱스 1:1 유지)."""
        self.add_track(0, points, color)
        self.set_active_track(0)
        self.fit_all()
        return True

    def set_swath(self, polygon, color="#3cb44b"):
        """단일 swath coverage 폴리곤 설정."""
        return self.add_coverage(0, polygon, color)

    def clear(self):
        """clear_all 의 v3 별칭."""
        return self.clear_all()


if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    v = MapView()
    v.resize(900, 650)
    v.show()

    trk = [(33.2351 + i * 1e-4, 126.5744 + i * 1e-4) for i in range(200)]
    v.set_track(trk, "demo")
    v.add_contact(126.5800, 33.2400, "C-01 O'Brien \\ test")
    v.highlight_ping(100)

    sys.exit(app.exec())
