import unittest
from types import SimpleNamespace

from manual_controller import ClickExecutor, ManualController, TradeCardAction
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import (
    ActionKind,
    OcrEvidence,
    OcrLine,
    ProposedAction,
    SlotRef,
)


class TradeCardParserTests(unittest.TestCase):
    def test_parses_trade_recommendation_as_hand_slot_action(self):
        self.assertTrue(
            hasattr(ActionKind, "TRADE_CARD"),
            "交易推荐需要独立的动作类型",
        )
        evidence = OcrEvidence(
            frame_id="frame-1",
            created_at=1.0,
            lines=(OcrLine("交易4号位卡牌", 0.99),),
            normalized_text="打法参考A\n交易4号位卡牌",
            confidence=0.99,
            backend="test",
            preprocessing="none",
        )

        action = RecommendationParser().parse(
            evidence, turn_number=7, log_revision=11
        )

        self.assertEqual(action.action, ActionKind.TRADE_CARD)
        self.assertEqual(action.source, SlotRef("hand_slot", "friendly", 4))
        self.assertEqual(action.normalized_instruction, "交易4号位卡牌")


class TradeCardAdapterTests(unittest.TestCase):
    def test_binds_trade_to_the_recommended_hand_card(self):
        card = SimpleNamespace(
            card_id="TRADE_001",
            cardtype="SPELL",
            entity_id="entity-42",
        )
        state = SimpleNamespace(
            game_num_turns_in_play=7,
            my_hand_cards=[SimpleNamespace(), card, SimpleNamespace()],
        )
        proposed = ProposedAction(
            action_id="action-1",
            frame_id="frame-1",
            created_at=1.0,
            turn_number=7,
            log_revision=11,
            raw_instruction="交易2号位卡牌",
            normalized_instruction="交易2号位卡牌",
            action=ActionKind.TRADE_CARD,
            source=SlotRef("hand_slot", "friendly", 2),
        )

        try:
            adapted = adapt_action(proposed, state)
        except RecommendationStateError as exc:
            self.fail(f"交易动作应能适配当前手牌：{exc}")

        self.assertEqual(type(adapted.manual_action).__name__, "TradeCardAction")
        self.assertEqual(adapted.manual_action.hand_index, 1)
        self.assertEqual(adapted.manual_action.card_id, "TRADE_001")
        self.assertEqual(adapted.manual_action.cardtype, "SPELL")
        self.assertEqual(adapted.manual_action.hand_entity_id, "entity-42")
        self.assertEqual(adapted.source_entity_id, "entity-42")
        self.assertEqual(adapted.postcondition, "hand_card_left")


class TradeCardClickExecutorTests(unittest.TestCase):
    def test_drags_selected_card_to_deck_immediately(self):
        events = []

        class FakeClickModule:
            @staticmethod
            def choose_card(hand_index, hand_count):
                events.append(("choose_card", hand_index, hand_count))

            @staticmethod
            def drag_card_to_deck():
                events.append(("drag_card_to_deck",))

            @staticmethod
            def cancel_click():
                events.append(("cancel_click",))

        executor = ClickExecutor(
            click_module=FakeClickModule,
            sleep_func=lambda seconds: events.append(("sleep", seconds)),
        )
        self.assertTrue(
            hasattr(executor, "trade_card"),
            "鼠标执行器需要提供独立的交易动作",
        )

        executor.trade_card(hand_index=1, hand_count=4)

        self.assertEqual(
            events,
            [
                ("choose_card", 1, 4),
                ("drag_card_to_deck",),
            ],
        )


class TradeCardControllerTests(unittest.TestCase):
    @staticmethod
    def _state(entity_id="entity-42"):
        card = SimpleNamespace(
            card_id="TRADE_001",
            cardtype="SPELL",
            entity_id=entity_id,
            name="测试交易牌",
        )
        return SimpleNamespace(
            is_my_turn=True,
            game_num_turns_in_play=7,
            my_hand_cards=[SimpleNamespace(), card, SimpleNamespace()],
        )

    @staticmethod
    def _action():
        return TradeCardAction(
            hand_index=1,
            card_id="TRADE_001",
            cardtype="SPELL",
            hand_entity_id="entity-42",
            turn_number=7,
        )

    def test_dispatches_validated_trade_to_click_executor(self):
        events = []
        executor = SimpleNamespace(
            trade_card=lambda hand_index, hand_count: events.append(
                (hand_index, hand_count)
            )
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(self._action(), self._state())

        self.assertTrue(result.executed)
        self.assertEqual(events, [(1, 3)])

    def test_rejects_trade_if_bound_hand_entity_has_changed(self):
        events = []
        executor = SimpleNamespace(
            trade_card=lambda hand_index, hand_count: events.append(
                (hand_index, hand_count)
            )
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )

        result = controller.execute(
            self._action(), self._state(entity_id="replacement-99")
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.message, "手牌对象已经变化，未执行交易。")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
