# 炉石日志根目录。脚本会自动选择最新的
# Hearthstone_YYYY_MM_DD_HH_MM_SS/Power.log。
# 自动探测候选（按顺序取第一个存在的）：
#   1. 环境变量 HS_LOG_ROOT（高级用户）
#   2. D:/Game/BattleNet/Hearthstone/Logs（国服/安装到 D 盘的常见位置）
#   3. %LOCALAPPDATA%/Blizzard/Hearthstone/Logs（默认安装位置）
import os

_HEARTHSTONE_LOG_CANDIDATES = [
    os.environ.get("HS_LOG_ROOT", ""),
    "D:/Game/BattleNet/Hearthstone/Logs",
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Blizzard", "Hearthstone", "Logs"),
]
HEARTHSTONE_LOG_ROOT = "D:/Hearthstone/Logs"
for _candidate in _HEARTHSTONE_LOG_CANDIDATES:
    if _candidate and os.path.isdir(_candidate):
        HEARTHSTONE_LOG_ROOT = _candidate
        break

# 你的炉石用户名, 注意英文标点符号'#', 把后面的数字也带上
# 可以输入中文
YOUR_NAME = "YOURNAME#1234"

# 关于控制台信息打印的设置
DEBUG_PRINT = True
WARN_PRINT = True
SYS_PRINT = True
INFO_PRINT = True
ERROR_PRINT = True

# 关于文件信息输出的设置
DEBUG_FILE_WRITE = True
WARN_FILE_WRITE = True
SYS_FILE_WRITE = True
INFO_FILE_WRITE = True
ERROR_FILE_WRITE = True

OPERATE_INTERVAL = 0.15
STATE_CHECK_INTERVAL = 1
TINY_OPERATE_INTERVAL = 0.08

# 我觉得这行注释之后的内容应该不需要修改……
FSM_LEAVE_HS = "Leave Hearth Stone"
FSM_MAIN_MENU = "Main Menu"
FSM_CHOOSING_HERO = "Choosing Hero"
FSM_MATCHING = "Match Opponent"
FSM_CHOOSING_CARD = "Choosing Card"
# FSM_NOT_MY_TURN = "Not My Turn"
# FSM_MY_TURN = "My Turn"
FSM_BATTLING = "Battling"
FSM_ERROR = "ERROR"
FSM_QUITTING_BATTLE = "Quitting Battle"
FSM_WAIT_MAIN_MENU = "Wait main menu"

LOG_CONTAINER_ERROR = 0
LOG_CONTAINER_INFO = 1

LOG_LINE_CREATE_GAME = "Create Game"
LOG_LINE_GAME_ENTITY = "Create Game Entity"
LOG_LINE_PLAYER_ENTITY = "Create Player Entity"
LOG_LINE_FULL_ENTITY = "Full Entity"
LOG_LINE_SHOW_ENTITY = "Show Entity"
LOG_LINE_CHANGE_ENTITY = "Change Entity"
LOG_LINE_BLOCK_START = "Block Start"
LOG_LINE_BLOCK_END = "Block End"
LOG_LINE_PLAYER_ID = "Player ID"
LOG_LINE_TAG_CHANGE = "Tag Change"
LOG_LINE_TAG = "Tag"
LOG_LINE_GENERAL_CHOICE_START = "General Choice Start"
LOG_LINE_GENERAL_CHOICE_ENTITY = "General Choice Entity"
LOG_LINE_GENERAL_CHOICE_READY = "General Choice Ready"
LOG_LINE_GENERAL_CHOICE_RESOLVED = "General Choice Resolved"

CARD_BASE = "BASE"
CARD_SPELL = "SPELL"
CARD_MINION = "MINION"
CARD_WEAPON = "WEAPON"
CARD_LOCATION = "LOCATION"
CARD_HERO = "HERO"
CARD_HERO_POWER = "HERO_POWER"
CARD_ENCHANTMENT = "ENCHANTMENT"
