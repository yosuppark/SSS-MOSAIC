# XTF Processor V1.0 사용자 설명서 (Sidescan Sonar & Norbit MBES Backscatter Suite)

본 문서는 사이드스캔 소나(SSS) 및 **Norbit MBES 백스캐터(Backscatter)** 데이터의 시각화, First Bottom 추적, 방사보정(AVG/AGC), 토우피쉬 운동 효과 제거, Fishbone 항적/헤딩 보정, 그리고 GeoTIFF 모자익 제작까지 전체 작업 공정을 설명하는 사용자 설명서입니다.

---

## 📌 목차 (Table of Contents)
1. **시스템 개요 및 주요 특징**
2. **프로그램 시작 및 환경 요구사항**
3. **Norbit MBES 백스캐터 데이터 로드 및 처리**
4. **First Bottom 추적 및 경사거리(Slant-Range) 보정**
5. **방사보정(Radiometric AVG / ARC) 및 TVG 프로세싱**
6. **토우피쉬 운동 효과(피칭/롤링 리플) 제거**
7. **Fishbone 항적 및 헤딩(Heading) 튜너**
8. **정밀 GeoTIFF 백스캐터 모자익 제작**
9. **문제 해결 및 FAQ**
10. **구글 닥스(Google Docs)에서 본 매뉴얼 활용 방법**

---

## 제1장. 시스템 개요 및 주요 특징
XTF Processor V1.0은 해양 물리탐사, 수중 구조물 진단, 침몰선/장애물 탐색, 해저 지질 조사 분야에서 널리 활용되는 사이드스캔 소나(Sidescan Sonar) 및 **Norbit MBES 백스캐터 데이터**를 전문적으로 후처리하고 맵 모자익을 생성하는 통합 시스템입니다.

* **데이터 포맷 지원**: 표준 XTF 및 Lowrance SL2 소나 데이터 지원
* **Norbit MBES 백스캐터 지원**: Norbit iWBMS, WINGHEAD 등 멀티빔 음향측심기 XTF export 완벽 대응 (단일 Swath, Port/Stbd, 멀티채널, Bathy 패킷 자동 해석)
* **워터폴 디스플레이**: 대용량 소나 신호를 지연 없이 탐색할 수 있는 GPU 가속 및 메모리 맵핑 뷰어
* **First Bottom 고도 보정**: 해저면 경계 신호 자동 코리도(Corridor) 추적 및 조각별 선형 보간 수동 편집
* **방사보정 (AVG/AGC)**: Beaudoin et al. 연구 기반 각도별 음향 산란 보정 및 Water Column 신호 보존
* **운동 효과 보정**: 해상 파도에 따른 토우피쉬 피칭/롤링 리플 무늬 주파수 분석 및 복원 (실시간 A/B 비교)
* **Fishbone 항적 튜너**: GPS 오차 보정, 고차 스플라인 항적 보간, 헤딩 평활화 및 시계열 동시 비교
* **GeoTIFF 모자익**: 실시간 Swath Coverage(조사폭) 산출 및 Alpha 블렌딩 적용 고해상도 백스캐터 GeoTIFF 래스터 출력

---

## 제2장. 프로그램 시작 및 환경 요구사항

### 2.1 배포용 독립 실행 파일(XTF_Processor_V1.0.exe) 실행
1. 배포 패키지 `dist/XTF_Processor_V1.0_Release.zip`을 작업 폴더에 압축 해제합니다.
2. 폴더 내 `XTF_Processor_V1.0/XTF_Processor_V1.0.exe`를 더블 클릭하여 실행합니다.
3. 별도의 Python 환경 설치 없이 타 PC에서도 즉시 구동됩니다.

---

## 제3장. Norbit MBES 백스캐터 데이터 로드 및 처리

Norbit MBES(Multibeam Echosounder) 시스템(iWBMS, WINGHEAD 등)에서 export한 XTF 파일의 특징과 처리 절차:
1. **단일 Swath Composite 채널**: 좌우 전체가 1개의 cross-track 백스캐터 라인으로 저장된 경우 중앙을 기준으로 좌현(Port)과 우현(Starboard)으로 자동 분할 정렬.
2. **Bathy + Backscatter 멀티채널**: 수심(Bathy) 채널을 자동 식별하여 배제하고 순수 음향 반사 강도(Port/Stbd) 채널만을 스마트 매핑.
3. **XTF_HEADER_BATHY (2) 및 Snippet 패킷**: 소나 패킷이 아닌 Bathymetry 패킷으로 저장된 멀티빔 XTF 파일도 자동 감지하여 채널 데이터 추출.
4. **음수 데시벨(dB) 정규화**: -60dB ~ 0dB 등 다양한 동적 범위의 음향 반사 강도를 0~255 그레이스케일로 선명하게 정규화.

---

## 제4장. 정밀 GeoTIFF 모자익 제작 (Backscatter Mosaic Builder)

1. 상단 메뉴 `모자익` -> `모자익 생성기`를 클릭합니다.
2. 참여할 소나 파일(SSS 및 Norbit MBES XTF)들을 체크 선택합니다.
3. 실시간으로 계산되는 총 Swath Coverage(실제 조사 면적 및 트랙폭)를 확인합니다.
4. 출력 해상도(Cell Size: 0.1m, 0.25m, 0.5m 등)와 좌표계(WGS84 / UTM Zone)를 지정합니다.
5. `빌드 시작`을 누르면 실시간 진행률 표시와 함께 GeoTIFF 및 투명 마스크(Alpha)가 생성됩니다.
6. 완료 시 우측 지도 뷰에 백스캐터 모자익 래스터가 Leaflet 타일 형태로 자동 로드됩니다.

---

## 제5장. 구글 닥스(Google Docs)에서 본 매뉴얼 활용 방법

본 매뉴얼 파일(`USER_MANUAL.docx`)은 구글 닥스(Google Docs)와 100% 호환되도록 표준 OpenXML 규격으로 제작되었습니다.

1. 웹 브라우저를 열고 Google Drive(https://drive.google.com)에 로그인합니다.
2. 좌측 상단의 `[+ 새로 만들기]` -> `[파일 업로드]`를 클릭하여 `USER_MANUAL.docx` 파일을 업로드합니다.
3. 업로드된 파일을 더블 클릭하면 Google Docs 뷰어로 즉시 열립니다.
4. 상단 메뉴에서 `[Google Docs로 열기]`를 클릭하면 모든 서식, 표, 제목 스타일이 유지된 상태로 영구 보관 및 실시간 공동 작업이 가능합니다.
