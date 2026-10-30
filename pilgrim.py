"""pilgrim.py — 🚶 AI가 함께 걷는 순례길 (한 절 더)

경주는 차로 '점 찍듯' 여행되기 쉬워요. 이 화면은 사찰과 사찰 사이를 걸어서 잇는 순례를
누구나 부담 없이 시작하고 끝까지 걸을 수 있게 도와요.

터치 3~4번이면 시작
  QR 찍기 → ① 누구랑 → ② 시간 → ③ 얼마나 걸을까 → ④ '이 길로 걷기 시작'

AI 기능 (기획서의 ①~⑦)
  ① 조건 이해      : 버튼 또는 한 마디 ("아이 둘, 두 시간, 오르막 싫어요")      → parse_condition
  ② 순례길 설계    : 도구(거리·고도·날씨·일몰)를 써서 1~3곳을 잇는 도보 순례길   → plan_route / agent_plan
  ③ 걷는 시간 예측 : 경사를 반영하는 토블러 보행 함수                            → walk_leg
  ④ 걸으며 듣기    : 출처 확인된 이야기(stories.json)만 써서 이야기 + 음성(TTS)   → leg_story / speak_button
  ⑤ 걷는 중 재계획 : '힘들어요'를 누르면 남은 시간·느려진 걸음으로 길을 다시 짬   → shorten
  ⑥ 순례 일지      : 걸은 길과 들은 이야기를 하루 기록으로 정리                  → journal_text
  ⑦ 기록·분석      : 시작·도착·그만둔 이유를 모아 구간별 완주율 확인              → log_event / dashboard

LLM 연결 (선택) — Streamlit Secrets에
    ANTHROPIC_API_KEY = "sk-ant-..."
    LLM_MODEL = "claude-haiku-5-5"
키가 없으면 모든 기능이 규칙 기반으로 돌아가요 (발표 때 인터넷이 끊겨도 안전).
"""
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import folium
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

from storage import append_json, load_json
from utils import CAMPUS_ID, base_map, haversine_km, kakao_walk_link, pin_icon, walk_geometry

KST = timezone(timedelta(hours=9))
SS = st.session_state
LOG_FILE = "pilgrim_log.json"
STORIES = json.loads((Path(__file__).parent / "stories.json").read_text(encoding="utf-8"))

WHO = ["혼자·친구", "아이와 함께", "어르신과 함께", "외국인 친구와"]
TIMES = {"1시간": 60, "2시간": 120, "반나절": 240}
LEVELS = ["가볍게 (평지만)", "보통", "산길도 좋아요"]
WHO_SPEED = {"아이와 함께": 0.75, "어르신과 함께": 0.75}      # 평소 걸음 대비 속도
STAY = {"bulguksa": 50, "museum": 40, "seokguram": 30}      # 둘러보는 시간(분), 나머지는 20분
MAX_STOPS = 3
NEAR_KM = 3.0                                                # 이 직선거리 안의 곳만 후보
STOP_REASONS = {
    "⏱️ 시간이 모자라요": "시간", "😮‍💨 너무 힘들어요": "체력", "😐 볼거리가 없어요": "흥미",
    "🌧️ 날씨·해 질 녘": "변수", "👨‍👩‍👧 같이 온 사람이 힘들어해요": "동행", "🤔 다른 이유": "기타",
}


def short(row) -> str:
    return str(row["name"]).split(" · ")[0]


def _eun(word: str) -> str:
    """받침 있으면 '은', 없으면 '는'."""
    ch = word[-1] if word else "가"
    return word + ("은" if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "는")


# ================================================================ ① 조건 이해
_KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4}


def parse_rule(text: str) -> dict:
    """LLM 없이 낱말로 알아듣기. 모르는 건 None."""
    t = text.replace(" ", "")
    out = {"who": None, "minutes": None, "level": None}
    if re.search(r"아이|애들|아기|아들|딸|초등|유모차", t):
        out["who"] = "아이와 함께"
    elif re.search(r"부모님|어르신|할머니|할아버지", t):
        out["who"] = "어르신과 함께"
    elif re.search(r"외국|foreign", t, re.I):
        out["who"] = "외국인 친구와"
    elif re.search(r"혼자|친구|연인|커플", t):
        out["who"] = "혼자·친구"
    if "반나절" in t:
        out["minutes"] = 240
    else:
        h, k, m = re.search(r"(\d+)시간", t), re.search(r"(한|두|세|네)시간", t), re.search(r"(\d+)분", t)
        mins = (int(h.group(1)) * 60 if h else _KO_NUM[k.group(1)] * 60 if k else 0) + (int(m.group(1)) if m else 0)
        if re.search(r"시간반", t):
            mins += 30
        out["minutes"] = mins or None
    if re.search(r"오르막싫|오르막은싫|평지|가볍게|힘든건싫|무릎", t):
        out["level"] = LEVELS[0]
    elif re.search(r"산길|등산|오르막좋|운동|빡세", t):
        out["level"] = LEVELS[2]
    return out


