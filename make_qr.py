"""make_qr.py — 사찰마다 붙일 QR 안내판(PNG) 만들기

사용법
  1) 앱을 배포해서 주소를 받는다 (예: https://gyeongju-sansa.streamlit.app)
  2) 아래 BASE_URL을 그 주소로 바꾼다
  3) python make_qr.py  →  qr/ 폴더에 사찰별 PNG가 생긴다 (인쇄해서 부착)

QR 안에는 '앱주소/?temple=사찰ID' 링크만 들어 있어요.
폰 기본 카메라로 찍으면 → 앱의 해당 사찰 소개 화면이 열리고 → 스탬프가 자동으로 찍혀요.
"""
import sys
from pathlib import Path

import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont

BASE_URL = "https://YOUR-APP.streamlit.app"      # ← 배포 후 실제 주소로 바꾸기
if len(sys.argv) > 1:                            # python make_qr.py https://주소  처럼 넘겨도 됨
    BASE_URL = sys.argv[1].rstrip("/")

HERE = Path(__file__).parent
OUT = HERE / "qr"

# 한글 글꼴 찾기 (윈도우 / 맥 / 코랩(나눔) / 리눅스(Noto) 순서로 시도)
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/malgunbd.ttf", 0),
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0),   # 코랩·Streamlit Cloud: fonts-nanum (packages.txt)
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),     # index 1 = 한국어(KR)
]


def font(size):
    for path, idx in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=idx)
    print("⚠️ 한글 글꼴을 못 찾아 기본 글꼴을 써요 (한글이 깨질 수 있음)")
    return ImageFont.load_default()


def make_poster(row, base_url: str | None = None) -> Image.Image:
    url = f"{(base_url or BASE_URL).rstrip('/')}/?temple={row['id']}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=20, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#3B2A1A", back_color="white").convert("RGB").resize((760, 760))

    W, H = 1000, 1400                                   # 인쇄용 세로 안내판
    img = Image.new("RGB", (W, H), "#FAF6EC")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 150], fill="#7A2E22")
    d.text((W / 2, 75), "경주 산사 순례길", font=font(58), fill="white", anchor="mm")
    d.text((W / 2, 235), row["name"].split(" · ")[0], font=font(72), fill="#2B2118", anchor="mm")
    d.text((W / 2, 315), row["hanja"].split(" · ")[0], font=font(38), fill="#8A7A66", anchor="mm")
    img.paste(qr_img, ((W - 760) // 2, 370))
    d.text((W / 2, 1180), "폰 카메라로 찍고 인증 사진을 올리면 스탬프!", font=font(38), fill="#2B2118", anchor="mm")
    d.text((W / 2, 1240), "Scan for the story (English included)", font=font(30), fill="#6B5B48", anchor="mm")
    d.text((W / 2, 1340), url, font=font(22), fill="#9A8D7C", anchor="mm")
    return img


if __name__ == "__main__":
    temples = pd.read_csv(HERE / "data" / "temples.csv").fillna("")
    targets = temples[temples["id"] != "campus"]
    OUT.mkdir(exist_ok=True)
    for _, row in targets.iterrows():
        make_poster(row).save(OUT / f"{row['id']}.png")
    print(f"{len(targets)}개 QR 안내판 생성 → {OUT}  (주소: {BASE_URL})")
