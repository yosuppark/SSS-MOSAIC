# Sidescan Sonar (SSS) Waterfall Viewer & Mosaic Processing Suite

고해상도 사이드스캔 소나(Sidescan Sonar, SSS) 데이터의 시각화, 항적 및 방사보정, 토우피쉬 운동 효과 제거, 바탐 추적, 그리고 정밀 지리참조 GeoTIFF 모자익 생성을 지원하는 전문 소나 후처리 소프트웨어입니다.

---

## 📌 주요 기능 (Key Features)

### 1. 소나 데이터 로딩 및 고해상도 워터폴 (Waterfall Display)
* **다양한 소나 포맷 지원**: 표준 **XTF (eXtended Triton Format)** 및 **Lowrance SL2** 포맷 완벽 지원.
* **고성능 워터폴 뷰어**: 수만 핑(Ping)의 대용량 소나 데이터를 끊김 없이 렌더링 (좌우 채널 분할, 핑 번호/시각/거리 축 지원).
* **동적 게인 및 컬러맵**: Linear/Log 감마 조절, 8비트/16비트 정규화, 다양한 해양 탐사 컬러맵(Gray, Copper, Ocean 등) 지원.
* **인터랙티브 타겟/컨택 피킹 (Contact Picking)**: 해저 침적물, 인공어초, 파이프라인 등 이상체 탐지 시 마우스 우클릭으로 침적물 길이/그림자 높이 계측, 스냅샷 저장 및 CSV 로깅 자동화.

### 2. 정밀 바탐 추적 및 경사보정 (First Bottom Tracking & Slant Range Correction)
* **워터컬럼 vs 해저면 경계 자동 인식**: First Arrival 신호의 음파 반사 강도 및 구배(Gradient)를 분석하여 First Bottom을 정밀 추출.
* **사용자 가이드형 자동 추적 (Corridor Search)**: 사용자가 워터폴 화면에서 기준점을 클릭하면, 상하 검색 버퍼(Buffer)를 기반으로 전/후 핑의 해저면을 자동 추적.
* **조각별 선형 보간 (Piecewise Linear) 수동 편집**: 노이즈 구간이나 급격한 수심 변화 구간을 마우스 드래그/클릭으로 손쉽게 직관적 수동 보정.
* **사이드카(Sidecar) 파일 연동**: 편집된 바탐 수심은 원본 XTF를 손상하지 않고 `[파일명].bottom.csv` 사이드카 파일로 자동 보존 및 모자익 시 즉시 반영.

### 3. 방사보정 및 TVG (Radiometric AVG / ARC & TVG Correction)
* **해양음향학적 각도 산란 보정 (Angle Varied Gain / Angular Response Curve)**: Beaudoin et al. 연구에 기반한 음파 산란 각도 보정 알고리즘 적용.
* **센터 과노출(Nadir Flare) 억제 및 외곽 감쇄 보정**: 직하방(Nadir)의 강한 정반사(Specular Peak)를 균일하게 억제하고, 사거리 외곽(Far Range)의 감쇄를 평탄화.
* **Water Column 신호 보존**: First Bottom 이전의 수층(Water Column) 영역은 게인 왜곡 없이 본래 신호를 그대로 보존하여 워터컬럼 노이즈 증폭 차단.
* **실시간 토글 및 고속 캐싱**: 원시 이미지 캐싱을 통해 수십 MB의 소나 데이터도 지연 없이 0.1초 내에 실시간 ON/OFF 비교 가능.

### 4. 토우피쉬 운동 효과(피칭/롤링) 보정 (Motion Ripple Correction)
* **물결형 교차 밴딩(Ripple Pattern) 제거**: 해상 파도에 의해 발생하는 토우피쉬의 피칭(Pitch) 및 롤링(Roll)으로 인한 전후/좌우 대칭 명암 반복 패턴 복원.
* **대화형 ROI 지정 및 주파수 분석**: 워터폴 상에서 이상 패턴 구간을 마우스로 지정하고 공간 주파수 및 파라미터 자동 분석.
* **실시간 A/B 비교 다이얼로그**: 보정 전/보정 후 이미지를 1:1 슬라이더 및 전환 토글로 실시간 비교 검증 후 적용.

