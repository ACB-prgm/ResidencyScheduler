from __future__ import annotations

import pytest

from residency_scheduler.shift_categories import (
	SHIFT_POINTS_BY_CATEGORY,
	SHIFT_POINT_SCALE,
	SHIFT_POINT_UNITS_BY_CATEGORY,
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


def test_solver_units_are_derived_from_shared_point_configuration():
	assert SHIFT_POINT_SCALE == 2
	assert SHIFT_POINT_UNITS_BY_CATEGORY == {
		category: int(points * SHIFT_POINT_SCALE)
		for category, points in SHIFT_POINTS_BY_CATEGORY.items()
	}
	assert SHIFT_POINTS_BY_CATEGORY["saturday"] > SHIFT_POINTS_BY_CATEGORY["friday"]
	assert SHIFT_POINTS_BY_CATEGORY["friday"] == SHIFT_POINTS_BY_CATEGORY["sunday"]
	assert SHIFT_POINTS_BY_CATEGORY["sunday"] > SHIFT_POINTS_BY_CATEGORY["weekday"]


def test_invalid_weekday_is_rejected():
	with pytest.raises(ValueError, match="0 through 6"):
		shift_category_for_weekday(7)
