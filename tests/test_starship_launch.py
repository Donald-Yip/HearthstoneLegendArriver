import unittest
from types import SimpleNamespace

import click as hearthstone_click
from manual_controller import (
    ClickExecutor,
    LaunchStarshipAction,
    ManualController,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.flow.recommendation_flow import RecommendationFlow
from src.parser.recommendation_parser import (
    RecommendationParseError,
    RecommendationParser,
)
from src.recommendation_models import (
    ActionKind,
    OcrEvidence,
    OcrLine,
    ProposedAction,
    SlotRef,
)


class StarshipLaunchParserTests(unittest.TestCase):
    def test_parses_targetless_minion_attack_for_state_based_adaptation(self):
        evidence = OcrEvidence(
            frame_id="frame-1",
            created_at=1.0,
            lines=(OcrLine("操作2号位随从攻击", 0.99),),
            normalized_text="打法参考A\n操作2号位随从攻击\n战列巡航舰",
            confidence=0.99,
            backend="test",
            preprocessing="none",
        )

        try:
            action = RecommendationParser().parse(
                evidence, turn_number=7, log_revision=11
            )
        except RecommendationParseError as exc:
            self.fail(f"无目标随从操作应交给状态适配层判断：{exc}")

        self.assertEqual(action.action, ActionKind.ATTACK)
        self.assertEqual(action.source, SlotRef("board_slot", "friendly", 2))
        self.assertIsNone(action.target)
        self.assertEqual(
            action.normalized_instruction,
            "操作2号位随从攻击",
        )


class StarshipLaunchAdapterTests(unittest.TestCase):
    def test_power_log_card_id_routes_cruiser_to_launch_action(self):
        ordinary = SimpleNamespace(
            card_id="MINION_001",
            entity_id="entity-1",
            zone_pos=1,
        )
        cruiser = SimpleNamespace(
            card_id="SC_999t",
            entity_id="starship-42",
            zone_pos=2,
        )
        state = SimpleNamespace(
            game_num_turns_in_play=7,
            my_minions=[ordinary, cruiser],
            my_locations=[],
            oppo_minions=[],
            oppo_locations=[],
        )
        proposed = ProposedAction(
            action_id="action-1",
            frame_id="frame-1",
            created_at=1.0,
            turn_number=7,
            log_revision=11,
            raw_instruction="操作2号位随从攻击",
            normalized_instruction="操作2号位随从攻击",
            action=ActionKind.ATTACK,
            source=SlotRef("board_slot", "friendly", 2),
            target=None,
        )

        try:
            adapted = adapt_action(proposed, state)
        except Exception as exc:
            self.fail(f"SC_999t 应适配为发射动作：{exc}")

        self.assertEqual(
            type(adapted.manual_action).__name__,
            "LaunchStarshipAction",
        )
        self.assertEqual(adapted.manual_action.starship_index, 1)
        self.assertEqual(adapted.manual_action.card_id, "SC_999t")
        self.assertEqual(
            adapted.manual_action.starship_entity_id,
            "starship-42",
        )
        self.assertEqual(adapted.source_entity_id, "starship-42")
        self.assertEqual(adapted.postcondition, "starship_launched")

    def test_targetless_ordinary_minion_attack_remains_rejected(self):
        state = SimpleNamespace(
            game_num_turns_in_play=7,
            my_minions=[SimpleNamespace(
                card_id="MINION_001",
                entity_id="entity-1",
                zone_pos=1,
            )],
            my_locations=[],
            oppo_minions=[],
            oppo_locations=[],
        )
        proposed = ProposedAction(
            action_id="action-2",
            frame_id="frame-1",
            created_at=1.0,
            turn_number=7,
            log_revision=11,
            raw_instruction="操作1号位随从攻击",
            normalized_instruction="操作1号位随从攻击",
            action=ActionKind.ATTACK,
            source=SlotRef("board_slot", "friendly", 1),
            target=None,
        )

        try:
            adapt_action(proposed, state)
        except RecommendationStateError as exc:
            self.assertEqual(str(exc), "attack_target_required")
        except Exception as exc:
            self.fail(f"普通随从缺少目标应安全拒绝：{exc}")
        else:
            self.fail("普通随从缺少目标时不应生成攻击动作")


class StarshipLaunchClickExecutorTests(unittest.TestCase):
    def test_clicks_starship_then_launch_button(self):
        events = []

        class FakeClickModule:
            @staticmethod
            def choose_my_board_entity(screen_index, board_count):
                events.append(
                    ("choose_my_board_entity", screen_index, board_count)
                )

            @staticmethod
            def click_launch_starship():
                events.append(("click_launch_starship",))

            @staticmethod
            def cancel_click():
                events.append(("cancel_click",))

        executor = ClickExecutor(click_module=FakeClickModule)
        self.assertTrue(
            hasattr(executor, "launch_starship"),
            "鼠标执行器需要提供独立的发射星舰动作",
        )

        executor.launch_starship(starship_screen_index=1, board_count=2)

        self.assertEqual(
            events,
            [
                ("choose_my_board_entity", 1, 2),
                ("click_launch_starship",),
            ],
        )

    def test_launch_button_uses_confirmed_coordinate(self):
        self.assertTrue(
            hasattr(hearthstone_click, "click_launch_starship"),
            "点击模块需要提供发射按钮点击原语",
        )
        positions = []
        original_left_click = hearthstone_click.left_click
        original_rand_sleep = hearthstone_click.rand_sleep
        hearthstone_click.left_click = (
            lambda x, y: positions.append((x, y))
        )
        hearthstone_click.rand_sleep = lambda _seconds: None
        try:
            hearthstone_click.click_launch_starship()
        finally:
            hearthstone_click.left_click = original_left_click
            hearthstone_click.rand_sleep = original_rand_sleep

        self.assertEqual(positions, [(1080, 920)])


class StarshipLaunchControllerTests(unittest.TestCase):
    @staticmethod
    def _state(starship_entity_id="starship-42"):
        ordinary = SimpleNamespace(
            card_id="MINION_001",
            entity_id="entity-1",
            zone_pos=1,
            name="普通随从",
        )
        cruiser = SimpleNamespace(
            card_id="SC_999t",
            entity_id=starship_entity_id,
            zone_pos=3,
            name="战列巡航舰",
        )
        return SimpleNamespace(
            is_my_turn=True,
            game_num_turns_in_play=7,
            my_minions=[ordinary, cruiser],
            my_board_slot_num=3,
        )

    @staticmethod
    def _action():
        return LaunchStarshipAction(
            starship_index=1,
            card_id="SC_999t",
            starship_entity_id="starship-42",
            turn_number=7,
        )

    def test_dispatches_validated_starship_launch_using_zone_position(self):
        calls = []
        executor = SimpleNamespace(
            launch_starship=lambda screen_index, board_count: calls.append(
                (screen_index, board_count)
            )
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(self._action(), self._state())

        self.assertTrue(result.executed)
        self.assertEqual(calls, [(2, 3)])

    def test_rejects_launch_if_power_log_entity_has_changed(self):
        calls = []
        executor = SimpleNamespace(
            launch_starship=lambda screen_index, board_count: calls.append(
                (screen_index, board_count)
            )
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(
            self._action(),
            self._state(starship_entity_id="replacement-99"),
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.message, "星舰对象已经变化，未执行发射。")
        self.assertEqual(calls, [])


class StarshipLaunchPostconditionTests(unittest.TestCase):
    def test_confirms_launch_when_power_log_starship_entity_changes(self):
        before = SimpleNamespace(my_minions=[SimpleNamespace(
            entity_id="starship-42",
            card_id="SC_999t",
        )])
        after = SimpleNamespace(my_minions=[SimpleNamespace(
            entity_id="starship-42",
            card_id="GDB_100t1",
        )])

        changed = RecommendationFlow._postcondition(
            "starship_launched",
            before,
            after,
            source_entity_id="starship-42",
        )

        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