def _llm():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY"), st.secrets.get("LLM_MODEL", "claude-haiku-5-5")
    except Exception:
        return None, None


def llm_ready() -> bool:
    return bool(_llm()[0])


def _post(payload: dict) -> dict | None:
    key, model = _llm()
    if not key:
        return None
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=30,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": model, **payload})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def ask_llm(system: str, user: str, max_tokens: int = 400) -> str | None:
    res = _post({"max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
    if not res:
        return None
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text").strip() or None


@st.cache_data(show_spinner=False, ttl=3600)
def _parse_llm(text: str) -> dict | None:
    raw = ask_llm("경주 관광객의 말에서 걷기 조건을 뽑아 JSON 한 줄로만 답해. "
                  f'{{"who": {WHO} 중 하나 또는 null, "minutes": 남은 분(숫자) 또는 null, '
                  f'"level": {LEVELS} 중 하나 또는 null}}', text, 120)
    try:
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return {"who": d.get("who") if d.get("who") in WHO else None,
                "minutes": int(d["minutes"]) if d.get("minutes") else None,
                "level": d.get("level") if d.get("level") in LEVELS else None}
    except Exception:
        return None


def parse_condition(text: str) -> dict:
    rule = parse_rule(text)
    got = _parse_llm(text) if llm_ready() else None
    return {k: (got or {}).get(k) or rule[k] for k in rule}


# ================================================================ 도구: 고도 · 날씨 · 일몰
@st.cache_data(persist="disk", show_spinner=False)
def _elevations_cached(lats: tuple, lons: tuple) -> list:
    r = requests.get("https://api.open-meteo.com/v1/elevation", timeout=8,
                     params={"latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons))})
    return r.json()["elevation"]


def elevations(temples) -> dict:
    """사찰별 해발고도(m). Open-Meteo 고도 API (Copernicus DEM). 못 받으면 빈 사전."""
    try:
        vals = _elevations_cached(tuple(round(x, 5) for x in temples["lat"]),
                                  tuple(round(x, 5) for x in temples["lon"]))
        return dict(zip(temples["id"], vals))
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=900)
def conditions() -> dict | None:
    """경주 지금 날씨와 오늘 해 지는 시각 (Open-Meteo). 못 받으면 None."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, params={
            "latitude": 35.84, "longitude": 129.21, "current": "precipitation,temperature_2m",
            "daily": "sunset", "timezone": "Asia/Seoul", "forecast_days": 1})
        d = r.json()
        sunset = datetime.fromisoformat(d["daily"]["sunset"][0]).replace(tzinfo=KST)
        return {"rain": float(d["current"]["precipitation"] or 0) > 0.1,
                "temp": d["current"]["temperature_2m"], "sunset": sunset}
    except Exception:
        return None


def hill_ids(temples, courses) -> set:
    """산길로 가야 하는 곳 (남산·토함산 주소, '어려움' 코스). 고도 데이터가 없을 때 경사 어림에 써요."""
    ids = set(temples.loc[temples["address"].str.contains("남산|토함산") |
                          temples["name"].str.contains("남산"), "id"])
    for c in courses:
        if c["difficulty"] == 3:
            ids |= set(c["stops"])
    return ids - {CAMPUS_ID}


# ================================================================ ③ 걷는 시간 예측 (토블러 보행 함수)
def tobler_kmh(slope: float) -> float:
    """경사(오르막 +, 내리막 −)에 따른 걷는 속도. 평지 약 5km/h, 살짝 내리막(−5%)에서 가장 빠름 6km/h.
    Tobler, W. (1993). 출처: https://en.wikipedia.org/wiki/Tobler%27s_hiking_function"""
    return 6 * math.exp(-3.5 * abs(slope + 0.05))


def grade_label(slope: float) -> str:
    s = abs(slope)
    return "평지" if s < 0.03 else ("완만한 오르막" if slope > 0 and s < 0.08 else
                                   "완만한 내리막" if s < 0.08 else "오르막" if slope > 0 else "내리막")


def walk_leg(a, b, who: str, elev: dict, hills: set, speed_factor: float = 1.0) -> dict:
    """a → b 걷기 한 구간: 거리, 경사, 예상 시간."""
    is_hill = a["id"] in hills or b["id"] in hills
    dist = haversine_km(a.lat, a.lon, b.lat, b.lon) * (1.5 if is_hill else 1.3)   # 길은 직선보다 길어요
    if a["id"] in elev and b["id"] in elev and elev[a["id"]] is not None and elev[b["id"]] is not None:
        climb = elev[b["id"]] - elev[a["id"]]
        slope, src = climb / (dist * 1000), "고도 데이터"
    else:                                                     # 고도 데이터가 없으면 산길 여부로 어림
        slope = 0.10 if b["id"] in hills and a["id"] not in hills else (
            -0.10 if a["id"] in hills and b["id"] not in hills else 0.08 if is_hill else 0.0)
        climb, src = slope * dist * 1000, "어림"
    kmh = tobler_kmh(slope) * WHO_SPEED.get(who, 1.0) * speed_factor
    grade = "산길" if (src == "어림" and a["id"] in hills and b["id"] in hills) else grade_label(slope)
    return {"from": a["id"], "to": b["id"], "km": round(dist, 2), "min": max(3, round(dist / kmh * 60)),
            "slope": slope, "climb": round(climb), "grade": grade, "hill": b["id"] in hills, "src": src}


# ================================================================ ② 순례길 설계 (규칙 기반 계획기)
def _cond_budget(cond: dict, cnd: dict | None) -> tuple[int, list]:
    budget, warns = cond["minutes"], []
    if cnd:
        left = int((cnd["sunset"] - datetime.now(KST)).total_seconds() // 60) - 20
        if left < budget:
            budget = max(0, left)
            warns.append(f"🌇 오늘 해 지는 시각이 {cnd['sunset']:%H:%M}이라, 해 지기 20분 전에 끝나도록 맞췄어요.")
        if cnd["rain"]:
            warns.append("☔ 지금 비가 와요. 실내에서 마무리할 수 있게 짰어요.")
    return budget, warns


def evaluate(stop_ids: list, T, cond: dict, elev: dict, hills: set, speed_factor: float = 1.0) -> dict:
    """정해진 순서대로 걸을 때의 구간·합계."""
    legs = [walk_leg(T.loc[a], T.loc[b], cond["who"], elev, hills, speed_factor)
            for a, b in zip(stop_ids, stop_ids[1:])]
    walk_min = sum(l["min"] for l in legs)
    stay = sum(STAY.get(s, 20) for s in stop_ids[1:])
    return {"stops": stop_ids, "legs": legs, "walk_km": round(sum(l["km"] for l in legs), 1),
            "walk_min": walk_min, "stay_min": stay, "total_min": walk_min + stay}


def plan_route(start_id: str, temples, cond: dict, elev: dict, hills: set, visited: set,
               cnd: dict | None = None, speed_factor: float = 1.0, keep_end: str | None = None,
               max_stops: int = MAX_STOPS) -> dict:
    T = temples.set_index("id", drop=False)
    budget, warns = _cond_budget(cond, cnd)
    rain = bool(cnd and cnd["rain"])
    level = cond["level"]
    start = T.loc[start_id]
    pool = [t for _, t in temples.iterrows()
            if t["id"] not in (CAMPUS_ID, start_id) and t["id"] not in visited
            and haversine_km(start.lat, start.lon, t.lat, t.lon) <= NEAR_KM]
    if level == LEVELS[0]:
        pool = [t for t in pool if t["id"] not in hills]

    route, used, cats = [start_id], 0, {start["category"]}
    while len(route) - 1 < max_stops:
        cur = T.loc[route[-1]]
        best = None
        for t in pool:
            if t["id"] in route:
                continue
            leg = walk_leg(cur, t, cond["who"], elev, hills, speed_factor)
            need = leg["min"] + STAY.get(t["id"], 20)
            if used + need > budget or leg["min"] > 45:          # 한 구간 45분 넘게 걷는 건 빼요
                continue
            score = -leg["min"] / 8
            score += 2 if t["category"] != cur["category"] else 0     # 앞 장소와 다른 볼거리
            score += 1 if t["category"] not in cats else 0
            score += 1 if t["id"] in STORIES else 0                   # 출처 확인된 이야기가 있는 곳
            if leg["hill"] and level == LEVELS[1]:
                score -= 3
            if rain:
                score += 3 if t["id"] == "museum" else (-1 if t["category"] in ("절터", "명소") else 0)
            if keep_end and t["id"] == keep_end:
                score += 4
            if best is None or score > best[0]:
                best = (score, t, need)
        if best is None:
            break
        route.append(best[1]["id"])
        cats.add(best[1]["category"])
        used += best[2]

    plan = evaluate(route, T, cond, elev, hills, speed_factor)
    plan.update(warnings=warns, planner="규칙 기반 계획기", budget=budget,
                reasons=_reasons(plan, T, cond, temples, hills, start_id, rain))
    return plan


def _reasons(plan, T, cond, temples, hills, start_id, rain) -> list:
    if len(plan["stops"]) < 2:
        return []
    names = [short(T.loc[s]) for s in plan["stops"][1:]]
    out = [f"{cond['who']} 걸음으로 약 {plan['walk_min']}분 걷고, 둘러보는 시간까지 약 {plan['total_min']}분이라 "
           f"남은 {cond['minutes']}분 안에 들어와요."]
    grades = {l["grade"] for l in plan["legs"]}
    if grades <= {"평지", "완만한 내리막"}:
        out.append("오르막이 거의 없는 평지 길이라 편하게 걸을 수 있어요.")
    else:
        ups = [f"{short(T.loc[l['to']])} 가는 길" for l in plan["legs"] if l["grade"] in ("오르막", "완만한 오르막", "산길")]
        if ups:
            out.append(f"오르막·산길 구간: {', '.join(ups)}. 경사만큼 천천히 걷는 시간으로 계산했어요.")
    cats = [T.loc[s]["category"] for s in plan["stops"]]
    if len(set(cats)) >= 3:
        out.append(f"{' · '.join(dict.fromkeys(cats))}로 이어져서 볼거리가 겹치지 않아요.")
    if cond["level"] == LEVELS[0]:
        start = T.loc[start_id]
        near_hills = [t for _, t in temples.iterrows() if t["id"] in hills
                      and haversine_km(start.lat, start.lon, t.lat, t.lon) <= NEAR_KM]
        if near_hills:
            out.append("'가볍게'를 골라서 근처 산길은 뺐어요.")
    if rain and "museum" in plan["stops"]:
        out.append("비가 와서 실내인 박물관을 넣었어요.")
    return out


# ================================================================ ② 순례길 설계 (LLM 에이전트, 선택)
AGENT_TOOLS = [
    {"name": "find_nearby", "description": "지금 위치(마지막으로 정한 곳)에서 걸어서 갈 수 있는 후보 장소와 걷는 시간·경사를 돌려준다.",
     "input_schema": {"type": "object", "properties": {"from_id": {"type": "string"}}, "required": ["from_id"]}},
    {"name": "check_route", "description": "장소 ID 순서대로 걸을 때 구간별 시간·경사, 전체 시간, 남은 시간 안에 되는지를 돌려준다.",
     "input_schema": {"type": "object", "properties": {"stop_ids": {"type": "array", "items": {"type": "string"}}},
                      "required": ["stop_ids"]}},
    {"name": "get_conditions", "description": "경주 지금 날씨와 오늘 해 지는 시각, 그래서 쓸 수 있는 시간(분).",
     "input_schema": {"type": "object", "properties": {}}},
]


def agent_plan(start_id, temples, cond, elev, hills, visited, cnd) -> dict | None:
    """LLM이 도구를 골라 부르며(function calling) 순례길을 정해요. 실패하면 None → 규칙 계획기."""
    if not llm_ready():
        return None
    T = temples.set_index("id", drop=False)
    budget, warns = _cond_budget(cond, cnd)

    def run(name, args):
        if name == "get_conditions":
            return {"rain": bool(cnd and cnd["rain"]), "sunset": f"{cnd['sunset']:%H:%M}" if cnd else None,
                    "usable_minutes": budget}
        if name == "find_nearby":
            a = T.loc[args.get("from_id", start_id)]
            rows = []
            for _, t in temples.iterrows():
                if t["id"] in (CAMPUS_ID, a["id"]) or t["id"] in visited:
                    continue
                if haversine_km(a.lat, a.lon, t.lat, t.lon) > NEAR_KM:
                    continue
                if cond["level"] == LEVELS[0] and t["id"] in hills:
                    continue
                l = walk_leg(a, t, cond["who"], elev, hills)
                rows.append({"id": t["id"], "name": short(t), "category": t["category"], "walk_min": l["min"],
                             "grade": l["grade"], "stay_min": STAY.get(t["id"], 20), "has_story": t["id"] in STORIES})
            return sorted(rows, key=lambda r: r["walk_min"])[:12]
        if name == "check_route":
            ids = [i for i in args.get("stop_ids", []) if i in T.index]
            ev = evaluate(ids, T, cond, elev, hills)
            return {"legs": [{k: l[k] for k in ("from", "to", "km", "min", "grade")} for l in ev["legs"]],
                    "total_min": ev["total_min"], "fits": ev["total_min"] <= budget}
        return {"error": "unknown tool"}

    system = (f"너는 경주 사찰 도보 순례길을 짜는 AI 에이전트야. 출발: {start_id}({short(T.loc[start_id])}). "
              f"동행: {cond['who']}, 남은 시간: {cond['minutes']}분, 걷기: {cond['level']}. "
              "도구로 조건과 후보를 확인하고, 1~3곳을 골라 check_route로 시간 안에 되는지 꼭 확인해. "
              "볼거리가 겹치지 않고 동행이 편한 길을 골라. 마지막 답은 JSON 한 줄만: "
              '{"stops": [출발 포함 장소 ID들], "reasons": [이유 2~3개, 짧은 한국어 문장]}')
    messages = [{"role": "user", "content": "순례길을 짜 줘."}]
    for _ in range(6):
        res = _post({"max_tokens": 800, "system": system, "tools": AGENT_TOOLS, "messages": messages})
        if not res:
            return None
        messages.append({"role": "assistant", "content": res["content"]})
        calls = [b for b in res["content"] if b.get("type") == "tool_use"]
        if not calls:
            text = "".join(b.get("text", "") for b in res["content"] if b.get("type") == "text")
            try:
                d = json.loads(text[text.find("{"): text.rfind("}") + 1])
                stops = [s for s in d["stops"] if s in T.index]
                if not stops or stops[0] != start_id:
                    stops = [start_id] + [s for s in stops if s != start_id]
                plan = evaluate(stops[: MAX_STOPS + 1], T, cond, elev, hills)
                if plan["total_min"] > budget or len(plan["stops"]) < 2:
                    return None
                plan.update(warnings=warns, planner="AI 에이전트 (LLM 도구 호출)", budget=budget,
                            reasons=[str(r) for r in d.get("reasons", [])][:3])
                return plan
            except Exception:
                return None
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b["id"],
             "content": json.dumps(run(b["name"], b.get("input", {})), ensure_ascii=False)} for b in calls]})
    return None


