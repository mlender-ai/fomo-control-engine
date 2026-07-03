from app.validation.engine import walk_forward


def test_walk_forward_window_split() -> None:
    result = walk_forward([0.01, -0.01, 0.02, 0.03, -0.02], n_windows=3)

    assert result["windows"]
    assert 0 <= result["consistency_rate"] <= 1
