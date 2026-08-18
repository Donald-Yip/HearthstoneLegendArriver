import unittest
from types import SimpleNamespace

from manual_controller import ManualController, PlayCardAction


class FullBoardMinionTests(unittest.TestCase):
    def test_full_board_does_not_block_recommended_minion_play(self):
        calls = []
        executor = SimpleNamespace(
            play_minion=lambda *args: calls.append(args)
        )
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor,
        )
        card = SimpleNamespace(
            card_id="MINION_001",
            cardtype="MINION",
            entity_id="entity-1",
            name="测试随从",
        )
        state = SimpleNamespace(
            is_my_turn=True,
            game_num_turns_in_play=7,
            my_hand_cards=[card],
            my_minions=[SimpleNamespace() for _ in range(7)],
            oppo_minions=[],
            my_board_slot_num=7,
            oppo_board_slot_num=0,
        )
        action = PlayCardAction(
            hand_index=0,
            card_id="MINION_001",
            cardtype="MINION",
            gap_index=7,
            hand_entity_id="entity-1",
            turn_number=7,
        )

        result = controller.execute(action, state)

        self.assertTrue(result.executed)
        self.assertEqual(
            calls,
            [(0, 1, 7, 7, 0, None)],
        )


if __name__ == "__main__":
    unittest.main()
