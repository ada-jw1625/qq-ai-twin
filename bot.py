"""QQ AI 分身机器人：NapCat (OneBot v11 正向 WebSocket) + OpenAI 兼容接口。

回好友私聊；在 cfg["groups"] 里的群，被 @ 或被回复时才回。
好友连发的几条消息会攒 debounce_seconds 秒再一起回。
主人（owner_qq）私聊发 #帮助 查看管理指令。
"""
import base64
import json
import logging
import random
import re
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
import websocket
from openai import OpenAI

import fun
import humanize
from memory import FriendMemory
from profiles import Profiles
from stickers import StickerLibrary

ROOT = Path(__file__).parent
DATA = ROOT / "data"
HISTORY_DIR = DATA / "history"
MEMORY_DIR = DATA / "memory"
GROUP_DIR = DATA / "groups"
NOTIFIED_FILE = DATA / "notified.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "bot.log", encoding="utf-8")])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
BOT = cfg.get("bot_name", "小白")            # 机器人自己的名字
OWNER = cfg.get("owner_name", "主人")        # 本人的称呼
persona = (ROOT / cfg.get("persona_file", "persona.md")).read_text(encoding="utf-8")
# 国内 API 直连，不走系统代理（v2rayN 没开时会连不上）
http = httpx.Client(trust_env=False, timeout=60, follow_redirects=True)
llm = OpenAI(base_url=cfg["llm_base_url"], api_key=cfg["llm_api_key"], http_client=http)

HISTORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
GROUP_DIR.mkdir(parents=True, exist_ok=True)
notified = set(json.loads(NOTIFIED_FILE.read_text())) if NOTIFIED_FILE.exists() else set()
profiles = Profiles(ROOT / cfg.get("profiles_dir", "profiles"))
fun.setup(BOT, OWNER, profiles.group_cps)
paused = False
self_id = None              # 机器人自己的 QQ 号，收到第一条事件时记下
bot_msg_ids = set()         # 机器人在群里发过的消息 id，用来判断别人是不是在回复它
MENTION_RE = re.compile(r"@\s*" + re.escape(BOT))

STICKER_TAG = re.compile(r"\[表情[:：]([^\]]*)\]")
# QQ 系统表情 id，狗头即「汪汪」
QQ_FACES = {"狗头": 277, "捂脸": 264, "斜眼笑": 178, "呲牙": 13, "流泪": 5, "害羞": 6,
            "笑哭": 182, "吃瓜": 271, "暗中观察": 269, "摸鱼": 285, "头秃": 267, "辣眼睛": 265, "哦哟": 266,
            "问号脸": 268, "我酸了": 273, "敬礼": 282, "比心": 319, "期待": 294, "委屈": 106, "可怜": 111,
            "卖萌": 175, "大哭": 9, "拥抱": 49, "爱心": 66, "赞": 76, "托腮": 212, "喵喵": 307, "啵啵": 214,
            "蹭一蹭": 219, "脑阔疼": 262, "坏笑": 101, "擦汗": 97}
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]\uFE0F?")
FACE_NAMES = {str(v): k for k, v in QQ_FACES.items()}
FACE_TAG = re.compile(r"\[(" + "|".join(QQ_FACES) + r")\]")


def to_segments(text):
    """把文字里的 [狗头] 等标记换成 QQ 表情消息段；没有标记就原样返回字符串。"""
    if not FACE_TAG.search(text):
        return text
    segs, pos = [], 0
    for m in FACE_TAG.finditer(text):
        if text[pos:m.start()]:
            segs.append({"type": "text", "data": {"text": text[pos:m.start()]}})
        segs.append({"type": "face", "data": {"id": str(QQ_FACES[m.group(1)])}})
        pos = m.end()
    if text[pos:]:
        segs.append({"type": "text", "data": {"text": text[pos:]}})
    return segs


