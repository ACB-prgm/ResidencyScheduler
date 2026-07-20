from __future__ import annotations

from fractions import Fraction
from math import lcm


SHIFT_POINTS_BY_CATEGORY = {
	"weekday": 1.0,
	"friday": 1.5,
	"saturday": 2.0,
	"sunday": 1.5,
}

SHIFT_CATEGORY_BY_WEEKDAY = {
	0: "weekday",
	1: "weekday",
	2: "weekday",
	3: "weekday",
	4: "friday",
	5: "saturday",
	6: "sunday",
}

_POINT_FRACTIONS = {
	category: Fraction(str(points))
	for category, points in SHIFT_POINTS_BY_CATEGORY.items()
}
SHIFT_POINT_SCALE = lcm(*(value.denominator for value in _POINT_FRACTIONS.values()))
SHIFT_POINT_UNITS_BY_CATEGORY = {
	category: int(value * SHIFT_POINT_SCALE)
	for category, value in _POINT_FRACTIONS.items()
}


def shift_category_for_weekday(weekday: int) -> str:
	try:
		return SHIFT_CATEGORY_BY_WEEKDAY[int(weekday)]
	except (KeyError, TypeError, ValueError) as exc:
		raise ValueError("Weekday must be an integer from 0 through 6.") from exc


def shift_points_for_weekday(weekday: int) -> float:
	return SHIFT_POINTS_BY_CATEGORY[shift_category_for_weekday(weekday)]


def shift_point_units_for_weekday(weekday: int) -> int:
	return SHIFT_POINT_UNITS_BY_CATEGORY[shift_category_for_weekday(weekday)]
