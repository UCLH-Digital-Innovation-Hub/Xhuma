import unittest

from app.ccda.helpers import validateNHSnumber


class TestValidateNHSNumber(unittest.TestCase):
    def test_valid_nhs_number(self):
        # Valid NHS number: 943 476 5919
        self.assertTrue(validateNHSnumber(9434765919))

    def test_invalid_check_digit(self):
        # Invalid NHS number: 943 476 5918 (incorrect check digit)
        self.assertFalse(validateNHSnumber(9434765918))

    def test_invalid_length_short(self):
        # NHS number with less than 10 digits
        self.assertFalse(validateNHSnumber(123456789))

    def test_invalid_length_long(self):
        # NHS number with more than 10 digits
        self.assertFalse(validateNHSnumber(12345678901))

    def test_invalid_check_digit_ten(self):
        # NHS number where calculated check digit is 10 (invalid)
        # Example: 401 023 2130
        self.assertFalse(validateNHSnumber(4010232130))

    def test_check_digit_eleven(self):
        # NHS number where calculated check digit is 11, which should be converted to 0
        # Example: 987 654 3210
        self.assertTrue(validateNHSnumber(9876543210))

    def test_non_numeric_input(self):
        # Non-numeric input should be invalid
        self.assertFalse(validateNHSnumber("testing"))

    def test_negative_number(self):
        # Negative numbers are invalid
        self.assertFalse(validateNHSnumber(-9434765919))


if __name__ == "__main__":
    unittest.main()