### 5. Fishbone 항적 및 헤딩 튜너 (Fishbone Track & Heading Tuner)
* **항적 보간 및 시계열 동기화**: 저주파수 GPS 수신 자료를 선형(Piecewise Linear) 및 고차 스플라인(Cubic Spline)으로 연속 시간선(Timeline)에 정밀 보간.
* **헤딩각(Heading) 평활화 및 조류(Crab Angle) 보정**: 급격한 선수 방위각 흔들림을 윈도우 필터링하여 소나 스캔 라인의 왜곡 제거.
* **보간 전/후 동시 도시 및 직관적 시각화**: 원본 항적과 보간 후 항적을 공간 좌표계 및 시계열(위도/경도/속도/헤딩) 차트에서 직관적으로 대조 검증.
* **항적 사이드카 연동**: `[파일명].nav.csv` 사이드카 파일로 저장되어 모자익 프로세서에 무결하게 전달.

### 6. 정밀 지리참조 GeoTIFF 모자익 빌더 (GeoTIFF Mosaic Builder)
* **실시간 Swath Coverage 계산**: 트랙별 소나 조사폭 및 면적(km², 헥타르)을 정확하게 산출.
* **경사거리(Slant Range) -> 수평거리(Ground Range) 기하학적 정규화**: 바탐 고도 정보를 기반으로 왜곡 없는 정밀 직사영 투영.
* **고해상도 GeoTIFF 출력**: WGS84 및 UTM 투영 좌표계 자동 매핑, 32비트 Float / 8비트 정규화, Alpha 마스크를 통한 다중 항적 자연스러운 블렌딩.
* **Leaflet 기반 인터랙티브 웹 지도 뷰어**: 생성된 모자익 래스터를 브라우저 및 내장 웹엔진 맵 상에 오버레이하여 현장 항적과 소나 영상을 동시 검토.

---

## 📂 폴더 구조 (Project Architecture)

```
c:\pywork\scan6\
├── main.py                          # 메인 애플리케이션 진입점 및 GUI 프레임워크
├── main.spec                        # PyInstaller 배포 빌드 스펙 (GDAL/PROJ 런타임 훅 연동)
├── waterfall_view.py                # 고성능 소나 워터폴 위젯, 바탐/리플 마우스 인터랙션
├── map_view.py                      # Leaflet 기반 항적 및 GeoTIFF 모자익 오버레이 지도
├── xtf_loader.py                    # XTF 파서, 메타데이터 파싱, AGC/바탐 사이드카 연동
├── bottom_loader.py                 # First Bottom 자동 추적 및 코리도 검색 알고리즘
├── mosaic_dialog.py                 # 모자익 생성 대화상자 (Swath 계산, 파라미터 제어)
├── mosaic_builder.py                # GeoTIFF 모자익 래스터라이징 엔진 (Slant-range 보정)
├── mosaic_min.py                    # 모자익 핵심 좌표 변환 및 버텍스 매핑
├── rthook_geo.py                    # 배포 환경 PROJ_LIB/GDAL_DATA 자동 탐색 런타임 훅
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
│   └── sl2_to_xtf.py                # Lowrance SL2 -> XTF 변환기
└── dist/
    └── main/                        # 배포용 독립 실행 폴더 (main.exe 포함)
```

---

## 🚀 설치 및 실행 방법 (Installation & Usage)

### 방법 1. 배포용 독립 실행 파일로 즉시 실행 (추천)
Python 환경이 설치되어 있지 않은 일반 PC에서도 즉시 실행할 수 있습니다.

