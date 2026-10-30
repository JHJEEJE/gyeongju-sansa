"""ai_guide.py — ✨ 다음 한 곳 (AI 순례 동행)

경주 여행이 늘 같은 유명 코스로 끝나는 이유는 '관심이 없어서'가 아니라
사람마다 상황(남은 시간 · 이동수단 · 같이 온 사람)이 다른데 모두에게 같은 코스만 보여 주기 때문이에요.
그래서 이 화면은 코스를 보여 주지 않고, '지금 이 사람이 갈 수 있는 다음 한 곳'을 골라 줘요.

흐름 (화면 하나, 터치 0~3번)
  ① 조건 파악 : 한 마디("애들이랑 2시간 있어요") 또는 버튼 3개 → 동행 · 남은 시간 · 이동수단
                LLM 키가 있으면 LLM이 알아듣고, 없으면 낱말 규칙으로 알아들어요.
  ② 도구 실행 : 거리 · 이동 시간 계산, 날씨 조회 (코드가 직접 계산 · 조회)
  ③ 판단      : 남은 시간 안에 못 가는 곳은 빼고, 점수를 매겨 1~2곳 추천
  ④ 설명      : 추천 이유 + 방금 본 곳과 다음 곳을 잇는 이야기
                (LLM은 temples.csv에 적힌 이야기 안에서만 말하게 해요 = RAG)
  ⑤ 기록      : '이번엔 안 갈래요' 이유(시간·이동·흥미·동행·기타)를 모아 → 왜 안 가는지 데이터로 확인

LLM 연결 (선택) — Streamlit Secrets에 두 줄 넣기:
    ANTHROPIC_API_KEY = "sk-ant-..."
    LLM_MODEL = "claude-haiku-5-5"      # 생략하면 이 모델
키가 없어도 모든 기능이 규칙 기반으로 돌아가요 (발표 때 인터넷이 끊겨도 안전).
"""
import json
import re
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st

from storage import append_json, load_json
from utils import CAMPUS_ID, haversine_km, kakao_walk_link, photo_html, _kname

KST = timezone(timedelta(hours=9))
FEEDBACK_FILE = "next_feedback.json"

COMPANIONS = ["혼자·친구", "아이와 함께", "어르신과 함께", "외국인 친구와"]
TIMES = {"1시간": 60, "2시간": 120, "반나절": 240}
MOVES = ["걸어서", "버스", "자가용·택시"]

# '이번엔 안 갈래요' 이유 = 기획서의 네 가지 장벽 + 기타
BARRIERS = {
    "⏱️ 시간이 모자라요": "시간",
    "🚌 가는 게 번거로워요": "이동",
    "😐 비슷해 보여서 안 끌려요": "흥미",
    "👨‍👩‍👧 같이 온 사람이 힘들어해요": "동행",
    "🤔 다른 이유": "기타",
}

FAMOUS = {"bulguksa", "seokguram"}       # 다들 이미 가는 곳 → 살짝 덜 추천 (같은 코스 탈출)
INDOOR = {"museum"}                       # 비 오는 날에도 괜찮은 곳
STAY_MIN = {"bulguksa": 60, "seokguram": 40, "museum": 60}   # 기본 30분
SS = st.session_state


# ================================================================ ① 조건 파악
_KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}


def parse_rule(text: str) -> dict:
    """LLM 없이 낱말로 알아듣기. 모르는 건 None으로 남겨요."""
    t = text.replace(" ", "")
    out = {"companion": None, "minutes": None, "move": None}
    if re.search(r"아이|애들|애기|아기|아들|딸|초등|유모차|꼬맹이", t):
        out["companion"] = "아이와 함께"
    elif re.search(r"부모님|어르신|할머니|할아버지|어머니|아버지", t):
        out["companion"] = "어르신과 함께"
    elif re.search(r"외국|foreign|friend", t, re.I):
        out["companion"] = "외국인 친구와"
    elif re.search(r"혼자|친구|커플|여자친구|남자친구|연인", t):
        out["companion"] = "혼자·친구"

    if "반나절" in t:
        out["minutes"] = 240
    else:
        h = re.search(r"(\d+(?:\.\d+)?)시간", t)
        k = re.search(r"(한|두|세|네|다섯)시간", t)
        m = re.search(r"(\d+)분", t)
        mins = 0
        if h:
            mins += float(h.group(1)) * 60
        elif k:
            mins += _KO_NUM[k.group(1)] * 60
        if "반" in t and (h or k) and re.search(r"시간반", t):
            mins += 30
        if m:
            mins += int(m.group(1))
        out["minutes"] = int(mins) or None

    if re.search(r"차있|차가있|자차|렌트|택시|운전|차로", t):
        out["move"] = "자가용·택시"
    elif re.search(r"버스|대중교통", t):
        out["move"] = "버스"
    elif re.search(r"걸어|도보|뚜벅|차없|차가없", t):
        out["move"] = "걸어서"
    return out


