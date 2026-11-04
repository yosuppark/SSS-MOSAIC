# XTF Processor V1.0 (Sidescan Sonar & Norbit MBES Backscatter Mosaic Suite)

고해상도 사이드스캔 소나(Sidescan Sonar, SSS) 및 **Norbit 멀티빔(MBES: iWBMS, WINGHEAD 등) 백스캐터(Backscatter) 데이터**의 시각화, 항적/방사보정, 토우피쉬 운동 효과 제거, 바탐 추적, 그리고 정밀 지리참조 GeoTIFF 모자익 생성을 지원하는 전문 소나 후처리 소프트웨어입니다.

---

## 📌 주요 기능 (Key Features)

### 1. 다양한 소나 및 멀티빔 XTF 포맷 완벽 지원
* **표준 사이드스캔 소나 (SSS)**: EdgeTech, Klein 등 전형적인 Dual-channel XTF 포맷 지원.
* **Norbit MBES Backscatter XTF Export 지원**:
  * Norbit iWBMS, WINGHEAD 등 최신 멀티빔 음향측심기에서 내보낸 XTF 포맷 완벽 대응.
  * **스마트 채널 매핑**: 단일 합성 채널(Full-swath composite cross-track), 2채널(Port/Stbd), 3채널 이상(Bathy + Backscatter/Snippet) 자동 감지 및 분리.
  * **다양한 패킷 지원**: `XTF_HEADER_SONAR (0)`, `XTF_HEADER_BATHY (2)`, `XTF_HEADER_BATHY_SNIPPET (19)`, `XTF_HEADER_RESON_7125 (61/62)` 자동 해석.
  * **음수 데시벨(dB) 백스캐터 정규화**: -60dB ~ 0dB 등 다양한 동적 범위의 음향 반사 강도를 0~255 그레이스케일로 선명하게 정규화.
* **Lowrance SL2 포맷 지원**: 해양 레저 및 소형 조사선 SL2 바이너리 포맷 연동.

### 2. 정밀 백스캐터 GeoTIFF 모자익 빌더 (Backscatter Mosaic Builder)
* **MBES & SSS 통합 모자익**: 멀티빔 백스캐터 및 사이드스캔 데이터를 단일 지리참조 GeoTIFF 영상으로 합성.
* **경사거리(Slant Range) -> 수평거리(Ground Range) 정규화**: 수심 및 바탐 고도 정보를 기반으로 왜곡 없는 정밀 직사영 투영.
* **실시간 Swath Coverage 계산**: 트랙별 소나 조사폭 및 면적(km², 헥타르) 실시간 산출.
* **고해상도 GeoTIFF 출력**: WGS84 및 UTM 투영 좌표계 자동 매핑, Alpha 마스크를 통한 다중 조사선 자연스러운 블렌딩.
* **Leaflet 기반 대화형 웹 지도 뷰어**: 생성된 백스캐터 모자익 래스터를 지도 위에 즉시 오버레이하여 현장 항적과 영상 정밀 대조.

### 3. 고성능 워터폴 디스플레이 (Waterfall Display)
* **대용량 소나 신호 무지연 렌더링**: 수만 핑(Ping)의 고해상도 음파 데이터를 부드럽게 스크롤 및 줌.
* **동적 게인 및 컬러맵**: Linear/Log 감마 조절, 8비트/16비트 정규화, 다양한 해양 탐사 컬러맵(Gray, Copper, Ocean 등) 지원.
* **인터랙티브 타겟/컨택 피킹 (Contact Picking)**: 해저 이상체, 인공어초, 침몰선 탐지 시 마우스 우클릭으로 크기/그림자 계측 및 CSV 자동 로깅.

### 4. 정밀 바탐 추적 및 경사보정 (First Bottom Tracking)
* **워터컬럼 vs 해저면 경계 자동 인식**: First Arrival 음파 반사 강도 및 구배(Gradient) 분석으로 First Bottom 정밀 추출.
* **사용자 가이드형 자동 추적 (Corridor Search)**: 사용자가 기준점을 클릭하면 상하 검색 버퍼(코리도) 내에서 전/후 핑을 지능적 자동 추적.
* **조각별 선형 보간 (Piecewise Linear) 수동 편집**: 노이즈 구간을 마우스 클릭으로 직관적 보정.
* **사이드카(Sidecar) 연동**: `[파일명].bottom.csv` 파일로 자동 보존되어 모자익 시 영구 반영.

