#
# Copyright 2016-2020 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import glob

import pytest

from dbus.exceptions import DBusException

from network.nettestlib import dummy_device
from network.nmnettestlib import iface_name, NMService, nm_connections

from vdsm.network.nm import networkmanager

from .netintegtestlib import requires_systemctl


IPV4ADDR = '10.1.1.1/29'


@pytest.fixture(scope='module', autouse=True)
def setup():
    requires_systemctl()
    nm_service = NMService()
    nm_service.setup()
    try:
        networkmanager.init()
    except DBusException as ex:
        if 'Failed to connect to socket' not in ex.args[0]:
            pytest.skip('dbus socket or the NM service may not be available')
    yield
    nm_service.teardown()


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


class TestNMService(object):
    def test_network_manager_service_is_running(self):
        assert networkmanager.is_running()


class TestNMConnectionCleanup(object):
    def test_remove_all_non_active_connection_from_a_device(self, nic0):
        iface = iface_name()
        with nm_connections(iface, IPV4ADDR, slaves=(nic0,), con_count=3):
            device = networkmanager.Device(iface)
            device.cleanup_inactive_connections()

            assert sum(1 for _ in device.connections()) == 1


class TestNMIfcfg2Connection(object):

    NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
    NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'

    def test_detect_connection_based_on_ifcfg_file(self, nic0):
        """
        NM may use ifcfg files as its storage format for connections via the
        ifcfg-rh settings plugin.
        This is the default option under RHEL/Centos/Fedora.
        When a connection is defined, it is saved in an ifcfg file, however,
        the filename is not recorded in NM records.
        In some scenarios, it is useful look for the ifcfg filename based on
        a given connection or the other way around, looking for the connection
        given the filename.
        """
        iface = iface_name()
        with nm_connections(
            iface, IPV4ADDR, slaves=(nic0,), con_count=3, save=True
        ):
            device = networkmanager.Device(iface)
            expected_uuids = {
                con.connection.uuid for con in device.connections()
            }

            actual_uuids = {
                networkmanager.ifcfg2connection(file)[0]
                for file in self._ifcfg_files()
            }

            assert actual_uuids >= expected_uuids

    @staticmethod
    def _ifcfg_files():
        paths = glob.iglob(TestNMIfcfg2Connection.NET_CONF_PREF + '*')
        for ifcfg_file_name in paths:
            yield ifcfg_file_name
