import numpy as np

from gestura.evaluation.compare_features import circular_std, detrend_phase


def test_circular_std_is_zero_for_a_constant_phase():
    phase = np.full((50, 4), 1.2)
    assert np.allclose(circular_std(phase), 0.0, atol=1e-3)


def test_circular_std_is_large_for_random_phase():
    rng = np.random.default_rng(0)
    phase = rng.uniform(-np.pi, np.pi, size=(2000, 4))
    assert (circular_std(phase) > 1.5).all()


def test_circular_std_ignores_the_wrap_at_pi():
    # Phases straddling +pi and -pi are close on the circle although far apart as numbers.
    phase = np.array([[3.10], [-3.10], [3.12], [-3.12]])
    assert circular_std(phase)[0] < 0.2


def test_detrend_removes_a_random_linear_slope_per_packet():
    rng = np.random.default_rng(1)
    idx = np.arange(12, 180)
    slopes = rng.uniform(-0.3, 0.3, size=(40, 1))
    offsets = rng.uniform(-np.pi, np.pi, size=(40, 1))
    phase = np.angle(np.exp(1j * (slopes * idx + offsets)))  # wrapped, as measured
    resid = detrend_phase(phase, idx)
    assert np.abs(resid).max() < 1e-6
    assert (circular_std(phase) > 0.5).all()
