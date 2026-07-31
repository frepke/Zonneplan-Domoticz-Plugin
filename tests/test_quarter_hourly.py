import os
import sys
import types
import unittest
from datetime import datetime as RealDateTime, timedelta, timezone
from unittest.mock import patch


PLUGIN_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PLUGIN_ROOT)

fake_requests = types.ModuleType("requests")
fake_requests.Session = lambda: object()
sys.modules.setdefault("requests", fake_requests)

import zonneplan_api as api_module


class FakeDateTime(RealDateTime):
    current = None

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.astimezone(tz) if tz is not None else value


def raw(value):
    return int(round(value * 10_000_000))


def quarter_item(start, buy, sell=None):
    end = start + timedelta(minutes=15)
    if sell is None:
        sell = buy - 0.1108481
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "price_tax_included": {"amount": raw(buy)},
        "price_tax_excluded": {"amount": raw(sell)},
        "sustainability_score": {"permille": 141},
    }


def quarter_cache(items):
    return {"data": {"chart": {"series": {"prices": items}}}}


def summary_item(start, buy, sell=None, gas=1.25):
    if sell is None:
        sell = buy - 0.1108481
    return {
        "datetime": start.isoformat(),
        "electricity_price": raw(buy),
        "electricity_price_excl_tax": raw(sell),
        "gas_price": raw(gas),
        "tariff_group": "normal",
        "sustainability_score": 5,
    }


class QuarterHourlyPriceTests(unittest.TestCase):
    def setUp(self):
        self.api = api_module.ZonneplanApi(lambda *_: None)
        self.base = RealDateTime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        self.cache = quarter_cache([
            quarter_item(self.base + timedelta(minutes=minute), 0.20 + minute / 1000.0)
            for minute in (0, 15, 30, 45)
        ])

    def current(self, minute):
        FakeDateTime.current = self.base + timedelta(minutes=minute)
        with patch.object(api_module, "datetime", FakeDateTime):
            return self.api.parse_current_electricity_prices(self.cache)

    def test_selects_exact_quarter_at_all_boundaries(self):
        expected = {
            0: "10:00", 14: "10:00", 15: "10:15", 29: "10:15",
            30: "10:30", 44: "10:30", 45: "10:45", 59: "10:45",
        }
        for minute, slot in expected.items():
            with self.subTest(minute=minute):
                self.assertEqual(self.current(minute)["slot_start"][11:16], slot)

    def test_live_response_scale_and_sell_field(self):
        sample = quarter_cache([{
            "start_date": "2026-07-31T22:00:00+00:00",
            "end_date": "2026-07-31T22:15:00+00:00",
            "price_tax_included": {"amount": 3522659},
            "price_tax_excluded": {"amount": 2414178},
            "sustainability_score": {"permille": 141},
        }])
        FakeDateTime.current = RealDateTime(2026, 7, 31, 22, 7, tzinfo=timezone.utc)
        with patch.object(api_module, "datetime", FakeDateTime):
            result = self.api.parse_current_electricity_prices(sample)
        self.assertAlmostEqual(result["electricity_price_incl"], 0.3522659)
        self.assertAlmostEqual(result["electricity_sell_price_ex_tax"], 0.2414178)
        self.assertEqual(result["source"], "quarter-hourly")

    def test_negative_prices_are_preserved(self):
        cache = quarter_cache([quarter_item(self.base, -0.05, -0.16)])
        FakeDateTime.current = self.base + timedelta(minutes=5)
        with patch.object(api_module, "datetime", FakeDateTime):
            result = self.api.parse_current_electricity_prices(cache)
        self.assertAlmostEqual(result["electricity_price_incl"], -0.05)
        self.assertAlmostEqual(result["electricity_sell_price_ex_tax"], -0.16)

    def test_stale_quarter_cache_falls_back_to_hourly_summary(self):
        stale = quarter_cache([quarter_item(self.base - timedelta(days=1), 0.99)])
        summary = {"data": {"price_per_hour": [summary_item(self.base, 0.31)]}}
        FakeDateTime.current = self.base + timedelta(minutes=40)
        with patch.object(api_module, "datetime", FakeDateTime):
            result = self.api.parse_current_electricity_prices(stale, summary)
        self.assertEqual(result["source"], "summary")
        self.assertAlmostEqual(result["electricity_price_incl"], 0.31)

    def test_hourly_summary_selects_one_full_hour(self):
        summary = {"data": {"price_per_hour": [
            summary_item(self.base, 0.31),
            summary_item(self.base + timedelta(hours=1), 0.32),
        ]}}
        FakeDateTime.current = self.base + timedelta(minutes=45)
        with patch.object(api_module, "datetime", FakeDateTime):
            result = self.api.parse_current_electricity_prices(None, summary)
        self.assertEqual(result["slot_start"][11:16], "10:00")
        self.assertAlmostEqual(result["gas_price_incl"], 1.25)

    def test_forecast_is_time_based_and_has_one_current_slot(self):
        now = self.base + timedelta(minutes=37)
        first = self.base - timedelta(hours=5)
        items = [quarter_item(first + timedelta(minutes=15 * index), 0.20)
                 for index in range(200)]
        FakeDateTime.current = now
        with patch.object(api_module, "datetime", FakeDateTime):
            payload = self.api.build_forecast_payload(
                quarter_cache(items), None, None, "test", past_hours=2, future_hours=38
            )

        self.assertEqual(payload["source"], "quarter-hourly")
        self.assertEqual(payload["interval_minutes"], 15)
        self.assertGreaterEqual(len(payload["hours"]), 160)
        self.assertEqual(sum(1 for entry in payload["hours"] if entry["is_current"]), 1)
        current = next(entry for entry in payload["hours"] if entry["is_current"])
        self.assertEqual(payload["electricity_now"], current["price"])
        self.assertEqual(payload["electricity_sell_now_ex_tax"], current["sell_price_ex_tax"])


if __name__ == "__main__":
    unittest.main()
