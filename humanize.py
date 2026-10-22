"""拟人化：随机拆句、打字时间、错别字、说话小彩蛋。拆句和打字时间参考 MaiMBot 的 utils.py。"""
import random
import re

# 括号动作（挠头）、表情包标记 [表情:xx] 不能被拆开
_PROTECT = re.compile(r"（[^（）]*）|\([^()]*\)|\[[^\[\]]*\]")


def _protect(text):
    mapping = {}

    def repl(m):
        key = f"\x00{len(mapping)}\x00"
        mapping[key] = m.group(0)
        return key
    return _PROTECT.sub(repl, text), mapping


def _recover(s, mapping):
    for k, v in mapping.items():
        s = s.replace(k, v)
    return s


def split_reply(text, max_parts=3):
    """把一段回复随机拆成几条消息：越长拆得越碎，问号感叹号句号后也可能断开。"""
    text = text.strip().replace(",", "，")
    if not text:
        return []
    parts = []
    for para in [p for p in text.splitlines() if p.strip()]:   # 模型自己换行的地方一定断开
        parts.extend(_split_para(para.strip()))
    parts = [p for p in parts if p]
    while len(parts) > max_parts:                               # 太碎就把相邻两条短的合起来
        i = min(range(len(parts) - 1), key=lambda k: len(parts[k]) + len(parts[k + 1]))
        parts[i:i + 2] = [parts[i] + " " + parts[i + 1]]
    return parts


def _split_para(text):
    n = len(text)
    if n < 6:
        return [text]
    strength = 0.3 if n < 12 else 0.6 if n < 32 else 0.85
    text, mapping = _protect(text)

    pieces, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "，" and random.random() < strength:
            pieces.append(cur[:-1])
            cur = ""
        elif ch in "。！？!?…~～" and random.random() < strength:
            pieces.append(cur.rstrip("。") if ch == "。" else cur)
            cur = ""
        elif ch == " " and random.random() < strength:
            pieces.append(cur.strip())
            cur = ""
    pieces.append(cur)

    out = []
    for p in pieces:
        p = _recover(p.strip().rstrip("，"), mapping).strip()
        if p.endswith("。") and random.random() < 0.7:   # 聊天里很少打句号
            p = p[:-1]
        if p:
            out.append(p)
    return out


def typing_seconds(s, zh=0.4, other=0.2, cap=8.0):
    """按字数估算打字时间：中文 0.4s/字，其他 0.2s/字，再加 0.3s 回车。"""
    zh_n = sum(1 for c in s if "一" <= c <= "鿿")
    t = zh_n * zh + (len(s) - zh_n) * other + 0.3
    return min(t * random.uniform(0.8, 1.2), cap)


# 真实聊天里常见的同音 / 形近错字，只挑不影响理解的
TYPO_TABLE = {
    "的": "得", "得": "的", "地": "的", "在": "再", "再": "在", "做": "作", "那": "哪",
    "吗": "嘛", "嘛": "吗", "已": "以", "以": "已", "吧": "八", "呢": "呐", "啦": "辣",
    "好": "号", "是": "事", "道": "到", "到": "道", "想": "像", "像": "想", "么": "嘛",
    "他": "她", "她": "他", "就": "揪", "还": "换", "真": "针", "完": "玩", "玩": "完",
}


def add_typo(sentence, rate):
    """以 rate 的概率给一句话换一个错字，返回 (新句子, 纠正消息或 None)。"""
    if random.random() >= rate:
        return sentence, None
    idx = [i for i, c in enumerate(sentence) if c in TYPO_TABLE]
    if not idx:
        return sentence, None
    i = random.choice(idx)
    wrong = sentence[:i] + TYPO_TABLE[sentence[i]] + sentence[i + 1:]
    fix = f"*{sentence[i]}" if random.random() < 0.7 else None   # 有时发现了会补一句纠正
    return wrong, fix


def style_egg():
    """小概率给这次回复加一点句式变化。"""
    r = random.random()
    if r < 0.04:
        return "这次回复可以用一下倒装句。"
    if r < 0.05:
        return "这次回复可以故意拽一句文言文，然后自己吐槽一下。"
    return ""