### 5. 방사보정 및 TVG (Radiometric AVG / ARC & TVG Correction)
* **해양음향학적 각도 산란 보정**: Beaudoin et al. 연구에 기반한 각도 반응 곡선(ARC) 알고리즘 적용.
* **센터 과노출(Nadir Flare) 억제 및 외곽 감쇄 보정**: 직하방 정반사 피크 억제 및 사거리 외곽 평탄화.
* **Water Column 신호 보존**: First Bottom 이전 수층 영역은 본래 신호를 그대로 보존하여 워터컬럼 노이즈 증폭 차단.

### 6. 토우피쉬 운동 효과(피칭/롤링) 제거 (Motion Ripple Correction)
* **물결형 교차 밴딩(Ripple Pattern) 제거**: 파도에 의한 피칭/롤링으로 발생하는 전후/좌우 대칭 명암 반복 패턴 복원.
* **대화형 ROI 지정 및 주파수 분석**: 이상 패턴 구간을 마우스로 지정하고 공간 주파수 및 파라미터 자동 분석.
* **실시간 A/B 비교 다이얼로그**: 보정 전/후 영상을 1:1 슬라이더 및 전환 토글로 실시간 비교 검증.

### 7. Fishbone 항적 및 헤딩 튜너 (Fishbone Track & Heading Tuner)
* **항적 보간 및 시계열 동기화**: 저주파수 GPS 수신 자료를 3차 스플라인(Cubic Spline)으로 연속 시간선에 정밀 보간.
* **헤딩각(Heading) 평활화**: 급격한 선수 방위각 흔들림을 윈도우 필터링하여 스캔 라인 왜곡 제거.
* **사이드카 연동**: `[파일명].nav.csv` 파일로 자동 저장되어 모자익 프로세서에 무결하게 전달.

---

### 9. Humminbird & Lowrance 소나 포맷 변환 및 모자이킹 지원 (PINGMapper / PINGVerter 참조)
* **다양한 소나 하드웨어 로그 완벽 지원**:
  * **Lowrance 소나**: `.SL2` (144B 프레임), `.SL3` (168B 프레임), `.SLG` 바이너리 프레임 파싱. Navico Mercator 투영 좌표를 WGS84 위경도로 무손실 변환.
  * **Humminbird 소나**: `.DAT` 메타데이터, `.SON` (`B002.SON`=Port, `B003.SON`=Starboard), `.IDX` 파일 자동 페어링 및 동기화. Humminbird 전용 좌표계 변환 수식 적용.
* **직접 파일 열기 (`Ctrl+O`) 지원**:
  * 파일 열기 대화상자에서 `.xtf`뿐만 아니라 `.sl2`, `.sl3`, `.slg`, `.dat`, `.son` 파일을 직접 선택 가능.
  * 선택 시 표준 2-채널 XTF 파일로 자동 변환되어 워터폴 디스플레이, 바탐 추적, 방사보정, 리플 보정 및 GeoTIFF 모자이킹까지 즉시 사용 가능.
* **소나 파일 포맷 변환기 GUI (`Ctrl+T` / 도구 메뉴)**:
  * 다중 소나 파일 및 폴더 일괄 탐색 추가 (`+ 폴더 탐색 추가`).
  * 소나 파일 형식, 파일 크기, 핑(Ping) 수, 유효성 신속 자동 검사(Probe).
  * 동일 폴더 또는 사용자 지정 출력 폴더 선택 및 실시간 진행률/로그 창 제공.
  * "변환 완료 후 메인 작업창에 자동 로드" 옵션을 통해 원클릭 모자이킹 워크플로우 완성.

