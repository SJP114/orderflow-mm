from orderflow_mm.sensitivity import run_sensitivity_grid
from tests.test_simulation import _simulation_bars


def test_sensitivity_grid_covers_strategy_fill_fee_and_queue_combinations() -> None:
    result = run_sensitivity_grid(
        _simulation_bars(), fee_bps_values=(0.0, 1.0), queue_fractions=(0.5,)
    )
    assert len(result) == 8
    assert set(result["strategy"]) == {"symmetric", "signal_inventory"}
    assert set(result["fill_model"]) == {"touch", "queue_aware"}
    assert set(result["maker_fee_bps"]) == {0.0, 1.0}
