# -*- coding: utf-8 -*-
"""map_view.py — Leaflet 기반 지도 위젯 (PySide6 QtWebEngine)
- 완전 독립형 오프라인(Offline) 모드 지원: 인터넷 연결 없이도 항적, 스와스폭, 핑 마커, 모자익 100% 작동
- 내장 Leaflet 라이브러리(CSS/JS/아이콘) 인라인 주입으로 외부 CDN 의존성 제로(0)
- 인터넷 연결 시 OpenStreetMap 배경 지도 자동 로드, 미연결 시 해양 항법 그리드(Nautical Grid) 배경 작동
- 레이어 토글 컨트롤(항적, 스와스폭, 모자익, 온라인 지도) 및 상태 표시기 제공
"""
from __future__ import annotations

import json
import os
import sys

# QtWebEngine 안정화 플래그 (Chromium 샌드박스 충돌 방지 및 오프라인 보장)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from overlay_mixin import ImageOverlayMixin

# 내장 Leaflet 자산 로드 (인터넷 차단 환경 대응)
try:
    from map_assets import LEAFLET_CSS, LEAFLET_JS, MARKER_ICON_B64, MARKER_SHADOW_B64
except ImportError:
    LEAFLET_CSS = ""
    LEAFLET_JS = ""
    MARKER_ICON_B64 = ""
    MARKER_SHADOW_B64 = ""


def _build_offline_html():
    """인터넷 없이도 100% 즉시 구동되는 완전 독립형 Leaflet HTML 문서 생성."""
    # 만약 map_assets가 비어있다면 assets/leaflet 파일에서 직접 읽기 시도
    css = LEAFLET_CSS
    js = LEAFLET_JS
    if not css or not js:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        css_path = os.path.join(base_dir, "assets", "leaflet", "leaflet.css")
        js_path = os.path.join(base_dir, "assets", "leaflet", "leaflet.js")
        if os.path.exists(css_path) and os.path.exists(js_path):
            with open(css_path, "r", encoding="utf-8") as f:
                css = f.read()
            with open(js_path, "r", encoding="utf-8") as f:
                js = f.read()

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>
{css}
</style>
<script>
{js}
</script>
<style>
  html, body {{
    height: 100%;
    margin: 0;
    padding: 0;
    background-color: #0b1528;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Malgun Gothic", "Segoe UI", Roboto, sans-serif;
  }}
  #map {{
    height: 100%;
    width: 100%;
    margin: 0;
    padding: 0;
    /* 해양 탐사용 세련된 다크 블루 그리드 배경 (인터넷 없을 때도 선명한 좌표계 제공) */
    background-color: #0a172a;
    background-image:
      linear-gradient(rgba(56, 102, 160, 0.28) 1px, transparent 1px),
      linear-gradient(90deg, rgba(56, 102, 160, 0.28) 1px, transparent 1px),
      linear-gradient(rgba(30, 58, 100, 0.14) 1px, transparent 1px),
      linear-gradient(90deg, rgba(30, 58, 100, 0.14) 1px, transparent 1px);
    background-size: 100px 100px, 100px 100px, 20px 20px, 20px 20px;
    background-position: -1px -1px;
  }}
  .contact-label {{
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid #1e293b;
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 11px;
    font-weight: bold;
    color: #0f172a;
    box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    white-space: nowrap;
  }}
  /* 오프라인/온라인 상태 인디케이터 */
  .status-badge {{
    position: absolute;
    bottom: 8px;
    left: 8px;
    z-index: 1000;
    background: rgba(15, 23, 42, 0.82);
    color: #94a3b8;
    border: 1px solid rgba(148, 163, 184, 0.25);
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    pointer-events: none;
    display: flex;
    align-items: center;
    gap: 5px;
  }}
  .status-dot {{
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #38bdf8;
    display: inline-block;
  }}
  .status-dot.offline {{
    background: #f59e0b;
  }}
  .status-dot.online {{
    background: #10b981;
  }}
</style>
</head>
<body>
<div id="map"></div>
<div id="map-status" class="status-badge">
  <span id="status-dot" class="status-dot offline"></span>
  <span id="status-text">오프라인 모드 (해양 항적 뷰)</span>
</div>
<script>
  // 기본 마커 아이콘 기본값 설정 (오프라인 base64 인라인)
  try {{
    var defaultIcon = L.icon({{
        iconUrl: 'data:image/png;base64,{MARKER_ICON_B64}',
        shadowUrl: 'data:image/png;base64,{MARKER_SHADOW_B64}',
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41]
    }});
    L.Marker.prototype.options.icon = defaultIcon;
  }} catch(e) {{}}

  // 1. 지도 캔버스 초기화 (Canvas / SVG 렌더러 활성화로 대용량 핑 고속 렌더링)
  var map = L.map('map', {{
      preferCanvas: true,
      attributionControl: false,
      zoomControl: true,
      minZoom: 1,
      maxZoom: 22
  }}).setView([33.24, 126.58], 12);

  // 2. 온라인 타일 레이어 (인터넷 가능 시 표시, 오프라인 시 오류 무시하고 해양 그리드 유지)
  var osmTileLayer = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      errorTileUrl: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="%230a172a"/><path d="M0 0h256v256H0z" fill="none" stroke="%231b2e4b" stroke-width="1"/></svg>'
  }});

  // 기본 레이어로 추가 (인터넷 있으면 지도 뜨고, 없으면 그리드 배경 유지)
  osmTileLayer.addTo(map);

  // 타일 로딩 감지
  var onlineDetected = false;
  osmTileLayer.on('tileload', function() {{
      if (!onlineDetected) {{
          onlineDetected = true;
          var dot = document.getElementById('status-dot');
          var txt = document.getElementById('status-text');
          if (dot) dot.className = 'status-dot online';
          if (txt) txt.innerText = '온라인 지도 연결됨';
      }}
  }});

  // 3. 레이어 그룹 관리자
  window.tracksGroup    = L.layerGroup().addTo(map);
  window.coveragesGroup = L.layerGroup().addTo(map);
  window.contactsGroup  = L.layerGroup().addTo(map);
  window.overlaysGroup  = L.layerGroup().addTo(map);

  // 전역 저장소
  window.tracks     = {{}};   // idx -> L.polyline
  window.coverages  = {{}};   // idx -> L.polygon
  window.contacts   = [];   // L.circleMarker 목록
  window.overlays   = [];   // L.imageOverlay 목록
  window.pingMarker = null;
  window.mapReady   = true;

  // 레이어 토글 컨트롤 (우측 상단)
  var overlayMaps = {{
      "항적 (Tracks)": window.tracksGroup,
      "스와스폭 (Swaths)": window.coveragesGroup,
      "이상체 (Contacts)": window.contactsGroup,
      "모자익 (Mosaics)": window.overlaysGroup,
      "온라인 지도 (OSM)": osmTileLayer
  }};
  L.control.layers(null, overlayMaps, {{position: 'topright', collapsed: true}}).addTo(map);

  console.log("[Map] Leaflet offline engine initialized successfully!");
