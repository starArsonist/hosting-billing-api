from datetime import date
from decimal import Decimal

import pytest

from hosting_billing.domain import (
    Assignment,
    DeviceUsage,
    TariffPoint,
    apply_override,
    build_client_report,
    price_on,
    tenant_on,
)

D = date
BASE = Decimal("4.00")


def usage(device_id, assignments, days, serial=None, model="X-100"):
    return DeviceUsage(device_id, serial or f"SN-{device_id}", model, assignments, days)


def march(first, last, kwh=10.0):
    return [(D(2026, 3, d), kwh) for d in range(first, last + 1)]


# --------------------------------------------------------------------------- price_on


def test_price_uses_base_before_first_change():
    changes = [TariffPoint(D(2026, 3, 10), Decimal("5.00"))]
    assert price_on(BASE, changes, D(2026, 3, 9)) == BASE
    assert price_on(BASE, [], D(2030, 1, 1)) == BASE


def test_price_change_is_effective_from_its_date_inclusive():
    changes = [TariffPoint(D(2026, 3, 10), Decimal("5.00"))]
    assert price_on(BASE, changes, D(2026, 3, 10)) == Decimal("5.00")
    assert price_on(BASE, changes, D(2026, 12, 31)) == Decimal("5.00")


def test_price_picks_latest_applicable_change_regardless_of_input_order():
    changes = [
        TariffPoint(D(2026, 5, 1), Decimal("6.00")),
        TariffPoint(D(2026, 3, 1), Decimal("5.00")),
    ]
    assert price_on(BASE, changes, D(2026, 4, 1)) == Decimal("5.00")
    assert price_on(BASE, changes, D(2026, 5, 1)) == Decimal("6.00")


# -------------------------------------------------------------------------- tenant_on


def test_tenant_follows_assignment_history():
    history = [Assignment(1, D(2026, 1, 1)), Assignment(2, D(2026, 3, 15))]
    assert tenant_on(history, D(2026, 3, 14)) == 1
    assert tenant_on(history, D(2026, 3, 15)) == 2


def test_tenant_before_first_assignment_is_first_tenant_and_none_without_history():
    history = [Assignment(7, D(2026, 3, 1))]
    assert tenant_on(history, D(2025, 1, 1)) == 7
    assert tenant_on([], D(2026, 1, 1)) is None


def test_tenant_ignores_input_order():
    history = [Assignment(2, D(2026, 3, 15)), Assignment(1, D(2026, 1, 1))]
    assert tenant_on(history, D(2026, 3, 20)) == 2


# ------------------------------------------------------------------------ the report


def test_simple_report_with_fixed_fee():
    device = usage(1, [Assignment(1, D(2026, 1, 1))], march(1, 3, kwh=10.5))
    report = build_client_report(1, BASE, [], [device], D(2026, 3, 1), D(2026, 3, 31), 100)
    bill = report.devices[0]
    assert bill.total_kwh == 31.5
    assert bill.energy_cost == Decimal("126.00")
    assert bill.fixed_fee == Decimal("100.00")
    assert report.total_to_pay == Decimal("226.00")


def test_tariff_change_inside_the_period_is_priced_per_day():
    """The headline fix: the old code billed everything at the *current* price."""
    changes = [TariffPoint(D(2026, 3, 4), Decimal("6.00"))]
    device = usage(1, [Assignment(1, D(2026, 1, 1))], march(1, 6, kwh=10.0))
    report = build_client_report(1, BASE, changes, [device], D(2026, 3, 1), D(2026, 3, 31))
    bill = report.devices[0]

    assert [(s.date_from.day, s.date_to.day, s.price_per_kwh, s.cost) for s in bill.segments] == [
        (1, 3, Decimal("4.00"), Decimal("120.00")),
        (4, 6, Decimal("6.00"), Decimal("180.00")),
    ]
    assert bill.energy_cost == Decimal("300.00")  # 3*10*4 + 3*10*6, not 6*10*6 = 360


