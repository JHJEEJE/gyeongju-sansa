# 경주 산사 순례길 — 앱 프로토타입

수학여행 코스 말고, 지금 나에게 맞는 경주 사찰 한 곳 (Streamlit + folium, 파이썬만 사용)

사찰 QR을 찍으면 동행 · 남은 시간 · 이동수단에 맞춰 **지금 갈 수 있는 다음 사찰 한 곳**을 골라 주고,
가는 길에 들을 이야기를 들려줘요. 안 가겠다고 하면 이유를 모아서 "사람들이 왜 다음 곳에 안 가는지"를 데이터로 확인해요.

## 폴더 구성
```
gyeongju_sansa_app/
├─ app.py            # 화면 (다음 한 곳 / 지도 / 코스 / 사진 / 스탬프 / QR 방문 페이지)
├─ ai_guide.py       # ✨ 다음 한 곳: 조건 파악 → 거리·날씨 계산 → 추천 → 이야기 → '안 갈래요' 기록
├─ utils.py          # 데이터 읽기, 거리 계산, 그림 핀(SVG), 지도 만들기
├─ community.py      # 사진 올리기 · 모두의 사진 · 스탬프판 · 앱 안 QR 만들기
├─ storage.py        # 이용자 사진 저장 (기본: 앱 폴더 / 설정 시: Supabase 영구 보관)
├─ make_qr.py        # 사찰별 QR 안내판 PNG 만들기
├─ packages.txt      # Streamlit Cloud에 한글 글꼴 설치 (QR 안내판 글자용)
├─ requirements.txt  # 필요한 라이브러리 (배포할 때 자동 설치)
├─ data/
│  ├─ temples.csv    # 사찰·유적 35곳 (위경도 + 이야기 + 영문 한 줄 + 대표 사진 주소)
│  ├─ courses.json   # 순례 코스 9개 (학생 7 + 외국인 1 + 불자 1)
│  ├─ details.json   # 큰 사찰 경내 볼거리 (불국사·석굴암·분황사 등 9곳, 관람 순서대로)
│  ├─ photos.json    # 사찰별 사진 여러 장 (상세 화면 사진 줄)
│  └─ routes.json    # 코스 구간별 실제 도보 거리(m)
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

## 3. QR 만들기 (앱 안에서)
앱의 **📿 스탬프 탭 → 🖨️ QR 안내판 만들기** 에서 사찰을 고르면 이 앱 주소로 QR 안내판이 만들어지고,
버튼으로 PNG를 내려받을 수 있어요. (코드 실행 필요 없음)

컴퓨터에서 한꺼번에 만들고 싶으면:
```bash
python make_qr.py https://○○○.streamlit.app
```
`qr/` 폴더에 사찰별 안내판 PNG가 생겨요. 인쇄해서 부착하면
폰 카메라로 찍기 → 해당 사찰 소개 화면이 열리고 → 스탬프가 자동으로 찍혀요.
(QR 안에는 `주소/?temple=사찰ID` 링크만 들어 있어요)

QR 없이 발표할 때는 앱의 **스탬프 탭 → '발표·테스트용'** 에서 QR을 찍은 것처럼 열어볼 수 있어요.

## ✨ 다음 한 곳 (AI 순례 동행)
- QR을 찍으면 스탬프 바로 아래에 **'여기서 다음 한 곳'**이 나와요. 앱 첫 탭에서는 지금 있는 곳을 직접 골라요.
- 누구랑 · 남은 시간 · 이동 버튼 3개만 누르면 끝. "애들이랑 2시간 있어요, 차 없어요"처럼 한 마디로 써도 버튼이 알아서 바뀌어요.
- 추천 방법 (`ai_guide.py` 의 `recommend`)
  1. 남은 시간 안에 다녀올 수 없는 곳, 1시간 넘게 걸어야 하는 곳은 빼요.
  2. 가까울수록, 방금 본 곳과 종류가 다를수록(현존 사찰 → 절터 등) 점수가 높아요.
  3. 아이·어르신과 함께면 산길(남산·토함산, '어려움' 코스)은 크게 감점, 비 오면 박물관 가점.
  4. 불국사·석굴암은 살짝 감점 (다들 이미 가는 곳 → 수학여행 코스 탈출).
- **'이번엔 안 갈래요'** 이유(시간 · 이동 · 흥미 · 동행 · 기타)는 `next_feedback.json`에 모여요.
  📿 스탬프 탭 → **📊 '안 갈래요' 이유 모아 보기**에서 장벽별 비율을 볼 수 있어요. (저장 장소는 사진과 같음)
- 날씨: Open-Meteo(키 필요 없음). 못 받아오면 날씨 없이 추천해요.
- 버스 시간은 아직 실제 노선이 아니라 거리로 어림한 값이에요 → 버스 도착정보 API로 바꿀 자리.

### LLM 연결 (선택)
키가 없어도 모든 기능이 **규칙 기반**으로 돌아가요. 키를 넣으면 ① 한 마디 알아듣기 ② 두 사찰을 잇는 이야기를 LLM이 맡아요.
이야기는 `temples.csv`의 사찰 소개글 두 개 안에서만 만들게 해서 없는 사실을 지어내지 않게 했어요.
```
# Streamlit Secrets (또는 .streamlit/secrets.toml)
ANTHROPIC_API_KEY = "sk-ant-..."
LLM_MODEL = "claude-haiku-5-5"   # 생략 가능
```

## 사진 공유 · 인증 스탬프
- QR을 찍으면 그 사찰 화면 맨 위에 **사진 올리기**가 나와요. 사진 + 닉네임을 넣고 '인증하고 스탬프 받기'를 누르면
  그 사진 위에 빨간 인증 도장이 찍혀 **📿 스탬프** 탭에 모여요 (닉네임으로 확인).
- 사찰 상세 화면의 **➕ 이 곳 사진 올리기**로 인증 없이 사진만 공유할 수도 있어요.
- 올라온 사진은 모두 **📸 사진** 탭에서 다 같이 봐요. 공식 사진이 없는 곳(석장사지 등)은 올라온 최신 사진이 대표 사진이 돼요.
- 저장 장소: 기본은 앱 폴더(`user_uploads/`). Streamlit Cloud에서는 앱이 다시 시작되면 사라져요.
  계속 남기려면 Supabase(무료)를 연결하세요 → 아래 'Supabase 연결'.

### Supabase 연결 (사진 영구 보관, 선택)
1. supabase.com 가입 → New project
2. 왼쪽 **Storage → New bucket** → 이름 `photos` → 만들기
3. **Project Settings → API**에서 `Project URL`과 `service_role` 키 복사
4. Streamlit Cloud 앱 화면 **Manage app → ⋮ → Settings → Secrets**에 붙여넣고 Save
   ```
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "복사한 service_role 키"
   ```
5. 앱이 다시 켜지면 '📸 사진' 탭 맨 아래 저장 장소가 'Supabase (영구 보관)'로 바뀌어요.

## 4. 자주 고칠 곳
| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 사찰 추가·설명 수정 | `data/temples.csv` 한 줄 추가 (id는 영어 소문자) |
| 사진 바꾸기·추가 | `temples.csv`의 `image_url`(대표 사진), `photos.json`(추가 사진)에 사진 주소 넣기 |
| 경내 볼거리 추가 | `data/details.json` |
| 코스 추가·순서 변경 | `data/courses.json` 의 `stops` 목록 |
| 핀 색·그림 | `utils.py` 의 `CATEGORY_STYLE`, `_SYMBOLS` |
| 걷는 속도·머무는 시간 | `utils.py` 의 `WALK_SPEED_KMH`, `STAY_MIN` |
| 배경지도 종류 | `utils.py` 의 `base_map()` |

## 실제 걷는 길 · 사진은 어떻게 나오나
- 코스 지도의 굵은 선: 앱이 실행될 때 OSRM 도보 길찾기 서버(routing.openstreetmap.de, OpenStreetMap 길 데이터)에서 받아와요. 못 받으면 점선(직선)으로 표시돼요.
- 사진: 국가유산청 국가유산 이미지 OpenAPI의 공식 사진을 서버가 받아 작게 줄여서 보여 줘요. 석장사지·캠퍼스는 공식 사진이 없어 그림 핀으로 나와요.
- 둘 다 한 번 받으면 저장돼서 다음부터는 빨라요.

## 좌표 출처
- 사찰·유적 좌표: 국가유산청 국가유산 OpenAPI (`longitude`, `latitude`, WGS84)
- 캠퍼스: OpenStreetMap 장소 좌표
- 석장사지: 공식 좌표가 없어 주소(석장동 산81-2) 기준 추정 → 현장 확인 또는 카카오 지오코딩으로 보정 필요