def preview(m):
    """日志里显示要发的消息。"""
    if isinstance(m, str):
        return m
    return "".join(s["data"].get("text", "") if s["type"] == "text" else
                   f"[{FACE_NAMES.get(s['data'].get('id'), 'QQ表情')}]" if s["type"] == "face" else "[表情包]" for s in m)


FORMAT_RULES = """
## 回复格式
- 只输出要发给对方的话：一段短短的口语，像打字聊天一样。代码会按逗号、空格、标点自动拆成几条消息发出去，你不用自己分行。
- 整段一般不超过 30 字；对方认真问问题时可以多说一点，但也别超过 80 字。
- 表情（严肃话题都不用）：
  - 开玩笑或语气重的句子末尾必须加 [狗头]；尴尬、不好意思、心虚时末尾加 [狗头]💦💦
  - 其他时候可以在最贴切的那句末尾放一个 emoji（😋 😭 💦 🤔 等）或 QQ 表情，QQ 表情写成方括号：""" \
    + " ".join(f"[{k}]" for k in QQ_FACES if k != "狗头") + """
    （代码会控制频率，大约每 5 句留一个，你不用刻意少放）
  - 对方让你发表情包（「发个表情包」「来张 xx 的表情包」「斗图」）时，一定会发一张：文字只回一小句，[表情:...] 写成对方要的那种感觉
  - 每次回复最后都写一个 [表情:这次想表达的感觉]，比如 [表情:得意坏笑]、[表情:哭哭撒娇]、[表情:无语]。代码按它从表情包库挑图，大约每 4 句发一张
- 对方发的图片会写成 [图片：内容描述]，表情包写成 [表情包：描述]，当作你看到了这张图来回应。"""


def vision(prompt, data: bytes, mime):
    b64 = base64.b64encode(data).decode()
    r = llm.chat.completions.create(
        model=cfg["vision_model"], max_tokens=1000, reasoning_effort="low",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
    return (r.choices[0].message.content or "").strip()


def llm_json(prompt):
    r = llm.chat.completions.create(
        model=cfg["llm_model"], max_tokens=2000, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}])
    text = r.choices[0].message.content or "{}"
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


stickers = StickerLibrary(DATA / "stickers", vision)


class Friend:
    def __init__(self, uid):
        self.mu = threading.Lock()       # 保护 buffer / timer
        self.busy = threading.Lock()     # 同一好友同时只处理一轮
        self.buffer = []
        self.timer = None
        self.nickname = ""
        self.day = date.today()
        self.count = 0
        self.memory = FriendMemory(MEMORY_DIR / f"{uid}.json", cfg)
        self.pacer = Pacer()


friends = {}
friends_mu = threading.Lock()
ws_app = None
send_mu = threading.Lock()


def get_friend(uid):
    with friends_mu:
        if uid not in friends:
            friends[uid] = Friend(uid)
        return friends[uid]


def call_api(action, params):
    with send_mu:
        ws_app.send(json.dumps({"action": action, "params": params}, ensure_ascii=False))


def send_private(uid, message):
    call_api("send_private_msg", {"user_id": uid, "message": message})


def send_group(gid, message):
    with send_mu:
        ws_app.send(json.dumps({"action": "send_group_msg", "params": {"group_id": gid, "message": message},
                                "echo": "group_send"}, ensure_ascii=False))


def load_history(uid):
    f = HISTORY_DIR / f"{uid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def save_history(uid, history):
    (HISTORY_DIR / f"{uid}.json").write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 收消息：把消息段转成文字，图片交给视觉模型 ----------

