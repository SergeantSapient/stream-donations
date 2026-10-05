"""The manual-entry currency list matches the exchange-rate provider."""

from __future__ import annotations

import unittest

from streamdonations.currencies import SUPPORTED_CURRENCIES, SUPPORTED_CURRENCY_CODES
from streamdonations.money import _DISPLAY_PREFIXES
from streamdonations.paste import _CURRENCIES


class CurrencyListTests(unittest.TestCase):
    def test_provider_list_is_complete_and_alphabetical(self) -> None:
        codes = [code for code, _name in SUPPORTED_CURRENCIES]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(len(codes), 162)
        self.assertEqual(len(SUPPORTED_CURRENCY_CODES), 162)
        self.assertEqual(dict(SUPPORTED_CURRENCIES)["USD"], "United States Dollar")
        self.assertEqual(dict(SUPPORTED_CURRENCIES)["PHP"], "Philippine Peso")
        self.assertNotIn("KPW", SUPPORTED_CURRENCY_CODES)
        self.assertNotIn("KICKS", SUPPORTED_CURRENCY_CODES)

    def test_paste_and_display_codes_can_be_chosen(self) -> None:
        pasted = set(_CURRENCIES.values())
        displayed = {code for _prefix, code in _DISPLAY_PREFIXES}
        self.assertTrue(pasted <= SUPPORTED_CURRENCY_CODES)
        self.assertTrue(displayed <= SUPPORTED_CURRENCY_CODES)
