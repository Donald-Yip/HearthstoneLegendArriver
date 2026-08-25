import _thread
import random
import sys
import threading
import time

import keyboard

import click
import get_screen
from manual_controller import (
    ClickExecutor, GlobalHotkeyInput, ManualController,
)
from strategy import StrategyState
from log_state import *
from src.capture.desktop_capture import DesktopCapture
from src.flow.mulligan_flow import MulliganFlow, MulliganStatus
from src.flow.recommendation_flow import (
    FlowStepStatus, RecommendationFlow,
)
from src.game_state.recommendation_adapter import adapt_action
from src.ocr.paddle_adapter import PaddleOcrAdapter
from src.ocr.stable_reader import StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_config import RecommendationConfig
from src.recommendation_models import ActionKind
from src.safety.recommendation_validator import RecommendationValidator


FSM_state = ""
time_begin = 0.0
game_count = 0
win_count = 0
quitting_flag = False
# 定时计划 / Web 控制台：置 True 表示“本局对战结束后停止自动化”。
# 对局进行中（换牌/对战/结算）不会立即退出，只有回到非对局状态才停止。
stop_after_current_game = False
shutdown_event = threading.Event()
log_state = LogState()
log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
choose_hero_count = 0
manual_controller = ManualController(
    input_func=GlobalHotkeyInput(
        keyboard, shutdown_event=shutdown_event),
    executor=ClickExecutor(click),
)
auto_mulligan_flow = None
recommendation_flow = None
recommendation_config = None
recommendation_capture = None
recommendation_parser = None
recommendation_reader = None
mulligan_reader = None
recommendation_validator = None
active_game_generation = -1
mulligan_delay_generation = None
player_turn_delay_key = None
last_automation_diagnostic = None
_snapshot_cache_key = None
_snapshot_cache = None
_mulligan_diagnostic_key = None
# 调试快照写盘节流：日志每次变化都全量序列化整个 log_state 会拖慢主循环，
# 只在间隔 SNAPSHOT_WRITE_INTERVAL 秒后重新写盘。
SNAPSHOT_WRITE_INTERVAL = 5.0
_last_snapshot_write = 0.0


def _automation_state():
    snapshot = refresh_snapshot()
    if snapshot is None:
        raise RuntimeError("power_log_snapshot_unavailable")
    snapshot.log_revision = log_state.revision
    return snapshot


def _automation_state_with_revision():
    snapshot = _automation_state()
    return snapshot, log_state.revision