</script>
</body>
</html>
"""
    return html


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

        # 완전 독립형 오프라인 HTML 로드 (외부 CDN 없이 즉시 렌더링)
        html = _build_offline_html()
        self.setHtml(html, QUrl("http://localhost/"))

        # 엔진 준비 상태 주기적 체크 (loadFinished 지연 또는 누락 대비)
        QTimer.singleShot(50, self._check_engine_ready)

    def _flush_pending(self):
        """대기 중인 JavaScript 명령 일괄 실행."""
        if self._ready and self._pending_js:
            pending = list(self._pending_js)
            self._pending_js.clear()
            for js in pending:
                self.page().runJavaScript(js)

    def _check_engine_ready(self):
        """Leaflet map 객체 및 기본 레이어 그룹이 실제로 준비되었는지 확인."""
        if self._ready:
            return

        def _on_check(ready):
            if ready is True:
                self._ready = True
                self._flush_pending()
            else:
                # 아직 초기화 중이면 50ms 후 재확인
                QTimer.singleShot(50, self._check_engine_ready)

        self.page().runJavaScript("!!(window.mapReady && window.map && window.tracksGroup)", _on_check)

    # ---------------------------------------------- JS 실행 헬퍼
    def _run_js(self, js):
        """페이지 로드 전 호출은 큐에 보관 후 로드 완료 시 일괄 실행."""
        if self._ready:
            self.page().runJavaScript(js)
        else:
            self._pending_js.append(js)

    def _on_load_finished(self, ok):
        """QWebEngineView 로드 이벤트 완료 시 실제 DOM/Map 준비 확인."""
        self._check_engine_ready()

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
            "  if(window.tracks[k]){"
            "    try{window.tracksGroup.removeLayer(window.tracks[k]);}catch(e){}"
            "  }"
            "  var poly=L.polyline(%s,{color:%s,weight:2,opacity:0.75});"
            "  window.tracks[k]=poly;"
            "  window.tracksGroup.addLayer(poly);"
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
            "  if(window.tracks[k]){"
            "    try{window.tracksGroup.removeLayer(window.tracks[k]);}catch(e){}"
            "    delete window.tracks[k];"
            "  }"
            "  if(window.coverages[k]){"
            "    try{window.coveragesGroup.removeLayer(window.coverages[k]);}catch(e){}"
            "    delete window.coverages[k];"
            "  }"
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
            "      fillOpacity:0.85}).addTo(map);"
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

    # ---------------------------------------------- coverage (스와스폭)
    def add_coverage(self, idx, polygon, color="#3cb44b"):
        """polygon: [(lat, lon), ...] 외곽선. swath coverage 반투명 표시."""
        pts = self._clean(polygon)
        if len(pts) < 3:
            return False
        js = (
            "(function(){"
            "  var k=%s,c=%s;"
            "  if(window.coverages[k]){"
            "    try{window.coveragesGroup.removeLayer(window.coverages[k]);}catch(e){}"
            "  }"
            "  var poly=L.polygon(%s,{color:c,weight:1,fillColor:c,fillOpacity:0.18});"
            "  window.coverages[k]=poly;"
            "  window.coveragesGroup.addLayer(poly);"
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
            "      weight:1.5,fillColor:%s,fillOpacity:0.9});"
            "  m.bindTooltip(%s,{permanent:true,direction:'top',"
            "      offset:[0,-6],className:'contact-label'});"
            "  window.contactsGroup.addLayer(m);"
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
            "    try{window.contactsGroup.removeLayer(m);}catch(e){}"
            "  });"
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
            "  try{"
            "    var b=L.featureGroup(g).getBounds();"
            "    if(b.isValid()){map.fitBounds(b.pad(0.08));}"
            "  }catch(e){}"
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
            "  try{"
            "    var b=t.getBounds();"
            "    if(b.isValid()){map.fitBounds(b.pad(0.08));}"
            "  }catch(e){}"
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
            "  try{window.tracksGroup.clearLayers();}catch(e){}"
            "  try{window.coveragesGroup.clearLayers();}catch(e){}"
            "  try{window.contactsGroup.clearLayers();}catch(e){}"
            "  try{window.overlaysGroup.clearLayers();}catch(e){}"
            "  if(window.pingMarker){"
            "    try{map.removeLayer(window.pingMarker);}catch(e){}"
            "  }"
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