def download(url):
    try:
        r = http.get(url, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("下载图片失败 %s: %s", url[:80], e)
        return None


def mime_of(data):
    return "image/gif" if data[:3] == b"GIF" else "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


def describe_segment(t, d):
    """图片 / 表情包 -> 文字描述；表情包顺手收进表情库。"""
    if t == "mface":
        url = d.get("url") or ("https://gxh.vip.qq.com/club/item/parcel/item/"
                               f"{d.get('emoji_id', '')[:2]}/{d.get('emoji_id', '')}/raw300.gif")
        summary = d.get("summary", "").strip("[]")
        data = download(url) if d.get("emoji_id") or d.get("url") else None
        if data:
            stickers.collect(data, hint=summary)
        return f"[表情包：{summary or '商城表情'}]"

    is_sticker = str(d.get("sub_type", "0")) == "1"
    data = download(d["url"]) if d.get("url") else None
    if not data:
        return "[表情包]" if is_sticker else "[图片]"
    if is_sticker:
        stickers.collect(data)
        try:
            return f"[表情包：{vision('这是聊天里对方发的表情包，用一句话描述它的情绪和画面，不超过 20 字。', data, mime_of(data))}]"
        except Exception:
            return "[表情包]"
    try:
        desc = vision("好友在 QQ 上发来一张图片。用一两句话描述内容，图里有文字就写出来，不超过 80 字。", data, mime_of(data))
        return f"[图片：{desc}]"
    except Exception:
        log.exception("看图失败")
        return "[图片]"


def segments_to_text(message, with_media=True):
    if isinstance(message, str):
        return message
    parts = []
    for seg in message:
        t, d = seg.get("type"), seg.get("data", {})
        if t == "text":
            parts.append(d.get("text", ""))
        elif t == "face":
            parts.append(f"[{FACE_NAMES.get(str(d.get('id')), 'QQ表情')}]")
        elif t in ("image", "mface"):
            parts.append(describe_segment(t, d) if with_media else "[图片]")
        elif t == "record":
            parts.append("[语音]")
        elif t == "video":
            parts.append("[视频]")
        elif t == "file":
            parts.append("[文件]")
        elif t == "reply":
            parts.append("[回复了你的消息]" if str(d.get("id")) in bot_msg_ids else "[引用了一条消息]")
        elif t == "at":
            qq = str(d.get("qq", ""))
            if qq == str(self_id):
                parts.append("@" + BOT)
            else:
                parts.append("@" + (profiles.name(qq) if qq.isdigit() and profiles.name(qq) else d.get("name") or qq))
        else:
            parts.append(f"[{t}]")
    return "".join(parts).strip()


# ---------- 生成回复 ----------

def generate_reply(uid, st, incoming):
    history = load_history(uid)
    text_in = "\n".join(incoming)
    history.append({"role": "user", "content": text_in, "t": time.time()})
    recent = "\n".join(m["content"] for m in history[-6:])
    who = profiles.name(uid)
    profile = profiles.friend(uid, private=True)
    system = "\n\n".join(filter(None, [
        persona,
        FORMAT_RULES,
        f"## 当前对话\n你正在和 QQ 好友「{st.nickname}」私聊" + (f"，你认得 TA：这是{who}" if who else "")
        + f"。现在是 {time.strftime('%Y-%m-%d %H:%M')}。对方连发的多条消息用换行隔开。",
        f"## 你对{who}的了解（档案）\n{profile}\n\n"
        "这是私聊，档案里关于 TA 自己的事（包括感情经历）都可以聊。档案里的事和 TA 说过的话可以像老朋友一样"
        "自然地提起、接梗、追问后续，别一次全倒出来，也别生硬地复述；TA 没提的伤心事别主动揭。"
        f"{OWNER}自己的隐私和第三个人的私事仍然不说。" if profile else "",
        st.memory.prompt(recent),
        humanize.style_egg(),
    ]))
    resp = llm.chat.completions.create(
        model=cfg["llm_model"],
        temperature=cfg.get("temperature", 1.3),
        max_tokens=1500,
        messages=[{"role": "system", "content": system}]
                 + [{"role": m["role"], "content": m["content"]} for m in history[-cfg["history_turns"] * 2:]],
    )
    reply = (resp.choices[0].message.content or "").strip()
    if reply:
        history.append({"role": "assistant", "content": reply, "t": time.time()})
    save_history(uid, history)
    return reply, history


class Pacer:
    """控制表情频率：约每 emoji_every 条消息留一个 emoji / QQ 表情（狗头不算），约每 sticker_every 条发一张表情包。"""
    def __init__(self):
        self.since_emoji = 99
        self.since_sticker = 99


def strip_emoji(s):
    if "[狗头]" in s:                                 # 狗头和跟着的 💦💦（尴尬）不受频率限制
        s = re.sub(r"(\[狗头\]\s*)((?:💦)+)", lambda m: m.group(1) + "\x00" * len(m.group(2)), s)
    s = EMOJI_RE.sub("", s).replace("\x00", "💦")
    return FACE_TAG.sub(lambda m: m.group(0) if m.group(1) == "狗头" else "", s).strip()


STICKER_REQUEST = re.compile(r"(发|来|整|甩|丢|给|斗)[个张点一些几]{0,3}.{0,8}表情包?|斗图")


def build_messages(reply, pacer, request=""):
    """回复文本 -> 依次要发的消息（文字拆句、QQ 表情、错别字、表情包）。
    request 是对方刚发的话：里面在要表情包（「发个表情包」「斗图」）就不管频率，一定发一张。"""
    feelings = STICKER_TAG.findall(reply)
    if request and STICKER_REQUEST.search(request):
        asked = re.sub(r"@?\s*" + re.escape(BOT) + r"|表情包?|斗图|发|来|整|甩|丢|给我?|[个张点一些几的吧呗啊呀！!？?~～，。\s]", "", request)
        feeling = asked or (feelings[0] if feelings and feelings[0].strip() else "")
        b64 = stickers.pick(feeling, fallback=True)
        if b64:
            pacer.since_sticker = -1                  # 下面会 +1，算刚发过
            text = STICKER_TAG.sub("", reply).strip()
            out = [to_segments(s) for s in humanize.split_reply(text)]
            out.append([{"type": "image", "data": {"file": f"base64://{b64}", "sub_type": 1, "summary": "[动画表情]"}}])
            return out
    text = STICKER_TAG.sub("", reply).strip()
    out = []
    for s in humanize.split_reply(text):
        s, fix = humanize.add_typo(s, cfg.get("typo_rate", 0))
        bare = strip_emoji(s)
        if bare != s.strip():                         # 这句带了 emoji / QQ 表情（狗头之外）
            if pacer.since_emoji >= cfg.get("emoji_every", 5) - 1 and bare:
                pacer.since_emoji = 0
            else:
                s = bare or s                         # 频率没到就去掉；整句只有表情的话留着
                pacer.since_emoji += 1
        else:
            pacer.since_emoji += 1
        pacer.since_sticker += 1
        if s:
            out.append(to_segments(s))
        if fix:
            out.append(fix)
    if feelings and feelings[0].strip() and pacer.since_sticker >= cfg.get("sticker_every", 7):
        b64 = stickers.pick(feelings[0])
        if b64:
            pacer.since_sticker = 0
            sticker = [{"type": "image", "data": {"file": f"base64://{b64}", "sub_type": 1, "summary": "[动画表情]"}}]
            # 表情包一般跟在最后，偶尔单独发在前面
            out.insert(0 if random.random() < 0.2 else len(out), sticker)
    return out


def maybe_consolidate(uid, st, history):
    """历史太长时把最老的一段整理成长期记忆。"""
    limit = cfg["history_turns"] * 2
    if len(history) <= limit:
        return
    cut = 20
    while cut < len(history) and history[cut]["role"] != "user":   # 切在一轮对话的边界
        cut += 1
    chunk, rest = history[:cut], history[cut:]
    try:
        st.memory.consolidate(llm_json, st.nickname, chunk)
    except Exception:
        log.exception("整理记忆失败 uid=%s", uid)
        return
    save_history(uid, rest)


def process(uid):
    st = get_friend(uid)
    with st.busy:
        with st.mu:
            raw, st.buffer = st.buffer, []
        if not raw or paused:
            return
        if st.day != date.today():
            st.day, st.count = date.today(), 0
        if st.count >= cfg["max_replies_per_friend_per_day"]:
            log.info("好友 %s 今日回复已达上限，跳过", uid)
            return
        st.count += 1
        st.memory.touch_day()
        st.memory.save()

        if uid not in notified:
            send_private(uid, cfg["notice"])
            notified.add(uid)
            NOTIFIED_FILE.write_text(json.dumps(sorted(notified, key=str), ensure_ascii=False))
            time.sleep(1.5)

        incoming = [segments_to_text(m) for m in raw]
        incoming = [t for t in incoming if t]
        if not incoming:
            return
        try:
            reply, history = generate_reply(uid, st, incoming)
        except Exception:
            log.exception("调用模型失败 uid=%s", uid)
            return
        msgs = build_messages(reply, st.pacer, request=" ".join(incoming))
        log.info("-> %s(%s): %s", st.nickname, uid, " / ".join(preview(m) for m in msgs))
        for i, m in enumerate(msgs):
            # 模拟打字时间；第一条额外加一点"看消息"的时间
            delay = humanize.typing_seconds(preview(m)) if preview(m) != "[表情包]" else random.uniform(0.8, 2)
            time.sleep(delay + (random.uniform(0.5, 1.5) if i == 0 else 0))
            send_private(uid, m)
        maybe_consolidate(uid, st, history)


# ---------- 群聊：被 @ 或被回复才说话 ----------

class Group:
    def __init__(self, gid):
        self.gid = gid
        self.mu = threading.Lock()
        self.busy = threading.Lock()
        self.pending = []                # 等待回复的 (uid, 显示名, 文本, message_id, 被@的其他人)
        self.timer = None
        self.last_repeat = 0             # 上次跟着复读的时间
        self.last_long = 0               # 上次做长任务（小剧场 / 抽CP / 总结）的时间
        self.cooldown_told = False       # 这轮冷却里提示过没有
        self.repeated = ""               # 上次复读的内容，同一句只跟一次
        self.day = date.today()
        self.count = 0
        self.log_file = GROUP_DIR / f"{gid}.json"
        self.log = json.loads(self.log_file.read_text(encoding="utf-8")) if self.log_file.exists() else []
        self.memory = FriendMemory(MEMORY_DIR / f"group_{gid}.json", cfg)
        self.pacer = Pacer()

    def add(self, uid, name, text, bot=False):
        with self.mu:
            self.log.append({"t": time.time(), "uid": uid, "name": name, "text": text[:300], "bot": bot})
            self.log_file.write_text(json.dumps(self.log, ensure_ascii=False), encoding="utf-8")


groups = {}
groups_mu = threading.Lock()


def get_group(gid):
    with groups_mu:
        if gid not in groups:
            groups[gid] = Group(gid)
        return groups[gid]


def display_name(uid, sender):
    """认识的人用档案里的名字，括号里带上群名片。"""
    card = sender.get("card") or sender.get("nickname") or str(uid)
    known = profiles.name(uid)
    return f"{known}（{card}）" if known else card


def generate_group_reply(g, trigger, task=None):
    uid, name, text, _, targets = trigger
    gname = profiles.group_names.get(g.gid, str(g.gid))
    n_ctx = task[2] if task and task[2] else cfg.get("group_context", 40)
    with g.mu:
        recent = g.log[-n_ctx:]
    lines = [time.strftime("%H:%M", time.localtime(m["t"])) + " "
             + (f"你（{BOT}）" if m["bot"] else m["name"]) + "：" + m["text"] for m in recent]
    # 被 @ 的人、说话的人、最近说过话的熟人：带上他们的公开档案（私聊专属那部分不进群）
    known = []
    for u in dict.fromkeys(list(targets) + [uid] + [m["uid"] for m in reversed(recent) if not m["bot"]]):
        p = profiles.friend(u, private=False)
        if p and len(known) < 6:
            known.append(p)
    mems = g.memory.recall(text + "\n" + "\n".join(m["text"] for m in recent[-8:]))
    system = "\n\n".join(filter(None, [
        persona,
        FORMAT_RULES,
        profiles.group(g.gid),
        "## 你认识的群友（公开档案，只在群里用这些）\n" + "\n\n".join(known) if known else "",
        "## 你记得的群里的事\n" + "\n".join(f"- {m['time']} {m['topic']}：{m['content']}" for m in mems) if mems else "",
        f"## 当前情况\n你在 QQ 群「{gname}」里，现在是 {time.strftime('%Y-%m-%d %H:%M')}。"
        "下面是最近的群聊记录，括号前是你认识的群友名字，括号里是群名片：\n" + "\n".join(lines),
        "" if task else humanize.style_egg(),
    ]))
    ask = f"{name} @了你 / 回复了你：{text}\n\n"
    if task:
        who = "、".join((profiles.name(t) or str(t)).split("（")[0] for t in targets) or name
        ask += f"【本次任务】{task[1]}" + (f"\n（要夸的人：{who}）" if task[0] == "praise" else "")
    else:
        ask += "用群友的口吻回 TA，只输出要发到群里的话。"
    resp = llm.chat.completions.create(
        model=cfg["llm_model"], temperature=cfg.get("temperature", 1.3), max_tokens=2000,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": ask}])
    return (resp.choices[0].message.content or "").strip()


def maybe_consolidate_group(g):
    limit = cfg.get("group_log_max", 300)
    with g.mu:
        if len(g.log) <= limit:
            return
        chunk, g.log = g.log[:limit // 2], g.log[limit // 2:]
        g.log_file.write_text(json.dumps(g.log, ensure_ascii=False), encoding="utf-8")
    msgs = [{"role": "assistant" if m["bot"] else "user", "t": m["t"],
             "content": ("" if m["bot"] else m["name"] + "：") + m["text"]} for m in chunk]
    try:
        g.memory.consolidate(llm_json, profiles.group_names.get(g.gid, "群聊") + "（QQ 群）", msgs)
    except Exception:
        log.exception("整理群记忆失败 gid=%s", g.gid)


def process_group(gid):
    g = get_group(gid)
    with g.busy:
        with g.mu:
            pending, g.pending = g.pending, []
        if not pending or paused:
            return
        if g.day != date.today():
            g.day, g.count = date.today(), 0
        if g.count >= cfg.get("max_group_replies_per_day", 100):
            log.info("群 %s 今日回复已达上限，跳过", gid)
            return
        g.count += 1

        key = f"group:{gid}"
        if key not in notified:
            send_group(gid, cfg.get("group_notice", cfg["notice"]))
            notified.add(key)
            NOTIFIED_FILE.write_text(json.dumps(sorted(notified, key=str), ensure_ascii=False))
            time.sleep(1.5)

        trigger = pending[-1]                     # 同时有好几个人 @，回最后一个，前面的已经在聊天记录里了
        task = fun.detect(trigger[2])
        if task and task[0] in fun.LONG_TASKS:
            wait = cfg.get("long_task_cooldown", 60) - (time.time() - g.last_long)
            if wait > 0:
                if not g.cooldown_told:          # 冷却中只提示一次，别刷屏
                    g.cooldown_told = True
                    send_group(gid, [{"type": "reply", "data": {"id": str(trigger[3])}},
                                     {"type": "text", "data": {"text": f"{BOT}写手在喝水，{int(wait) + 1} 秒后再点哦"}},
                                     {"type": "face", "data": {"id": str(QQ_FACES["狗头"])}}])
                log.info("长任务冷却中（还剩 %d 秒），跳过 %s", wait, trigger[1])
                return
            g.last_long, g.cooldown_told = time.time(), False
        if task and task[0] == "menu":
            reply = task[1]
        else:
            try:
                reply = generate_group_reply(g, trigger, task)
            except Exception:
                log.exception("群聊调用模型失败 gid=%s", gid)
                return
        if task and task[3]:                      # 抽CP / 总结 / 小剧场：整段发，不拆句
            msgs = [to_segments(STICKER_TAG.sub("", reply).replace("**", "").strip())]   # QQ 不渲染 markdown
        else:
            msgs = build_messages(reply, g.pacer, request=trigger[2])
        log.info("-> 群%s 回 %s: %s", gid, trigger[1], " / ".join(preview(m) for m in msgs))
        for i, m in enumerate(msgs):
            time.sleep((humanize.typing_seconds(preview(m)) if preview(m) != "[表情包]" else random.uniform(0.8, 2))
                       + (random.uniform(0.5, 1.5) if i == 0 else 0))
            seg = [{"type": "text", "data": {"text": m}}] if isinstance(m, str) else m
            if i == 0 and trigger[3]:
                seg = [{"type": "reply", "data": {"id": str(trigger[3])}}] + seg
            send_group(gid, seg)
            g.add(self_id, BOT, preview(m), bot=True)
        maybe_consolidate_group(g)


def maybe_repeat(g, uid, text, segs, prev):
    """两个不同的群友接连发了同一句纯文字，有一定概率跟着复读一次。"""
    if not prev or prev["bot"] or prev["uid"] == uid or prev["text"] != text or paused:
        return
    if any(s.get("type") not in ("text", "face") for s in segs) or len(text) > 60:
        return
    if text == g.repeated or time.time() - g.last_repeat < cfg.get("repeat_cooldown", 300):
        return
    if random.random() > cfg.get("repeat_probability", 0.6):
        return
    g.repeated, g.last_repeat = text, time.time()

    def go():
        time.sleep(random.uniform(1.5, 4))
        send_group(g.gid, [{"type": "text", "data": {"text": text}}])
        g.add(self_id, BOT, text, bot=True)
        log.info("-> 群%s 复读: %s", g.gid, text)
    threading.Thread(target=go, daemon=True).start()


def on_group_message(ev):
    gid = ev["group_id"]
    uid = ev["user_id"]
    if gid not in cfg.get("groups", []) or uid in profiles.ignore or uid == self_id:
        return
    message = ev.get("message", [])
    text = segments_to_text(message, with_media=False)
    if not text:
        return
    name = display_name(uid, ev.get("sender", {}))
    g = get_group(gid)
    with g.mu:
        prev = g.log[-1] if g.log else None
    g.add(uid, name, text)
    segs = message if isinstance(message, list) else []
    typed = "".join(s.get("data", {}).get("text", "") for s in segs if s.get("type") == "text")
    mentioned = any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(self_id) for s in segs) \
        or any(s.get("type") == "reply" and str(s.get("data", {}).get("id")) in bot_msg_ids for s in segs) \
        or MENTION_RE.search(typed) is not None       # 手打的「@名字」（没点选成真正的 @）也算
    if not mentioned:
        maybe_repeat(g, uid, text, segs, prev)
        return
    targets = [int(s["data"]["qq"]) for s in segs if s.get("type") == "at"
               and str(s.get("data", {}).get("qq", "")).isdigit() and str(s["data"]["qq"]) != str(self_id)]
    log.info("<- 群%s %s(%s) @%s: %s", gid, name, uid, BOT, text)
    with g.mu:
        g.pending.append((uid, name, text, ev.get("message_id"), targets))
        if g.timer:
            g.timer.cancel()
        g.timer = threading.Timer(2, process_group, args=(gid,))
        g.timer.daemon = True
        g.timer.start()


# ---------- 主人指令 ----------

def handle_owner_command(text):
    global paused
    args = text.split()
    cmd = args[0]
    if cmd == "#帮助":
        return ("#暂停 / #恢复 / #状态\n#好感：所有认识的人的好感度\n#好感 QQ号或外号 [新数值]：查看或设置好感度（0~100）\n"
                "#记忆 QQ号或外号：查看记住的事\n#忘记 QQ号或外号：清空对这个人的记忆\n#表情库：表情包数量\n"
                "外号在 profiles/index.json 的 aliases 里配置")
    if cmd == "#好感" and len(args) == 1:
        rows = []
        for uid, name in profiles.names.items():
            if uid == cfg.get("owner_qq"):
                continue
            mem = get_friend(uid).memory
            rows.append((mem.affinity, f"{name.split('（')[0]}：{mem.affinity}（{mem.level()[0]}）"))
        return "\n".join(r for _, r in sorted(rows, reverse=True)) or "还没有认识的人"
    if cmd == "#暂停":
        paused = True
        return "已暂停，不再自动回复"
    if cmd == "#恢复":
        paused = False
        return "已恢复自动回复"
    if cmd == "#状态":
        return f"{'暂停中' if paused else '运行中'}，今天聊过的好友 {sum(1 for f in friends.values() if f.count)} 个"
    if cmd == "#表情库":
        return f"表情包 {len(stickers.index)} 张"
    if cmd in ("#好感", "#记忆", "#忘记") and len(args) >= 2:
        uid = profiles.resolve(args[1])
        if uid is None:
            return f"不认识「{args[1]}」，可以用 QQ 号，或者在 profiles/index.json 的 aliases 里加外号"
        who = (profiles.name(uid) or str(uid)).split("（")[0]
        mem = get_friend(uid).memory
        if cmd == "#好感":
            if len(args) >= 3 and args[2].lstrip("-").isdigit():
                mem.affinity = max(0, min(100, int(args[2])))
                mem.save()
            return f"{who} 好感度 {mem.affinity}（{mem.level()[0]}）"
        if cmd == "#记忆":
            if not mem.memories:
                return f"对{who}还没有记忆"
            return "\n".join(f"{m['time']} {m['topic']}：{m['content']}" for m in mem.memories[-15:])
        mem.memories = []
        mem.save()
        return f"已清空对{who}的记忆"
    return None


def on_message(_ws, raw):
    global self_id
    ev = json.loads(raw)
    if ev.get("echo") == "group_send":           # 群消息发送回执：记下 id，之后别人回复它就算在跟小白说话
        mid = (ev.get("data") or {}).get("message_id")
        if mid:
            bot_msg_ids.add(str(mid))
        return
    if "status" in ev and "post_type" not in ev:   # 接口调用的回执
        if ev.get("status") != "ok":
            log.warning("NapCat 调用失败: %s %s", ev.get("retcode"), ev.get("message") or ev.get("wording"))
        return
    if ev.get("self_id"):
        self_id = ev["self_id"]
    if ev.get("post_type") == "message" and ev.get("message_type") == "group":
        on_group_message(ev)
        return
    if ev.get("post_type") != "message" or ev.get("message_type") != "private":
        return
    if ev.get("sub_type") not in ("friend", None):  # 跳过临时会话等
        return
    uid = ev["user_id"]
    message = ev.get("message", ev.get("raw_message", ""))
    preview = segments_to_text(message, with_media=False)
    if not preview:
        return
    nickname = ev.get("sender", {}).get("remark") or ev.get("sender", {}).get("nickname") or str(uid)
    log.info("<- %s(%s): %s", nickname, uid, preview)

    if uid == cfg.get("owner_qq") and preview.startswith("#"):
        ack = handle_owner_command(preview)
        if ack:
            send_private(uid, ack)
            return

    st = get_friend(uid)
    with st.mu:
        st.nickname = nickname
        st.buffer.append(message)
        if st.timer:
            st.timer.cancel()
        st.timer = threading.Timer(cfg["debounce_seconds"], process, args=(uid,))
        st.timer.daemon = True
        st.timer.start()


def main():
    global ws_app
    threading.Thread(target=stickers.scan_manual, daemon=True).start()
    ws_app = websocket.WebSocketApp(
        cfg["napcat_ws_url"],
        header=[f"Authorization: Bearer {cfg['napcat_token']}"],
        on_open=lambda _ws: log.info("已连接 NapCat"),
        on_message=on_message,
        on_error=lambda _ws, e: log.warning("WebSocket 错误: %s", e),
        on_close=lambda _ws, code, msg: log.warning("连接断开 %s %s", code, msg),
    )
    ws_app.run_forever(reconnect=5)


if __name__ == "__main__":
    main()