# ================================================================ ④ 걸으며 듣는 이야기 (RAG + TTS)
def _first(text: str) -> str:
    i = str(text).find("요.")
    return str(text)[: i + 2] if 0 < i < 140 else str(text)[:100] + "…"


@st.cache_data(show_spinner=False, ttl=86400)
def _retell(text: str, who: str) -> str | None:
    tone = {"아이와 함께": "초등학생도 알아듣게 쉽고 다정하게", "어르신과 함께": "차분하고 공손하게",
            "외국인 친구와": "외국인에게 설명하듯 배경을 짧게 곁들여 (한국어로)"}.get(who, "친구에게 말하듯")
    return ask_llm("너는 경주 사찰 순례 해설사야. 아래 [자료]에 있는 사실만 써. 자료에 없는 연도·인물·전설은 절대 더하지 마. "
                   f"걸으면서 듣기 좋게 4문장 이내로, 말투는 {tone}. 목록이나 화살표 없이 문장으로.",
                   f"[자료] {text}", 400)


def leg_story(a_id: str, b_id: str, T, who: str) -> tuple[str, list, bool]:
    """(이야기, 출처 목록, AI가 말투를 바꿨는지)."""
    bridge = STORIES.get("_bridges", {}).get(f"{a_id}>{b_id}", "")
    if b_id in STORIES:
        base, sources = STORIES[b_id]["story"], STORIES[b_id]["sources"]
    else:
        base, sources = str(T.loc[b_id]["story"]), [{"title": "팀 작성 소개글 (국가유산청 자료 바탕, 출처 확인 중)", "url": ""}]
    text = (bridge + " " + base).strip()
    told = _retell(text, who) if llm_ready() else None
    return (told or text), sources, bool(told)