def initialize_recommendation_automation():
    """Rebuild per-game flows while reusing expensive OCR components.

    每次调用都会重新读取 ui_config.json 的 recommendation_roi（重建轻量的
    RecommendationConfig + DesktopCapture），因此用校准工具画完框后，直接重开
    对局/重启自动化即可生效，无需重启 web_ui。昂贵的 OCR 引擎（reader 与
    paddle backend）仍只创建一次、跨对局复用。
    """
    global auto_mulligan_flow, recommendation_flow
    global recommendation_config, recommendation_capture
    global recommendation_parser, recommendation_reader
    global mulligan_reader, recommendation_validator

    # 轻量：每次都重建，以便拾取校准后的最新 ROI / 尺寸配置。
    recommendation_config = RecommendationConfig()
    recommendation_capture = DesktopCapture(recommendation_config)

    if recommendation_parser is None:
        recommendation_parser = RecommendationParser()

    if recommendation_reader is None:
        recommendation_reader = StableRecommendationReader(
            recommendation_config, PaddleOcrAdapter(),
            text_normalizer=recommendation_parser.normalize_action_text)
        # 不设"打法参考A"信标：对战时该标题不在截图区域内（实测
        # 面板直出「打出N号位…」指令文本），设信标会把正确指令清空为
        # recommendation_not_stable 死循环。面板是否在场由 parser
        # 严格句式（打出N号位随从/放置于我方N号位 等）唯一把关，
        # 与换牌 reader 同一设计。
    if mulligan_reader is None:
        # 换牌面板只有留牌建议（无"打法参考A"标题）：不设信标，
        # 面板是否在场由 `替换N号位卡牌` 换牌句式唯一把关。
        # 独立 if 保证每次 initialize（含 config 已存在的后续对局）
        # 都会为闭包绑定该变量。
        mulligan_reader = StableRecommendationReader(
            recommendation_config, recommendation_reader.backend,
            text_normalizer=recommendation_parser.normalize_action_text)
        recommendation_validator = RecommendationValidator(
            recommendation_config)

    def read_mulligan_action():
        # 换牌面板是否在场，由 OCR 证据裁定：识别出的文本必须能解析出
        # `替换N号位卡牌` 换牌句式（无"打法参考A"信标的专用 reader）。
        evidence = mulligan_reader.read(
            lambda: recommendation_capture.capture(ocr_panel_ok=True),
            recommendation_capture.crop_recommendation)
        action = recommendation_parser.parse(
            evidence, log_state.game_num_turns_in_play, log_state.revision)
        if action.action != ActionKind.MULLIGAN:
            raise RuntimeError("recommendation_is_not_mulligan")
        return action

    auto_mulligan_flow = MulliganFlow(
        click, read_mulligan_action, _automation_state,
        action_context=click.hearthstone_action_session,
        stopped=shutdown_event.is_set,
        # 上次修复把 first_delay 误设为 ready(7)，导致“开局先等7s 再OCR，
        # OCR后 又等7s 再点击”两段 7s 叠加。这里 OCR 前的 7s 由
        # ChoosingCardAction 的 ready 延时负责；MulliganFlow 内的延时是
        # “OCR 成功 → 点击”之间的缓冲，统一用 post_ocr(5s)。
        first_delay=recommendation_config.mulligan_post_ocr_delay_seconds,
        retry_delay=recommendation_config.mulligan_post_ocr_delay_seconds)
    recommendation_flow = RecommendationFlow(
        capture=recommendation_capture,
        reader=recommendation_reader,
        parser=recommendation_parser,
        state_supplier=_automation_state_with_revision,
        adapter=adapt_action,
        validator=recommendation_validator,
        controller=manual_controller,
        result_timeout=recommendation_config.result_timeout_seconds,
        post_action_delay=recommendation_config.post_action_delay_seconds,
        stopped=shutdown_event.is_set,
    )


def reset_game_session():
    """Clear every match-scoped automation state for a newly created game."""
    global active_game_generation, choose_hero_count
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key
    initialize_recommendation_automation()
    active_game_generation = log_state.game_generation
    choose_hero_count = 0
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    click.center_mouse()


def init():
    global log_state, log_iter, choose_hero_count, active_game_generation
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key

    log_state = LogState()
    log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
    choose_hero_count = 0
    active_game_generation = -1
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    shutdown_event.clear()
    initialize_recommendation_automation()
    click.center_mouse()


def update_log_state():
    global active_game_generation
    global _last_snapshot_write
    log_container = next(log_iter)
    if log_container.log_type == LOG_CONTAINER_ERROR:
        return False

    previous_revision = log_state.revision
    for log_line_container in log_container.message_list:
        ok = update_state(log_state, log_line_container)
        # if not ok:
        #     return False

    if log_state.game_generation != active_game_generation:
        reset_game_session()

    if (DEBUG_FILE_WRITE and log_state.revision != previous_revision
            and time.time() - _last_snapshot_write
            >= SNAPSHOT_WRITE_INTERVAL):
        _last_snapshot_write = time.time()
        with open("./log/game_state_snapshot.txt", "w", encoding="utf8") as f:
            f.write(str(log_state))

    # 注意如果Power.log没有更新, 这个函数依然会返回. 应该考虑到game_state只是被初始化
    # 过而没有进一步更新的可能
    if log_state.game_entity_id == 0:
        return False

    return True


def refresh_snapshot():
    """Read pending Power.log events and build a fresh manual snapshot."""
    global _snapshot_cache_key, _snapshot_cache
    if not update_log_state():
        return None
    cache_key = (log_state.game_generation, log_state.revision)
    if cache_key != _snapshot_cache_key:
        _snapshot_cache = StrategyState(log_state)
        _snapshot_cache_key = cache_key
    return _snapshot_cache


def wait_for_log_update(start_revision=None, timeout=2.0):
    """Wait briefly for evidence that an input changed game state."""
    if start_revision is None:
        start_revision = log_state.revision
    deadline = time.time() + timeout
    while time.time() < deadline:
        if update_log_state() and log_state.revision > start_revision:
            return True
    manual_controller.output("尚未检测到状态变化，请查看游戏后刷新或重试。")
    return False


