"""表情包库，思路参考 MaiMBot 的 emoji_manager：
- 自动收集好友发来的表情包（QQ 的 mface 商城表情、sub_type=1 的自定义表情），用视觉模型写一句描述
- data/stickers/manual/ 里手动放的图片，启动时自动补描述
- 发消息时模型在回复里写 [表情:想表达的感觉]，按文本相似度挑一张最像的发出去
"""
import base64
import hashlib
import json
import logging
import random
import threading
from pathlib import Path

from memory import _bigrams, _cosine

log = logging.getLogger("bot")

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DESCRIBE_PROMPT = ("这是一张聊天里收藏的图。如果它不是表情包（而是生活照片、自拍、聊天截图、文档、风景、商品图等），只回答「不是表情包」。"
                   "如果是表情包，用一句话描述它表达的情绪、语气和画面内容（比如：得意地坏笑的小猫，有点欠揍），图上有字就带上，不超过 30 字。")


class StickerLibrary:
    def __init__(self, root: Path, describe):
        self.root = root
        self.describe = describe          # describe(prompt, image_bytes, mime) -> str
        (root / "collected").mkdir(parents=True, exist_ok=True)
        (root / "manual").mkdir(parents=True, exist_ok=True)
        self.index_file = root / "index.json"
        self.index = json.loads(self.index_file.read_text(encoding="utf-8")) if self.index_file.exists() else {}
        self.mu = threading.Lock()

    def _save(self):
        self.index_file.write_text(json.dumps(self.index, ensure_ascii=False, indent=1), encoding="utf-8")

    def scan_manual(self):
        """给 manual/ 里新放进来的图补描述。文件名本身也算描述的一部分。"""
        for f in sorted((self.root / "manual").iterdir()):
            key = f"manual/{f.name}"
            if f.suffix.lower() in IMG_EXT and key not in self.index:
                self._add(key, f.read_bytes(), hint=f.stem)

    def collect(self, data: bytes, hint=""):
        """收集好友发来的表情包，按内容哈希去重。"""
        h = hashlib.md5(data).hexdigest()[:16]
        ext = ".gif" if data[:3] == b"GIF" else ".png" if data[:4] == b"\x89PNG" else ".jpg"
        key = f"collected/{h}{ext}"
        if key in self.index or len(self.index) >= 500:
            return
        (self.root / key).write_bytes(data)
        self._add(key, data, hint)

    def _add(self, key, data, hint=""):
        mime = {"GIF": "image/gif", "\x89PN": "image/png"}.get(data[:3].decode("latin1"), "image/jpeg")
        try:
            desc = self.describe(DESCRIBE_PROMPT, data, mime)
        except Exception:
            log.exception("表情包描述失败 %s", key)
            return
        if "不是表情包" in desc:
            log.info("跳过非表情包 %s", key)
            (self.root / key).unlink(missing_ok=True)
            return
        with self.mu:
            self.index[key] = {"desc": (hint + " " + desc).strip(), "used": 0}
            self._save()
        log.info("表情包入库 %s：%s", key, self.index[key]["desc"])

    def pick(self, feeling, threshold=0.12, fallback=False):
        """按「想表达的感觉」挑一张表情包，返回 base64 字符串或 None。fallback=True 时没匹配上就随机挑一张。"""
        q = _bigrams(feeling)
        with self.mu:
            scored = [(_cosine(q, _bigrams(v["desc"])), k) for k, v in self.index.items()]
        top = sorted([s for s in scored if s[0] >= threshold], reverse=True)[:3]
        if not top:
            if not (fallback and scored):
                return None
            top = [random.choice(scored)]
        key = random.choice(top)[1]             # 前三里随机，免得老发同一张
        path = self.root / key
        if not path.exists():
            return None
        with self.mu:
            self.index[key]["used"] += 1
            self._save()
        log.info("发表情包 [%s] -> %s", feeling, key)
        return base64.b64encode(path.read_bytes()).decode()