def _llm_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY"), st.secrets.get("LLM_MODEL", "claude-haiku-5-5")
    except Exception:
        return None, None


def llm_ready() -> bool:
    return bool(_llm_key()[0])


def ask_llm(system: str, user: str, max_tokens: int = 400) -> str | None:
    """Claude API에 한 번 물어보기. 실패하면 None (→ 규칙 기반으로 대신)."""
    key, model = _llm_key()
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=20,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def parse_llm(text: str) -> dict | None:
    system = ("너는 경주 관광객의 말을 듣고 여행 조건을 뽑는 도우미야. 반드시 JSON 한 줄만 답해. "
              f'형식: {{"companion": {COMPANIONS} 중 하나 또는 null, "minutes": 숫자(남은 분) 또는 null, '
              f'"move": {MOVES} 중 하나 또는 null}}. 말에 없는 건 null.')
    raw = ask_llm(system, text, max_tokens=120)
    try:
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return {"companion": d.get("companion") if d.get("companion") in COMPANIONS else None,
                "minutes": int(d["minutes"]) if d.get("minutes") else None,
                "move": d.get("move") if d.get("move") in MOVES else None}
    except Exception:
        return None


def parse_condition(text: str) -> dict:
    """LLM이 있으면 LLM 먼저, 비어 있는 칸은 낱말 규칙으로 채우기."""
    rule = parse_rule(text)
    got = parse_llm(text) if llm_ready() else None
    if not got:
        return rule
    return {k: got.get(k) or rule.get(k) for k in rule}


def _closest_time(minutes: int) -> str:
    return min(TIMES, key=lambda k: abs(TIMES[k] - minutes))


# ================================================================ ② 도구: 이동 시간 · 날씨
def travel_minutes(a, b, move: str, companion: str, hill: bool = False) -> tuple[int, str]:
    """a → b 이동 시간(분)과 실제 이동 방법. 1km 안쪽이면 버스·차 대신 걷기.
    버스 시간은 실제 노선이 아니라 거리로 어림한 값이에요 (나중에 버스 도착정보 API로 바꿀 자리)."""
    straight = haversine_km(a.lat, a.lon, b.lat, b.lon)
    road = straight * 1.3                                  # 직선보다 길이 30% 길다고 어림
    slow = companion in ("아이와 함께", "어르신과 함께")
    if move == "걸어서" or straight < 1.0:
        speed = 3.0 if slow else 4.0
        if hill:                                           # 산길은 오르막이라 더 느리게
            speed *= 0.6
        return int(road / speed * 60), "걸어서"
    if move == "버스":
        return int(road / 20 * 60 + 15 + 8), "버스"          # 버스 기다림 15분 + 정류장까지 걷기 8분
    return int(road / 30 * 60 + 10), "자가용·택시"            # 주차·하차 10분


