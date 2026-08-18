import unittest
from types import SimpleNamespace

from manual_controller import (
    ClickExecutor,
    HeroPowerAction,
    ManualController,
    Target,
)
from src.game_state.recommendation_adapter import adapt_action
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


class TargetedHeroPowerParserTests(unittest.TestCase):
    def test_parses_supported_optional_targets(self):
        cases = (
            ("目标是敌方英雄", SlotRef("hero", "enemy")),
            ("目标是对方2号位随从", SlotRef("board_slot", "enemy", 2)),
            ("目标是我方英雄", SlotRef("hero", "friendly")),
            ("目标是己方1号位", SlotRef("board_slot", "friendly", 1)),
        )
        for target_line, expected_target in cases:
            with self.subTest(target_line=target_line):
                evidence = OcrEvidence(
                    frame_id="frame-1",
                    created_at=1.0,
                    lines=(
                        OcrLine("使用英雄技能", 0.99),
                        OcrLine(target_line, 0.99),
                    ),
                    normalized_text=f"打法参考A\n使用英雄技能\n{target_line}",
                    confidence=0.99,
                    backend="test",
                    preprocessing="none",
                )

                try:
                    action = RecommendationParser().parse(
                        evidence, turn_number=7, log_revision=11
                    )
                except RecommendationParseError as exc:
                    self.fail(f"英雄技能目标应能解析：{target_line}: {exc}")

                self.assertEqual(action.action, ActionKind.USE_HERO_POWER)
                self.assertEqual(action.target, expected_target)


class TargetedHeroPowerAdapterTests(unittest.TestCase):
    def test_binds_supported_targets_to_power_log_entities(self):
        friendly_minion = SimpleNamespace(
            entity_id="friendly-minion",
            card_id="MINION_FRIENDLY",
            zone_pos=2,
        )
        enemy_minion = SimpleNamespace(
            entity_id="enemy-minion",
            card_id="MINION_ENEMY",
            zone_pos=1,
        )
        state = SimpleNamespace(
            game_num_turns_in_play=7,
            my_hero_power=SimpleNamespace(entity_id="hero-power"),
            my_hero=SimpleNamespace(entity_id="friendly-hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
            my_minions=[friendly_minion],
            my_locations=[SimpleNamespace(zone_pos=1)],
            oppo_minions=[enemy_minion],
            oppo_locations=[],
        )
        cases = (
            (
                SlotRef("hero", "enemy"),
                Target("enemy", "hero", None, "enemy-hero"),
                "enemy-hero",
            ),
            (
                SlotRef("board_slot", "enemy", 1),
                Target("enemy", "minion", 0, "enemy-minion"),
                "enemy-minion",
            ),
            (
                SlotRef("hero", "friendly"),
                Target("friendly", "hero", None, "friendly-hero"),
                "friendly-hero",
            ),
            (
                SlotRef("board_slot", "friendly", 2),
                Target("friendly", "minion", 0, "friendly-minion"),
                "friendly-minion",
            ),
        )
        for proposed_target, expected_target, expected_entity_id in cases:
            with self.subTest(proposed_target=proposed_target):
                proposed = ProposedAction(
                    action_id="action-1",
                    frame_id="frame-1",
                    created_at=1.0,
                    turn_number=7,
                    log_revision=11,
                    raw_instruction="使用英雄技能",
                    normalized_instruction="使用英雄技能",
                    action=ActionKind.USE_HERO_POWER,
                    source=SlotRef("hero_power", "friendly"),
                    target=proposed_target,
                )

                adapted = adapt_action(proposed, state)

                self.assertEqual(
                    getattr(adapted.manual_action, "target", None),
                    expected_target,
                )
                self.assertEqual(
                    adapted.source_entity_id,
                    "hero-power",
                )
                self.assertEqual(
                    adapted.target_entity_id,
                    expected_entity_id,
                )


class TargetedHeroPowerClickExecutorTests(unittest.TestCase):
    def test_clicks_skill_then_target(self):
        events = []

        class FakeClickModule:
            @staticmethod
            def click_skill():
                events.append(("click_skill",))

            @staticmethod
            def choose_oppo_hero():
                events.append(("choose_oppo_hero",))

            @staticmethod
            def cancel_click():
                events.append(("cancel_click",))

        executor = ClickExecutor(click_module=FakeClickModule)
        target = Target("enemy", "hero", None, "enemy-hero")

        try:
            executor.use_hero_power(target, my_count=2, oppo_count=3)
        except TypeError as exc:
            self.fail(f"英雄技能执行器应接受可选目标：{exc}")

        self.assertEqual(
            events,
            [
                ("click_skill",),
                ("choose_oppo_hero",),
                ("cancel_click",),
            ],
        )

    def test_targetless_power_keeps_original_click_primitive(self):
        events = []

        class FakeClickModule:
            @staticmethod
            def use_skill_no_point():
                events.append(("use_skill_no_point",))

            @staticmethod
            def cancel_click():
                events.append(("cancel_click",))

        executor = ClickExecutor(click_module=FakeClickModule)

        executor.use_hero_power()

        self.assertEqual(events, [("use_skill_no_point",)])


class TargetedHeroPowerControllerTests(unittest.TestCase):
    @staticmethod
    def _state(target_entity_id="enemy-minion"):
        return SimpleNamespace(
            is_my_turn=True,
            game_num_turns_in_play=7,
            my_minions=[],
            oppo_minions=[SimpleNamespace(
                entity_id=target_entity_id,
                zone_pos=2,
            )],
            my_board_slot_num=0,
            oppo_board_slot_num=2,
        )

    @staticmethod
    def _action():
        return HeroPowerAction(
            target=Target(
                "enemy", "minion", 0, "enemy-minion"
            ),
            turn_number=7,
        )

    def test_dispatches_targeted_power_using_current_screen_position(self):
        calls = []
        executor = SimpleNamespace(
            use_hero_power=lambda *args: calls.append(args)
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(self._action(), self._state())

        self.assertTrue(result.executed)
        self.assertEqual(
            calls,
            [(Target("enemy", "minion", 1, "enemy-minion"), 0, 2)],
        )

    def test_rejects_targeted_power_if_bound_entity_has_changed(self):
        calls = []
        executor = SimpleNamespace(
            use_hero_power=lambda *args: calls.append(args)
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(
            self._action(),
            self._state(target_entity_id="replacement-99"),
        )

        self.assertFalse(result.executed)
        self.assertEqual(
            result.message,
            "英雄技能目标已经不存在，未执行操作。",
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
