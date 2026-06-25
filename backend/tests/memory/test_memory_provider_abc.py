from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


def test_memory_layer_values():
    assert MemoryLayer.L2.value == "l2"
    assert MemoryLayer.L3.value == "l3"


def test_memory_turn_is_frozen_dataclass():
    turn = MemoryTurn(
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        run_id="r1",
        iteration=0,
        user_msgs=["hi"],
        asst_msgs=["hello"],
    )
    assert turn.user_id == "u1"
    assert turn.user_msgs == ["hi"]


def test_memory_provider_is_abstract():
    # Cannot instantiate the ABC directly.
    import pytest

    with pytest.raises(TypeError):
        MemoryProvider()  # type: ignore[abstract]
