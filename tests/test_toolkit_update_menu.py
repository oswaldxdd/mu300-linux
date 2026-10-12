"""Keep the charge2 toolkit's update page aligned with the guarded updater."""
import unittest

from helpers import BIN


TOOLKIT = BIN / 'mu300-toolkit'


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


class ToolkitUpdateMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TOOLKIT.read_text()

    def test_update_menu_has_no_online_apply_path(self):
        menu = section(self.source, 'menu_update() {', 'menu_system() {')
        self.assertIn('mu300-update check', menu)
        self.assertIn('mu300-update version', menu)
        self.assertIn('online full-system, kernel and boot-image updates are disabled', menu)
        self.assertIn('matching offline full charge2 test package', menu)
        self.assertIn('mu300-update rollback', menu)
        self.assertIn('mu300-update clean', menu)
        self.assertNotRegex(menu, r'mu300-update\s+apply\b')
        self.assertNotIn('Update the installed systems', menu)
        self.assertNotIn('Updating takes the systems', menu)

    def test_vpn_module_menu_injection_is_not_present(self):
        for marker in ('MU300_VPN_CMD', 'VPN_ORPHAN_LIB', 'vpn_present()', 'vpn_orphaned()',
                       'menu_vpn_missing()', 'Engines (install / remove)'):
            self.assertNotIn(marker, self.source)
        network = section(self.source, 'menu_network() {', 'MU300_SERVICES=')
        services = section(self.source, 'menu_services() {', '# Software update from a published release')
        self.assertIn('"VPN"', network)
        self.assertNotIn('vpn_present', network)
        self.assertIn('for s in $MU300_SERVICES', services)
        self.assertNotIn('vpn_present', services)

    def test_release_and_dfs_information_remain(self):
        info = section(self.source, 'info() {', 'menu_wifi() {')
        wifi = section(self.source, 'menu_wifi() {', 'vpn_enabled() {')
        self.assertIn('/etc/mu300/image-version', info)
        self.assertIn('/mnt/mu300-disk/boot/installed.tag', info)
        self.assertIn('driver cannot detect radar', wifi)
        self.assertIn('(52-144) is replaced by the default one.', wifi)


if __name__ == '__main__':
    unittest.main()
