"""community.py — 이용자가 함께 채우는 화면 조각들

- upload_box        : 사진 올리기 (QR 인증용 / 그냥 공유용)
- visitor_photos    : 사찰 상세 아래 '방문자 사진' 줄
- feed_view         : '모두의 사진' 탭
- stamp_board_view  : 닉네임으로 보는 내 스탬프판 (인증 사진 위에 도장)
- qr_maker          : 앱 주소로 사찰별 QR 안내판 만들기 (인쇄용 내려받기)
"""
import base64
import io
from urllib.parse import urlsplit

import streamlit as st

from make_qr import make_poster
from storage import delete_photo, get_store, photo_bytes, photos_for, save_photo, when_text
from utils import pin_svg

SS = st.session_state


# ---------------------------------------------------------------- 그림 조각
def seal_svg(name: str, date: str, size: int = 78) -> str:
    """빨간 인증 도장 (사찰 이름 + 인증 + 날짜)."""
    name = name[:6]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        '<g transform="rotate(-14 50 50)" opacity="0.9">'
        '<circle cx="50" cy="50" r="45" fill="rgba(255,255,255,0.18)" stroke="#C62828" stroke-width="5"/>'
        '<circle cx="50" cy="50" r="37" fill="none" stroke="#C62828" stroke-width="1.6"/>'
        f'<text x="50" y="32" text-anchor="middle" font-size="12" font-weight="700" fill="#C62828">{name}</text>'
        '<text x="50" y="60" text-anchor="middle" font-size="22" font-weight="900" fill="#C62828">인증</text>'
        f'<text x="50" y="78" text-anchor="middle" font-size="11" font-weight="700" fill="#C62828">{date}</text>'
        '</g></svg>'
    )


def _uri(rec: dict, thumb: bool = True) -> str | None:
    data = photo_bytes(rec["file"], thumb)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode() if data else None


def _date(rec: dict) -> str:
    return rec["created"][5:10].replace("-", ".")          # '10.06'


def tile_html(rec: dict, temple_name: str, *, seal: bool = True, meta: bool = True) -> str:
    """정사각형 사진 칸. 인증 사진이면 도장을 찍어 줘요."""
    src = _uri(rec)
    img = f"<img src='{src}' alt=''>" if src else "<div class='ph-missing'>사진을 불러오지 못했어요</div>"
    stamp = (f"<div class='seal'>{seal_svg(temple_name, _date(rec))}</div>"
             if seal and rec["kind"] == "stamp" else "")
    info = ""
    if meta:
        cap = f"<div class='ph-cap'>{rec['caption']}</div>" if rec.get("caption") else ""
        info = (f"<div class='ph-meta'><b>{temple_name}</b><span>{rec['nickname']} · {when_text(rec['created'])}</span></div>"
                f"{cap}")
    return f"<div class='ph'><div class='ph-box'>{img}{stamp}</div>{info}</div>"


def grid(tiles: list[str], cols: int = 3) -> str:
    return f"<div class='ph-grid c{cols}'>{''.join(tiles)}</div>"


