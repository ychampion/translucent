"""Fixed-order binary64 arithmetic for executable intervention world models.

These small regressions do not need BLAS or LAPACK. Python float operations and
``math.fsum`` fix their reduction order, and logistic regression always executes
the same number of Newton steps. Numerical tolerances belong to the registered
verification protocol; they never relax the required discrete action.
"""

from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
import math

NUMERICS_VERSION = 2
IRLS_STEPS = 16


def mean(values):
    """Average an explicit sequence with an accurately rounded reduction."""
    return math.fsum(values) / len(values)


def dot(left, right):
    """Accumulate products in the explicit input order without a native kernel."""
    if len(left) != len(right):
        raise ValueError("Dot product dimensions differ")
    return math.fsum(a * b for a, b in zip(left, right))


def linear_fit(x, y):
    """Return intercept and slope using centered, closed-form least squares."""
    x, y = [float(v) for v in x], [float(v) for v in y]
    if len(x) != len(y) or len(x) == 0:
        raise ValueError("Line fitting needs aligned nonempty observations")
    center_x, center_y = mean(x), mean(y)
    dx = [v - center_x for v in x]
    dy = [v - center_y for v in y]
    variance = dot(dx, dx)
    slope = dot(dx, dy) / variance if variance else 0.0
    return center_y - slope * center_x, slope


def quadratic_coefficient(x, y):
    """Fit the quadratic term after orthogonalizing it against constant and x."""
    x, y = [float(v) for v in x], [float(v) for v in y]
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("Quadratic fitting needs three aligned observations")
    center = mean(x)
    dx = [v - center for v in x]
    square = [v * v for v in dx]
    variance = math.fsum(square)
    if not variance:
        return 0.0
    center_square = mean(square)
    linear = dot(dx, square) / variance
    basis = [v2 - center_square - linear * v for v, v2 in zip(dx, square)]
    norm = dot(basis, basis)
    return dot(basis, y) / norm if norm else 0.0


def interpolate(at, x, y):
    """Linear interpolation with fixed scalar arithmetic and clamped endpoints."""
    if len(x) != len(y) or len(x) == 0:
        raise ValueError("Interpolation needs aligned nonempty observations")
    index = bisect_right(x, at)
    if index == 0:
        return float(y[0])
    if index == len(x):
        return float(y[-1])
    left, right = float(x[index - 1]), float(x[index])
    low, high = float(y[index - 1]), float(y[index])
    return low + (float(at) - left) * (high - low) / (right - left)


def quantile(values, probability):
    """The usual linear empirical quantile, evaluated in a fixed scalar order."""
    values = sorted(float(v) for v in values)
    if not values or not 0 <= probability <= 1:
        raise ValueError("Quantile needs observations and probability in [0,1]")
    rank = (len(values) - 1) * probability
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (rank - lower) * (values[upper] - values[lower])


def sigmoid(value):
    return 1.0 / (1.0 + math.exp(max(-35.0, min(35.0, -value))))


def _cholesky_solve(matrix, rhs):
    """Solve a positive-definite system with fixed loop and summation order."""
    size = len(rhs)
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            value = matrix[i][j] - math.fsum(
                lower[i][k] * lower[j][k] for k in range(j)
            )
            if i == j:
                if value <= 0:
                    raise ValueError("Logistic Hessian is not positive definite")
                lower[i][j] = math.sqrt(value)
            else:
                lower[i][j] = value / lower[j][j]
    forward = []
    for i in range(size):
        forward.append(
            (rhs[i] - math.fsum(lower[i][j] * forward[j] for j in range(i)))
            / lower[i][i]
        )
    result = [0.0] * size
    for i in reversed(range(size)):
        result[i] = (
            forward[i] - math.fsum(lower[j][i] * result[j] for j in range(i + 1, size))
        ) / lower[i][i]
    return result


@lru_cache(maxsize=4096)
def _logistic_coefficients(rows, outcomes, regularization):
    columns = list(zip(*rows))
    mu = [mean(column) for column in columns]
    sd = [
        math.sqrt(math.fsum((value - m) ** 2 for value in column) / len(rows)) or 1.0
        for m, column in zip(mu, columns)
    ]
    design = [
        [1.0] + [(value - m) / scale for value, m, scale in zip(row, mu, sd)]
        for row in rows
    ]
    columns = list(zip(*design))
    size = len(columns)
    products = {
        (i, j): [a * b for a, b in zip(columns[i], columns[j])]
        for i in range(size)
        for j in range(i + 1)
    }
    weights = [0.0] * size
    for _ in range(IRLS_STEPS):
        probabilities = [sigmoid(dot(row, weights)) for row in design]
        variance = [p * (1.0 - p) for p in probabilities]
        residual = [p - y for p, y in zip(probabilities, outcomes)]
        gradient = [
            dot(column, residual) + (regularization * weights[i] if i else 0.0)
            for i, column in enumerate(columns)
        ]
        hessian = [[0.0] * size for _ in range(size)]
        for (i, j), values in products.items():
            value = dot(variance, values)
            if i == j and i:
                value += regularization
            hessian[i][j] = hessian[j][i] = value
        step = _cholesky_solve(hessian, gradient)
        weights = [value - delta for value, delta in zip(weights, step)]
    return tuple(mu), tuple(sd), tuple(weights)


def logistic_fit(rows, outcomes, regularization):
    """Return fresh coefficient containers from an immutable, fully keyed cache."""
    mu, sd, weights = _logistic_coefficients(
        tuple(tuple(float(value) for value in row) for row in rows),
        tuple(float(value) for value in outcomes),
        float(regularization),
    )
    return dict(mu=list(mu), sd=list(sd), w=list(weights))