### 8. 완전 오프라인(Offline) 환경 100% 지원 (선박/해상 인터넷 차단 환경 완벽 대응)
* **외부 네트워크 의존성 제로(0)**: 기존 외부 Leaflet CDN(`unpkg.com`) 의존성을 완전히 제거하고, 독립형 Leaflet 엔진(CSS, JS, 마커 Data-URI)을 로컬에 완벽 내장.
* **해양 항법 다크 그리드 (Nautical Marine Grid)**: 인터넷이 전혀 없는 선박 현장에서도 세련된 다크 블루 해양 좌표 그리드 배경이 즉시 표출되어 시각적 명확성 제공.
* **모든 지도 기능 100% 정상 작동**:
  * 조사선 항적(Track Polylines)
  * 소나 스와스폭(Swath Coverage Polygons)
  * 실시간 핑 호버 위치 원형 마커
  * 해저 타겟/이상체(Contact Markers & Tooltips)
  * 백스캐터 GeoTIFF 모자익 오버레이
* **온라인/오프라인 하이브리드 지원**: 인터넷 연결 시 OpenStreetMap(OSM) 타일이 자동 활성화되며, 우측 상단 레이어 컨트롤에서 개별 레이어 및 OSM 배경 지도를 자유롭게 온/오프 가능.


---

## 📂 폴더 구조 (Project Architecture)

```
c:\pywork\scan6\
├── main.py                          # XTF Processor V1.0 메인 진입점 및 GUI
├── main.spec                        # PyInstaller XTF_Processor_V1.0 배포 빌드 스펙
├── xtf_loader.py                    # SSS 및 Norbit MBES 백스캐터 XTF 스마트 로더
├── waterfall_view.py                # 고성능 소나 워터폴 위젯, 바탐/리플 마우스 인터랙션
├── map_view.py                      # Leaflet 기반 항적 및 GeoTIFF 모자익 오버레이 지도
├── bottom_loader.py                 # First Bottom 자동 추적 및 코리도 검색 알고리즘
├── mosaic_dialog.py                 # 모자익 생성 대화상자 (Swath 계산, 파라미터 제어)
├── mosaic_builder.py                # GeoTIFF 백스캐터 모자익 래스터라이징 엔진
├── mosaic_min.py                    # 모자익 핵심 좌표 변환 및 버텍스 매핑
├── rthook_geo.py                    # PROJ_LIB/GDAL_DATA 자동 탐색 런타임 훅
├── processing/                      # 소나 전문 후처리 패키지
│   ├── __init__.py
│   ├── agc.py                       # 방사보정 (AVG/ARC & TVG) 알고리즘
│   ├── bottom_io.py                 # First Bottom (.bottom.csv) 입출력 관리자
│   ├── contacts.py                  # 타겟/이상체 (Contacts) 계측 및 CSV 로깅
│   ├── fishbone_dialog.py           # Fishbone 항적/헤딩 튜너 대화상자
│   ├── motion_ripple.py             # 피칭/롤링 리플 분석 및 복원 코어 알고리즘
│   ├── motion_ripple_dialog.py      # 리플 보정 파라미터 튜닝 및 A/B 비교 UI
│   ├── nav_dialog.py                # 항적 보간 설정 UI
│   ├── nav_interpolator.py          # 고정밀 항적 보간 및 헤딩 평활화 엔진
│   ├── nav_io.py                    # 항적 (.nav.csv) 사이드카 입출력
│   ├── sl2_to_xtf.py                # Lowrance SL2 -> XTF 레거시 변환기
│   ├── sonar_converter.py           # Humminbird / Lowrance 범용 소나 파서 & XTF 변환 코어
│   └── converter_dialog.py          # 소나 포맷 변환기 GUI 다이얼로그 (배치 변환/로그)
└── dist/
    └── XTF_Processor_V1.0/          # 배포용 독립 실행 폴더 (XTF_Processor_V1.0.exe 포함)
```

---

## 🚀 설치 및 실행 방법 (Installation & Usage)

### 방법 1. 배포용 독립 실행 파일로 즉시 실행 (권장)
Python 및 GIS 환경이 설치되어 있지 않은 일반 PC에서도 즉시 실행할 수 있습니다.

