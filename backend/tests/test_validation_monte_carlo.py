from app.validation.engine import monte_carlo


def test_monte_carlo_is_deterministic_with_seed() -> None:
    returns = [0.01, -0.02, 0.03, 0.01]

    assert monte_carlo(returns, n_simulations=100, seed=7) == monte_carlo(returns, n_simulations=100, seed=7)