@st.cache_data(show_spinner=False, ttl=600)
def weather_now() -> dict | None:
    """경주 지금 날씨 (Open-Meteo, 키 필요 없음). 못 받으면 None."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": 35.84, "longitude": 129.21,
                                 "current": "precipitation,temperature_2m"}, timeout=5)
        cur = r.json()["current"]
        return {"rain": float(cur.get("precipitation") or 0) > 0.1, "temp": cur.get("temperature_2m")}
    except Exception:
        return None


def hill_ids(temples, courses) -> set:
    """산길로 가야 하는 곳: 남산·토함산 주소 + '어려움' 코스에 있는 곳 (데이터에서 어림)."""
    ids = set(temples.loc[temples["address"].str.contains("남산|토함산") |
                          temples["name"].str.contains("남산"), "id"])
    for c in courses:
        if c["difficulty"] == 3:
            ids |= set(c["stops"])
    return ids - {CAMPUS_ID}


# ================================================================ ③ 판단: 갈 수 있는 곳 골라 점수 매기기
def recommend(cur, temples, cond: dict, hills: set, visited: set, rainy: bool, n: int = 2) -> list:
    picks = []
    for _, t in temples.iterrows():
        if t["id"] in (cur["id"], CAMPUS_ID) or t["id"] in visited:
            continue
        is_hill = t["id"] in hills
        go, how = travel_minutes(cur, t, cond["move"], cond["companion"], is_hill)
        stay = STAY_MIN.get(t["id"], 30)
        if go + stay > cond["minutes"]:                    # 남은 시간 안에 못 다녀옴 → 제외
            continue
        if how == "걸어서" and go > 60:                     # 1시간 넘게 걷는 건 제외
            continue
        slow = cond["companion"] in ("아이와 함께", "어르신과 함께")

        score, why = -go / 10, [f"{how} 약 {go}분 · 둘러보는 데 {stay}분"]
        if t["category"] != cur["category"]:
            score += 2
            why.append(f"방금 본 곳({cur['category']})과 달리 {_ira(t['category'])} 또 다른 볼거리예요")
        if t["id"] in FAMOUS:
            score -= 1
        elif cur["id"] in FAMOUS:
            why.append("수학여행 코스 밖에서 경주를 한 곳 더 만나는 기회예요")
        if slow and is_hill:
            score -= 5
        elif slow:
            why.append(f"산길이 아니라 {cond['companion'].replace(' 함께', '')}도 편하게 갈 수 있어요")
        if rainy:
            if t["id"] in INDOOR:
                score += 3
                why.append("비가 와도 실내에서 볼 수 있어요")
            elif how == "걸어서":
                score -= go / 15
        if cond["companion"] == "외국인 친구와" and t["desc_en"]:
            score += 0.5
            why.append("영어 한 줄 소개가 있어요")
        picks.append({"row": t, "go": go, "how": how, "stay": stay, "score": score, "why": why, "hill": is_hill})
    picks.sort(key=lambda p: p["score"], reverse=True)
    out = []
    for p in picks:                                        # 비슷한 곳 두 개를 나란히 추천하지 않게
        if any(p["row"]["category"] == q["row"]["category"] and
               haversine_km(p["row"].lat, p["row"].lon, q["row"].lat, q["row"].lon) < 1.0 for q in out):
            continue
        out.append(p)
        if len(out) == n:
            break
    return out


def _has_batchim(word: str) -> bool:
    ch = word[-1]
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _eun(word: str) -> str:
    return word + ("은" if _has_batchim(word) else "는")


def _ira(word: str) -> str:
    """받침 있으면 '이라', 없으면 '라' (절터라 / 석탑이라)."""
    return word + ("이라" if _has_batchim(word) else "라")


# ================================================================ ④ 설명: 두 곳을 잇는 이야기
def _first(text: str) -> str:
    text = str(text)
    i = text.find("요.")
    return text[: i + 2] if 0 < i < 120 else text[:90] + "…"


@st.cache_data(show_spinner=False, ttl=86400)
def bridge_story(cur_name, cur_story, nxt_name, nxt_story, companion) -> tuple[str, bool]:
    """(이야기, LLM이 만들었는지). LLM은 아래 두 글 안의 사실만 쓰게 해요."""
    tone = {"아이와 함께": "초등학생도 알아듣게 쉽고 재밌게",
            "어르신과 함께": "차분하고 공손하게",
            "외국인 친구와": "한국어로 쓰되 외국인에게 설명하듯 배경을 짧게 곁들여"}.get(companion, "친구에게 말하듯")
    system = ("너는 경주 사찰 순례 해설사야. 아래 [자료] 두 개에 적힌 사실만 사용해. 자료에 없는 연도·인물·전설은 지어내지 마. "
              "두 곳의 공통점이나 차이를 하나 잡아 '다음 곳에 가 보고 싶게' 3문장 이내로 말해. "
              f"말투: {tone}. 화살표나 목록 없이 문장으로.")
    user = f"[자료1] {cur_name}: {cur_story}\n[자료2] {nxt_name}: {nxt_story}"
    got = ask_llm(system, user, max_tokens=300)
    if got:
        return got, True
    return f"방금 본 {cur_name}에 이어, {_eun(nxt_name)} 이런 곳이에요. {_first(nxt_story)}", False


# ================================================================ ⑤ 기록: 갈게요 / 안 갈래요
def log_choice(cur_id: str, nxt_id: str, cond: dict, choice: str, barrier: str = ""):
    append_json(FEEDBACK_FILE, {
        "time": datetime.now(KST).isoformat(timespec="seconds"),
        "from": cur_id, "to": nxt_id, "choice": choice, "barrier": barrier,
        "companion": cond["companion"], "minutes": cond["minutes"], "move": cond["move"],
    })


def feedback_records() -> list:
    return load_json(FEEDBACK_FILE, [])


# ================================================================ 화면
def _apply_text(key: str):
    """한 마디를 쓰면 아래 버튼 3개가 알아서 바뀌어요."""
    text = SS.get(f"{key}_text", "").strip()
    if not text:
        return
    got = parse_condition(text)
    if got["companion"]:
        SS[f"{key}_who"] = got["companion"]
    if got["minutes"]:
        SS[f"{key}_time"] = _closest_time(got["minutes"])
        SS[f"{key}_minutes"] = got["minutes"]
    if got["move"]:
        SS[f"{key}_move"] = got["move"]


def _clear_exact_minutes(key: str):
    SS.pop(f"{key}_minutes", None)


def _travel_link(a, b, how: str) -> str:
    by = {"걸어서": "walk", "버스": "traffic", "자가용·택시": "car"}[how]
    if by == "walk":
        return kakao_walk_link(a, b)
    return (f"https://map.kakao.com/link/by/{by}/{_kname(a)},{a['lat']},{a['lon']}"
            f"/{_kname(b)},{b['lat']},{b['lon']}")


def next_place_view(temples, courses, thumb_map: dict, current_id: str | None = None, key: str = "np"):
    """'다음 한 곳' 화면. QR로 들어오면 current_id가 정해져 있어요."""
    T = temples.set_index("id", drop=False)
    visited = set(SS.get("visited", []))

    if current_id is None:
        places = temples[temples["id"] != CAMPUS_ID]
        names = places["name"].tolist()
        default = names.index("불국사") if "불국사" in names else 0
        pick = st.selectbox("지금 어디에 있어요?", names, index=default, key=f"{key}_where")
        current_id = places.iloc[names.index(pick)]["id"]
    cur = T.loc[current_id]
    short = str(cur["name"]).split(" · ")[0]

    st.text_input("한 마디로 말해도 돼요 (선택)", key=f"{key}_text", on_change=_apply_text, args=(key,),
                  placeholder="예) 애들이랑 2시간 정도 있어요, 차는 없어요")
    for k, v in ((f"{key}_who", COMPANIONS[0]), (f"{key}_time", "2시간"), (f"{key}_move", MOVES[0])):
        SS.setdefault(k, v)
    who = st.segmented_control("누구랑?", COMPANIONS, key=f"{key}_who") or COMPANIONS[0]
    tsel = st.segmented_control("남은 시간", list(TIMES), key=f"{key}_time",
                                on_change=_clear_exact_minutes, args=(key,)) or "2시간"
    move = st.segmented_control("이동은?", MOVES, key=f"{key}_move") or MOVES[0]
    cond = {"companion": who, "minutes": SS.get(f"{key}_minutes") or TIMES[tsel], "move": move}

    weather = weather_now()
    rainy = bool(weather and weather["rain"])
    picks = recommend(cur, temples, cond, hill_ids(temples, courses), visited, rainy)

    note = f"🤖 {'AI(LLM)' if llm_ready() else '규칙 기반'} 추천 · {short}에서 출발 · 남은 {cond['minutes']}분"
    if move == "버스":
        note += " · 버스 시간은 거리로 어림했어요"
    if weather:
        note += f" · 지금 경주 {'☔ 비' if rainy else '☀️ 맑음'} {weather['temp']}°C"
    st.caption(note)

    if not picks:
        st.warning("남은 시간 안에 다녀올 수 있는 곳이 없어요. 시간을 늘리거나 이동 방법을 바꿔 보세요. "
                   "오늘은 여기서 천천히 둘러보는 것도 좋아요 🙂")
        return

    for n, p in enumerate(picks):
        r = p["row"]
        name = str(r["name"]).split(" · ")[0]
        with st.container(border=True):
            pic = photo_html(thumb_map.get(r["image_url"]), r["image_url"], r["category"],
                             height="150px", radius="10px")
            label = "가장 추천" if n == 0 else "이런 곳도"
            st.markdown(f"{pic}<div style='margin-top:8px;font-size:0.8rem;opacity:.7'>{label}</div>"
                        f"<div style='font-size:1.15rem;font-weight:700'>{name}</div>",
                        unsafe_allow_html=True)
            st.markdown("\n".join(f"- {w}" for w in p["why"]))
            if n == 0:
                with st.spinner("가는 길에 들을 이야기 만드는 중…"):
                    story, by_ai = bridge_story(short, cur["story"], name, r["story"], who)
                st.markdown(f"**📖 가는 길에 들을 이야기**  \n{story}")
                if by_ai:
                    st.caption("AI가 국가유산 자료 기반 사찰 소개글 안에서만 만든 이야기예요.")
            else:
                st.caption(_first(r["story"]))

            st.link_button(f"🧭 {p['how']} 길찾기 (카카오맵)", _travel_link(cur, r, p["how"]),
                           use_container_width=True)
            _choice_buttons(cur, r, cond, f"{key}_{r['id']}")


def _choice_buttons(cur, r, cond, k: str):
    done = SS.get(f"{k}_done")
    if done:
        st.success(done)
        return
    if st.button("✅ 여기로 갈게요", key=f"{k}_go", use_container_width=True):
        log_choice(cur["id"], r["id"], cond, "go")
        SS[f"{k}_done"] = "좋아요! 도착하면 그곳 QR을 찍어 스탬프를 받아 보세요."
        st.rerun()
    picked = st.pills("이번엔 안 갈래요? 이유 하나만 눌러 주세요", list(BARRIERS), key=f"{k}_no")
    if picked:
        log_choice(cur["id"], r["id"], cond, "no", BARRIERS[picked])
        SS[f"{k}_done"] = "알려 줘서 고마워요. 다음 추천을 더 잘 맞춰 볼게요."
        st.rerun()


def feedback_dashboard(temples):
    """관리자용: 사람들이 왜 다음 곳에 안 가는지 (기획서의 네 가지 장벽 검증)."""
    recs = feedback_records()
    if not recs:
        st.caption("아직 기록이 없어요. '다음 한 곳' 화면에서 '갈게요 / 안 갈래요'를 누르면 여기에 모여요.")
        return
    go = sum(r["choice"] == "go" for r in recs)
    no = [r for r in recs if r["choice"] == "no"]
    c1, c2, c3 = st.columns(3)
    c1.metric("추천 받은 뒤 선택", f"{len(recs)}번")
    c2.metric("'갈게요' 비율", f"{go / len(recs) * 100:.0f}%")
    c3.metric("'안 갈래요'", f"{len(no)}번")
    if no:
        counts = {b: 0 for b in ("시간", "이동", "흥미", "동행", "기타")}
        for r in no:
            counts[r["barrier"]] = counts.get(r["barrier"], 0) + 1
        st.markdown("**안 가는 이유 (장벽별)**")
        st.bar_chart(counts, horizontal=True, height=220)
    names = temples.set_index("id")["name"].to_dict()
    with st.expander("기록 전체 보기"):
        st.dataframe([{**r, "from": names.get(r["from"], r["from"]), "to": names.get(r["to"], r["to"])}
                      for r in reversed(recs)], use_container_width=True, hide_index=True)
