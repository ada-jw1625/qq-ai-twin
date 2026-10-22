# QQ AI 分身机器人

用大模型模仿你的说话风格，挂在 QQ 小号上陪好友聊天、在群里接梗的机器人。基于 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11）+ 任意 OpenAI 兼容接口（默认 DeepSeek）。

> ⚠️ NapCat 是非官方协议，**有被风控、冻结的风险，请用小号**。
> 机器人第一次和人聊天时会自动发一条"我是 AI 分身"的提示，建议也提前告诉朋友们。

## 功能

**私聊**
- 用你的语气聊天：短句、口头禅、表情，模型先写一段，代码随机拆成几条、按字数模拟打字时间再发
- 好友连发几条会攒几秒一起回
- 看得懂图片和表情包（视觉模型描述后再回）
- **好友档案**：手写每个好友的称呼、爱好、共同回忆，聊天时自然地用上
- **长期记忆**：聊天记录攒多了自动概括成"话题 + 一句话"存下来，按相似度取回
- **好感度**：0~100 分六档，每档对应不同的亲近语气，随聊天慢慢变化

**群聊**（只在被 @ 或被回复时说话）
- 认得群友（按 QQ 号对应档案），群里只用公开档案，私聊内容不进群
- 小功能：`@机器人 菜单` / `抽CP` / `总结`（爬楼）/ `夸夸 @某人` / `写个 xx 的小剧场`
- 群友接连复读同一句时偶尔跟一句
- 长回复全群冷却，每天回复次数有上限

**表情**
- QQ 系统表情（狗头、捂脸、吃瓜……）和 emoji，代码控制出现频率
- 表情包库：自动收集好友发的表情包，也能批量导入自己的收藏；模型说想表达什么感觉，代码按描述挑图；有人说"发个表情包"时一定发

**主人指令**（只认 `owner_qq` 的私聊）：`#帮助` `#暂停` `#恢复` `#状态` `#好感 [外号] [数值]` `#记忆 外号` `#忘记 外号` `#表情库`

## 快速开始

### 1. 准备 NapCat
1. 从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.Windows.Node.zip`，解压，用小号扫码登录（`napcat.bat`，之后可以 `napcat.bat -q <QQ号>` 快速登录）
2. 打开 WebUI（地址和 token 在启动日志里），在网络配置里新建 **WebSocket 服务器**：`127.0.0.1:3001`，设一个 token，消息格式 `array`
3. 如果登录后 NapCat 立刻崩溃（Worker 退出码 3221225477），把 `napcat/config/napcat.json` 和 `napcat_<QQ号>.json` 里的 `packetBackend` 改成 `"disable"`、`o3HookMode` 改成 `0`，再重启

### 2. 配置机器人
```bash
pip install -r requirements.txt
cp config.example.json config.json          # 填 NapCat token、API key、owner_qq、bot_name、owner_name、groups
cp persona.example.md persona.md            # 写你的人设（只写能公开的）
cp -r profiles.example profiles             # 写好友档案（可以先留空）
```

### 3. 启动
先确认 NapCat 已登录，再：
```bash
python bot.py          # Windows 也可以双击 start_bot.bat；关闭用 stop_bot.bat
```
看到"已连接 NapCat"就好了。改代码、`persona.md`、`config.json`、`profiles/index.json` 后要重启。

## 目录结构

```
bot.py              主程序：收发消息、私聊 / 群聊流程、主人指令、表情频率
persona.example.md  人设模板（复制成 persona.md）
humanize.py         拆句、打字时间、句式小彩蛋
memory.py           长期记忆 + 好感度
profiles.py         读好友 / 群档案
profiles.example/   档案模板（复制成 profiles/）
  index.json        QQ号 → 名字、外号、要忽略的账号（别的机器人）、群名、群里的玩笑 CP
  friends/<QQ号>.md 「## 公开档案」（群里也用）+「## 私聊专属」（只和本人私聊时用）
  groups/<群号>.md  群梗、群聊规则
stickers.py         表情包库（视觉模型写描述，文本相似度挑图）
fun.py              群小功能
import_stickers.py  批量导入表情包：python import_stickers.py <文件夹> [数量]
data/               运行时生成：聊天记录、记忆、群聊记录、表情包（不进仓库）
```

## 常用配置（config.json）

| 键 | 说明 |
|---|---|
| `bot_name` / `owner_name` | 机器人的名字（群里 @ 它用这个）/ 本人的称呼 |
| `owner_qq` | 主号 QQ，只有它能用主人指令 |
| `groups` | 要开启的群号列表 |
| `llm_model` / `vision_model` | 聊天模型 / 看图模型 |
| `emoji_every` / `sticker_every` | 大约每几条消息出现一个表情 / 一张表情包 |
| `long_task_cooldown` | 抽CP、总结、小剧场的全群冷却秒数 |
| `max_replies_per_friend_per_day` / `max_group_replies_per_day` | 每天回复上限 |
| `history_turns` / `max_memories` | 私聊带多少轮上下文 / 长期记忆上限 |

## 加群聊功能

在 `fun.py` 的 `detect()` 里加一个分支：匹配关键词，返回 `(任务名, 给模型的要求, 带几条群聊记录, 是否整段发送)`。长回复把任务名加进 `LONG_TASKS`。需要随机结果的在代码里算好，写进要求里让模型"不许改"。

## 隐私建议

- `persona.md` 只放能对所有好友公开的信息；档案的「私聊专属」只会在和本人私聊时使用
- 不要把第三个人的私事、住址电话、健康状况写进档案
- 每次对话的人设、档案和最近聊天记录都会发给你配置的模型服务商
