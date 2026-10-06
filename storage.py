"""storage.py — 이용자들이 올린 사진을 저장하고 불러오기

사진 한 장 = 기록 하나:
  {"id", "temple_id", "nickname", "kind"("stamp"=QR 인증 / "share"=그냥 공유), "caption", "created", "file"}

저장 장소는 두 가지 중 자동으로 골라요.
  1) Supabase (영구 보관)  — Streamlit 설정의 Secrets에 아래 두 줄이 있으면 사용
        SUPABASE_URL = "https://xxxx.supabase.co"
        SUPABASE_KEY = "service_role 키"
     + Supabase Storage에 'photos' 라는 bucket 만들기
  2) 앱 폴더 (user_uploads/) — 위 설정이 없을 때 기본값.
     내 컴퓨터에서는 계속 남지만, Streamlit Cloud에서는 앱이 재시작되면 사라져요.
"""
import io
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import streamlit as st
from PIL import Image, ImageOps

try:                                   # 아이폰 HEIC 사진도 열 수 있게 (없으면 그냥 넘어감)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

KST = timezone(timedelta(hours=9))
BUCKET = "photos"
INDEX = "index.json"
LOCAL_DIR = Path(__file__).parent / "user_uploads"
_lock = threading.Lock()


# ---------------------------------------------------------------- 사진 다듬기
def prepare_photo(raw: bytes, max_side: int = 1280) -> bytes:
    """폰 사진 → 회전 바로잡기 + 크기 줄이기 + JPEG.
    새로 저장하면서 위치(GPS) 같은 촬영 정보는 빠져요 (개인정보 보호)."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")      # 세로로 찍은 사진이 눕지 않게
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)        # exif를 안 넣고 저장 → 위치정보 제거
    return buf.getvalue()


def make_thumb(jpeg: bytes, side: int = 360) -> bytes:
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size                                          # 가운데를 정사각형으로 잘라 썸네일
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    return buf.getvalue()


# ---------------------------------------------------------------- 저장소 ① 앱 폴더
class LocalStore:
    name = "앱 폴더 (임시 저장)"
    permanent = False

    def __init__(self):
        LOCAL_DIR.mkdir(exist_ok=True)
        self.index_path = LOCAL_DIR / INDEX

    def _read(self) -> list:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write(self, records: list):
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.index_path)

    def records(self) -> list:
        return self._read()

    def put_image(self, file: str, data: bytes):
        (LOCAL_DIR / file).write_bytes(data)

    def get_image(self, file: str) -> bytes | None:
        p = LOCAL_DIR / file
        return p.read_bytes() if p.exists() else None

    def add(self, rec: dict):
        with _lock:
            recs = self._read()
            recs.append(rec)
            self._write(recs)

    def remove(self, rec_id: str):
        with _lock:
            recs = self._read()
            keep = [r for r in recs if r["id"] != rec_id]
            for r in recs:
                if r["id"] == rec_id:
                    (LOCAL_DIR / r["file"]).unlink(missing_ok=True)
            self._write(keep)


# ---------------------------------------------------------------- 저장소 ② Supabase
class SupabaseStore:
    name = "Supabase (영구 보관)"
    permanent = True

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/storage/v1/object"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}"}

    def _get(self, path: str) -> bytes | None:
        r = requests.get(f"{self.base}/{BUCKET}/{path}", headers=self.h, timeout=15)
        return r.content if r.status_code == 200 else None

    def _put(self, path: str, data: bytes, ctype: str):
        r = requests.post(f"{self.base}/{BUCKET}/{path}", data=data, timeout=30,
                          headers={**self.h, "Content-Type": ctype, "x-upsert": "true"})
        r.raise_for_status()

    def records(self) -> list:
        raw = self._get(INDEX)
        return json.loads(raw.decode("utf-8")) if raw else []

    def put_image(self, file: str, data: bytes):
        self._put(file, data, "image/jpeg")

    def get_image(self, file: str) -> bytes | None:
        return self._get(file)

    def _save_index(self, recs: list):
        self._put(INDEX, json.dumps(recs, ensure_ascii=False).encode("utf-8"), "application/json")

    def add(self, rec: dict):
        with _lock:
            recs = self.records()
            recs.append(rec)
            self._save_index(recs)

    def remove(self, rec_id: str):
        with _lock:
            recs = self.records()
            for r in recs:
                if r["id"] == rec_id:
                    requests.delete(f"{self.base}/{BUCKET}/{r['file']}", headers=self.h, timeout=15)
            self._save_index([r for r in recs if r["id"] != rec_id])


@st.cache_resource
def get_store():
    try:
        url, key = st.secrets.get("SUPABASE_URL"), st.secrets.get("SUPABASE_KEY")
    except Exception:                     # secrets 파일이 아예 없을 때
        url = key = None
    if url and key:
        return SupabaseStore(url, key)
    return LocalStore()


# ---------------------------------------------------------------- 앱에서 쓰는 함수들
def save_photo(temple_id: str, nickname: str, kind: str, raw: bytes, caption: str = "") -> dict:
    store = get_store()
    now = datetime.now(KST)
    rec_id = now.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    rec = {"id": rec_id, "temple_id": temple_id, "nickname": nickname.strip()[:20],
           "kind": kind, "caption": caption.strip()[:60], "created": now.isoformat(timespec="seconds"),
           "file": f"{temple_id}/{rec_id}.jpg"}
    jpeg = prepare_photo(raw)
    if isinstance(store, LocalStore):
        (LOCAL_DIR / temple_id).mkdir(exist_ok=True)
    store.put_image(rec["file"], jpeg)
    store.add(rec)
    list_photos.clear()
    return rec


def delete_photo(rec_id: str):
    get_store().remove(rec_id)
    list_photos.clear()


@st.cache_data(ttl=20, show_spinner=False)
def list_photos() -> list:
    """모든 사진 기록, 최신순. (20초마다 새로 읽어서 다른 사람이 올린 사진도 보이게)"""
    try:
        recs = get_store().records()
    except Exception:
        recs = []
    return sorted(recs, key=lambda r: r["created"], reverse=True)


@st.cache_data(show_spinner=False, max_entries=500)
def photo_bytes(file: str, thumb: bool = True) -> bytes | None:
    """사진 내용 (thumb=True면 정사각형 작은 사진). 한 번 읽은 건 기억해 둬요."""
    try:
        data = get_store().get_image(file)
    except Exception:
        data = None
    if data and thumb:
        return make_thumb(data)
    return data


def photos_for(temple_id: str | None = None, nickname: str | None = None, kind: str | None = None) -> list:
    out = list_photos()
    if temple_id:
        out = [r for r in out if r["temple_id"] == temple_id]
    if nickname:
        out = [r for r in out if r["nickname"] == nickname.strip()]
    if kind:
        out = [r for r in out if r["kind"] == kind]
    return out


def when_text(iso: str) -> str:
    """'방금', '3시간 전', '10/6' 처럼 짧게."""
    try:
        t = datetime.fromisoformat(iso)
    except Exception:
        return ""
    d = datetime.now(KST) - t
    if d < timedelta(minutes=1):
        return "방금"
    if d < timedelta(hours=1):
        return f"{int(d.total_seconds() // 60)}분 전"
    if d < timedelta(days=1):
        return f"{int(d.total_seconds() // 3600)}시간 전"
    return f"{t.month}/{t.day}"
