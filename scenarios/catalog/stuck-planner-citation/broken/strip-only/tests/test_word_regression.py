import unittest

from tally.words import count_words


class TrailingNewline(unittest.TestCase):
    def test_trailing_newline(self):
        self.assertEqual(2, count_words("one two\n"))


if __name__ == "__main__":
    unittest.main()