def speak_button(text: str, key: str):
    """폰 브라우저에 들어 있는 음성 합성(TTS)으로 읽어 줘요. 인터넷·키 필요 없음."""
    components.html(f"""
<button id="b" style="width:100%;padding:12px;border-radius:10px;border:1px solid #CBBFA8;background:#FFFDF8;
 font-size:16px;font-weight:600;color:#3B2A1A;cursor:pointer">🔊 이야기 듣기</button>
<script>
const t={json.dumps(text)}; const b=document.getElementById('b'); let on=false;
b.onclick=()=>{{ const s=window.speechSynthesis; if(!s){{b.textContent='이 브라우저는 음성을 지원하지 않아요';return;}}
 if(on){{s.cancel();on=false;b.textContent='🔊 이야기 듣기';return;}}
 const u=new SpeechSynthesisUtterance(t); u.lang='ko-KR'; u.rate=1.0;
 u.onend=()=>{{on=false;b.textContent='🔊 이야기 듣기';}}; s.cancel(); s.speak(u); on=true; b.textContent='⏹ 멈추기'; }};
</script>""", height=58)


def sources_html(sources: list) -> str:
    parts = [f"<a href='{s['url']}' target='_blank'>{s['title']}</a>" if s.get("url") else s["title"] for s in sources]
    return "<div style='font-size:0.75rem;opacity:.7;margin-top:4px'>출처: " + " · ".join(parts) + "</div>"


