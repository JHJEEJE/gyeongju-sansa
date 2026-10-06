"""utils.py — 데이터 불러오기 · 거리 계산 · 사진 · 실제 도보 경로 · 그림 핀 · 지도 만들기

app.py(화면)에서 가져다 쓰는 도구 모음이에요.
- load_temples / load_courses / load_details / load_routes : data 폴더의 파일 읽기
- course_stats / leg_distances_m : 코스 거리·소요시간 (미리 계산한 실제 도보 거리 사용)
- walk_geometry      : 두 지점 사이 '실제로 걷는 길' 모양 받아오기 (OSRM 도보 길찾기)
- thumbs / hero_src  : 사찰 사진을 받아 작게 줄인 뒤 화면에 넣기
- pin_svg / pin_icon : 사찰 종류별 '그림 핀' (SVG로 직접 그림)
- build_overview_map / build_course_map : folium 지도 만들기 (모바일은 두 손가락으로 지도 이동)
"""
import base64
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import folium
import pandas as pd
import requests
import streamlit as st
from branca.element import MacroElement
from folium.elements import JSCSSMixin
from folium.plugins import LocateControl
from PIL import Image

BASE_DIR = Path(__file__).parent
CAMPUS_ID = "campus"

WALK_SPEED_KMH = 4.0   # 보통 걸음 속도
STAY_MIN = 15          # 사찰 한 곳에서 머무는 시간(분)
BUS_WAIT_MIN = 10      # 버스 기다리는 시간(분)
DIFF_LABEL = {1: "쉬움", 2: "보통", 3: "어려움"}
HTTP_HEADERS = {"User-Agent": "gyeongju-sansa-prototype/1.0 (Dongguk WISE student project)"}

# 사찰 종류별 핀 색깔과 핀 안에 그릴 그림
CATEGORY_STYLE = {
    "현존 사찰": {"color": "#B23A2B", "symbol": "temple"},
    "절터":      {"color": "#7A6A58", "symbol": "ruins"},
    "불상·석탑": {"color": "#C08A1E", "symbol": "pagoda"},
    "명소":      {"color": "#2E7D6B", "symbol": "mound"},
    "출발점":    {"color": "#2F5DA8", "symbol": "flag"},
}


# ---------------------------------------------------------------- 데이터
def _find(filename: str) -> Path | None:
    """data 폴더 안 → 앱 폴더 바로 아래 순서로 파일을 찾아요.
    (GitHub에 올릴 때 data 폴더가 빠져도 파일만 앱 옆에 있으면 동작)"""
    for p in (BASE_DIR / "data" / filename, BASE_DIR / filename):
        if p.exists():
            return p
    return None


def _must_find(filename: str) -> Path:
    p = _find(filename)
    if p is None:
        raise FileNotFoundError(
            f"{filename} 을(를) 찾을 수 없어요. GitHub 저장소에 {filename} 이 올라갔는지 확인하세요."
        )
    return p


def _read_json(filename: str, default):
    p = _find(filename)
    if p is None:                      # 없어도 앱은 돌아가게 (해당 기능만 숨김)
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_temples() -> pd.DataFrame:
    df = pd.read_csv(_must_find("temples.csv")).fillna("")
    for col in ("image_url", "image_caption"):   # 예전 CSV여도 오류 안 나게
        if col not in df.columns:
            df[col] = ""
    return df


def load_courses() -> list:
    with open(_must_find("courses.json"), encoding="utf-8") as f:
        return json.load(f)


def load_details() -> dict:
    """큰 사찰의 경내 볼거리 (details.json)."""
    return {k: v for k, v in _read_json("details.json", {}).items() if not k.startswith("_")}


def load_gallery() -> dict:
    """사찰별 추가 사진 목록 (photos.json) → {사찰ID: [{url, desc}, ...]}"""
    return {k: v for k, v in _read_json("photos.json", {}).items() if not k.startswith("_")}


def load_routes() -> dict:
    """코스 구간별 실제 도보 거리(m) (routes.json)."""
    return {k: v for k, v in _read_json("routes.json", {}).items() if not k.startswith("_")}