def test_transfer_inside_the_period_bills_each_tenant_only_for_their_days():
    history = [Assignment(1, D(2026, 1, 1)), Assignment(2, D(2026, 3, 4))]
    device = usage(1, history, march(1, 6, kwh=10.0))
    args = (BASE, [], [device], D(2026, 3, 1), D(2026, 3, 31))

    first = build_client_report(1, *args)
    second = build_client_report(2, *args)
    assert first.devices[0].total_kwh == 30.0  # days 1-3
    assert second.devices[0].total_kwh == 30.0  # days 4-6
    assert first.devices[0].total_kwh + second.devices[0].total_kwh == 60.0  # nothing lost/doubled


def test_each_client_is_billed_with_their_own_tariff():
    history = [Assignment(1, D(2026, 1, 1)), Assignment(2, D(2026, 3, 4))]
    device = usage(1, history, march(1, 6, kwh=10.0))
    rich = build_client_report(2, Decimal("10.00"), [], [device], D(2026, 3, 1), D(2026, 3, 31))
    assert rich.energy_cost == Decimal("300.00")


def test_period_bounds_are_inclusive_and_exclude_other_days():
    device = usage(1, [Assignment(1, D(2026, 1, 1))], march(1, 10, kwh=1.0))
    report = build_client_report(1, BASE, [], [device], D(2026, 3, 3), D(2026, 3, 5))
    assert [d.day.day for d in report.devices[0].days] == [3, 4, 5]


def test_device_without_billable_days_has_no_line_and_no_fixed_fee():
    other_tenant = usage(1, [Assignment(2, D(2026, 1, 1))], march(1, 3))
    no_stats_in_period = usage(2, [Assignment(1, D(2026, 1, 1))], march(1, 3))
    report = build_client_report(
        1, BASE, [], [other_tenant, no_stats_in_period], D(2026, 4, 1), D(2026, 4, 30), 100
    )
    assert report.devices == ()
    assert report.total_to_pay == Decimal("0.00")


def test_fixed_fee_is_charged_once_per_billed_device():
    devices = [usage(i, [Assignment(1, D(2026, 1, 1))], march(1, 2)) for i in (1, 2, 3)]
    report = build_client_report(1, BASE, [], devices, D(2026, 3, 1), D(2026, 3, 31), "50.00")
    assert report.fixed_fees == Decimal("150.00")


def test_rounding_is_half_up_per_segment():
    device = usage(1, [Assignment(1, D(2026, 1, 1))], [(D(2026, 3, 1), 0.125)])
    report = build_client_report(1, Decimal("1.00"), [], [device], D(2026, 3, 1), D(2026, 3, 1))
    assert report.energy_cost == Decimal("0.13")  # 0.125 -> 0.13, banker's rounding would give 0.12


def test_invalid_period_is_rejected():
    with pytest.raises(ValueError):
        build_client_report(1, BASE, [], [], D(2026, 3, 5), D(2026, 3, 1))


# ------------------------------------------------------------------------- overrides


def test_override_replaces_only_the_provided_fields():
    original = {"uptime_hours": 20.0, "consumption_kwh": 60.0, "hashrate_ths": 100.0}
    effective = apply_override(original, {"consumption_kwh": 72.5, "uptime_hours": None})
    assert effective.consumption_kwh == 72.5
    assert effective.uptime_hours == 20.0 and effective.hashrate_ths == 100.0
    assert effective.overridden_fields == ("consumption_kwh",)
    assert effective.is_override


def test_no_override_returns_original_untouched():
    original = {"uptime_hours": 20.0, "consumption_kwh": 60.0, "hashrate_ths": 100.0}
    effective = apply_override(original, None)
    assert effective.consumption_kwh == 60.0 and not effective.is_override
    assert original["consumption_kwh"] == 60.0