# ================================================================ ⑦ 기록
def _sid() -> str:
    if "pg_session" not in SS:
        SS["pg_session"] = uuid.uuid4().hex[:8]
    return SS["pg_session"]


def log_event(event: str, **data):
    try:
        append_json(LOG_FILE, {"time": datetime.now(KST).isoformat(timespec="seconds"),
                               "session": _sid(), "event": event, **data})
    except Exception:
        pass


# ================================================================ ⑥ 순례 일지
def journal_text(T, walked: list, heard: list, stopped: str = "") -> str:
    names = [short(T.loc[s]) for s in walked]
    km = sum(l["km"] for l in SS.get("pg_done_legs", []))
    mins = sum(l["min"] for l in SS.get("pg_done_legs", []))
    lines = [f"🪷 {datetime.now(KST):%Y.%m.%d} 나의 경주 순례 일지", ""]
    if len(names) >= 2:
        lines.append(f"{names[0]}에서 출발해 {', '.join(names[1:])}까지 약 {km:.1f}km를 {mins}분 동안 걸었어요.")
    else:
        lines.append(f"{names[0]}에서 순례를 시작했어요.")
    if stopped:
        lines.append(f"오늘은 {names[-1]}에서 걷기를 멈췄어요. ({stopped})")
    if heard:
        lines += ["", "길 위에서 들은 이야기"] + [f"· {short(T.loc[b])}: {_first(s)}" for b, s in heard]
    base = "\n".join(lines)
    if llm_ready():
        told = ask_llm("아래 순례 기록을 바탕으로 따뜻한 하루 일기 5문장을 써. 기록에 없는 사실은 더하지 마.", base, 400)
        if told:
            return base + "\n\n" + told
    return base


