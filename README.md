# 올림픽공원 공연 후 출구별 출차 나우캐스트

**팀 뛰뛰빵빵** (이대훈·김성주) · 2026 한국체육산업개발(주) 공공데이터 활용 경진대회 출품작

공연 시작 30분 뒤 **실시간 주차현황**(동2·남2·북2문 합계) 한 번으로, 공연이 끝난 뒤 남2문·동2문·북2문으로 나갈 **공연 관람 차량 수**와 80% 범위를 예측한다. 출차가 몰리기 약 2.5~3시간 전에 운영자(인력 배치)와 관람객(출구 선택)에게 알린다.

- 서비스: https://wkqhd5095.github.io/olympic-park-exit-nowcast/
- 서버·DB·로그인 없음. 정적 페이지 + 브라우저 계산 → **운영비 0원**, 개인정보 수집 없음

## 성능 (2025년, 출구별 공연 차량 출차량 평균절대오차)
| 검증 | 출구별 평소값(베이스라인) | 본 서비스 | 감소 (공연 단위 부트스트랩 95% 구간) |
|---|---|---|---|
| 시간순: 1~6월 학습 → 7~12월 평가 (공연 10개, 81 출구-일) | 98.1대 | 62.6대 | 36% (15~48%) |
| 공연 하나씩 빼고 검증 (공연 41개, 282 출구-일) | 95.4대 | 70.3대 | 26% (14~37%) |

80% 예측 구간 적중률: 시간순 85%, 교차검증 78%. 한계는 화면의 "한계" 절에 그대로 적었다.

## 저장소 구조
```
docs/                    GitHub Pages로 배포되는 화면 (index.html, app.js, style.css, data/*.json)
scripts/build_data.py    분석 결과 → docs/data/*.json
analysis/data_collect/   데이터 수집·정제 (문화공공데이터광장 API, 공연 캘린더, 공연 차량 곡선)
analysis/nowcast/        나우캐스트 학습·검증·민감도, 동결 모델(model_frozen.json), 당일 예측 기록
analysis/queue_check/    '출구에 줄이 서는가' 검정과 포화 사례 통계
```

## 화면이 쓰는 데이터
| 데이터 | 공공데이터 ID | 쓰임 |
|---|---|---|
| 올림픽공원 입출차정보(전체구역) | 15159093 (포털 LINK형 → 문화공공데이터광장 API_CPM_008) | 2025년 학습·검증 (중복 제거 후 2,303,876건). 2025년 입력 S는 이 기록으로 재구성 |
| 실시간 주차현황 | 15168686 | 당일 입력. [kspo-parking-logger](https://github.com/wkqhd5095/kspo-parking-logger)가 5분마다 기록(11/11 시작, 11/13부터 상시) → 화면이 그 기록(raw CSV)을 직접 읽음 |
| 올림픽공원 차량입출차·공연·날씨 연계 데이터 | 15164822 (문화공공데이터광장 API_CPM_009) | 관람객 수·재차대수 검산 |
| 기관 홈페이지 공연 일정 | — | 시작 시각·러닝타임 (`docs/data/shows.json`) |

## 운영·갱신 방법
1. **공연 일정 추가**: `docs/data/shows.json`에 날짜·공연명·시작 시각·러닝타임을 한 줄 추가 (GitHub 웹 편집기로 가능)
2. **연 1회 모델 갱신**: 새 연도 입출차 데이터로 `analysis/` 스크립트를 다시 돌린 뒤 `python scripts/build_data.py <팀 작업 폴더>`
3. 화면은 별도 빌드가 없다. main 브랜치에 올리면 Pages가 바로 반영한다.

## 재현 (팀 작업 폴더 구조 기준)
분석 스크립트는 팀 작업 폴더(`03_데이터`, `04_분석`, `05_개발/sim`) 배치를 가정한다. 원자료(약 2.4M행)는 용량·출처 정책상 저장소에 넣지 않았다.
```
python analysis/data_collect/collect_kcisa.py collect cpm008   # 인증키는 .env (저장소에 올리지 않음)
python analysis/data_collect/prep.py                           # 정제·공연 차량 곡선
python analysis/nowcast/nowcast.py && python analysis/nowcast/reduction_ci.py
python scripts/build_data.py <팀 작업 폴더>
```
필요 패키지: pandas, numpy, matplotlib (화면 자체는 의존성 없음)

## 라이선스
- 코드: MIT (LICENSE)
- 데이터: 한국체육산업개발(주) 제공 공공데이터, 공공누리 출처표시 조건을 따른다.
