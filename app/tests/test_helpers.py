import unittest
from datetime import datetime

from app.ccda.helpers import date_helper, readable_date
from unittest import TestCase
from unittest.mock import MagicMock
from app.ccda.helpers import effective_time_helper, date_helper
from fhirclient.models import period
from app.ccda.models.datatypes import SXCM_TS


class TestDateHelper(unittest.TestCase):
    def test_valid_iso_date(self):
        """Test date_helper with a valid ISO date string."""
        isodate = "2023-03-15T12:34:56Z"
        expected_result = "20230315"
        self.assertEqual(date_helper(isodate), expected_result)

    def test_iso_date_without_time(self):
        """Test date_helper with an ISO date string without time."""
        isodate = "2023-03-15"
        expected_result = "20230315"
        self.assertEqual(date_helper(isodate), expected_result)

    def test_invalid_iso_date(self):
        """Test date_helper with an invalid ISO date string."""
        isodate = "invalid-date"
        with self.assertRaises(ValueError):
            date_helper(isodate)

    def test_empty_iso_date(self):
        """Test date_helper with an empty string."""
        isodate = ""
        with self.assertRaises(ValueError):
            date_helper(isodate)


class TestReadableDate(unittest.TestCase):
    def test_valid_date(self):
        """Test readable_date with a valid YYYYMMDD date string."""
        date = "20230315"
        expected_result = "15/03/2023"
        self.assertEqual(readable_date(date), expected_result)

    def test_invalid_date_format(self):
        """Test readable_date with an invalid date format."""
        date = "15-03-2023"
        with self.assertRaises(ValueError):
            readable_date(date)

    def test_empty_date(self):
        """Test readable_date with an empty string."""
        date = ""
        with self.assertRaises(ValueError):
            readable_date(date)

    def test_non_numeric_date(self):
        """Test readable_date with a non-numeric string."""
        date = "invalid"
        with self.assertRaises(ValueError):
            readable_date(date)

class TestEffectiveTimeHelper(TestCase):
    def test_effective_time_with_start_and_end(self):
        """Test effective_time_helper with both start and end dates."""
        # mock_period = MagicMock(spec=period.Period)
        # mock_period.start.isostring = "2023-03-15T12:34:56Z"
        # mock_period.end.isostring = "2023-03-20T12:34:56Z"
        mock_period = period.Period({
            "start": "2024-05-22T00:00:00+01:00",
            "end": "2025-03-26T00:00:00+00:00",
        })
        expected_start = SXCM_TS(operator="low")
        expected_start.value = date_helper(mock_period.start.isostring)

        expected_end = SXCM_TS(operator="high")
        expected_end.value = date_helper(mock_period.end.isostring)

        result = effective_time_helper(mock_period)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].operator, expected_start.operator)
        self.assertEqual(result[0].value, expected_start.value)
        self.assertEqual(result[1].operator, expected_end.operator)
        self.assertEqual(result[1].value, expected_end.value)

    def test_effective_time_with_only_start(self):
        """Test effective_time_helper with only a start date."""
        mock_period = period.Period({
            "start": "2024-05-22T00:00:00+01:00",
            "end": None,
        })

        expected_start = SXCM_TS(operator="low")
        expected_start.value = date_helper(mock_period.start.isostring)

        result = effective_time_helper(mock_period)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].operator, expected_start.operator)
        self.assertEqual(result[0].value, expected_start.value)


if __name__ == "__main__":
    unittest.main()