# ================================================================ 지도
def route_map(stops: list, T, height: int = 300, key: str = "pg_map"):
    pts = [T.loc[s] for s in stops]
    m = base_map(center=[pts[0].lat, pts[0].lon], zoom=15)
    allpts = [[p.lat, p.lon] for p in pts]
    for a, b in zip(pts, pts[1:]):
        geom = walk_geometry(float(a.lat), float(a.lon), float(b.lat), float(b.lon))
        if geom:
            folium.PolyLine(geom, color="#FFFFFF", weight=8, opacity=0.9).add_to(m)
            folium.PolyLine(geom, color="#7A2E22", weight=5, opacity=0.95).add_to(m)
            allpts += geom
        else:
            folium.PolyLine([[a.lat, a.lon], [b.lat, b.lon]], color="#7A2E22", weight=3,
                            dash_array="8 6", opacity=0.7).add_to(m)
    for n, p in enumerate(pts, 1):
        folium.Marker([p.lat, p.lon], icon=pin_icon(p["category"], number=n), tooltip=f"{n}. {short(p)}").add_to(m)
    lats, lons = [p[0] for p in allpts], [p[1] for p in allpts]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding_top_left=(30, 50),
                 padding_bottom_right=(30, 15))
    st_folium(m, height=height, use_container_width=True, returned_objects=[], key=key)


# ================================================================ 화면: 순례길 만들기
def _apply_text(k: str):
    got = parse_condition(SS.get(f"{k}_text", ""))
    if got["who"]:
        SS[f"{k}_who"] = got["who"]
    if got["minutes"]:
        SS[f"{k}_time"] = min(TIMES, key=lambda x: abs(TIMES[x] - got["minutes"]))
        SS[f"{k}_mins"] = got["minutes"]
    if got["level"]:
        SS[f"{k}_level"] = got["level"]


def _start_walk(k, plan, cond):
    SS["pg_walk"] = {"key": k, "plan": plan, "cond": cond, "leg": 0, "used": 0,
                     "walked": [plan["stops"][0]], "heard": [], "speed": 1.0, "stopped": ""}
    SS["pg_done_legs"] = []
    log_event("start", start=plan["stops"][0], stops=plan["stops"], planner=plan["planner"], **cond)


