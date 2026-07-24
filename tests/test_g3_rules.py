from __future__ import annotations

import unittest
from datetime import UTC, datetime

from relay.errors import RelayError
from relay.schedules.rules import next_occurrences, validate_rule


class G3RuleTests(unittest.TestCase):
    def test_daily_returns_multiple_times_in_order(self):
        rule = {"type": "daily", "times": ["13:00", "09:00"], "timezone": "Asia/Seoul"}

        occurrences = next_occurrences(rule, datetime(2026, 7, 22, 23, 0, tzinfo=UTC), limit=3)

        self.assertEqual(
            [item.local_time.strftime("%Y-%m-%d %H:%M") for item in occurrences],
            ["2026-07-23 09:00", "2026-07-23 13:00", "2026-07-24 09:00"],
        )

    def test_weekly_uses_iso_weekdays(self):
        rule = {"type": "weekly", "weekdays": [1, 3, 5], "times": ["07:00"], "timezone": "Asia/Seoul"}

        occurrences = next_occurrences(rule, datetime(2026, 7, 22, 23, 0, tzinfo=UTC), limit=3)

        self.assertEqual([item.local_time.weekday() + 1 for item in occurrences], [5, 1, 3])

    def test_monthly_skips_missing_day_by_default(self):
        rule = {"type": "monthly", "month_days": [31], "times": ["09:00"], "timezone": "UTC"}

        occurrences = next_occurrences(rule, datetime(2026, 1, 1, tzinfo=UTC), limit=3)

        self.assertEqual(
            [item.local_time.strftime("%Y-%m-%d") for item in occurrences], ["2026-01-31", "2026-03-31", "2026-05-31"]
        )

    def test_monthly_can_use_last_day_policy(self):
        rule = {
            "type": "monthly",
            "month_days": [31],
            "times": ["09:00"],
            "missing_day_policy": "last_day",
            "timezone": "UTC",
        }

        occurrences = next_occurrences(rule, datetime(2026, 1, 31, 10, 0, tzinfo=UTC), limit=2)

        self.assertEqual([item.local_time.strftime("%Y-%m-%d") for item in occurrences], ["2026-02-28", "2026-03-31"])

    def test_n_days_uses_anchor_date(self):
        rule = {
            "type": "n_days",
            "interval_days": 3,
            "anchor_date": "2026-07-23",
            "times": ["09:00"],
            "timezone": "Asia/Seoul",
        }

        occurrences = next_occurrences(rule, datetime(2026, 7, 22, 23, 0, tzinfo=UTC), limit=3)

        self.assertEqual(
            [item.local_time.strftime("%Y-%m-%d") for item in occurrences], ["2026-07-23", "2026-07-26", "2026-07-29"]
        )

    def test_once_returns_one_occurrence_then_none(self):
        rule = {"type": "once", "run_at_local": "2026-08-03T10:30:00", "timezone": "Asia/Seoul"}

        first = next_occurrences(rule, datetime(2026, 8, 1, tzinfo=UTC))
        second = next_occurrences(rule, first[0].instant_utc)

        self.assertEqual(first[0].local_time.strftime("%Y-%m-%d %H:%M"), "2026-08-03 10:30")
        self.assertEqual(second, [])

    def test_invalid_rule_raises_stable_error(self):
        with self.assertRaises(RelayError) as context:
            validate_rule({"type": "daily", "times": ["25:00"], "timezone": "Not/AZone"})

        self.assertEqual(context.exception.code, "SCHEDULE_RULE_INVALID")

    def test_nonexistent_dst_time_is_skipped(self):
        rule = {"type": "daily", "times": ["02:30"], "timezone": "America/New_York"}

        occurrences = next_occurrences(rule, datetime(2026, 3, 7, 0, 0, tzinfo=UTC), limit=2)

        self.assertEqual(
            [item.local_time.strftime("%Y-%m-%d %H:%M") for item in occurrences],
            ["2026-03-07 02:30", "2026-03-09 02:30"],
        )

    def test_ambiguous_dst_time_uses_first_occurrence(self):
        rule = {"type": "once", "run_at_local": "2026-11-01T01:30:00", "timezone": "America/New_York"}

        occurrences = next_occurrences(rule, datetime(2026, 10, 31, tzinfo=UTC))

        self.assertEqual(occurrences[0].local_time.fold, 0)
        self.assertEqual(occurrences[0].instant_utc, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))

    def test_occurrence_key_is_stable(self):
        rule = {"type": "daily", "times": ["09:00"], "timezone": "UTC"}
        after = datetime(2026, 7, 23, tzinfo=UTC)

        first = next_occurrences(rule, after)[0]
        second = next_occurrences(rule, after)[0]

        self.assertEqual(first.occurrence_key, second.occurrence_key)


if __name__ == "__main__":
    unittest.main()