# ---------------------------------------------------------------- 거리 계산
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """두 위경도 사이의 직선거리(km). 지구를 반지름 6371km 공으로 보고 계산."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def leg_distances_m(course: dict, temples_by_id: pd.DataFrame, routes: dict) -> list:
    """구간마다 (거리m, 버스여부) 목록.
    routes.json에 실제 도보 거리가 있으면 그 값을, 없으면 직선거리 × path_factor 로 어림."""
    stops, bus = course["stops"], set(course.get("bus_legs", []))
    real = routes.get(course["id"], [])
    out = []
    for i in range(len(stops) - 1):
        a, b = temples_by_id.loc[stops[i]], temples_by_id.loc[stops[i + 1]]
        straight = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000
        if i in bus:
            out.append((straight, True))
        elif i < len(real) and real[i]:
            out.append((float(real[i]), False))
        else:
            out.append((straight * course.get("path_factor", 1.3), False))
    return out


def walk_minutes(meters: float, difficulty: int) -> int:
    m = meters / 1000 / WALK_SPEED_KMH * 60
    if difficulty == 3:                 # 산길은 30% 더 느리게
        m *= 1.3
    return int(round(m))


def course_stats(course: dict, temples_by_id: pd.DataFrame, routes: dict) -> dict:
    """코스의 도보거리·예상시간·들르는 곳 수."""
    legs = leg_distances_m(course, temples_by_id, routes)
    walk_m = sum(d for d, is_bus in legs if not is_bus)
    bus_m = sum(d for d, is_bus in legs if is_bus)
    n_sites = len({s for s in course["stops"] if s != CAMPUS_ID})
    minutes = (walk_minutes(walk_m, course["difficulty"])
               + bus_m / 1000 / 20 * 60 + BUS_WAIT_MIN * len(course.get("bus_legs", []))
               + STAY_MIN * n_sites)
    return {
        "walk_km": round(walk_m / 1000, 1),
        "bus_km": round(bus_m / 1000, 1),
        "minutes": int(round(minutes / 5) * 5),
        "n_sites": n_sites,
        "legs": legs,
    }


def fmt_minutes(m: int) -> str:
    h, mm = divmod(int(m), 60)
    return f"{h}시간 {mm}분" if h and mm else (f"{h}시간" if h else f"{mm}분")


# ---------------------------------------------------------------- 실제 도보 경로
OSRM_FOOT = "https://routing.openstreetmap.de/routed-foot/route/v1/foot/"


@st.cache_data(persist="disk", show_spinner=False)
def _walk_geometry_cached(lat1: float, lon1: float, lat2: float, lon2: float) -> list:
    """성공한 결과만 저장(캐시)해요. 실패하면 오류를 내서 저장되지 않게 → 다음에 다시 시도."""
    url = f"{OSRM_FOOT}{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
    r = requests.get(url, headers=HTTP_HEADERS, timeout=8)
    data = r.json()
    if data.get("code") != "Ok":
        raise ValueError(data.get("code"))
    coords = data["routes"][0]["geometry"]["coordinates"]   # [경도, 위도] 순서
    return [[lat, lon] for lon, lat in coords]


def walk_geometry(lat1: float, lon1: float, lat2: float, lon2: float):
    """두 지점 사이 실제 걷는 길의 좌표 목록 [[위도, 경도], ...].
    OpenStreetMap 길 데이터를 쓰는 무료 OSRM 도보 길찾기 서버에 물어봐요.
    한 번 받은 길은 저장해 두고, 실패하면 None (→ 지도에는 직선으로 표시)."""
    try:
        return _walk_geometry_cached(lat1, lon1, lat2, lon2)
    except Exception:
        return None


# ---------------------------------------------------------------- 사진
@st.cache_data(persist="disk", show_spinner=False)
def _download_resized(url: str, width: int) -> str:
    """사진을 내려받아 width 폭으로 줄인 JPEG를 data URI 문자열로.
    성공한 것만 저장(캐시)돼요. 실패하면 오류 → 다음에 다시 시도."""
    last = None
    for u in (url.replace("http://", "https://", 1), url):   # https 먼저, 안 되면 http
        try:
            r = requests.get(u, headers=HTTP_HEADERS, timeout=10)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail((width, width * 2))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=75, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            last = e
    raise RuntimeError(f"사진을 못 받았어요: {url} ({last})")


def _safe_download(url: str, width: int) -> str | None:
    if not url:
        return None
    try:
        return _download_resized(url, width)
    except Exception:
        return None


def thumbs(urls: tuple, width: int = 240) -> dict:
    """여러 장을 한꺼번에(동시에) 받아 작은 사진으로. {원본주소: data URI}
    서버가 대신 받아서 넣어 주니 폰에서는 빠르고, 원본 사이트 사정에 덜 흔들려요."""
    urls = [u for u in dict.fromkeys(urls) if u]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda u: _safe_download(u, width), urls))
    return {u: d for u, d in zip(urls, results) if d}


def hero_src(url: str) -> str | None:
    """상세 화면 맨 위 큰 사진용 (폭 760px)."""
    return _safe_download(url, 760)


def placeholder_uri(category: str) -> str:
    """사진이 없거나 못 불러올 때 대신 보여 줄 그림 (종류별 핀)."""
    svg = pin_svg(category, scale=1.2)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def photo_html(src: str | None, fallback_url: str, category: str, *,
               height: str = "180px", width: str = "100%", radius: str = "12px") -> str:
    """사진 상자 HTML. 순서: 서버가 받은 사진 → (실패 시) 원본 주소 직접 → (그것도 실패 시) 핀 그림.
    뒤에 핀 그림을 깔고 위에 사진을 올려서, 사진이 깨지면 자연스럽게 핀 그림이 보여요."""
    img_src = src or fallback_url
    img = (f"<img src='{img_src}' alt='' loading='lazy' referrerpolicy='no-referrer' "
           f"style='width:100%;height:100%;object-fit:cover;display:block'>") if img_src else ""
    return (
        f"<div class='photo' style='width:{width};height:{height};border-radius:{radius};"
        f"background:#EFE8DA url({placeholder_uri(category)}) center/auto 46% no-repeat;"
        f"overflow:hidden;flex:none'>{img}</div>"
    )


# ---------------------------------------------------------------- 그림 핀
# 34x44 크기 핀 안쪽(가운데 17,16)에 그리는 흰색 그림들
_SYMBOLS = {
    # 한옥 지붕 + 건물 (현존 사찰)
    "temple": (
        '<path d="M5.5 15.5 Q11 13.5 17 8 Q23 13.5 28.5 15.5 Z" fill="#fff"/>'
        '<rect x="9.5" y="15.5" width="15" height="7.5" fill="#fff"/>'
        '<rect x="15" y="17.8" width="4" height="5.2" fill="{c}"/>'
    ),
    # 주춧돌만 남은 절터
    "ruins": (
        '<rect x="8.5" y="12" width="4" height="10" rx="0.6" fill="#fff"/>'
        '<rect x="15" y="9" width="4" height="13" rx="0.6" fill="#fff"/>'
        '<rect x="21.5" y="14.5" width="4" height="7.5" rx="0.6" fill="#fff"/>'
        '<rect x="6.5" y="22" width="21" height="2.2" fill="#fff"/>'
    ),
    # 3층 석탑 (불상·석탑)
    "pagoda": (
        '<rect x="16.3" y="5" width="1.4" height="3.6" fill="#fff"/>'
        '<path d="M11.5 11 H22.5 L20.3 8.6 H13.7 Z" fill="#fff"/>'
        '<rect x="14.2" y="11" width="5.6" height="2.4" fill="#fff"/>'
        '<path d="M10.5 15.8 H23.5 L21.2 13.4 H12.8 Z" fill="#fff"/>'
        '<rect x="13.6" y="15.8" width="6.8" height="2.4" fill="#fff"/>'
        '<path d="M9.5 20.6 H24.5 L22.2 18.2 H11.8 Z" fill="#fff"/>'
        '<rect x="12" y="20.6" width="10" height="3" fill="#fff"/>'
    ),
    # 둥근 무덤·언덕 (명소)
    "mound": (
        '<path d="M6.5 22.5 Q17 3.5 27.5 22.5 Z" fill="#fff"/>'
        '<rect x="6" y="22.5" width="22" height="1.8" fill="#fff"/>'
    ),
    # 깃발 (출발점)
    "flag": (
        '<rect x="11" y="7" width="1.8" height="17" fill="#fff"/>'
        '<path d="M12.8 7.5 H25 L21.5 11.5 L25 15.5 H12.8 Z" fill="#fff"/>'
    ),
}


def pin_svg(category: str, number: int | None = None, scale: float = 1.0, grey: bool = False) -> str:
    """사찰 종류에 맞는 그림 핀 SVG 문자열. number를 주면 그림 대신 순서 번호를 넣어요."""
    style = CATEGORY_STYLE.get(category, CATEGORY_STYLE["명소"])
    color = "#B9B4AC" if grey else style["color"]
    if number is None:
        inner = _SYMBOLS[style["symbol"]].replace("{c}", color)
    else:
        inner = (
            '<circle cx="17" cy="16" r="10" fill="#fff"/>'
            f'<text x="17" y="20.5" text-anchor="middle" font-size="13" font-weight="700" '
            f'font-family="Arial, sans-serif" fill="{color}">{number}</text>'
        )
    w, h = int(34 * scale), int(44 * scale)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 34 44">'
        '<ellipse cx="17" cy="42.5" rx="6" ry="1.6" fill="#000" opacity="0.25"/>'
        f'<path d="M17 42 C17 42 3 27 3 16 A14 14 0 1 1 31 16 C31 27 17 42 17 42 Z" '
        f'fill="{color}" stroke="#fff" stroke-width="2"/>'
        f"{inner}</svg>"
    )


def pin_icon(category: str, number: int | None = None) -> folium.DivIcon:
    """folium 지도에 올릴 그림 핀. 핀 끝(아래 꼭짓점)이 정확한 위치를 가리키도록 anchor 설정."""
    return folium.DivIcon(
        html=pin_svg(category, number),
        icon_size=(34, 44),
        icon_anchor=(17, 42),
        class_name="temple-pin",
    )


# ---------------------------------------------------------------- 외부 지도 앱 링크
def _kname(row) -> str:
    return quote(str(row["name"]).split(" · ")[0].replace(",", " ").replace("/", " "))


def map_links(row) -> dict:
    """카카오·네이버·구글 지도로 바로 여는 링크 (템플맵처럼 3사 연결)."""
    short = str(row["name"]).split(" · ")[0]
    return {
        "카카오맵 길찾기": f"https://map.kakao.com/link/to/{_kname(row)},{row['lat']},{row['lon']}",
        "네이버지도": f"https://map.naver.com/p/search/{quote('경주 ' + short)}",
        "구글지도": f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}",
    }


def kakao_walk_link(a, b) -> str:
    """두 곳 사이 카카오맵 '도보' 길찾기 (실제 길 안내는 카카오맵 앱이 해 줘요)."""
    return (f"https://map.kakao.com/link/by/walk/{_kname(a)},{a['lat']},{a['lon']}"
            f"/{_kname(b)},{b['lat']},{b['lon']}")


# ---------------------------------------------------------------- 지도 만들기
class GestureHandling(JSCSSMixin, MacroElement):
    """모바일에서 한 손가락은 화면 스크롤, 두 손가락이어야 지도가 움직이게 하는 플러그인.
    (Leaflet.GestureHandling — 구글 지도 웹의 '두 손가락으로 지도 이동'과 같은 방식)"""
    default_js = [("leaflet_gesture_js",
                   "https://cdn.jsdelivr.net/npm/leaflet-gesture-handling@1.2.2/dist/leaflet-gesture-handling.min.js")]
    default_css = [("leaflet_gesture_css",
                    "https://cdn.jsdelivr.net/npm/leaflet-gesture-handling@1.2.2/dist/leaflet-gesture-handling.min.css")]


GESTURE_TEXT = {
    "touch": "지도를 움직이려면 두 손가락을 사용하세요",
    "scroll": "지도를 확대/축소하려면 Ctrl을 누른 채 스크롤하세요",
    "scrollMac": "지도를 확대/축소하려면 ⌘를 누른 채 스크롤하세요",
}


def base_map(center, zoom: int) -> folium.Map:
    """배경지도(타일) 3종을 깔아 둔 빈 지도. 오른쪽 위 버튼으로 바꿀 수 있어요."""
    m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True,
                   gestureHandling=True, gestureHandlingOptions={"text": GESTURE_TEXT, "duration": 1500})
    GestureHandling().add_to(m)
    # show=False: 처음 열 때는 '기본 지도'만 보이게 (나머지는 오른쪽 위 버튼으로 전환)
    folium.TileLayer("OpenStreetMap", name="기본 지도").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Topo Map",
        name="지형 지도 (산길 확인용)",
        show=False,
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="위성 사진",
        show=False,
    ).add_to(m)
    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    LocateControl(strings={"title": "내 위치 보기"}).add_to(m)
    # 모바일에서 지도 하단 저작권 표시가 너무 커지지 않도록 글자 크기 줄이기
    m.get_root().header.add_child(folium.Element(
        "<style>.leaflet-control-attribution{font-size:9px!important;line-height:1.2}</style>"
    ))
    return m


def _popup_html(row, thumb: str | None = None) -> str:
    img_src = thumb or row.get("image_url", "")
    img = (f"<img src='{img_src}' referrerpolicy='no-referrer' "
           f"style='width:100%;height:96px;object-fit:cover;border-radius:6px;margin-bottom:6px'>"
           if img_src else "")
    return (
        f"<div style='font-family:sans-serif;width:180px'>{img}"
        f"<b style='font-size:14px'>{row['name']}</b><br>"
        f"<span style='color:#777;font-size:12px'>{row['hanja']} · {row['category']}</span></div>"
    )


def build_overview_map(temples: pd.DataFrame, thumb_map: dict | None = None) -> folium.Map:
    """전체 사찰 지도: 캠퍼스 기준 도보권 원 + 모든 사찰 그림 핀."""
    thumb_map = thumb_map or {}
    campus = temples.set_index("id").loc[CAMPUS_ID]
    m = base_map(center=[35.847, 129.210], zoom=13)

    # 캠퍼스에서 걸어서 약 30분 / 60분 거리 (직선 1.5km / 3km)
    for radius in (1500, 3000):
        folium.Circle(
            location=[campus.lat, campus.lon], radius=radius,
            color="#2F5DA8", weight=1.5, dash_array="6 6",
            fill=True, fill_opacity=0.04, interactive=False,
        ).add_to(m)

    for _, row in temples.iterrows():
        folium.Marker(
            location=[row["lat"], row["lon"]],
            icon=pin_icon(row["category"]),
            tooltip=row["name"],                 # 핀을 누르면 이 이름으로 어떤 사찰인지 알아내요
            popup=folium.Popup(_popup_html(row, thumb_map.get(row["image_url"])), max_width=200),
        ).add_to(m)
    return m


def build_course_map(course: dict, temples_by_id: pd.DataFrame, thumb_map: dict | None = None):
    """코스 지도: 순서 번호 핀 + 실제 도보 경로(버스 구간은 점선).
    돌려주는 값: (지도, 실제 길을 못 받아와 직선으로 그린 구간 수)"""
    thumb_map = thumb_map or {}
    pts = [temples_by_id.loc[s] for s in course["stops"]]
    color = course.get("color", "#B23A2B")
    m = base_map(center=[pts[0].lat, pts[0].lon], zoom=14)
    all_latlon = [[p.lat, p.lon] for p in pts]
    n_fallback = 0

    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if i in course.get("bus_legs", []):
            folium.PolyLine([[a.lat, a.lon], [b.lat, b.lon]], color=color, weight=3,
                            opacity=0.7, dash_array="2 8", tooltip="🚌 버스 이동").add_to(m)
            continue
        geom = walk_geometry(float(a.lat), float(a.lon), float(b.lat), float(b.lon))
        if geom:
            # 흰 테두리를 먼저 깔고 그 위에 색 선 → 어떤 배경지도에서도 잘 보이게
            folium.PolyLine(geom, color="#FFFFFF", weight=8, opacity=0.9).add_to(m)
            folium.PolyLine(geom, color=color, weight=5, opacity=0.95,
                            tooltip=f"🚶 {i + 1}→{i + 2} 구간").add_to(m)
            all_latlon += geom
        else:
            n_fallback += 1
            folium.PolyLine([[a.lat, a.lon], [b.lat, b.lon]], color=color, weight=3,
                            opacity=0.6, dash_array="10 6", tooltip="직선 표시 (길 정보 없음)").add_to(m)

    seen, order = set(), 0
    for row in pts:
        if row["id"] in seen:          # 원점 회귀 코스는 출발점 핀을 한 번만
            continue
        seen.add(row["id"])
        order += 1
        folium.Marker(
            location=[row.lat, row.lon],
            icon=pin_icon(row["category"], number=order),
            tooltip=f"{order}. {row['name']}",
            popup=folium.Popup(_popup_html(row, thumb_map.get(row["image_url"])), max_width=200),
        ).add_to(m)

    lats = [p[0] for p in all_latlon]
    lons = [p[1] for p in all_latlon]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]],
                 padding_top_left=(30, 55), padding_bottom_right=(30, 15))  # 핀 높이만큼 위쪽 여백
    return m, n_fallback
