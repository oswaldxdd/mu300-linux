"""Country-aware 5 GHz channel selection for the SC2355, which cannot detect radar on DFS channels."""
from helpers import TOP, ShellTest

CHANNELS = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'wifi-channels.sh'


class WifiChannels(ShellTest):
    def call(self, shell, expr):
        return self.sh(shell, f'. "{CHANNELS}"\n{expr}')

    def test_country_channel_lists_keep_regulatory_differences(self):
        for shell in self.each_shell():
            r = self.call(shell, '''
test "$(wifi_ap5_channels tr)" = '36 40 44 48' || exit 1
test "$(wifi_ap5_channels bd)" = '149 153 157 161 165' || exit 2
test "$(wifi_ap5_channels id)" = '36 40 44 48 149 153 157 161' || exit 3
test -z "$(wifi_ap5_channels 00)" || exit 4
test -z "$(wifi_ap5_channels am)" || exit 5
''')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_auto_and_explicit_dfs_channels_choose_country_safe_fallbacks(self):
        for shell in self.each_shell():
            r = self.call(shell, '''
test "$(wifi_ap5_default TR)" = 36 || exit 1
test "$(wifi_ap5_default BD)" = 149 || exit 2
test -z "$(wifi_ap5_default 00)" || exit 3
test "$(wifi_ap5_resolve TR auto)" = 36 || exit 4
test "$(wifi_ap5_resolve TR 100)" = 36 || exit 5
test "$(wifi_ap5_resolve TR 157)" = 36 || exit 6
test -z "$(wifi_ap5_resolve 00 36)" || exit 7
''')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn('a DFS channel', r.stderr)
            self.assertIn('not available to the hotspot in country TR', r.stderr)

    def test_width_is_capped_by_the_allowed_block(self):
        for shell in self.each_shell():
            r = self.call(shell, '''
test "$(wifi_ap5_width TR 36)" = 80 || exit 1
test "$(wifi_ap5_width ID 149)" = 80 || exit 2
test "$(wifi_ap5_width ID 165)" = 20 || exit 3
''')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


if __name__ == '__main__':
    import unittest
    unittest.main()
