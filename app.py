"""경주 산사 순례길 — 앱 프로토타입 (Streamlit + folium)

실행:  streamlit run app.py
화면 구성 (터치 3번 이내 원칙)
  ① 사찰 지도  : 그림 핀을 누르면(1번) 바로 아래에 사진 + 사찰 이야기
  ② 순례 코스  : 코스 탭(1) → '코스 보기'(2) → 실제 도보 경로 지도 + 사진 타임라인
  ③ 사진      : 이용자들이 올린 사찰 사진을 모두가 같이 봐요
  ④ 스탬프    : 닉네임으로 내 스탬프 모아 보기 (인증 사진 위에 빨간 도장)
  QR 방문     : 사찰에 붙인 QR을 폰 카메라로 찍으면 ?temple=사찰ID 로 열리고,
                그 자리에서 사진을 올리면 스탬프가 돼요.
모바일: 한 손가락으로는 화면이 스크롤되고, 지도는 두 손가락으로 움직여요.
"""
import json

import streamlit as st
from streamlit_folium import st_folium

from community import (
    feed_view, inject_css, qr_maker, stamp_board_view, stamp_upload_section, user_hero_uri, visitor_photos,
)
from utils import (
    CAMPUS_ID, CATEGORY_STYLE, DIFF_LABEL,
    build_course_map, build_overview_map, course_stats, fmt_minutes, hero_src,
    kakao_walk_link, load_courses, load_details, load_gallery, load_routes, load_temples,
    map_links, photo_html, pin_svg, thumbs, walk_minutes,
)

