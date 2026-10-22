"""把一个文件夹里的表情图导入机器人的表情库（按修改时间取最近的 N 张，非表情包自动跳过）。

用法：python import_stickers.py <文件夹> [数量=300]
导入前先停掉 bot.py，避免两边同时写 data/stickers/index.json。
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot import stickers, log

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def main():
    folder = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    files = sorted((f for f in folder.iterdir() if f.suffix.lower() in IMG_EXT),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    before = len(stickers.index)
    log.info("准备导入 %d 张，表情库现有 %d 张", len(files), before)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda f: stickers.collect(f.read_bytes()), files))
    log.info("导入完成：新增 %d 张，表情库共 %d 张", len(stickers.index) - before, len(stickers.index))


if __name__ == "__main__":
    main()
