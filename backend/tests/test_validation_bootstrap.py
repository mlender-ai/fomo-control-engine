from app.validation.engine import bootstrap_sharpe


def test_bootstrap_sharpe_ci_is_deterministic() -> None:
    returns = [0.01, -0.02, 0.03, 0.01, 0.02]

    result = bootstrap_sharpe(returns, n_bootstrap=100, seed=11)

    assert result == bootstrap_sharpe(returns, n_bootstrap=100, seed=11)
    assert len(result["sharpe_ci"]) == 2