st.set_page_config(page_title="경주 산사 순례길", page_icon="🪷", layout="centered")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 3rem;}
    .chip {display:inline-block; padding:2px 9px; margin:0 4px 4px 0; border-radius:999px;
           font-size:0.78rem; background:#F3EEE4; color:#5A4A36; border:1px solid #E5DCCB;}
    .chip.diff1 {background:#E6F2EA; color:#2E7D5B; border-color:#CBE3D3;}
    .chip.diff2 {background:#FFF3DC; color:#8A5A00; border-color:#F2DDB0;}
    .chip.diff3 {background:#FBE4E1; color:#A12F22; border-color:#F0C6C0;}
    .route {font-size:0.84rem; color:#6B6257; line-height:1.5; margin-top:2px;}
    .legend span {display:inline-flex; align-items:center; margin-right:10px; font-size:0.8rem; color:#6B6257;}
    .legend svg {margin-right:3px;}
    .maplinks {display:flex; gap:6px; margin:6px 0 10px;}
    .maplink {flex:1; text-align:center; padding:8px 4px; border:1px solid #DDD5C6; border-radius:8px;
              font-size:0.85rem; text-decoration:none !important; color:#333 !important; background:#fff;}
    /* 코스 카드의 사진 줄 */
    .strip {display:flex; gap:6px; overflow-x:auto; padding-bottom:4px; margin:6px 0 8px;}
    .strip .cell {position:relative; flex:none;}
    .strip .no {position:absolute; left:4px; top:4px; background:rgba(0,0,0,.6); color:#fff;
                font-size:11px; font-weight:700; border-radius:999px; padding:0 6px; line-height:18px;}
    .card-title {font-size:1.05rem; font-weight:700; color:inherit;}
    .card-sub {font-size:0.88rem; opacity:.75; margin-bottom:4px;}
    /* 코스 상세의 사진 타임라인 */
    .stop {display:flex; gap:12px; align-items:stretch; background:#FFFDF8; border:1px solid #E8E0D0;
           border-radius:14px; padding:8px; color:#2B2118;}
    .stop-body {min-width:0; display:flex; flex-direction:column; justify-content:center;}
    .stop-head {display:flex; align-items:center; gap:6px; flex-wrap:wrap;}
    .stop-no {width:22px; height:22px; border-radius:50%; color:#fff; font-size:12px; font-weight:700;
              display:inline-flex; align-items:center; justify-content:center; flex:none;}
    .stop-name {font-weight:700; font-size:0.98rem;}
    .stop-cat {font-size:0.72rem; color:#7A6A58;}
    .stop-sum {font-size:0.84rem; color:#5A4E42; line-height:1.45; margin-top:3px;}
    .leg {display:flex; align-items:center; gap:8px; margin:2px 0 2px 26px; padding:6px 0 6px 16px;
          border-left:3px dotted #CBBFA8; font-size:0.84rem; color:#6B6257;}
    .leg a {margin-left:auto; font-size:0.8rem; padding:3px 9px; border:1px solid #DDD5C6; border-radius:999px;
            text-decoration:none !important; color:#3B2A1A !important; background:#fff; white-space:nowrap;}
    /* 큰 사찰 경내 볼거리 */
    .item {display:flex; gap:10px; padding:8px 0; border-bottom:1px solid #EEE6D8;}
    .item:last-child {border-bottom:none;}
    .item-no {font-weight:700; color:#B23A2B; width:18px; flex:none; padding-top:2px;}
    .item-body {min-width:0; font-size:0.86rem; line-height:1.5;}
    .item-name {font-weight:700; font-size:0.93rem;}
    .badge {display:inline-block; font-size:0.68rem; padding:0 6px; border-radius:4px; margin-left:4px;
            background:#7A2E22; color:#fff; vertical-align:2px;}
    .credit {font-size:0.72rem; opacity:.6; margin:4px 0 8px;}
    .gallery {margin-top:0;}
    .gcell {flex:none; width:128px;}
    .gcap {font-size:0.72rem; opacity:.7; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
    </style>
    """,
    unsafe_allow_html=True,
)

inject_css()                                     # 사진 칸 · 도장 모양

# ---------------------------------------------------------------- 데이터 준비
temples = load_temples()
T = temples.set_index("id", drop=False)          # T.loc["bunhwangsa"] 처럼 ID로 바로 찾기
courses = load_courses()
COURSE_BY_ID = {c["id"]: c for c in courses}
DETAILS = load_details()                         # 큰 사찰 경내 볼거리
ROUTES = load_routes()                           # 코스 구간별 실제 도보 거리
GALLERY = load_gallery()                         # 사찰별 사진 여러 장
STAMP_TARGETS = temples[temples["id"] != CAMPUS_ID]   # QR을 붙일 곳 = 캠퍼스 제외 전부

# 사진: 사찰 대표 사진 + 경내 볼거리 사진을 한 번에 받아 작게 줄여 둠 (처음 한 번만 느림)
ALL_IMAGES = tuple(temples["image_url"].tolist()
                   + [it.get("img", "") for d in DETAILS.values() for it in d.get("items", [])])
with st.spinner("사진 불러오는 중…"):
    THUMB = thumbs(ALL_IMAGES, 240)

VIEWS = ["🗺️ 지도", "🚶 코스", "📸 사진", "📿 스탬프"]


# ---------------------------------------------------------------- 화면 이동 함수
def open_course(course_id: str):
    st.session_state["view"] = VIEWS[1]
    st.session_state["selected_course"] = course_id
    st.query_params.clear()


def close_course():
    st.session_state["selected_course"] = None


def go_home():
    st.query_params.clear()


# ---------------------------------------------------------------- 공통 UI 조각
def chips_html(*items) -> str:
    return "".join(f"<span class='chip {cls}'>{text}</span>" for text, cls in items)


def chips(*items):
    st.markdown(chips_html(*items), unsafe_allow_html=True)


def legend():
    parts = "".join(f"<span>{pin_svg(cat, scale=0.5)}{cat}</span>" for cat in CATEGORY_STYLE)
    st.markdown(f"<div class='legend'>{parts}</div>", unsafe_allow_html=True)


def short_name(row) -> str:
    return str(row["name"]).split(" · ")[0]


def first_sentence(text: str, limit: int = 70) -> str:
    """이야기의 첫 문장만 (타임라인 요약용)."""
    text = str(text)
    i = text.find("요.")
    if 0 < i < limit + 30:
        return text[: i + 2]
    return text[:limit].rstrip() + "…"


def thumb(row_or_url, category: str, **size) -> str:
    url = row_or_url if isinstance(row_or_url, str) else row_or_url["image_url"]
    return photo_html(THUMB.get(url), url, category, **size)


def render_inside(temple_id: str):
    """큰 사찰의 경내 볼거리 (관람 순서대로)."""
    d = DETAILS.get(temple_id)
    if not d:
        return
    st.markdown(f"**🏯 경내 둘러보기** · {d.get('time', '')}")
    if d.get("intro"):
        st.caption(d["intro"])
    rows = []
    for n, it in enumerate(d["items"], 1):
        badge = f"<span class='badge'>{it['badge']}</span>" if it.get("badge") else ""
        pic = thumb(it.get("img", ""), "불상·석탑", height="64px", width="84px", radius="8px")
        rows.append(
            f"<div class='item'><div class='item-no'>{n}</div>{pic}"
            f"<div class='item-body'><div class='item-name'>{it['name']}{badge}</div>{it['desc']}</div></div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


def render_gallery(row):
    """대표 사진 아래 옆으로 넘겨 보는 사진 줄 (photos.json). 열었을 때만 받아와요."""
    extra = [p for p in GALLERY.get(row["id"], []) if p["url"] != row["image_url"]]
    if not extra:
        return
    small = thumbs(tuple(p["url"] for p in extra), 240)
    cells = "".join(
        f"<div class='gcell'>{photo_html(small.get(p['url']), p['url'], row['category'], height='92px', width='128px', radius='8px')}"
        f"<div class='gcap'>{p['desc']}</div></div>"
        for p in extra
    )
    st.markdown(f"<div class='strip gallery'>{cells}</div>", unsafe_allow_html=True)


def render_temple_detail(row, show_courses: bool = True, show_hero: bool = True):
    """템플맵 상세 페이지처럼: 사진 · 이름(한자) · 종류 · 주소 · 문화유산 · 이야기 · 경내 볼거리 · 지도 3사 연결."""
    if show_hero:
        src = hero_src(row["image_url"]) if row["image_url"] else user_hero_uri(row["id"])
        st.markdown(photo_html(src or THUMB.get(row["image_url"]), row["image_url"], row["category"],
                               height="210px"), unsafe_allow_html=True)
        if row["image_caption"]:
            st.markdown(f"<div class='credit'>사진: 국가유산청 · {row['image_caption']}</div>",
                        unsafe_allow_html=True)
        render_gallery(row)
    st.markdown(
        f"<h3 style='margin-bottom:0'>{pin_svg(row['category'], scale=0.7)} {row['name']} "
        f"<span style='font-size:0.7em;opacity:.6;font-weight:400'>{row['hanja']}</span></h3>",
        unsafe_allow_html=True,
    )
    chips((row["category"], ""), (row["era"], ""))
    st.caption(f"📍 {row['address']}")

    if row["heritage"]:
        st.markdown("**🏛️ 문화유산**")
        st.markdown("\n".join(f"- {h}" for h in str(row["heritage"]).split(" · ")))

    st.markdown("**📖 이야기**")
    st.write(row["story"])
    if row["tip"]:
        st.caption(f"💡 {row['tip']}")
    if row["desc_en"]:
        st.caption(f"🌏 *{row['desc_en']}*")

    render_inside(row["id"])

    if show_hero:                       # 지도에서 연 상세 · QR 화면에서만 (코스 타임라인은 가볍게)
        visitor_photos(row)

    links = "".join(
        f"<a class='maplink' href='{url}' target='_blank'>{label}</a>"
        for label, url in map_links(row).items()
    )
    st.markdown(f"<div class='maplinks'>{links}</div>", unsafe_allow_html=True)

    if "추정" in str(row["coord_source"]):
        st.caption("⚠️ 이 곳은 공식 좌표가 없어 주소로 추정한 위치예요. (현장 확인 후 보정 예정)")

    if show_courses:
        related = [c for c in courses if row["id"] in c["stops"]]
        if related:
            st.markdown("**🚶 이 곳을 지나는 코스**")
            for c in related:
                st.button(
                    f"{c['title']} — {c['subtitle']}", key=f"rel_{row['id']}_{c['id']}",
                    on_click=open_course, args=(c["id"],), use_container_width=True,
                )


# ---------------------------------------------------------------- 코스 정렬 (누를 때마다 ↑↓ 전환)
SORT_WORDS = {("거리", "asc"): "가까운 순", ("거리", "desc"): "먼 순",
              ("난이도", "asc"): "쉬운 순", ("난이도", "desc"): "어려운 순"}
ss = st.session_state
if "sort_key" not in ss:
    ss["sort_key"], ss["sort_dir"] = "거리", "asc"


def on_sort(picked: str):
    if picked == ss["sort_key"]:                        # 같은 버튼을 또 누름 → 방향만 뒤집기
        ss["sort_dir"] = "desc" if ss["sort_dir"] == "asc" else "asc"
    else:                                               # 다른 기준을 누름 → 그 기준 오름차순
        ss["sort_key"], ss["sort_dir"] = picked, "asc"


def sort_label(k: str) -> str:
    if k != ss["sort_key"]:
        return f"{k} ↕"
    return f"{k} {'↑' if ss['sort_dir'] == 'asc' else '↓'}"


# ================================================================ ① QR로 들어온 경우
qr_id = st.query_params.get("temple")
if qr_id and qr_id in T.index:
    row = T.loc[qr_id]
    stamp_upload_section(row)                              # 인증 사진 올리기 → 도장 찍힌 스탬프
    st.divider()
    render_temple_detail(row)
    st.divider()
    st.button("🏠 앱 홈으로", on_click=go_home, use_container_width=True)
    st.stop()

# ================================================================ 메인 화면
st.markdown("## 🪷 경주 산사 순례길")
st.caption("동국대 WISE캠퍼스에서 걸어가는 신라 불교 이야기")

if "view" not in ss:
    ss["view"] = VIEWS[0]
view = st.segmented_control("메뉴", VIEWS, key="view", label_visibility="collapsed") or VIEWS[0]

# ---------------------------------------------------------------- ① 사찰 지도
if view == VIEWS[0]:
    st.caption("핀을 누르면 아래에 사진과 이야기가 나와요 · 지도는 두 손가락으로 움직여요")
    out = st_folium(
        build_overview_map(temples, THUMB), height=400, use_container_width=True,
        returned_objects=["last_object_clicked_tooltip"], key="overview_map",
    )
    legend()
    clicked = (out or {}).get("last_object_clicked_tooltip")
    hit = temples[temples["name"] == clicked]
    if len(hit):
        st.divider()
        render_temple_detail(hit.iloc[0])
    else:
        st.info("👆 지도에서 궁금한 사찰 핀을 눌러 보세요. 점선 원은 캠퍼스에서 직선 1.5km / 3km 거리예요.")

# ---------------------------------------------------------------- ② 순례 코스
elif view == VIEWS[1]:
    sel = ss.get("selected_course")

    if sel:  # ---- 코스 상세: 실제 도보 경로 지도 + 사진 타임라인
        c = COURSE_BY_ID[sel]
        s = course_stats(c, T, ROUTES)
        color = c.get("color", "#B23A2B")
        st.button("← 코스 목록", on_click=close_course)
        st.markdown(f"### {c['title']}  \n{c['subtitle']}")
        chips(
            (f"난이도 {DIFF_LABEL[c['difficulty']]}", f"diff{c['difficulty']}"),
            (f"도보 {s['walk_km']}km", ""), (f"약 {fmt_minutes(s['minutes'])}", ""),
            (f"{s['n_sites']}곳", ""), (c["mode"], ""), (c["target"], ""),
        )
        st.caption(f"“{c['theme']}”")
        st.write(c["desc"])
        if c.get("desc_en"):
            st.caption(f"🌏 *{c['desc_en']}*")

        with st.spinner("실제 걷는 길을 불러오는 중…"):
            cmap, n_fallback = build_course_map(c, T, THUMB)
        st_folium(cmap, height=360, use_container_width=True, returned_objects=[], key=f"course_map_{sel}")
        if n_fallback:
            st.caption(f"⚠️ {n_fallback}개 구간은 길 정보를 못 불러와 직선(점선)으로 표시했어요. "
                       "아래 '길찾기'를 누르면 카카오맵이 실제 길을 안내해요.")
        else:
            st.caption("굵은 선 = 실제 걷는 길 (OpenStreetMap 기준) · 점선 = 버스 이동")

        st.markdown("**순서대로 둘러보기**")
        order_of, order = {}, 0
        for sid in c["stops"]:
            if sid not in order_of:
                order += 1
                order_of[sid] = order

        for i, sid in enumerate(c["stops"]):
            r = T.loc[sid]
            is_return = sid in c["stops"][:i]            # 원점 회귀 코스의 마지막 = 출발점 복귀
            if is_return:
                st.markdown(f"<div class='stop' style='padding:10px 12px'>🏁 <b>{short_name(r)}</b>"
                            f"&nbsp;으로 돌아오면 코스 끝!</div>", unsafe_allow_html=True)
            else:
                pic = thumb(r, r["category"], height="78px", width="104px", radius="10px")
                st.markdown(
                    f"<div class='stop'>{pic}<div class='stop-body'>"
                    f"<div class='stop-head'><span class='stop-no' style='background:{color}'>{order_of[sid]}</span>"
                    f"<span class='stop-name'>{short_name(r)}</span><span class='stop-cat'>{r['category']}</span></div>"
                    f"<div class='stop-sum'>{first_sentence(r['story'])}</div></div></div>",
                    unsafe_allow_html=True,
                )
                more = "자세히 · 경내 볼거리" if sid in DETAILS else "자세히 보기"
                with st.expander(more):
                    render_temple_detail(r, show_courses=False, show_hero=False)

            if i < len(c["stops"]) - 1:                  # 다음 장소까지 이동 정보
                dist_m, is_bus = s["legs"][i]
                nxt = T.loc[c["stops"][i + 1]]
                if is_bus:
                    leg = f"🚌 버스 이동 · 약 {int(dist_m / 1000 / 20 * 60) + 10}분"
                else:
                    leg = f"🚶 {dist_m / 1000:.1f}km · 약 {walk_minutes(dist_m, c['difficulty'])}분"
                st.markdown(f"<div class='leg'>{leg}<a href='{kakao_walk_link(r, nxt)}' target='_blank'>"
                            f"카카오맵 길찾기 ›</a></div>", unsafe_allow_html=True)
        st.info(f"🏠 돌아오는 길: {c['return_tip']}")

    else:  # ---- 코스 목록 (대상 + 정렬 + 스크롤)
        who = st.segmented_control("누구를 위한 코스?", ["전체", "학생", "외국인", "불자"], default="전체") or "전체"
        st.markdown("<div style='font-size:0.875rem;margin-bottom:-6px'>정렬 (한 번 더 누르면 반대로)</div>",
                    unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small"):   # 모바일에서도 한 줄로
            for k in ("거리", "난이도"):
                st.button(sort_label(k), key=f"sort_{k}", on_click=on_sort, args=(k,),
                          type="primary" if k == ss["sort_key"] else "secondary")
        key, desc = ss["sort_key"], ss["sort_dir"] == "desc"

        items = [(c, course_stats(c, T, ROUTES)) for c in courses if who == "전체" or c["target"] == who]
        if key == "거리":
            items.sort(key=lambda x: (x[1]["walk_km"], x[0]["difficulty"]), reverse=desc)
        else:
            items.sort(key=lambda x: (x[0]["difficulty"], x[1]["walk_km"]), reverse=desc)

        st.caption(f"코스 {len(items)}개 · {key} {SORT_WORDS[(key, ss['sort_dir'])]} · 아래로 스크롤")
        with st.container(height=600):
            for c, s in items:
                with st.container(border=True):
                    uniq = list(dict.fromkeys(c["stops"]))
                    strip = "".join(
                        f"<div class='cell'>{thumb(T.loc[sid], T.loc[sid, 'category'], height='62px', width='78px', radius='8px')}"
                        f"<span class='no'>{n}</span></div>"
                        for n, sid in enumerate(uniq, 1)
                    )
                    names = []
                    for i, sid in enumerate(c["stops"]):
                        n = short_name(T.loc[sid])
                        names.append(("🚌 " if (i - 1) in c.get("bus_legs", []) else "") + n)
                    st.markdown(
                        f"<div class='card-title'>{c['title']}</div><div class='card-sub'>{c['subtitle']}</div>"
                        + chips_html(
                            (f"난이도 {DIFF_LABEL[c['difficulty']]}", f"diff{c['difficulty']}"),
                            (f"도보 {s['walk_km']}km", ""), (f"약 {fmt_minutes(s['minutes'])}", ""),
                            (c["target"], ""),
                        )
                        + f"<div class='strip'>{strip}</div><div class='route'>{' → '.join(names)}</div>",
                        unsafe_allow_html=True,
                    )
                    st.button("코스 보기", key=f"open_{c['id']}", on_click=open_course,
                              args=(c["id"],), use_container_width=True)

# ---------------------------------------------------------------- ③ 모두의 사진
elif view == VIEWS[2]:
    feed_view(temples)

# ---------------------------------------------------------------- ④ 스탬프
else:
    stamp_board_view(STAMP_TARGETS)
    st.divider()
    with st.expander("🖨️ QR 안내판 만들기 (관리자용)"):
        qr_maker(STAMP_TARGETS)
    with st.expander("🔧 발표·테스트용: QR 찍은 것처럼 열기"):
        names = STAMP_TARGETS["name"].tolist()
        pick = st.selectbox("사찰 선택", names, index=names.index("불국사") if "불국사" in names else 0)
        pid = STAMP_TARGETS.loc[STAMP_TARGETS["name"] == pick, "id"].iloc[0]
        st.caption(f"실제 QR에는 이 주소가 들어가요 → `앱주소/?temple={pid}`")
        if st.button("QR 찍은 것처럼 열기", use_container_width=True):
            st.query_params["temple"] = pid
            st.rerun()
