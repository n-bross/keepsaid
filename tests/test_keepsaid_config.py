import os
import unittest
from unittest.mock import patch

from keepsaid_config import env


class KeepsaidConfigTests(unittest.TestCase):
    def test_new_name_takes_priority_over_legacy_name(self):
        with patch.dict(os.environ, {'KEEPSAID_SHARE_URL': 'new', 'SOUNDCORE_SHARE_URL': 'old'}, clear=True):
            self.assertEqual(env('SHARE_URL', 'default'), 'new')

    def test_existing_installations_keep_legacy_configuration(self):
        with patch.dict(os.environ, {'SOUNDCORE_SHARE_URL': 'old'}, clear=True):
            self.assertEqual(env('SHARE_URL', 'default'), 'old')

    def test_missing_configuration_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env('SHARE_URL', 'default'), 'default')