def pilgrim_view(temples, courses, current_id: str | None = None, key: str = "pg"):
    T = temples.set_index("id", drop=False)
    if SS.get("pg_walk"):
        walk_view(temples, courses)
        return

    if current_id is None:
        places = temples[temples["id"] != CAMPUS_ID]
        names = places["name"].tolist()
        pick = st.selectbox("지금 어디에 있어요?", names,
                            index=names.index("분황사") if "분황사" in names else 0, key=f"{key}_where")
        current_id = places.iloc[names.index(pick)]["id"]

    for k2, v in ((f"{key}_who", WHO[0]), (f"{key}_time", "2시간"), (f"{key}_level", LEVELS[1])):
        SS.setdefault(k2, v)
    who = st.segmented_control("① 누구랑 왔어요?", WHO, key=f"{key}_who") or WHO[0]
    tsel = st.segmented_control("② 시간은 얼마나 있어요?", list(TIMES), key=f"{key}_time",
                                on_change=lambda: SS.pop(f"{key}_mins", None)) or "2시간"
    level = st.segmented_control("③ 얼마나 걸을까요?", LEVELS, key=f"{key}_level") or LEVELS[1]
    with st.expander("✍️ 버튼 대신 말로 할래요 (선택)"):
        st.text_input("예) 초등학생 둘이랑 두 시간, 오르막은 싫어요", key=f"{key}_text",
                      on_change=_apply_text, args=(key,))
    cond = {"who": who, "minutes": SS.get(f"{key}_mins") or TIMES[tsel], "level": level}

    hills, elev, cnd = hill_ids(temples, courses), elevations(temples), conditions()
    visited = set(SS.get("visited", [])) - {current_id}
    cache_key = json.dumps([current_id, cond, sorted(visited)], ensure_ascii=False)
    cache = SS.setdefault("pg_plans", {})
    if cache_key not in cache:
        with st.spinner("AI가 순례길을 짜는 중…"):
            cache[cache_key] = (agent_plan(current_id, temples, cond, elev, hills, visited, cnd)
                                or plan_route(current_id, temples, cond, elev, hills, visited, cnd))
    plan = cache[cache_key]

    st.markdown("---")
    if len(plan["stops"]) < 2:
        st.warning("이 조건으로 걸어서 다녀올 수 있는 곳이 없어요. 시간을 늘리거나 '보통'·'산길도 좋아요'를 골라 보세요. "
                   "오늘은 여기서 천천히 둘러보는 것도 좋은 순례예요 🙂")
        for w in plan["warnings"]:
            st.caption(w)
        return

    path = " → ".join(short(T.loc[s]) for s in plan["stops"])
    st.markdown(f"#### 🚶 추천 순례길\n**{path}**")
    st.markdown(f"<span class='chip'>🚶 약 {plan['walk_km']}km</span><span class='chip'>걷기 약 {plan['walk_min']}분</span>"
                f"<span class='chip'>둘러보기 포함 약 {plan['total_min']}분</span>", unsafe_allow_html=True)
    for r in plan["reasons"]:
        st.markdown(f"- {r}")
    for w in plan["warnings"]:
        st.caption(w)
    route_map(plan["stops"], T, key=f"{key}_map")
    src = {l["src"] for l in plan["legs"]}
    st.caption(f"🤖 {plan['planner']} · 걷는 시간: 토블러 보행 함수 + {'고도 데이터(Open-Meteo)' if '고도 데이터' in src else '산길 여부로 경사 어림'}"
               f" · {who} 걸음 반영")
    if st.button("🚶 이 길로 걷기 시작", type="primary", use_container_width=True, key=f"{key}_go"):
        _start_walk(key, plan, cond)
        st.rerun()


# ================================================================ 화면: 걷는 중
def walk_view(temples, courses):
    T = temples.set_index("id", drop=False)
    w = SS["pg_walk"]
    plan, cond = w["plan"], w["cond"]
    legs, i = plan["legs"], w["leg"]

    if i >= len(legs) or w["stopped"]:
        _finish_view(T, w)
        return

    leg = legs[i]
    a, b = T.loc[leg["from"]], T.loc[leg["to"]]
    st.progress(i / len(legs), text=f"순례길 {i + 1}/{len(legs)} 구간 · {' → '.join(short(T.loc[s]) for s in plan['stops'])}")
    st.markdown(f"### 🚶 {short(b)}까지 걷는 중")
    st.markdown(f"<span class='chip'>약 {leg['km']}km</span><span class='chip'>약 {leg['min']}분</span>"
                f"<span class='chip'>{leg['grade']}</span>", unsafe_allow_html=True)

    story, sources, by_ai = leg_story(a["id"], b["id"], T, cond["who"])
    with st.container(border=True):
        st.markdown(f"**📖 가는 길에 듣는 이야기**  \n{story}")
        st.markdown(sources_html(sources), unsafe_allow_html=True)
        if by_ai:
            st.caption("AI가 위 출처의 내용 안에서만 말투를 바꿔 들려줘요.")
        speak_button(story, key=f"tts_{i}")
    st.caption("실제 서비스에서는 폰 위치가 가까워지면 이야기가 자동으로 나와요. 지금은 버튼으로 진행해요.")
    st.link_button("🧭 걷는 길 안내 (카카오맵)", kakao_walk_link(a, b), use_container_width=True)

    if st.button(f"📍 {short(b)}에 도착했어요", type="primary", use_container_width=True, key=f"arrive_{i}"):
        w["leg"] += 1
        w["used"] += leg["min"] + STAY.get(b["id"], 20)
        w["walked"].append(b["id"])
        w["heard"].append((b["id"], story))
        SS.setdefault("pg_done_legs", []).append(leg)
        SS.setdefault("visited", []).append(b["id"])
        log_event("arrive", leg=i, to=b["id"])
        st.rerun()

    if i < len(legs) - 1 and st.button("😮‍💨 힘들어요, 길을 줄여 주세요", use_container_width=True, key=f"tired_{i}"):
        shorten(temples, courses, w, a["id"], b["id"])
        st.rerun()

    with st.expander("🛑 오늘은 여기까지 할래요"):
        why = st.pills("이유 하나만 골라 주세요", list(STOP_REASONS), key=f"stop_{i}")
        if why:
            w["stopped"] = why
            log_event("stop", leg=i, at=a["id"], reason=STOP_REASONS[why])
            st.rerun()