1. 배포 패키지 [XTF_Mosaic_Viewer_Release.zip](file:///c:/pywork/scan6/dist/XTF_Mosaic_Viewer_Release.zip) 압축 해제
2. 폴더 내 [dist/main/main.exe](file:///c:/pywork/scan6/dist/main/main.exe) 실행

### 방법 2. Python 개발 환경에서 실행

#### 요구 사항
* Python 3.10 ~ 3.12 (64-bit 권장)
* GDAL / PROJ 환경 구성 (rasterio, pyproj)

#### 필수 패키지 설치
```bash
pip install -r requirements.lock.txt
```
*(주요 종속성: `PySide6`, `numpy`, `rasterio`, `pyproj`, `pyxtf`)*

#### 실행
```bash
python main.py
```

---

## 🛠️ 주요 워크플로우 가이드 (Step-by-Step Workflow)

### 1. 파일 열기 및 워터폴 확인
1. 상단 메뉴 `파일(F)` -> `열기` (`Ctrl+O`)를 눌러 `.xtf` 또는 `.sl2` 파일을 선택합니다.
2. 좌측 패널에 파일 목록과 트랙 요약(핑 수, 시작/종료 시각, 좌표 등)이 표시됩니다.
3. 중앙 워터폴 화면에 좌현(Port)과 우현(Starboard)의 음파 강도 영상이 실시간 렌더링됩니다.

### 2. First Bottom 바탐 추적 및 편집
1. 도구 모음에서 **[바탐 편집]** 모드를 활성화합니다.
2. 워터폴 화면에서 워터컬럼과 해저면의 첫 반사 경계(First Bottom) 지점을 클릭합니다.
3. 시스템이 클릭된 지점을 기준으로 상하 버퍼(코리도) 내에서 전/후 핑의 해저면을 자동 추적합니다.
4. 신호가 약하거나 튀는 구간은 직접 마우스 클릭으로 조각별 선형(Piecewise Linear)으로 보정합니다.
5. 저장을 누르면 `[파일명].bottom.csv`가 생성되어 영구 보존됩니다.

### 3. 방사보정(AVG/AGC) 적용
1. 상단 툴바의 **[AGC]** 체크박스를 활성화합니다.
2. Nadir 부근의 하얗게 타버린 정반사광이 제거되고, 외곽 신호가 균일하게 향상됩니다.
3. 워터컬럼 내부 신호는 보존되어 수층의 부유물 및 어군 관측이 가능합니다.

### 4. 토우피쉬 운동 리플(피칭/롤링) 제거
1. 워터폴 화면에서 파도 주기에 따라 명암이 교차 반복되는 구간을 확인합니다.
2. 상단 메뉴 `프로세싱` -> **[토우피쉬 운동 리플 보정]**을 선택합니다.
3. 화면에서 리플 패턴이 잘 나타난 관심 영역(ROI)을 드래그합니다.
4. 주기(Ping Period)와 감쇄 계수를 조정한 후 **[미리보기]** 또는 **[A/B 전환]**으로 결과를 비교합니다.
5. 적용을 클릭하여 스트라이프 노이즈가 제거된 깨끗한 영상을 획득합니다.

### 5. Fishbone 항적 및 헤딩 튜닝
1. 상단 메뉴 `프로세싱` -> **[Fishbone 항적/헤딩 튜너]**를 실행합니다.
2. 맵 상에서 원본 항적(녹색 점/선)과 보간 항적(적색 선)을 확인합니다.
3. 시간대별 위도, 경도, 속도, 헤딩각의 시계열 그래프를 점검하고 스무딩 윈도우 크기를 조절합니다.
4. **[적용]**을 누르면 `[파일명].nav.csv`가 자동 생성되어 모자익 생성 시 지리적 왜곡을 원천 방지합니다.

### 6. GeoTIFF 모자익 제작
1. 상단 메뉴 `모자익` -> **[모자익 생성기]**를 클릭합니다.
2. 참여할 소나 파일들을 선택하고 해상도(Cell Size, 예: 0.1m ~ 0.5m) 및 투영 좌표계(UTM)를 지정합니다.
3. 실시간 Swath Coverage(조사폭 및 면적)를 확인하고 **[빌드 시작]**을 누릅니다.
4. 생성이 완료되면 고해상도 GeoTIFF 래스터가 내장 맵 상에 즉시 오버레이되어 나타납니다.

---

## ⌨️ 단축키 및 마우스 조작 (Shortcuts & Controls)

| 기능 | 키 / 마우스 | 설명 |
| :--- | :--- | :--- |
| **파일 열기** | `Ctrl + O` | XTF / SL2 소나 데이터 열기 |
| **워터폴 확대/축소** | `Ctrl + 마우스 휠` | 시간축/거리축 확대 및 축소 |
| **화면 이동** | `마우스 휠 클릭 + 드래그` | 워터폴 뷰 패닝(Panning) |
| **이상체 피킹** | `마우스 우클릭 드래그` | 이상체 영역 선택 및 계측/저장 |
| **바탐 피킹** | `마우스 좌클릭` | 바탐 편집 모드에서 해저면 기준점 설정 |
| **AGC 토글** | `상단 툴바 체크` | 방사보정 실시간 적용/해제 |

---

## 📄 라이선스 및 기술 정보 (Technical Details)
* **GUI Framework**: PySide6 (Qt 6 for Python)
* **GIS / Remote Sensing Engine**: Rasterio, GDAL 3.x, PyProj (PROJ 9.x)
* **Numerical Core**: NumPy (Vectorized Acceleration)
* **Binary File Parser**: PyXTF, Lowrance SL2 Binary Parser
* **DirectWrite Font Optimization**: Windows 11 DirectWrite 호환 백터 폰트(Malgun Gothic) 렌더링 엔진 적용
