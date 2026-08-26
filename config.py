"""用户/站点/机器/自动化 配置的单一来源（分层合并）。

所有设置集中到这一个文件，来源优先级从低到高：

    1. 内置默认值（本文件）
    2. ui_config.json —— Web 控制台 / 校准工具持久化的用户配置
    3. 环境变量 —— 机器 / 高级覆盖（如 HS_LOG_ROOT、HS_USER_NAME、HS_PORT）

约定：
    * 本模块只做"解析站点/用户/机器配置"，不含业务逻辑。
    * 其他模块直接 `from config import ...` 或 `import config` 取值，
      不要再在代码里写死机器路径/用户身份/延时数值。
    * web 层与推荐自动化层共用本文件，不再各自读 ui_config.json。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "ui_config.json"

_env = os.environ.get


def _load_ui_config() -> dict:
    """读取 ui_config.json；缺失/非法时返回空 dict（走默认值）。"""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_UI = _load_ui_config()


def _first(*values, default=""):
    """返回第一个非空值，用于默认值回退。"""
    for value in values:
        if value not in (None, "", 0):
            return value
    return default


# ---------------------------------------------------------------- 用户身份
# 环境变量 HS_USER_NAME > ui_config.json 的 name > 占位默认。
USER_NAME = _first(_env("HS_USER_NAME"), _UI.get("name"), "YOURNAME#1234")

# ---------------------------------------------------------------- 炉石日志根目录
_LOCALAPPDATA_BLIZZARD = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Blizzard", "Hearthstone", "Logs")


def _resolve_log_root() -> str:
    """环境变量 HS_LOG_ROOT > ui_config.json 的 log_root > LOCALAPPDATA 自动探测。"""
    candidates = (
        _env("HS_LOG_ROOT"),
        _UI.get("log_root"),
        _LOCALAPPDATA_BLIZZARD if os.path.isdir(_LOCALAPPDATA_BLIZZARD) else "",
    )
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    # 都不存在：返回配置值/空串，交由调用方处理（web 会提示用户填写）。
    return _first(_env("HS_LOG_ROOT"), _UI.get("log_root"))


HEARTHSTONE_LOG_ROOT = _resolve_log_root()

# ---------------------------------------------------------------- Web 控制台
HOST = _env("HS_HOST", "127.0.0.1")
BASE_PORT = int(_env("HS_PORT", "8765"))
LOG_BUFFER_SIZE = int(_env("HS_LOG_BUFFER_SIZE", "500"))

# ---------------------------------------------------------------- 操作节奏（环境变量可覆盖）
OPERATE_INTERVAL = float(_env("HS_OPERATE_INTERVAL", "0.15"))
STATE_CHECK_INTERVAL = float(_env("HS_STATE_CHECK_INTERVAL", "1.0"))
TINY_OPERATE_INTERVAL = float(_env("HS_TINY_OPERATE_INTERVAL", "0.08"))

# ---------------------------------------------------------------- 自动投降默认值
# 单一来源：web 层与 FSM_action 层共用，避免各自硬编码。
DEFAULT_AUTO_CONCEDE = {"enabled": False, "threshold": 10.0, "rounds": 3}

# ---------------------------------------------------------------- 日志 / 快照
# 日志尾部轮询间隔（log_op.py 读取 Power.log 时使用）。
LOG_TAIL_WAIT_INTERVAL = float(_env("HS_LOG_TAIL_WAIT_INTERVAL", "0.05"))
# game_state 快照写盘最小间隔（秒）。
SNAPSHOT_WRITE_INTERVAL = float(_env("HS_SNAPSHOT_WRITE_INTERVAL", "5.0"))

# ---------------------------------------------------------------- 卡牌数据下载
# 来源于互联网的炉石JSON数据下载API, 更多信息可以访问 https://hearthstonejson.com/
JSON_URL = _env("HS_JSON_URL",
                "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json")
# 下载/重下载都设超时，避免主循环被网络卡死。
DOWNLOAD_TIMEOUT_SECONDS = int(_env("HS_DOWNLOAD_TIMEOUT_SECONDS", "30"))

# ---------------------------------------------------------------- OCR 模型目录
# 环境变量 HS_OCR_MODEL_ROOT > %LOCALAPPDATA%/AutoHS/ocr_models/paddleocr。
OCR_MODEL_ROOT = os.environ.get("HS_OCR_MODEL_ROOT") or os.path.normpath(
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                 "AutoHS", "ocr_models", "paddleocr"))


# ---------------------------------------------------------------- 推荐自动化调优
def _user_roi() -> Optional[tuple[int, int, int, int]]:
    """读取用户校准的推荐区域（ui_config.json 的 recommendation_roi）。

    校准工具 calibrate_roi.py 把桌面上拖拽结果写入该文件；这里在每次创建
    配置时应用（打包版/源码版路径一致：应用目录=本文件所在目录）。
    未配置或格式非法时返回 None 走默认值。
    """
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        roi = data.get("recommendation_roi")
        vals = tuple(int(v) for v in roi)
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return None


def _user_confirm_roi() -> Optional[tuple[int, int, int, int]]:
    """读取用户校准的换牌"确认"按钮区域（ui_config.json 的 mulligan_confirm_roi）。"""
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        roi = data.get("mulligan_confirm_roi")
        vals = tuple(int(v) for v in roi)
        if len(vals) == 4 and 0 <= vals[0] < vals[2] and 0 <= vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return None


# 可由 ui_config.json 的 delays 段可修改的延时字段（默认值取上游时序，
# 但用户可在 ui_config.json 覆盖，避免写死）。
_USER_DELAY_KEYS = (
    "mulligan_ready_delay_seconds",
    "mulligan_post_ocr_delay_seconds",
    "pre_action_delay_seconds",
    "post_action_delay_seconds",
    "ocr_preprocess_scale",
)


def _user_delays() -> dict:
    """读取用户可修改的延时（ui_config.json 的 delays 段）。返回仅含合法数值的键。"""
    try:
        cfg_path = ROOT / "ui_config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        delays = data.get("delays") or {}
        result = {}
        for key in _USER_DELAY_KEYS:
            if key in delays:
                try:
                    value = float(delays[key])
                except (TypeError, ValueError):
                    continue
                if value >= 0:
                    result[key] = value
        return result
    except Exception:
        return {}


@dataclass(frozen=True)
class RecommendationConfig:
    # ------------------------------------------------------------------ 屏幕
    # 分辨率必须与炉石传说一致（程序校验用），DPI 100%.
    desktop_size: tuple[int, int] = (1920, 1080)
    desktop_dpi: int = 96

    # 盒子面板完整区域（屏幕坐标 left, top, right, bottom）。
    # 覆盖左侧推荐面板：宽 271，高 938。
    recommendation_roi: tuple[int, int, int, int] = (7, 200, 202, 500)


    # ------------------------------------------------------------------ 稳定帧
    # 读取面板需连续 stable_frames 帧识别出相同文本才算稳（防动画中间帧）。
    max_attempts: int = 3
    stable_frames: int = 2
    # 单行识别置信度低于该值即视为"读不清"重试。
    min_ocr_confidence: float = 0.70
    retry_interval_seconds: float = 0.1

    # ------------------------------------------------------------------ 换牌
    # 每局进入换牌阶段后等待 N 秒，再开始识图和换牌操作（上游时序）。
    mulligan_ready_delay_seconds: float = 20.0
    # 换牌建议已稳定识别后立即点击，不再追加等待（上游时序）。
    mulligan_post_ocr_delay_seconds: float = 0.0
    # 换牌"确认"按钮区域（屏幕坐标 left, top, right, bottom）。
    # 对齐 commit_choose_card 的点击点 (960,850)，以该点为中心外扩。
    # 点击确认后该按钮消失；仍能识别到"确认"说明换牌未提交成功，需重试。
    mulligan_confirm_roi: tuple[int, int, int, int] = (860, 810, 1060, 890)

    # ------------------------------------------------------------------ 出牌
    # 每个新回合开始延时一次（给盒子更新推荐留时间），
    # 同回合内多次出牌操作之间不重复延时。
    pre_action_delay_seconds: float = 7.0
    # 一次操作执行完成之后到下轮截图+OCR 的延时（0 = 立即开始）；
    # 配合上面"回合只延时一次"使用。
    post_action_delay_seconds: float = 0.0
    # 单次读取/单次执行的超时保护。
    recognition_timeout_seconds: float = 2.0
    result_timeout_seconds: float = 5.0

    # ------------------------------------------------------------------ OCR
    # 预处理缩放倍数：1.5x 是实测识别精度/速度最佳点
    # （1.0x 快约 28% 但识别率下降不可接受；3.0x 无效且慢）。
    # 仍可用环境变量 OCR_PREPROCESS_SCALE 临时覆盖。
    ocr_preprocess_scale: float = 1.4
    # 自动线程数下限：OpenMP/MKL 线程默认取机器物理核数，
    # 低于此下限固定为此值（核数少的机器保守）。
    ocr_thread_min: int = 4
    # 调试用：每次 OCR 的实际输入图按顺序存盘目录（空 = 关闭）。
    # 优先被环境变量 OCR_FRAME_DIR 覆盖。
    ocr_frame_dump_dir: str = ""

    def __post_init__(self) -> None:
        # 校准工具写入的用户区域优先于代码默认值。
        roi = _user_roi()
        if roi is not None:
            object.__setattr__(self, "recommendation_roi", roi)
        confirm_roi = _user_confirm_roi()
        if confirm_roi is not None:
            object.__setattr__(self, "mulligan_confirm_roi", confirm_roi)
        # 用户可在 ui_config.json 的 delays 段覆盖延时（默认采用上游时序）。
        for key, value in _user_delays().items():
            object.__setattr__(self, key, value)
