"""경주 산사 순례길 — 앱 프로토타입 (Streamlit + folium)

실행:  streamlit run app.py
화면 구성 (터치 3번 이내 원칙)
  ① 사찰 지도  : 그림 핀을 누르면(1번) 바로 아래에 사찰 이야기
  ② 순례 코스  : 코스 탭(1) → '코스 지도 보기'(2) → 사찰 펼치기(3)
  ③ 스탬프    : QR로 인증한 사찰 모아 보기
  QR 방문     : 사찰에 붙인 QR을 폰 카메라로 찍으면 ?temple=사찰ID 로 열려
                앱 안에서 누를 것 없이 소개 + 스탬프가 바로 찍혀요.
"""
import json

import streamlit as st
from streamlit_folium import st_folium

from utils import (
    CAMPUS_ID, CATEGORY_STYLE, DIFF_LABEL,
    build_course_map, build_overview_map, course_stats, fmt_minutes,
    load_courses, load_temples, map_links, pin_svg,
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
    .route {font-size:0.86rem; color:#555; line-height:1.5;}
    .legend span {display:inline-flex; align-items:center; margin-right:10px; font-size:0.8rem; color:#555;}
    .legend svg {margin-right:3px;}
    .maplinks {display:flex; gap:6px; margin:6px 0 10px;}
    .maplink {flex:1; text-align:center; padding:8px 4px; border:1px solid #DDD5C6; border-radius:8px;
              font-size:0.85rem; text-decoration:none !important; color:#333 !important; background:#fff;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- 데이터 준비
temples = load_temples()
T = temples.set_index("id", drop=False)          # T.loc["bunhwangsa"] 처럼 ID로 바로 찾기
courses = load_courses()
COURSE_BY_ID = {c["id"]: c for c in courses}
STAMP_TARGETS = temples[temples["id"] != CAMPUS_ID]   # QR을 붙일 곳 = 캠퍼스 제외 전부

VIEWS = ["🗺️ 사찰 지도", "🚶 순례 코스", "📿 스탬프"]


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
def chips(*items):
    html = "".join(f"<span class='chip {cls}'>{text}</span>" for text, cls in items)
    st.markdown(html, unsafe_allow_html=True)


def legend():
    parts = "".join(
        f"<span>{pin_svg(cat, scale=0.5)}{cat}</span>" for cat in CATEGORY_STYLE
    )
    st.markdown(f"<div class='legend'>{parts}</div>", unsafe_allow_html=True)


def render_temple_detail(row, show_courses: bool = True):
    """템플맵 상세 페이지처럼: 이름(한자) · 종류 · 주소 · 문화유산 · 이야기 · 지도 3사 연결."""
    st.markdown(
        f"<h3 style='margin-bottom:0'>{pin_svg(row['category'], scale=0.7)} {row['name']} "
        f"<span style='font-size:0.7em;color:#888;font-weight:400'>{row['hanja']}</span></h3>",
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


# ---------------------------------------------------------------- 스탬프 (브라우저 저장소)
# 로그인 없이, 폰 브라우저의 localStorage에 {사찰ID: 방문시각}을 저장해요.
STAMP_KEY = "gj_sansa_stamps"


def stamp_write(temple_id: str):
    st.iframe(
        f"""<script>
        const k = "{STAMP_KEY}";
        let s = {{}};
        try {{ s = JSON.parse(localStorage.getItem(k) || "{{}}"); }} catch (e) {{}}
        if (!s["{temple_id}"]) s["{temple_id}"] = new Date().toISOString();
        localStorage.setItem(k, JSON.stringify(s));
        </script>""",
        height=1,
    )


def stamp_board():
    items = [
        {"id": r["id"], "name": r["name"].split(" · ")[0],
         "on": pin_svg(r["category"], scale=0.9), "off": pin_svg(r["category"], scale=0.9, grey=True)}
        for _, r in STAMP_TARGETS.iterrows()
    ]
    rows = (len(items) + 2) // 3
    st.iframe(
        f"""
        <div id="head" style="font-family:sans-serif;margin:4px 0 10px;font-size:15px"></div>
        <div id="grid" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-family:sans-serif"></div>
        <button id="reset" style="margin-top:12px;font-size:12px;color:#888;background:none;border:1px solid #ddd;
                border-radius:6px;padding:4px 10px">스탬프 초기화(테스트용)</button>
        <script>
        const k = "{STAMP_KEY}";
        const items = {json.dumps(items, ensure_ascii=False)};
        function draw() {{
          let s = {{}};
          try {{ s = JSON.parse(localStorage.getItem(k) || "{{}}"); }} catch (e) {{}}
          const got = items.filter(t => s[t.id]).length;
          document.getElementById("head").innerHTML =
            "모은 스탬프 <b>" + got + "</b> / " + items.length +
            (got >= 3 ? " &nbsp;🎉 3곳 이상! 제휴 상점 쿠폰 대상이에요" : "");
          document.getElementById("grid").innerHTML = items.map(t => {{
            const on = !!s[t.id];
            const when = on ? new Date(s[t.id]).toLocaleDateString("ko-KR") : "미방문";
            return `<div style="border:1px solid ${{on ? '#E5DCCB' : '#eee'}};border-radius:10px;padding:8px 4px;
                     text-align:center;background:${{on ? '#FFFBF3' : '#fafafa'}}">
                     ${{on ? t.on : t.off}}<div style="font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:${{on ? '#333' : '#aaa'}}">${{t.name}}</div>
                     <div style="font-size:10px;color:#999">${{when}}</div></div>`;
          }}).join("");
        }}
        document.getElementById("reset").onclick = () => {{ localStorage.removeItem(k); draw(); }};
        draw();
        </script>
        """,
        height=70 + rows * 100 + 50,
    )


# ================================================================ ① QR로 들어온 경우
qr_id = st.query_params.get("temple")
if qr_id and qr_id in T.index:
    row = T.loc[qr_id]
    stamp_write(qr_id)                                     # 누르지 않아도 스탬프 자동 저장
    st.success(f"📷 QR 방문 인증 완료! **{row['name']}** 스탬프를 받았어요.")
    render_temple_detail(row)
    st.divider()
    st.button("🏠 앱 홈으로", on_click=go_home, use_container_width=True)
    st.stop()

# ================================================================ 메인 화면
st.markdown("## 🪷 경주 산사 순례길")
st.caption("동국대 WISE캠퍼스에서 걸어가는 신라 불교 이야기")

if "view" not in st.session_state:
    st.session_state["view"] = VIEWS[0]
view = st.segmented_control("메뉴", VIEWS, key="view", label_visibility="collapsed") or VIEWS[0]

# ---------------------------------------------------------------- ① 사찰 지도
if view == VIEWS[0]:
    st.caption("그림 핀을 누르면 아래에 사찰 이야기가 나와요 · 점선 원은 캠퍼스에서 걸어서 약 30분/60분")
    out = st_folium(
        build_overview_map(temples), height=440, use_container_width=True,
        returned_objects=["last_object_clicked_tooltip"], key="overview_map",
    )
    legend()
    clicked = (out or {}).get("last_object_clicked_tooltip")
    hit = temples[temples["name"] == clicked]
    if len(hit):
        st.divider()
        render_temple_detail(hit.iloc[0])
    else:
        st.info("👆 지도에서 궁금한 사찰 핀을 눌러 보세요.")

# ---------------------------------------------------------------- ② 순례 코스
elif view == VIEWS[1]:
    sel = st.session_state.get("selected_course")

    if sel:  # ---- 코스 상세
        c = COURSE_BY_ID[sel]
        s = course_stats(c, T)
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

        st_folium(build_course_map(c, T), height=380, use_container_width=True,
                  returned_objects=[], key=f"course_map_{sel}")

        st.markdown("**순서대로 둘러보기** (눌러서 펼치기)")
        order, seen = 0, set()
        for sid in c["stops"]:
            if sid in seen:
                continue
            seen.add(sid)
            order += 1
            r = T.loc[sid]
            with st.expander(f"{order}. {r['name']}"):
                render_temple_detail(r, show_courses=False)
        st.info(f"🏠 돌아오는 길: {c['return_tip']}")

    else:  # ---- 코스 목록 (정렬 + 스크롤)
        # 드롭다운(2번 터치) 대신 한 번 누르면 바로 바뀌는 버튼형 선택
        who = st.segmented_control("대상", ["전체", "학생", "외국인", "불자"], default="전체") or "전체"
        order_by = st.segmented_control(
            "정렬", ["거리 짧은 순", "거리 긴 순", "난이도 쉬운 순", "난이도 어려운 순"],
            default="거리 짧은 순",
        ) or "거리 짧은 순"

        items = []
        for c in courses:
            if who != "전체" and c["target"] != who:
                continue
            items.append((c, course_stats(c, T)))

        sort_key = {
            "거리 짧은 순":     (lambda x: (x[1]["walk_km"], x[0]["difficulty"]), False),
            "거리 긴 순":       (lambda x: (x[1]["walk_km"], x[0]["difficulty"]), True),
            "난이도 쉬운 순":   (lambda x: (x[0]["difficulty"], x[1]["walk_km"]), False),
            "난이도 어려운 순": (lambda x: (x[0]["difficulty"], x[1]["walk_km"]), True),
        }[order_by]
        items.sort(key=sort_key[0], reverse=sort_key[1])

        st.caption(f"코스 {len(items)}개 · 아래로 스크롤해서 보세요")
        with st.container(height=560):
            for c, s in items:
                with st.container(border=True):
                    st.markdown(f"**{c['title']}** · {c['subtitle']}")
                    chips(
                        (f"난이도 {DIFF_LABEL[c['difficulty']]}", f"diff{c['difficulty']}"),
                        (f"도보 {s['walk_km']}km", ""), (f"약 {fmt_minutes(s['minutes'])}", ""),
                        (c["target"], ""),
                    )
                    names = []
                    for i, sid in enumerate(c["stops"]):
                        n = T.loc[sid, "name"].split(" · ")[0]
                        names.append(("🚌 " if (i - 1) in c.get("bus_legs", []) else "") + n)
                    st.markdown(f"<div class='route'>{' → '.join(names)}</div>", unsafe_allow_html=True)
                    st.button("코스 지도 보기", key=f"open_{c['id']}", on_click=open_course,
                              args=(c["id"],), use_container_width=True)

# ---------------------------------------------------------------- ③ 스탬프
else:
    st.caption("사찰에 붙은 QR을 폰 카메라로 찍으면 자동으로 스탬프가 찍혀요. (이 폰의 브라우저에 저장)")
    stamp_board()
    with st.expander("🔧 발표·테스트용: QR 없이 방문 인증 체험"):
        pick = st.selectbox("사찰 선택", STAMP_TARGETS["name"].tolist())
        pid = STAMP_TARGETS.loc[STAMP_TARGETS["name"] == pick, "id"].iloc[0]
        st.caption(f"실제 QR에는 이 주소가 들어가요 → `앱주소/?temple={pid}`")
        if st.button("QR 찍은 것처럼 열기", use_container_width=True):
            st.query_params["temple"] = pid
            st.rerun()