CSS = """
<style>
.ph-grid {display:grid; gap:8px; margin:6px 0 10px;}
.ph-grid.c3 {grid-template-columns:repeat(3,minmax(0,1fr));}
.ph-grid.c2 {grid-template-columns:repeat(2,minmax(0,1fr));}
.ph {min-width:0;}
.ph-box {position:relative; aspect-ratio:1/1; border-radius:10px; overflow:hidden; background:#EFE8DA;}
.ph-box img {width:100%; height:100%; object-fit:cover; display:block;}
.ph-missing {font-size:11px; opacity:.6; display:flex; align-items:center; justify-content:center; height:100%;}
.seal {position:absolute; right:-4px; bottom:-6px; width:58%; max-width:96px;}
.seal svg {width:100%; height:auto; display:block;}
.ph-meta {font-size:0.74rem; line-height:1.3; margin-top:4px; display:flex; flex-direction:column;}
.ph-meta b {white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.ph-meta span {opacity:.65; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.ph-cap {font-size:0.72rem; opacity:.8; margin-top:1px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.st-empty {aspect-ratio:1/1; border-radius:10px; border:1px dashed #D8CFBF; background:#FAF7F0;
           display:flex; align-items:center; justify-content:center;}
.st-empty svg {opacity:.55;}
.st-name {font-size:0.72rem; text-align:center; margin-top:3px; white-space:nowrap; overflow:hidden;
          text-overflow:ellipsis; opacity:.75;}
.store-note {font-size:0.75rem; opacity:.65;}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- 닉네임
def nickname_input(key: str, label: str = "닉네임") -> str:
    """닉네임은 한 번 적으면 다른 화면에서도 그대로 써요."""
    def _sync():
        SS["nickname"] = SS[key].strip()
    st.text_input(label, value=SS.get("nickname", ""), key=key, max_chars=20,
                  placeholder="예: 정보경영 희찬", on_change=_sync)
    return SS.get(key, SS.get("nickname", "")).strip()


# ---------------------------------------------------------------- 사진 올리기
def upload_box(row, kind: str, key: str):
    """kind='stamp' → QR 인증 사진(스탬프에 도장) / 'share' → 그냥 사진 공유."""
    name = str(row["name"]).split(" · ")[0]
    is_stamp = kind == "stamp"
    file = st.file_uploader(
        "📷 사진 찍기 또는 앨범에서 고르기" if is_stamp else "사진 고르기",
        type=["jpg", "jpeg", "png", "heic", "webp"], key=f"up_{key}",
    )
    nick = nickname_input(f"nick_{key}")
    caption = st.text_input("한 줄 남기기 (선택)", key=f"cap_{key}", max_chars=60,
                            placeholder="예: 다보탑 앞에서 친구랑!")
    label = f"✅ {name} 인증하고 스탬프 받기" if is_stamp else "사진 올리기"
    if st.button(label, key=f"go_{key}", type="primary", use_container_width=True,
                 disabled=not (file and nick)):
        try:
            rec = save_photo(row["id"], nick, kind, file.getvalue(), caption)
        except Exception as e:
            st.error(f"사진을 올리지 못했어요. 다른 사진으로 다시 해 보세요. ({type(e).__name__})")
            return None
        SS["nickname"] = nick
        SS[f"done_{key}"] = rec["id"]
        st.rerun()
    if not (file and nick):
        st.caption("사진과 닉네임을 넣으면 버튼이 켜져요. 사진의 위치 정보는 지우고 저장해요.")
    return None


def just_uploaded(row, key: str):
    """방금 올린 사진이 있으면 축하 화면."""
    rec_id = SS.get(f"done_{key}")
    if not rec_id:
        return False
    rec = next((r for r in photos_for(row["id"]) if r["id"] == rec_id), None)
    if not rec:
        return False
    name = str(row["name"]).split(" · ")[0]
    if rec["kind"] == "stamp":
        st.success(f"🎉 {name} 스탬프 완료! '📿 스탬프' 탭에서 모아 볼 수 있어요.")
    else:
        st.success("사진을 올렸어요! '📸 모두의 사진'에서 다른 사람들도 볼 수 있어요.")
    st.markdown(grid([tile_html(rec, name)], cols=2), unsafe_allow_html=True)
    return True


def stamp_upload_section(row):
    """QR로 들어왔을 때 맨 위에 보이는 인증 영역."""
    name = str(row["name"]).split(" · ")[0]
    key = f"stamp_{row['id']}"
    st.markdown(f"### 📷 {name}에 도착했어요!")
    if just_uploaded(row, key):
        return
    nick = SS.get("nickname", "")
    mine = photos_for(row["id"], nickname=nick, kind="stamp") if nick else []
    if mine:
        st.info(f"{nick}님은 이미 {name} 스탬프가 있어요. 사진을 더 올려도 돼요.")
    else:
        st.caption("지금 여기서 사진을 찍어 올리면, 그 사진 위에 인증 도장이 찍혀 스탬프가 돼요.")
    with st.container(border=True):
        upload_box(row, "stamp", key)


def user_hero_uri(temple_id: str) -> str | None:
    """공식 사진이 없는 곳은 이용자가 올린 최신 사진을 대표 사진으로."""
    recs = photos_for(temple_id)
    return _uri(recs[0], thumb=False) if recs else None


def visitor_photos(row):
    """사찰 상세 화면 아래 '방문자 사진'."""
    name = str(row["name"]).split(" · ")[0]
    recs = photos_for(row["id"])
    st.markdown(f"**📸 방문자 사진** · {len(recs)}장")
    if recs:
        st.markdown(grid([tile_html(r, name, meta=False) for r in recs[:6]]), unsafe_allow_html=True)
        if len(recs) > 6:
            st.caption("더 많은 사진은 '📸 모두의 사진' 탭에서 볼 수 있어요.")
    else:
        st.caption("아직 올라온 사진이 없어요. 첫 사진을 올려 주세요!")
    key = f"share_{row['id']}"
    if not just_uploaded(row, key):
        with st.expander("➕ 이 곳 사진 올리기"):
            upload_box(row, "share", key)


# ---------------------------------------------------------------- 모두의 사진
def feed_view(temples):
    names = dict(zip(temples["id"], temples["name"].str.split(" · ").str[0]))
    recs = photos_for()
    store = get_store()
    st.caption(f"방문자들이 올린 사진 {len(recs)}장 · 빨간 도장은 QR로 인증한 사진이에요.")
    if not recs:
        st.info("아직 사진이 없어요. 사찰 상세 화면의 '이 곳 사진 올리기'나 QR 인증으로 첫 사진을 올려 보세요.")
    else:
        used = [tid for tid in temples["id"] if any(r["temple_id"] == tid for r in recs)]
        pick = st.selectbox("어디 사진?", ["전체"] + [names[t] for t in used], key="feed_pick")
        if pick != "전체":
            tid = next(t for t in used if names[t] == pick)
            recs = [r for r in recs if r["temple_id"] == tid]
        shown = recs[:30]
        st.markdown(grid([tile_html(r, names.get(r["temple_id"], "")) for r in shown]), unsafe_allow_html=True)
        my = SS.get("nickname", "")
        mine = [r for r in recs if my and r["nickname"] == my]
        if mine:
            with st.expander(f"🗑️ 내가 올린 사진 지우기 ({my})"):
                for r in mine:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"{names.get(r['temple_id'], '')} · {when_text(r['created'])}"
                             + (" · 인증" if r["kind"] == "stamp" else ""))
                    if c2.button("지우기", key=f"del_{r['id']}"):
                        delete_photo(r["id"])
                        st.rerun()
    st.markdown(f"<div class='store-note'>저장 장소: {store.name}"
                + ("" if store.permanent else " · 앱이 다시 시작되면 사진이 지워질 수 있어요") + "</div>",
                unsafe_allow_html=True)


# ---------------------------------------------------------------- 스탬프판
def stamp_board_view(targets):
    st.caption("사찰의 QR을 찍고 인증 사진을 올리면, 그 사진에 도장이 찍혀 여기에 모여요.")
    nick = nickname_input("nick_board", "내 닉네임 (사진 올릴 때 쓴 이름)")
    if not nick:
        st.info("닉네임을 넣으면 내 스탬프가 보여요.")
        return
    stamps = {}
    for r in photos_for(nickname=nick, kind="stamp"):        # 최신순 → 사찰마다 가장 최근 인증 사진
        stamps.setdefault(r["temple_id"], r)
    got = len(stamps)
    msg = f"**{nick}** 님의 스탬프 **{got}** / {len(targets)}"
    if got >= 3:
        msg += " 🎉 3곳 이상! 제휴 상점 쿠폰 대상이에요"
    st.markdown(msg)
    st.progress(got / len(targets))
    tiles = []
    for _, row in targets.iterrows():
        name = str(row["name"]).split(" · ")[0]
        if row["id"] in stamps:
            tiles.append(f"<div class='ph'>{tile_html(stamps[row['id']], name, meta=False)}"
                         f"<div class='st-name'><b>{name}</b></div></div>")
        else:
            tiles.append(f"<div class='ph'><div class='st-empty'>{pin_svg(row['category'], scale=0.9, grey=True)}</div>"
                         f"<div class='st-name'>{name}</div></div>")
    done = [t for t, rid in zip(tiles, targets["id"]) if rid in stamps]
    todo = [t for t, rid in zip(tiles, targets["id"]) if rid not in stamps]
    st.markdown(grid(done + todo), unsafe_allow_html=True)        # 받은 스탬프가 앞으로


# ---------------------------------------------------------------- QR 만들기 (관리자용)
def detected_base_url() -> str:
    """지금 열려 있는 앱 주소 (예: https://gyeongju-sansa.streamlit.app)."""
    try:
        u = urlsplit(st.context.url or "")
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    except Exception:
        pass
    return ""


def qr_maker(targets):
    st.caption("사찰을 고르면 이 앱 주소로 QR 안내판이 만들어져요. 내려받아 인쇄해서 붙이면 끝!")
    names = targets["name"].str.split(" · ").str[0].tolist()
    default = names.index("불국사") if "불국사" in names else 0
    pick = st.selectbox("QR 만들 사찰", names, index=default, key="qr_pick")
    base = st.text_input("앱 주소 (자동으로 채워져요. 틀리면 고치세요)", value=detected_base_url(), key="qr_base",
                         placeholder="https://○○○.streamlit.app")
    row = targets.iloc[names.index(pick)]
    if not base.startswith("http"):
        st.warning("앱 주소를 https:// 로 시작하게 넣어 주세요.")
        return
    img = make_poster(row, base)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    st.image(buf.getvalue(), caption=f"{base.rstrip('/')}/?temple={row['id']}", width=260)
    st.download_button(f"⬇️ {pick} QR 안내판 내려받기 (PNG)", buf.getvalue(),
                       file_name=f"QR_{row['id']}.png", mime="image/png", use_container_width=True)