def shorten(temples, courses, w, here_id, heading_id):
    """⑤ 걷는 중 재계획: 걸음을 20% 느리게 다시 계산하고, 남은 시간 안에서 길을 다시 짜요.
    원래 마지막 목적지는 되도록 지키고 중간을 건너뛰어요."""
    T = temples.set_index("id", drop=False)
    cond = dict(w["cond"])
    if cond["level"] == LEVELS[2]:
        cond["level"] = LEVELS[1]
    elif cond["level"] == LEVELS[1]:
        cond["level"] = LEVELS[0]
    w["speed"] *= 0.8
    remain = max(0, w["cond"]["minutes"] - w["used"])
    cond_r = {**cond, "minutes": remain}
    old_rest = w["plan"]["stops"][w["leg"] + 1:]
    new = plan_route(here_id, temples, cond_r, elevations(temples), hill_ids(temples, courses),
                     set(w["walked"]), conditions(), speed_factor=w["speed"], keep_end=old_rest[-1],
                     max_stops=max(1, len(old_rest) - 1))
    if len(new["stops"]) < 2:
        new = evaluate([here_id, heading_id], T, cond_r, elevations(temples), hill_ids(temples, courses), w["speed"])
        new.update(planner="재계획", reasons=[], warnings=[])
    done = w["plan"]["stops"][: w["leg"] + 1]
    full_stops = done + new["stops"][1:]
    full = evaluate(full_stops, T, w["cond"], elevations(temples), hill_ids(temples, courses), w["speed"])
    full.update(planner=w["plan"]["planner"] + " · 걷는 중 재계획", reasons=new.get("reasons", []), warnings=[])
    skipped = [short(T.loc[s]) for s in old_rest if s not in full_stops]
    w["plan"] = full
    SS["pg_toast"] = (f"길을 줄였어요. {_eun(', '.join(skipped))} 건너뛰고 "
                      f"{short(T.loc[full_stops[-1]])}에서 마무리해요." if skipped else
                      "걸음 속도에 맞춰 남은 시간을 다시 계산했어요.")
    log_event("shorten", leg=w["leg"], at=here_id, skipped=[s for s in old_rest if s not in full_stops])


def _finish_view(T, w):
    stopped = w["stopped"]
    if stopped:
        st.markdown(f"### 🙏 오늘은 {short(T.loc[w['walked'][-1]])}에서 멈췄어요")
        st.caption("멈춘 이유는 다음 사람의 순례길을 더 잘 짜는 데 쓰여요.")
    else:
        if not w.get("celebrated"):
            st.balloons()
            log_event("finish", stops=w["walked"])
            w["celebrated"] = True
        st.markdown("### 🎉 순례 완주!")
    st.caption("도착한 사찰마다 QR을 찍으면 스탬프가 찍혀요 (📿 스탬프 탭).")
    text = journal_text(T, w["walked"], w["heard"], stopped)
    with st.container(border=True):
        st.markdown("**📒 오늘의 순례 일지**")
        st.text(text)
    st.download_button("⬇️ 순례 일지 저장", text, file_name=f"순례일지_{datetime.now(KST):%Y%m%d}.txt",
                       use_container_width=True)
    if st.button("🔁 새 순례길 짜기", use_container_width=True):
        SS.pop("pg_walk", None)
        st.rerun()


def walk_toast():
    msg = SS.pop("pg_toast", None)
    if msg:
        st.toast(msg, icon="🧭")


# ================================================================ 관리자: 기록 보기
def dashboard(temples):
    recs = load_json(LOG_FILE, [])
    if not recs:
        st.caption("아직 기록이 없어요. 순례길을 시작·도착·멈추면 여기에 모여요.")
        return
    names = temples.set_index("id")["name"].to_dict()
    starts = [r for r in recs if r["event"] == "start"]
    fin = [r for r in recs if r["event"] == "finish"]
    stops = [r for r in recs if r["event"] == "stop"]
    c1, c2, c3 = st.columns(3)
    c1.metric("순례 시작", f"{len(starts)}번")
    c2.metric("완주율", f"{len(fin) / len(starts) * 100:.0f}%" if starts else "-")
    c3.metric("길 줄이기", f"{sum(r['event'] == 'shorten' for r in recs)}번")
    if stops:
        counts = {}
        for r in stops:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        st.markdown("**걷기를 멈춘 이유**")
        st.bar_chart(counts, horizontal=True, height=200)
        where = {}
        for r in stops:
            n = names.get(r["at"], r["at"])
            where[n] = where.get(n, 0) + 1
        st.markdown("**멈춘 곳**")
        st.bar_chart(where, horizontal=True, height=200)
    with st.expander("기록 전체"):
        st.dataframe(list(reversed(recs)), use_container_width=True, hide_index=True)
