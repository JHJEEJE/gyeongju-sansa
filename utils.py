"""utils.py — 데이터 불러오기 · 거리 계산 · 그림 핀 · 지도 만들기

app.py(화면)에서 가져다 쓰는 도구 모음이에요.
- load_temples / load_courses : data 폴더의 CSV·JSON 읽기
- haversine_km / course_stats  : 위경도로 거리·소요시간 계산
- pin_svg / pin_icon           : 사찰 종류별 '그림 핀' (SVG로 직접 그림)
- build_overview_map / build_course_map : folium 지도 만들기
"""
import json
import math
from pathlib import Path
from urllib.parse import quote

import folium
import pandas as pd
from folium.plugins import LocateControl

BASE_DIR = Path(__file__).parent
CAMPUS_ID = "campus"

WALK_SPEED_KMH = 4.0   # 보통 걸음 속도
STAY_MIN = 15          # 사찰 한 곳에서 머무는 시간(분)
DIFF_LABEL = {1: "쉬움", 2: "보통", 3: "어려움"}

# 사찰 종류별 핀 색깔과 핀 안에 그릴 그림
CATEGORY_STYLE = {
    "현존 사찰": {"color": "#B23A2B", "symbol": "temple"},
    "절터":      {"color": "#7A6A58", "symbol": "ruins"},
    "불상·석탑": {"color": "#C08A1E", "symbol": "pagoda"},
    "명소":      {"color": "#2E7D6B", "symbol": "mound"},
    "출발점":    {"color": "#2F5DA8", "symbol": "flag"},
}


# ---------------------------------------------------------------- 데이터
def load_temples() -> pd.DataFrame:
    return pd.read_csv(BASE_DIR / "data" / "temples.csv").fillna("")


def load_courses() -> list:
    with open(BASE_DIR / "data" / "courses.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 거리 계산
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """두 위경도 사이의 직선거리(km). 지구를 반지름 6371km 공으로 보고 계산."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def course_stats(course: dict, temples_by_id: pd.DataFrame) -> dict:
    """코스의 도보거리·버스거리·예상시간·들르는 곳 수 계산.

    직선거리에 path_factor(보통 1.3)를 곱해 실제 걷는 길이를 어림해요.
    (직선으로 걸을 수는 없으니 30% 정도 더 걷는다고 보는 것)
    """
    stops = course["stops"]
    walk_km, bus_km = 0.0, 0.0
    for i in range(len(stops) - 1):
        a, b = temples_by_id.loc[stops[i]], temples_by_id.loc[stops[i + 1]]
        d = haversine_km(a.lat, a.lon, b.lat, b.lon)
        if i in course.get("bus_legs", []):
            bus_km += d
        else:
            walk_km += d * course.get("path_factor", 1.3)

    n_sites = len({s for s in stops if s != CAMPUS_ID})
    walk_min = walk_km / WALK_SPEED_KMH * 60
    if course["difficulty"] == 3:      # 산길은 30% 더 느리게
        walk_min *= 1.3
    bus_min = bus_km / 20 * 60 + 10 * len(course.get("bus_legs", []))  # 시내버스 20km/h + 대기 10분
    minutes = walk_min + bus_min + STAY_MIN * n_sites
    return {
        "walk_km": round(walk_km, 1),
        "bus_km": round(bus_km, 1),
        "minutes": int(round(minutes / 5) * 5),
        "n_sites": n_sites,
    }


def fmt_minutes(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{h}시간 {mm}분" if h and mm else (f"{h}시간" if h else f"{mm}분")


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
def map_links(row) -> dict:
    """카카오·네이버·구글 지도로 바로 여는 링크 (템플맵처럼 3사 연결)."""
    short = str(row["name"]).split(" · ")[0]
    kakao_name = quote(short.replace(",", " "))
    return {
        "카카오맵 길찾기": f"https://map.kakao.com/link/to/{kakao_name},{row['lat']},{row['lon']}",
        "네이버지도": f"https://map.naver.com/p/search/{quote('경주 ' + short)}",
        "구글지도": f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}",
    }


# ---------------------------------------------------------------- 지도 만들기
def base_map(center, zoom: int) -> folium.Map:
    """배경지도(타일) 3종을 깔아 둔 빈 지도. 오른쪽 위 버튼으로 바꿀 수 있어요."""
    m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True)
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


def _popup_html(row) -> str:
    return (
        f"<div style='font-family:sans-serif;min-width:150px'>"
        f"<b style='font-size:14px'>{row['name']}</b><br>"
        f"<span style='color:#777;font-size:12px'>{row['hanja']} · {row['category']}</span><br>"
        f"<span style='font-size:12px'>아래에 자세한 이야기가 나와요 👇</span></div>"
    )


def build_overview_map(temples: pd.DataFrame) -> folium.Map:
    """전체 사찰 지도: 캠퍼스 기준 도보권 원 + 모든 사찰 그림 핀."""
    campus = temples.set_index("id").loc[CAMPUS_ID]
    m = base_map(center=[35.847, 129.210], zoom=13)

    # 캠퍼스에서 걸어서 약 30분 / 60분 거리 (직선 1.5km / 3km)
    for radius, label in [(1500, "도보 약 30분"), (3000, "도보 약 60분")]:
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
            popup=folium.Popup(_popup_html(row), max_width=240),
        ).add_to(m)
    return m


def build_course_map(course: dict, temples_by_id: pd.DataFrame) -> folium.Map:
    """코스 지도: 순서 번호 핀 + 이동 경로 선(버스 구간은 점선)."""
    pts = [temples_by_id.loc[s] for s in course["stops"]]
    m = base_map(center=[pts[0].lat, pts[0].lon], zoom=14)

    for i in range(len(pts) - 1):
        is_bus = i in course.get("bus_legs", [])
        folium.PolyLine(
            [[pts[i].lat, pts[i].lon], [pts[i + 1].lat, pts[i + 1].lon]],
            color=course.get("color", "#B23A2B"),
            weight=4 if not is_bus else 3,
            opacity=0.85,
            dash_array="8 8" if is_bus else None,
            tooltip="🚌 버스 이동" if is_bus else None,
        ).add_to(m)

    seen = set()
    order = 0
    for row in pts:
        if row["id"] in seen:          # 원점 회귀 코스는 출발점 핀을 한 번만
            continue
        seen.add(row["id"])
        order += 1
        folium.Marker(
            location=[row.lat, row.lon],
            icon=pin_icon(row["category"], number=order),
            tooltip=row["name"],
            popup=folium.Popup(_popup_html(row), max_width=240),
        ).add_to(m)

    lats = [p.lat for p in pts]
    lons = [p.lon for p in pts]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]],
                 padding_top_left=(30, 55), padding_bottom_right=(30, 15))  # 핀 높이만큼 위쪽 여백
    return m
