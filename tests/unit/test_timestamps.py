from __future__ import annotations

import unittest
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from crypto_lab.timestamps import UNIX_EPOCH
from crypto_lab.timestamps import unix_ms_to_utc_datetime
from crypto_lab.timestamps import unix_ns_to_utc_datetime
from crypto_lab.timestamps import utc_datetime_to_ms
from crypto_lab.timestamps import utc_datetime_to_ns


class ExactTimestampTests(unittest.TestCase):
    def test_microseconds_and_negative_epoch_use_integer_arithmetic(self) -> None:
        before = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
        after = datetime(2021, 8, 24, 12, 34, 56, 1, tzinfo=UTC)
        self.assertEqual(utc_datetime_to_ns(before), -1_000)
        self.assertEqual(utc_datetime_to_ns(after), 1_629_808_496_000_001_000)
        self.assertEqual(unix_ns_to_utc_datetime(-1_000), before)
        self.assertEqual(unix_ns_to_utc_datetime(utc_datetime_to_ns(after)), after)

    def test_half_open_boundary_preserves_exact_one_microsecond_predecessor(self) -> None:
        boundary = datetime(2025, 1, 1, tzinfo=UTC)
        previous = boundary - timedelta(microseconds=1)
        boundary_ns = utc_datetime_to_ns(boundary)
        self.assertEqual(utc_datetime_to_ns(previous), boundary_ns - 1_000)
        self.assertLess(utc_datetime_to_ns(previous), boundary_ns)
        self.assertEqual(unix_ns_to_utc_datetime(boundary_ns), boundary)

    def test_far_future_date_does_not_lose_precision(self) -> None:
        value = datetime(2500, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
        delta = value - UNIX_EPOCH
        expected = (
            delta.days * 86_400_000_000_000
            + delta.seconds * 1_000_000_000
            + delta.microseconds * 1_000
        )
        self.assertEqual(utc_datetime_to_ns(value), expected)
        self.assertEqual(unix_ns_to_utc_datetime(expected), value)

    def test_millisecond_conversion_floors_submillisecond_without_float(self) -> None:
        value = datetime(2025, 1, 1, microsecond=999, tzinfo=UTC)
        milliseconds = utc_datetime_to_ms(value)
        self.assertEqual(unix_ms_to_utc_datetime(milliseconds), datetime(2025, 1, 1, tzinfo=UTC))

    def test_non_utc_and_unrepresentable_nanoseconds_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            utc_datetime_to_ns(datetime(2025, 1, 1))
        with self.assertRaises(ValueError):
            utc_datetime_to_ns(
                datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            )
        with self.assertRaises(ValueError):
            unix_ns_to_utc_datetime(1)


if __name__ == "__main__":
    unittest.main()