1. 배포 패키지 [XTF_Processor_V1.0_Release.zip](file:///c:/pywork/scan6/dist/XTF_Processor_V1.0_Release.zip) 압축 해제
2. 폴더 내 [XTF_Processor_V1.0/XTF_Processor_V1.0.exe](file:///c:/pywork/scan6/dist/XTF_Processor_V1.0/XTF_Processor_V1.0.exe) 실행

### 방법 2. Python 개발 환경에서 실행
```bash
pip install -r requirements.lock.txt
python main.py
```

---

## 🛠️ Humminbird / Lowrance 소나 파일 변환 및 모자이킹 워크플로우

### 방법 A. 파일 열기(`Ctrl+O`)로 원클릭 즉시 열기 및 모자이킹
1. 상단 메뉴 `파일(F)` -> `열기` (`Ctrl+O`) 클릭
2. 파일 형식 필터에서 `모든 지원 소나 파일 (*.xtf *.sl2 *.sl3 *.slg *.dat *.son)` 선택 후 원하는 소나 파일 선택
3. 프로그램이 자동으로 XTF로 변환 후 워터폴 및 지도 화면에 즉시 로드
4. `Ctrl+M`을 눌러 고해상도 백스캐터 GeoTIFF 모자익 생성!

### 방법 B. 소나 파일 변환기(`Ctrl+T`)를 통한 일괄 배치 변환
1. 상단 메뉴 `도구(T)` -> `소나 파일 포맷 변환기...` (또는 `Ctrl+T`) 실행
2. `[➕ 파일 추가...]` 또는 `[📁 폴더 탐색 추가...]`를 눌러 Lowrance(`.sl2`, `.sl3`, `.slg`) 또는 Humminbird(`.dat`, `.son`) 파일 추가
3. 출력 폴더 및 `[x] 변환 완료 후 메인 작업창에 자동 로드` 옵션 확인
4. `[🚀 변환 시작]` 버튼 클릭 ➔ 변환 완료 후 작업창에 자동 로드 및 모자이킹 준비 완료!

---

## 🛠️ Norbit MBES 백스캐터 모자익 생성 워크플로우

1. **Norbit XTF 파일 로드**:
   * 상단 메뉴 `파일(F)` -> `열기` (`Ctrl+O`)를 눌러 Norbit MBES에서 export한 `.xtf` 파일을 선택합니다.
   * 시스템이 Norbit 백스캐터 채널(단일 Swath, Port/Stbd, 멀티채널) 및 패킷(`sonar`, `bathy` 등)을 자동 식별하여 워터폴 화면에 표시합니다.
2. **First Bottom / 수심 확인**:
   * 멀티빔 음측기의 경우 수심/고도계 정보가 메타데이터에 자동 반영됩니다.
   * 필요 시 **[바탐 편집]** 모드를 활성화하여 해저면 기준점을 미세 조정합니다.
3. **방사보정(AVG/AGC)**:
   * 상단 툴바의 **[AGC]**를 체크하여 입사각에 따른 음파 강도 불균일을 평탄화합니다.
4. **GeoTIFF 모자익 제작**:
   * 상단 메뉴 `모자익` -> **[모자익 생성기]**를 클릭합니다.
   * 출력 해상도(예: 0.2m ~ 0.5m) 및 목표 좌표계(EPSG:32652 등)를 설정하고 **[빌드 시작]**을 누릅니다.
   * 생성이 완료되면 고해상도 백스캐터 래스터(`_mosaic.tif`)가 우측 지도 화면에 즉시 로드됩니다.

---

## 📄 기술 사양
* **애플리케이션 명칭**: XTF Processor V1.0
* **실행 바이너리**: `XTF_Processor_V1.0.exe`
* **GUI Framework**: PySide6 (Qt 6 for Python)
* **GIS / Remote Sensing**: Rasterio, GDAL 3.x, PyProj (PROJ 9.x)
* **Numerical Core**: NumPy (Vectorized Acceleration)
* **Sonar / MBES Parsers**: PyXTF (Patched for MBES), Lowrance SL2 Binary Parser
