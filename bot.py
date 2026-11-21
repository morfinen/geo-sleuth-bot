import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OFFSET_FILE = Path("offset.json")
GEO_SLEUTH_DIR = Path("geo-sleuth")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text()).get("offset", 0)
        except (json.JSONDecodeError, OSError):
            return 0
    return 0


def save_offset(offset: int):
    try:
        OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    except OSError as e:
        print(f"Не удалось сохранить offset: {e}")


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Пришли фото — попробую определить, где оно сделано.\n"
        "Это может занять несколько минут."
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    await message.answer("🔍 Анализирую фото… Это может занять пару минут.")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "input.jpg"
        await bot.download_file(file.file_path, img_path)

        result = await run_geo_sleuth(img_path)

        if result["ok"]:
            text = (
                f"📍 Координаты: {result['lat']}, {result['lon']}\n"
                f"🧭 Азимут: {result['heading']}°\n"
                f"📏 Радиус ошибки: ±{result['radius']} м\n"
                f"📊 Уверенность: {result['confidence']}"
            )
            await message.answer(text)
            if result.get("evidence_image") and Path(result["evidence_image"]).exists():
                await message.answer_photo(FSInputFile(result["evidence_image"]))
        else:
            await message.answer(f"❌ Не удалось: {result['error']}")


async def run_geo_sleuth(img_path: Path) -> dict:
    scripts = GEO_SLEUTH_DIR / "skills" / "geo-sleuth" / "scripts"
    out_dir = img_path.parent / "out"
    out_dir.mkdir(exist_ok=True)

    cmds = [
        ["uv", "run", str(scripts / "intake.py"), str(img_path), "--out-dir", str(out_dir)],
        ["uv", "run", str(scripts / "geo.py"), str(out_dir)],
    ]

    for cmd in cmds:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode()[:500]}

    for json_file in out_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())
            if "lat" in data and "lon" in data:
                return {
                    "ok": True,
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                    "heading": data.get("heading", "N/A"),
                    "radius": data.get("radius", "N/A"),
                    "confidence": data.get("confidence", "N/A"),
                    "evidence_image": data.get("evidence_image"),
                }
        except (json.JSONDecodeError, OSError):
            continue

    return {"ok": False, "error": "Не найден JSON с координатами в выводе geo-sleuth"}


async def main():
    offset = load_offset()
    await dp.start_polling(
        bot,
        polling_timeout=25,
        offset=offset,
        handle_signals=False,
    )


if __name__ == "__main__":
    asyncio.run(main())
