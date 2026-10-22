"""好友 / 群的手写档案（profiles/）。

friends/<QQ号>.md 分「## 公开档案」和「## 私聊专属」两段：群聊里只用公开档案，私聊时两段都用。
groups/<群号>.md 是群的档案和群聊规则。index.json 记录 QQ 号 -> 名字，以及要忽略的账号（别的机器人）。
"""
import json
from pathlib import Path

PRIVATE_MARK = "## 私聊专属"


class Profiles:
    def __init__(self, root: Path):
        self.root = root
        idx_file = root / "index.json"
        idx = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.exists() else {}
        self.names = {int(k): v for k, v in idx.get("friends", {}).items()}
        self.group_names = {int(k): v for k, v in idx.get("groups", {}).items()}
        self.ignore = set(idx.get("ignore", []))
        self.aliases = {a.lower(): int(uid) for uid, names in idx.get("aliases", {}).items() for a in names}
        self.group_cps = idx.get("group_cps", [])       # 群里常开的玩笑 CP，给「抽CP」用

    def resolve(self, who):
        """QQ 号或外号（index.json 的 aliases）-> QQ 号，认不出返回 None。"""
        who = who.strip()
        if who.isdigit():
            return int(who)
        return self.aliases.get(who.lower())

    def name(self, uid):
        return self.names.get(int(uid))

    def friend(self, uid, private=True):
        f = self.root / "friends" / f"{uid}.md"
        if not f.exists():
            return ""
        text = f.read_text(encoding="utf-8")
        if not private:
            text = text.split(PRIVATE_MARK)[0]
        return text.strip()

    def group(self, gid):
        f = self.root / "groups" / f"{gid}.md"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
