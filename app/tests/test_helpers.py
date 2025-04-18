import unittest
from datetime import datetime

from app.ccda.helpers import date_helper, readable_date


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


if __name__ == "__main__":
    unittest.main()
