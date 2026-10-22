"""每个好友一份长期记忆 + 好感度。思路参考 MaiMBot 的 memory_system 和 relationship_manager：
历史记录溢出时，把最老的一段让模型概括成「话题: 一句话」存下来；聊天时按文本相似度取回相关记忆。
同一次概括顺便让模型判断对方态度，更新好感度。记忆只在和这个好友聊天时使用，不会串到别人那里。
"""
import json
import logging
import math
import time
from collections import Counter
from pathlib import Path

log = logging.getLogger("bot")

LEVELS = [  # (下限, 关系, 回复态度)
    (0, "关系冷淡", "礼貌但简短，不主动延伸话题"),
    (15, "不太熟", "客气友好，少开过火的玩笑"),
    (30, "普通朋友", "轻松友好，正常玩梗"),
    (50, "熟络的朋友", "放松随意，会接梗、吐槽、分享自己的日常"),
    (70, "好朋友", "很放得开，爱撒娇、互损、主动关心对方"),
    (85, "挚友", "最黏糊最随意的一档，什么梗都能接，会记挂对方的事"),
]

EXTRACT_PROMPT = """下面是你（{bot}，{owner}的 AI 分身）在「{name}」里的一段聊天记录（{period}）。

{chat}

请完成两件事，输出 JSON：
1. memories：从中提取值得长期记住的信息，比如对方的近况、喜好、计划、聊过的重要话题、群里的新梗、你答应过的事。每条是 {{"topic": "2~6 字的话题", "content": "一句自然的话概括，带上时间、人和具体细节"}}。闲聊寒暄不用记，没有就返回空数组。最多 5 条。
   不要记录任何人的感情关系、健康、家庭、工作单位、住址、联系方式等隐私。
2. affinity_delta：这段聊天里对方对你的态度，-5 到 5 的整数（友好热情为正，冷淡反感为负，普通闲聊给 0 或 1）。

格式：{{"memories": [...], "affinity_delta": 0}}"""

MERGE_PROMPT = """下面是关于 QQ 好友「{name}」的 {n} 条记忆，有重复和过时的。请合并整理成不超过 {keep} 条，保留具体细节和时间，同一话题的合并成一条，过时的以新的为准。
{items}
输出 JSON：{{"memories": [{{"topic": "...", "content": "..."}}]}}"""


def _bigrams(text):
    text = "".join(ch for ch in text if not ch.isspace())
    return Counter(text[i:i + 2] for i in range(len(text) - 1)) or Counter(text)


def _cosine(a, b):
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class FriendMemory:
    def __init__(self, path: Path, cfg):
        self.path = path
        self.cfg = cfg
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.memories = d.get("memories", [])
        self.affinity = d.get("affinity", cfg.get("initial_affinity", 40))
        self.last_day = d.get("last_day", "")

    def save(self):
        self.path.write_text(json.dumps({"memories": self.memories, "affinity": self.affinity,
                                         "last_day": self.last_day}, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    # ---------- 好感度 ----------
    def level(self):
        for lo, name, attitude in reversed(LEVELS):
            if self.affinity >= lo:
                return name, attitude
        return LEVELS[0][1:]

    def add_affinity(self, delta):
        self.affinity = max(0, min(100, self.affinity + delta))

    def touch_day(self):
        """每天第一次聊天，好感度 +1（常来聊的人会慢慢变熟）。"""
        today = time.strftime("%Y-%m-%d")
        if self.last_day != today:
            self.last_day = today
            self.add_affinity(1)

    # ---------- 记忆 ----------
    def recall(self, text, k=5, threshold=0.08):
        q = _bigrams(text)
        scored = [(_cosine(q, _bigrams(m["topic"] + m["content"])), m) for m in self.memories]
        scored = [x for x in scored if x[0] >= threshold]
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:k]]

    def prompt(self, text):
        name, attitude = self.level()
        lines = [f"你和对方的关系：{name}（好感度 {self.affinity}/100）。回复态度：{attitude}。"]
        mems = self.recall(text)
        if mems:
            lines.append("你记得和对方有关的这些事（自然地用上，别生硬复述）：")
            lines += [f"- {m['time']} 关于「{m['topic']}」：{m['content']}" for m in mems]
        return "\n".join(lines)

    def consolidate(self, llm_json, name, chunk):
        """把一段旧聊天记录概括进记忆，并按对方态度调整好感度。"""
        times = [m["t"] for m in chunk if m.get("t")]
        period = (time.strftime("%Y-%m-%d %H:%M", time.localtime(times[0])) + " 至 "
                  + time.strftime("%m-%d %H:%M", time.localtime(times[-1]))) if times else "时间不详"
        chat = "\n".join(f"{'对方' if m['role'] == 'user' else '你'}：{m['content']}" for m in chunk)
        res = llm_json(EXTRACT_PROMPT.format(name=name, period=period, chat=chat,
                                             bot=self.cfg.get("bot_name", "小白"), owner=self.cfg.get("owner_name", "主人")))
        day = time.strftime("%Y-%m-%d", time.localtime(times[-1] if times else time.time()))
        new = [{"topic": m["topic"], "content": m["content"], "time": day}
               for m in res.get("memories", []) if m.get("topic") and m.get("content")]
        self.memories.extend(new)
        delta = max(-5, min(5, int(res.get("affinity_delta", 0))))
        self.add_affinity(delta)
        log.info("记忆整理 %s：新增 %d 条，好感度 %+d -> %d", name, len(new), delta, self.affinity)

        max_n, keep = self.cfg.get("max_memories", 60), self.cfg.get("max_memories", 60) * 2 // 3
        if len(self.memories) > max_n:
            items = "\n".join(f"- [{m['time']}] {m['topic']}：{m['content']}" for m in self.memories)
            merged = llm_json(MERGE_PROMPT.format(name=name, n=len(self.memories), keep=keep, items=items))
            if merged.get("memories"):
                today = time.strftime("%Y-%m-%d")
                self.memories = [{"topic": m["topic"], "content": m["content"], "time": m.get("time", today)}
                                 for m in merged["memories"]][:keep]
                log.info("记忆合并 %s：-> %d 条", name, len(self.memories))
        self.save()
