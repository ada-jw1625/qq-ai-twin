"""群里的小功能：@机器人 菜单 / 抽CP / 总结 / 夸夸 / 小剧场。复读在 bot.maybe_repeat 里。

群友之间的玩笑 CP 写在 profiles/index.json 的 "group_cps" 里（不进代码仓库），这里只放公开的二次元 CP。
"""
import random
import re

ACG_CPS = ["爱素（千早爱音 × 长崎素世）", "海希（八幡海铃 × 椎名立希）", "初祥（三角初华 × 丰川祥子）",
           "睦祥（若叶睦 × 丰川祥子）", "爱希（千早爱音 × 椎名立希）", "灯爱（高松灯 × 千早爱音）",
           "睦素（若叶睦 × 长崎素世）", "鸣神（雷电影 × 八重神子）", "仆芙（仆人 × 芙宁娜）"]
GROUP_CPS = []
BOT, OWNER = "小白", "主人"

LONG_TASKS = {"cp", "summary", "story"}     # 长回复，全群共用冷却

MENU_TEMPLATE = """{bot}会的小功能（@{bot} + 下面的词）：
🎲 抽CP —— 随机抽一对，附赠小剧场
📜 总结 / 爬楼 —— 帮刚上线的群友总结刚才聊了啥
🌸 夸夸 @某人 —— 认真夸一个群友（不 @ 就夸你自己）
✍️ 写个 xx 的小剧场 —— 一段全年龄小段子
🔁 群友复读的时候我也会偷偷跟一句
⏳ 抽CP、总结、小剧场比较长，全群每分钟只能点一次"""
MENU = MENU_TEMPLATE.format(bot=BOT)


def setup(bot_name, owner_name, group_cps):
    """bot.py 启动时调用：传入机器人名字、本人称呼和群友 CP 列表。"""
    global BOT, OWNER, GROUP_CPS, MENU
    BOT, OWNER, GROUP_CPS = bot_name, owner_name, list(group_cps)
    MENU = MENU_TEMPLATE.format(bot=BOT)


def detect(text):
    """返回 (任务名, 给模型的额外要求, 要带多少条群聊记录, 是否整段发送)。普通聊天返回 None。"""
    t = re.sub(r"@\s*" + re.escape(BOT), "", text).strip().lower()
    if re.fullmatch(r"(菜单|功能|帮助|help|你会什么|你会干嘛)[？?！!。~～]*", t):
        return "menu", MENU, 0, True
    if "抽cp" in t or "抽个cp" in t:
        pool = GROUP_CPS if GROUP_CPS and random.random() < 0.5 else ACG_CPS
        cp = random.choice(pool)
        return ("cp", f"群友让你抽 CP，结果是**{cp}**（这是代码随机抽的，不许改）。先像开奖一样宣布结果，"
                f"再写两三句甜甜的、搞笑的小剧场。如果抽中的是群友 CP，就当群里的老梗开玩笑，不涉及任何真实感情经历。"
                f"整段不超过 100 字。", 20, True)
    if re.search(r"总结|爬楼|聊了啥|聊了什么|说了啥|说了什么|发生了什么", t):
        return ("summary", "群友让你帮忙爬楼：把上面的群聊记录总结成 3~6 行，每行一件事，带上是谁说的，"
                "口吻轻松好玩，像群友在转述。不总结任何人的隐私和真实感情八卦。只输出总结本身。", 150, True)
    if "夸夸" in t or "夸一下" in t or "夸我" in t:
        return ("praise", "群友让你夸人：认真、具体地夸，结合你对 TA 的了解（档案里的爱好、做过的事、群里说过的话），"
                f"真诚又带点{OWNER}的俏皮，三四句，别空泛，别夸外貌。", 40, False)
    if re.search(r"小剧场|写个|写段|写一段|段子|写篇", t):
        return ("story", "群友点了一个小剧场：按 TA 说的角色和设定写一段全年龄、甜或者搞笑的小段子，"
                "150 字以内，有对话有画面，不写 R18，不写真实群友的真实感情。只输出段子本身。", 20, True)
    return None
