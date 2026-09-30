# 경주 산사 순례길 — 앱 프로토타입

동국대 WISE캠퍼스 학생·교직원을 위한 경주 사찰 순례 앱 (Streamlit + folium, 파이썬만 사용)

## 폴더 구성
```
gyeongju_sansa_app/
├─ app.py            # 화면 (지도 / 코스 / 스탬프 / QR 방문 페이지)
├─ utils.py          # 데이터 읽기, 거리 계산, 그림 핀(SVG), 지도 만들기
├─ make_qr.py        # 사찰별 QR 안내판 PNG 만들기
├─ requirements.txt  # 필요한 라이브러리 (배포할 때 자동 설치)
├─ data/
│  ├─ temples.csv    # 사찰·유적 35곳 (위경도 + 이야기 + 영문 한 줄)
│  └─ courses.json   # 순례 코스 9개 (학생 7 + 외국인 1 + 불자 1)
├─ notebooks/
│  └─ 01_사찰위치_전처리_colab.ipynb   # 위치 데이터 수집·변환 과정 (코랩)
└─ qr_sample/        # QR 안내판 예시 (예시 주소라 배포 후 다시 만들어야 함)
```

## 1. 내 컴퓨터에서 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```
브라우저에서 http://localhost:8501 이 열려요.
폰으로 보려면 같은 와이파이에서 터미널에 나온 `Network URL`로 접속하세요.

## 2. 인터넷에 올리기 (무료, Streamlit Community Cloud)
1. 이 폴더를 GitHub 저장소에 올리기
2. https://share.streamlit.io → **New app** → 저장소 선택 → Main file: `app.py` → Deploy
3. `https://○○○.streamlit.app` 같은 주소가 생김

## 3. QR 만들기
```bash
python make_qr.py https://○○○.streamlit.app
```
`qr/` 폴더에 사찰별 안내판 PNG가 생겨요. 인쇄해서 부착하면
폰 카메라로 찍기 → 해당 사찰 소개 화면이 열리고 → 스탬프가 자동으로 찍혀요.
(QR 안에는 `주소/?temple=사찰ID` 링크만 들어 있어요)

QR 없이 발표할 때는 앱의 **스탬프 탭 → '발표·테스트용'** 에서 QR을 찍은 것처럼 열어볼 수 있어요.

## 4. 자주 고칠 곳
| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 사찰 추가·설명 수정 | `data/temples.csv` 한 줄 추가 (id는 영어 소문자) |
| 코스 추가·순서 변경 | `data/courses.json` 의 `stops` 목록 |
| 핀 색·그림 | `utils.py` 의 `CATEGORY_STYLE`, `_SYMBOLS` |
| 걷는 속도·머무는 시간 | `utils.py` 의 `WALK_SPEED_KMH`, `STAY_MIN` |
| 배경지도 종류 | `utils.py` 의 `base_map()` |

## 좌표 출처
- 사찰·유적 좌표: 국가유산청 국가유산 OpenAPI (`longitude`, `latitude`, WGS84)
- 캠퍼스: OpenStreetMap 장소 좌표
- 석장사지: 공식 좌표가 없어 주소(석장동 산81-2) 기준 추정 → 현장 확인 또는 카카오 지오코딩으로 보정 필요