def wait_until_battle_starts():
    loop_count = 0
    while True:
        if not update_log_state():
            return FSM_ERROR
        if log_state.is_end:
            return FSM_QUITTING_BATTLE
        if log_state.game_num_turns_in_play > 0:
            return FSM_BATTLING
        loop_count += 1
        if loop_count >= 60:
            warn_print("Time out in Choosing Card")
            return FSM_ERROR
        time.sleep(STATE_CHECK_INTERVAL)


def system_exit():
    global quitting_flag

    sys_print(f"一共完成了{game_count}场对战, 赢了{win_count}场")
    print_info_close()

    quitting_flag = True
    shutdown_event.set()
    if threading.current_thread() is threading.main_thread():
        raise SystemExit(0)
    _thread.interrupt_main()


def request_stop_after_game():
    """请求“本局对战结束后停止”。

    对局进行中时不会中断当前操作；当状态机回到非对局状态
    （主菜单/选职业/匹配/炉石未运行等）后自动化线程自动退出。
    再次调用 request_cancel_stop_after_game() 可在本局结束前撤销。
    """
    global stop_after_current_game
    stop_after_current_game = True
    info_print("已请求：本局对战结束后停止自动化。")
    return True


def request_cancel_stop_after_game():
    """撤销“本局结束后停止”，让自动化继续打下去。

    在线程退出前调用即可，无需重启脚本；本局结束前都可自由更改。
    """
    global stop_after_current_game
    stop_after_current_game = False
    info_print("已取消「本局结束后停止」，自动化继续运行。")
    return True


def request_immediate_stop():
    """Web 模式下的立即停止：只终止自动化线程，不影响服务器主线程。"""
    global quitting_flag
    info_print("收到立即停止指令，正在终止自动化……")
    quitting_flag = True
    shutdown_event.set()
    return True


