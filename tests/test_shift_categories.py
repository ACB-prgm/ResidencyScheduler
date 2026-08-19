from __future__ import annotations

import pytest

from residency_scheduler.shift_categories import (
	SHIFT_CATEGORY_WEIGHTS,
	SHIFT_WEIGHT_SCALE,
	SHIFT_WEIGHT_UNITS_BY_CATEGORY,
	shift_category_for_weekday,
	shift_point_units_for_weekday,
	shift_points_for_weekday,
)


@pytest.mark.parametrize(
	("weekday", "category", "points", "units"),
	[
		(0, "weekday", 1.0, 2),
		(1, "weekday", 1.0, 2),
		(2, "weekday", 1.0, 2),
		(3, "weekday", 1.0, 2),
		(4, "friday", 1.5, 3),
		(5, "saturday", 2.0, 4),
		(6, "sunday", 1.5, 3),
	],
)
def test_shift_category_points_and_solver_units(weekday, category, points, units):
	assert shift_category_for_weekday(weekday) == category
	assert shift_points_for_weekday(weekday) == points
	assert shift_point_units_for_weekday(weekday) == units


def test_solver_units_are_derived_from_shared_category_weight_configuration():
	assert SHIFT_WEIGHT_SCALE == 2
	assert SHIFT_WEIGHT_UNITS_BY_CATEGORY == {
		category: int(points * SHIFT_WEIGHT_SCALE)
		for category, points in SHIFT_CATEGORY_WEIGHTS.items()
	}
	assert SHIFT_CATEGORY_WEIGHTS["saturday"] > SHIFT_CATEGORY_WEIGHTS["friday"]
	assert SHIFT_CATEGORY_WEIGHTS["friday"] == SHIFT_CATEGORY_WEIGHTS["sunday"]
	assert SHIFT_CATEGORY_WEIGHTS["sunday"] > SHIFT_CATEGORY_WEIGHTS["weekday"]


def test_invalid_weekday_is_rejected():
	with pytest.raises(ValueError, match="0 through 6"):
		shift_category_for_weekday(7)