def print_out():
    global FSM_state
    global time_begin
    global game_count

    # sys_print("Enter State " + str(FSM_state))

    if FSM_state == FSM_LEAVE_HS:
        warn_print("HearthStone not found! Try to go back to HS")

    if FSM_state == FSM_CHOOSING_CARD:
        # 只在“真正打完一局”时计数（见 Battling），开局只记录开始时间。
        # sys_print("The " + str(game_count) + " game begins")
        time_begin = time.time()

    if FSM_state == FSM_QUITTING_BATTLE:
        # sys_print("The " + str(game_count) + " game ends")
        time_now = time.time()
        if time_begin > 0:
            info_print("The last game last for : {} mins {} secs"
                       .format(int((time_now - time_begin) // 60),
                               int(time_now - time_begin) % 60))

    return


def ChoosingHeroAction():
    global choose_hero_count

    if quitting_flag or stop_after_current_game:
        sys.exit(0)

    print_out()

    # 有时脚本会卡在某个地方, 从而在FSM_Matching
    # 和FSM_CHOOSING_HERO之间反复横跳. 这时候要
    # 重启炉石
    # choose_hero_count会在每一次开始留牌时重置
    choose_hero_count += 1
    if choose_hero_count >= 20:
        return FSM_ERROR

    time.sleep(2)
    click.run_hearthstone_action(click.match_opponent)
    time.sleep(1)
    return FSM_MATCHING


def MatchingAction():
    print_out()
    loop_count = 0

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        time.sleep(STATE_CHECK_INTERVAL+random.random()+random.random()+random.random())

        click.run_hearthstone_action(click.commit_error_report)

        ok = update_log_state()
        if ok:
            if not log_state.is_end:
                return FSM_CHOOSING_CARD

        curr_state = get_screen.get_state()
        if curr_state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO

        loop_count += 1
        # print("寻找对手计时器")
        # print(loop_count)
        if loop_count >= 60:
            warn_print("Time out in Matching Opponent")
            return FSM_ERROR


def ChoosingCardAction():
    global choose_hero_count, mulligan_delay_generation
    global quitting_flag, stop_after_current_game, shutdown_event
    choose_hero_count = 0

    print_out()
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if snapshot.game_num_turns_in_play > 0:
        return FSM_BATTLING

    # CREATE_GAME increments game_generation.  Bind the ready delay to that
    # generation so every match waits once, including matches after the first.
    while mulligan_delay_generation != log_state.game_generation:
        waiting_generation = log_state.game_generation
        delay = (recommendation_config.mulligan_ready_delay_seconds
                 if auto_mulligan_flow is not None else 2)
        manual_controller.output(
            f"[SYS] 换牌阶段：延时 {delay:.0f}s 后开始识别留牌……")
        time.sleep(delay)
        mulligan_delay_generation = waiting_generation
        snapshot = refresh_snapshot()
        if snapshot is None:
            return FSM_ERROR
        if snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING

    if auto_mulligan_flow is not None:
        auto_mulligan_flow.reset_delay()  # 每局首次用 ready(7)，重试用 post_ocr(5)
        # 换牌自动流：
        # - 识别/执行失败 → 立即重试（原重试机制不变）
        # - 成功后不再重复执行，只快速轮询到对局进入战斗回合。
        #   旧逻辑拿“点击后的手牌”当基准对比，Power.log 更新快时会误判
        #   “点击未生效”并重复跑换牌流，实际换牌已成功（表现：一直提示失败）。
        confirmed_waiting = False
        retry_count = 0
        verified = False
        while True:
            # 循环内必须检查停止标志：主循环只在状态分发处检查，
            # 本循环若能无限运行，立即停止后鼠标会继续点击。
            # 「本局结束后停止」不在此处生效（那是打完本局才停），
            # 本局内随时可通过 request_cancel_stop_after_game 反悔。
            if quitting_flag:
                sys.exit(0)
            fresh = refresh_snapshot()
            if fresh is None:
                return FSM_ERROR
            if fresh.is_end:
                return FSM_QUITTING_BATTLE
            if fresh.game_num_turns_in_play > 0:
                return FSM_BATTLING
            if confirmed_waiting:
                if not verified:
                    verified = True
                    # 二次校验：面板仍在 → 点击未生效，重新执行换牌。
                    if auto_mulligan_flow.panel_present():
                        confirmed_waiting = False
                        verified = False
                        retry_count = 0
                        manual_controller.output(
                            "换牌点击未生效（面板仍在），重新执行……")
                        continue
                # 已提交：等待对局开始（换牌窗口已过，不再重复执行）。
                time.sleep(0.3)
                continue
            result = auto_mulligan_flow.run()
            if result.status == MulliganStatus.CONFIRMED:
                confirmed_waiting = True
                verified = False
                retry_count = 0
                _report_mulligan_diagnostic(
                    "confirmed", "已执行换牌，验证是否生效……")
                time.sleep(0.3)
                continue
            message = f"换牌推荐暂不可执行，继续重试：{result.diagnostics}"
            # 换牌面板已不在/阶段已变更时给出更明确提示，避免误以为卡死。
            diag = result.diagnostics
            if (diag == "recommendation_is_not_mulligan"
                    or diag.endswith(":recommendation_is_not_mulligan")
                    or diag == "mulligan_stage_changed"
                    or diag == "hand_changed"):
                message = ("换牌阶段未检测到可执行的留牌面板（可能已提交或"
                           "推荐尚未刷新），等待对局开始……")
            _report_mulligan_diagnostic(result.diagnostics, message)
            # 受控退避：瞬时失败（OCR 未稳/推荐刷新中）逐步拉长等待，最大 2s。
            retry_delay = min(0.5 * (2 ** retry_count), 2.0)
            retry_count += 1
            time.sleep(retry_delay)

    selected = manual_controller.choose_mulligan(snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    if not manual_controller.mulligan_is_current(snapshot, fresh_snapshot):
        manual_controller.output("留牌状态已经变化，本次选择未点击，请重新确认。")
        if fresh_snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if fresh_snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    try:
        with click.hearthstone_action_session():
            try:
                for hand_index in selected:
                    click.replace_starting_card(
                        hand_index, fresh_snapshot.my_hand_card_num)
                click.commit_choose_card()
            except Exception:
                try:
                    click.cancel_click()
                except Exception:
                    pass
                raise
    except Exception as exc:
        manual_controller.output(f"留牌鼠标操作失败：{exc}")
        return FSM_ERROR
    return wait_until_battle_starts()


def run_manual_battle_step():
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if not snapshot.is_my_turn:
        return None

    manual_controller.output(snapshot.format_for_manual_control())
    action = manual_controller.prompt_turn_action(snapshot)
    action = manual_controller.bind_to_turn(action, snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    revision_before = log_state.revision
    result = manual_controller.execute(action, fresh_snapshot)
    manual_controller.output(result.message)
    if result.recovery_needed:
        return FSM_ERROR
    if result.executed:
        wait_for_log_update(revision_before)
    return None


def _report_automation_diagnostic(code, message):
    """Report a stable automation state once instead of every loop."""
    global last_automation_diagnostic
    if last_automation_diagnostic == code:
        return
    manual_controller.output(message)
    last_automation_diagnostic = code


def _report_mulligan_diagnostic(code, message):
    """Report a stable mulligan retry state once instead of every 0.3s loop.

    换牌阶段如果 OCR 暂时读不出/面板已变更，原逻辑会每 0.3s 打印一次
    「换牌推荐暂不可执行」，在浮窗里刷屏。这里按诊断码去重，只在原因
    变化时输出一次，浮窗能稳定看见卡在哪一步。
    """
    global _mulligan_diagnostic_key
    if _mulligan_diagnostic_key == code:
        return
    manual_controller.output(message)
    _mulligan_diagnostic_key = code


def run_automatic_battle_step():
    """Observe opponent turns; execute one newly validated player action."""
    global player_turn_delay_key, last_automation_diagnostic

    snapshot = refresh_snapshot()
    if snapshot is None:
        _report_automation_diagnostic(
            "power_log_unavailable", "Power.log 暂不可用，继续重试。")
        return None
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if not snapshot.is_my_turn:
        _report_automation_diagnostic("opponent_turn", "等待对手操作。")
        # 对方回合清空延迟标记：每次切回我方回合必延时一次，
        # 同回合内多次出牌不再重复延时（不依赖可能失真的回合号）。
        player_turn_delay_key = None
        return None
    _report_automation_diagnostic("my_turn", "轮到己方操作：开始读取推荐……")
    turn = snapshot.game_num_turns_in_play
    if player_turn_delay_key != turn:
        # 每个新回合开始只延时一次（给盒子更新推荐留时间），
        # 同回合内的多次出牌操作之间不重复延时。
        player_turn_delay_key = turn
        manual_controller.output(
            f"[SYS] 回合 {turn} 开始：延时 "
            f"{recommendation_config.pre_action_delay_seconds:.0f}s 后开始 OCR……")
        time.sleep(recommendation_config.pre_action_delay_seconds)
        manual_controller.output(
            f"[SYS] 回合 {turn} 延时结束，开始本轮推荐读取。")
    if recommendation_flow is None:
        return run_manual_battle_step()

    result = recommendation_flow.run_player_turn_step()
    if result.status == FlowStepStatus.RETRY:
        if result.diagnostics == "discover_choice_still_open":
            message = "发现选择仍在，准备重新点击。"
        else:
            message = (
                "当前推荐暂不可执行，继续重试："
                f"{result.diagnostics}")
        _report_automation_diagnostic(
            f"retry:{result.diagnostics}", message)
    elif result.status == FlowStepStatus.OBSERVE:
        observe_messages = {
            "opponent_turn": "等待对手操作。",
            "waiting_recommendation_update": "等待盒子更新推荐。",
            "stale_mulligan_recommendation": "等待盒子刷新对局推荐。",
        }
        message = observe_messages.get(
            result.diagnostics,
            f"自动流程观察中：{result.diagnostics}")
        _report_automation_diagnostic(
            f"observe:{result.diagnostics}", message)
    else:
        last_automation_diagnostic = None
    return None


def Battling():
    global win_count, game_count

    print_out()
    while True:
        if quitting_flag:
            sys.exit(0)
        next_state = run_automatic_battle_step()
        if next_state == FSM_QUITTING_BATTLE:
            # 对局真正结束才计数：game_count=已完成场数，win_count=胜场。
            game_count += 1
            if log_state.my_entity.query_tag("PLAYSTATE") == "WON":
                win_count += 1
                info_print("你赢得了这场对战")
            else:
                info_print("你输了")
            return next_state
        if next_state == FSM_ERROR:
            return next_state
        time.sleep(0.2)


def QuittingBattle():
    print_out()

    time.sleep(5)

    loop_count = 0
    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        state = get_screen.get_state()
        if state in [FSM_CHOOSING_HERO, FSM_LEAVE_HS]:
            return state
        click.run_hearthstone_action(lambda: (
            click.cancel_click(),
            click.test_click(),
            click.commit_error_report(),
        ))

        loop_count += 1
        if loop_count >= 15:
            return FSM_ERROR

        time.sleep(STATE_CHECK_INTERVAL+random.random()+random.random()+random.random())


def GoBackHSAction():
    global FSM_state

    print_out()
    time.sleep(3)

    while not get_screen.test_hs_available():
        if quitting_flag or stop_after_current_game:
            sys.exit(0)
        click.enter_HS()
        time.sleep(10)

    # 有时候炉石进程会直接重写Power.log, 这时应该重新创建文件操作句柄
    init()

    return FSM_WAIT_MAIN_MENU


def MainMenuAction():
    print_out()

    time.sleep(3)

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)

        state = get_screen.get_state()

        # 重新连接对战之类的
        if state == FSM_BATTLING:
            ok = update_log_state()
            if ok and log_state.available:
                return FSM_BATTLING
        if state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO


def WaitMainMenu():
    print_out()
    wait_main_menu_count = 0
    while get_screen.get_state() != FSM_MAIN_MENU:
        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)
        wait_main_menu_count += 1
        if wait_main_menu_count >= 5:
            break
    return FSM_MAIN_MENU


def HandleErrorAction():
    print_out()

    if not get_screen.test_hs_available():
        return FSM_LEAVE_HS
    manual_controller.output("状态暂不可确认，等待后重新检测。")
    time.sleep(STATE_CHECK_INTERVAL)
    state = get_screen.get_state()
    known_states = {
        FSM_LEAVE_HS, FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_CHOOSING_CARD, FSM_BATTLING, FSM_QUITTING_BATTLE,
        FSM_WAIT_MAIN_MENU,
    }
    return state if state in known_states else FSM_ERROR


def FSM_dispatch(next_state):
    dispatch_dict = {
        FSM_LEAVE_HS: GoBackHSAction,
        FSM_MAIN_MENU: MainMenuAction,
        FSM_CHOOSING_HERO: ChoosingHeroAction,
        FSM_MATCHING: MatchingAction,
        FSM_CHOOSING_CARD: ChoosingCardAction,
        FSM_BATTLING: Battling,
        FSM_ERROR: HandleErrorAction,
        FSM_QUITTING_BATTLE: QuittingBattle,
        FSM_WAIT_MAIN_MENU: WaitMainMenu,
    }

    debug_print(f"当前状态为：+{next_state}")
    if next_state not in dispatch_dict:
        error_print("Unknown state!")
        return FSM_ERROR
    else:
        return dispatch_dict[next_state]()


def _initial_fsm_state():
    """启动/恢复时判断当前所处阶段，用于“立即接管”。

    屏幕像素(get_screen.get_state)在 BATTLING 时常不可靠，可能把对局误判成
    主菜单，导致恢复后要等下一回合才进入战斗。改用 Power.log 兜底：
    对局中(含对方回合)直接进入 Battling，换牌期进入 ChoosingCard；
    只有未对局时才退回屏幕检测。
    log_iter_func 每次开新 Power.log 会从头读到 EOF 一次性产出，
    因此一次 update_log_state() 即可把 log_state 快进到当前最新。
    """
    try:
        update_log_state()
    except Exception:
        pass
    if log_state.game_entity_id != 0 and not log_state.is_end:
        if log_state.game_num_turns_in_play > 0:
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    state = get_screen.get_state()
    return state if state else FSM_MAIN_MENU


def AutoHS_automata():
    global FSM_state, quitting_flag

    if get_screen.test_hs_available():
        hs_hwnd = get_screen.get_HS_hwnd()
        get_screen.move_window_foreground(hs_hwnd)
        time.sleep(0.5+random.random())

    # 出现这些状态时对局一定不在进行中，满足“打完本局再停止”的条件
    between_game_states = (
        FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_WAIT_MAIN_MENU, FSM_LEAVE_HS, "",
    )

    while 1:
        if quitting_flag:
            sys.exit(0)
        if stop_after_current_game and FSM_state in between_game_states:
            info_print("已到计划停止时间，本局对战已经结束，自动化停止。")
            quitting_flag = True
            shutdown_event.set()
            sys.exit(0)
        if FSM_state == "":
            FSM_state = _initial_fsm_state()
        FSM_state = FSM_dispatch(FSM_state)





if __name__ == "__main__":
    keyboard.add_hotkey("ctrl+q", system_exit)

    init()
