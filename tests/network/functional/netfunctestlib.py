#
# Copyright 2016-2017 Red Hat, Inc.
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

from __future__ import absolute_import

from contextlib import contextmanager
from copy import deepcopy

import six

import pytest

from vdsm.common import fileutils
from vdsm.network import kernelconfig
from vdsm.network.canonicalize import bridge_opts_dict_to_sorted_str
from vdsm.network.canonicalize import bridge_opts_str_to_dict
from vdsm.network.ip import dhclient
from vdsm.network.ip.address import ipv6_supported, prefix2netmask
from vdsm.network.link.iface import iface
from vdsm.network.link.bond import sysfs_options as bond_options
from vdsm.network.link.bond import sysfs_options_mapper as bond_opts_mapper
from vdsm.network.netinfo import bridges
from vdsm.network.netlink import monitor
from vdsm.network.netlink import waitfor

from functional.utils import getProxy, SUCCESS

try:
    import ipaddress
except ImportError:
    ipaddress = None

NOCHK = {'connectivityCheck': False}

IFCFG_DIR = '/etc/sysconfig/network-scripts/'
IFCFG_PREFIX = IFCFG_DIR + 'ifcfg-'


parametrize_switch = pytest.mark.parametrize(
    'switch', [pytest.mark.legacy_switch('legacy'),
               pytest.mark.ovs_switch('ovs')])

parametrize_bridged = pytest.mark.parametrize('bridged', [False, True],
                                              ids=['bridgeless', 'bridged'])

parametrize_bonded = pytest.mark.parametrize('bonded', [False, True],
                                             ids=['unbonded', 'bonded'])


def requires_ipaddress():
    """
    ipaddress package is a part of the Python std from PY3.3, on PY2 we need
    the backported implementation installed.
    """
    if ipaddress is None:
        pytest.skip('ipaddress package is not installed')


class NetFuncTestCase(object):

    def setup_method(self, m):
        self.vdsm_proxy = getProxy()

    def update_netinfo(self):
        self.netinfo = self.vdsm_proxy.netinfo

    def update_running_config(self):
        self.running_config = self.vdsm_proxy.config

    @property
    def setupNetworks(self):
        return SetupNetworks(self.vdsm_proxy, self._setup_networks_post_hook())

    def _setup_networks_post_hook(self):
        def assert_kernel_vs_running():
            # Refresh caps and running config data
            self.update_netinfo()
            self.update_running_config()
            self.assert_kernel_vs_running_config()
        return assert_kernel_vs_running

    def assertNetwork(self, netname, netattrs):
        """
        Aggregates multiple network checks to ease usage.
        The checks are between the requested setup (input) and current reported
        state (caps).
        """
        self.assertNetworkExists(netname)

        bridged = netattrs.get('bridged', True)
        if bridged:
            self.assertNetworkBridged(netname)
        else:
            self.assertNetworkBridgeless(netname)

        self.assertHostQos(netname, netattrs)

        self.assertSouthboundIface(netname, netattrs)
        self.assertVlan(netattrs)
        self.assertNetworkIp(netname, netattrs)
        self.assertLinksUp(netname, netattrs)
        self.assertNetworkSwitchType(netname, netattrs)
        self.assertNetworkMtu(netname, netattrs)

    def assertHostQos(self, netname, netattrs):
        network_caps = self.netinfo.networks[netname]
        if 'hostQos' in netattrs:
            qos_caps = _normalize_qos_config(network_caps['hostQos'])
            assert netattrs['hostQos'] == qos_caps

    def assertNetworkExists(self, netname):
        assert netname in self.netinfo.networks

    def assertNetworkBridged(self, netname):
        network_caps = self.netinfo.networks[netname]
        assert network_caps['bridged']
        assert netname in self.netinfo.bridges

    def assertNetworkBridgeless(self, netname):
        network_caps = self.netinfo.networks[netname]
        assert not network_caps['bridged']
        assert netname not in self.netinfo.bridges

    def assertSouthboundIface(self, netname, netattrs):
        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')
        vlan = netattrs.get('vlan')
        bridged = netattrs.get('bridged', True)

        if bridged:
            iface = netname
        elif vlan is not None:
            iface = '{}.{}'.format(nic or bond, vlan)
        else:
            iface = nic or bond

        network_caps = self.netinfo.networks[netname]
        assert iface == network_caps['iface']

    def assertVlan(self, netattrs):
        vlan = netattrs.get('vlan')
        if vlan is None:
            return

        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')
        iface = '{}.{}'.format(nic or bond, vlan)

        assert iface in self.netinfo.vlans
        vlan_caps = self.netinfo.vlans[iface]
        assert isinstance(vlan_caps['vlanid'], int)
        assert int(vlan) == vlan_caps['vlanid']

    def assertBridgeOpts(self, netname, netattrs):
        bridge_caps = self.netinfo.bridges[netname]

        stp_request = 'on' if netattrs.get('stp', False) else 'off'
        assert bridge_caps['stp'] == stp_request

        self._assertCustomBridgeOpts(netattrs, bridge_caps)

    def _assertCustomBridgeOpts(self, netattrs, bridge_caps):
        custom_attrs = netattrs.get('custom', {})
        if 'bridge_opts' in custom_attrs:
            req_bridge_opts = dict([opt.split('=', 1) for opt in
                                    custom_attrs['bridge_opts'].split(' ')])
            bridge_opts_caps = bridge_caps['opts']
            for br_opt, br_val in six.iteritems(req_bridge_opts):
                assert br_val == bridge_opts_caps[br_opt]

    # FIXME: Redundant because we have NetworkExists + kernel_vs_running_config
    def assertNetworkExistsInRunning(self, netname, netattrs):
        self.update_running_config()
        netsconf = self.running_config.networks

        assert netname in netsconf
        netconf = netsconf[netname]

        bridged = netattrs.get('bridged')
        assert bridged == netconf.get('bridged')

    def assertNoNetwork(self, netname):
        self.assertNoNetworkExists(netname)
        self.assertNoBridgeExists(netname)
        self.assertNoNetworkExistsInRunning(netname)

    def assertNoNetworkExists(self, net):
        assert net not in self.netinfo.networks

    def assertNoBridgeExists(self, bridge):
        assert bridge not in self.netinfo.bridges

    def assertNoVlan(self, southbound_port, tag):
        vlan_name = '{}.{}'.format(southbound_port, tag)
        assert vlan_name not in self.netinfo.vlans

    def assertNoNetworkExistsInRunning(self, net):
        self.update_running_config()
        assert net not in self.running_config.networks

    def assertNetworkSwitchType(self, netname, netattrs):
        requested_switch = netattrs.get('switch', 'legacy')
        running_switch = self.netinfo.networks[netname]['switch']
        assert requested_switch == running_switch

    def assertNetworkMtu(self, netname, netattrs):
        requested_mtu = netattrs.get('mtu', 1500)
        netinfo = _normalize_caps(self.netinfo)
        running_mtu = netinfo.networks[netname]['mtu']
        assert requested_mtu == running_mtu

    def assertLinkMtu(self, devname, netattrs):
        requested_mtu = netattrs.get('mtu', 1500)
        netinfo = _normalize_caps(self.netinfo)
        if devname in netinfo.nics:
            running_mtu = netinfo.nics[devname]['mtu']
        elif devname in netinfo.bondings:
            running_mtu = netinfo.bondings[devname]['mtu']
        elif devname in netinfo.vlans:
            running_mtu = netinfo.vlans[devname]['mtu']
        elif devname in netinfo.bridges:
            running_mtu = netinfo.bridges[devname]['mtu']
        elif devname in netinfo.networks:
            running_mtu = netinfo.networks[devname]['mtu']
        else:
            raise DeviceNotInCapsError(devname)
        assert requested_mtu == int(running_mtu)

    def assertBond(self, bond, attrs):
        self.assertBondExists(bond)
        self.assertBondSlaves(bond, attrs['nics'])
        if 'options' in attrs:
            self.assertBondOptions(bond, attrs['options'])
        self.assertBondExistsInRunninng(bond, attrs['nics'])
        self.assertBondSwitchType(bond, attrs)
        self.assertBondHwAddress(bond, attrs)

    def assertBondExists(self, bond):
        assert bond in self.netinfo.bondings

    def assertBondSlaves(self, bond, nics):
        assert set(nics) == set(self.netinfo.bondings[bond]['slaves'])

    def assertBondOptions(self, bond, options):
        requested_opts = _split_bond_options(options)
        running_opts = self.netinfo.bondings[bond]['opts']
        normalized_active_opts = _normalize_bond_opts(running_opts)
        assert set(requested_opts) <= set(normalized_active_opts)

    def assertBondExistsInRunninng(self, bond, nics):
        assert bond in self.running_config.bonds
        assert set(nics) == set(self.running_config.bonds[bond]['nics'])

    def assertBondSwitchType(self, bondname, bondattrs):
        requested_switch = bondattrs.get('switch', 'legacy')
        running_switch = self.netinfo.bondings[bondname]['switch']
        assert requested_switch == running_switch

    def assertBondHwAddress(self, bondname, bondattrs):
        requested_hwaddress = bondattrs.get('hwaddr')
        if requested_hwaddress:
            actual_hwaddress = self.netinfo.bondings[bondname]['hwaddr']
            assert requested_hwaddress == actual_hwaddress

    def assertNoBond(self, bond):
        self.assertNoBondExists(bond)
        self.assertNoBondExistsInRunning(bond)

    def assertNoBondExists(self, bond):
        assert bond not in self.netinfo.bondings

    def assertNoBondExistsInRunning(self, bond):
        self.update_running_config()
        assert bond not in self.running_config.bonds

    def assertNetworkIp(self, net, attrs):
        if _ipv4_is_unused(attrs) and _ipv6_is_unused(attrs):
            return

        network_netinfo = self.netinfo.networks[net]

        bridged = attrs.get('bridged', True)
        vlan = attrs.get('vlan')
        bond = attrs.get('bonding')
        nic = attrs.get('nic')
        if bridged:
            topdev_netinfo = self.netinfo.bridges[net]
        elif vlan is not None:
            vlan_name = '{}.{}'.format(bond or nic, attrs['vlan'])
            topdev_netinfo = self.netinfo.vlans[vlan_name]
        elif bond:
            topdev_netinfo = self.netinfo.bondings[bond]
        else:
            topdev_netinfo = self.netinfo.nics[nic]

        if 'ipaddr' in attrs:
            self.assertStaticIPv4(attrs, network_netinfo)
            self.assertStaticIPv4(attrs, topdev_netinfo)
        if attrs.get('bootproto') == 'dhcp':
            self.assertDHCPv4(network_netinfo)
            self.assertDHCPv4(topdev_netinfo)
        if _ipv4_is_unused(attrs):
            self.assertDisabledIPv4(network_netinfo)
            self.assertDisabledIPv4(topdev_netinfo)

        if 'ipv6addr' in attrs:
            self.assertStaticIPv6(attrs, network_netinfo)
            self.assertStaticIPv6(attrs, topdev_netinfo)
        elif attrs.get('dhcpv6'):
            self.assertDHCPv6(network_netinfo)
            self.assertDHCPv6(topdev_netinfo)
        elif attrs.get('ipv6autoconf'):
            self.assertIPv6Autoconf(network_netinfo)
            self.assertIPv6Autoconf(topdev_netinfo)
        elif _ipv6_is_unused(attrs):
            self.assertDisabledIPv6(network_netinfo)
            self.assertDisabledIPv6(topdev_netinfo)

        self.assertRoutesIPv4(attrs, network_netinfo)
        self.assertRoutesIPv4(attrs, topdev_netinfo)

        self.assertRoutesIPv6(attrs, network_netinfo)
        self.assertRoutesIPv6(attrs, topdev_netinfo)

    def assertStaticIPv4(self, netattrs, ipinfo):
        requires_ipaddress()
        address = netattrs['ipaddr']
        netmask = (netattrs.get('netmask') or
                   prefix2netmask(int(netattrs.get('prefix'))))
        assert address == ipinfo['addr']
        assert netmask == ipinfo['netmask']
        ipv4 = ipaddress.IPv4Interface(
            u'{}/{}'.format(address, netmask))
        assert str(ipv4.with_prefixlen) in ipinfo['ipv4addrs']

    def assertStaticIPv6(self, netattrs, ipinfo):
        assert netattrs['ipv6addr'] in ipinfo['ipv6addrs']

    def assertDHCPv4(self, ipinfo):
        assert ipinfo['dhcpv4']
        assert ipinfo['addr'] != ''
        assert len(ipinfo['ipv4addrs']) > 0

    def assertDHCPv6(self, ipinfo):
        assert ipinfo['dhcpv6']
        assert len(ipinfo['ipv6addrs']) > 0

    def assertIPv6Autoconf(self, ipinfo):
        assert ipinfo['ipv6autoconf']
        assert len(ipinfo['ipv6addrs']) > 0

    def assertDisabledIPv4(self, ipinfo):
        assert not ipinfo['dhcpv4']
        assert ipinfo['addr'] == ''
        assert ipinfo['ipv4addrs'] == []

    def assertDisabledIPv6(self, ipinfo):
        # TODO: We need to report if IPv6 is enabled on iface/host and
        # differentiate that from not acquiring an address.
        assert [] == ipinfo['ipv6addrs']

    def assertDhclient(self, iface, family):
        return dhclient.is_active(iface, family)

    def assertNoDhclient(self, iface, family):
        assert not self.assertDhclient(iface, family)

    def assertRoutesIPv4(self, netattrs, ipinfo):
        # TODO: Support sourceroute on OVS switches
        if netattrs.get('switch', 'legacy') == 'legacy':
            is_dynamic = netattrs.get('bootproto') == 'dhcp'
            if is_dynamic:
                # When dynamic is used, route is assumed to be included.
                assert ipinfo['gateway']
            else:
                gateway = netattrs.get('gateway', '')
                assert gateway == ipinfo['gateway']

        self.assertDefaultRouteIPv4(netattrs, ipinfo)

    def assertRoutesIPv6(self, netattrs, ipinfo):
        # TODO: Support sourceroute for IPv6 networks
        if netattrs.get('defaultRoute', False):
            is_dynamic = netattrs.get('ipv6autoconf') or netattrs.get('dhcpv6')
            if is_dynamic:
                # When dynamic is used, route is assumed to be included.
                assert ipinfo['ipv6gateway']
            else:
                gateway = netattrs.get('ipv6gateway', '::')
                assert gateway == ipinfo['ipv6gateway']

    def assertDefaultRouteIPv4(self, netattrs, ipinfo):
        # When DHCP is used, route is assumed to be included in the response.
        is_gateway_requested = (bool(netattrs.get('gateway')) or
                                netattrs.get('bootproto') == 'dhcp')
        is_default_route_requested = (netattrs.get('defaultRoute', False) and
                                      is_gateway_requested)
        assert is_default_route_requested == ipinfo['ipv4defaultroute']

    def assertLinksUp(self, net, attrs):
        switch = attrs.get('switch', 'legacy')
        if switch == 'legacy':
            expected_links = _gather_expected_legacy_links(
                net, attrs, self.netinfo)
        elif switch == 'ovs':
            expected_links = _gather_expected_ovs_links(
                net, attrs, self.netinfo)
        if expected_links:
            for dev in expected_links:
                assert iface(dev).is_oper_up(), 'Dev {} is DOWN'.format(dev)

    def assertNameservers(self, nameservers):
        assert nameservers == self.netinfo.nameservers[:len(nameservers)]

    def assert_kernel_vs_running_config(self):
        """
        This is a special test, that checks setup integrity through
        non vdsm api data.
        The networking configuration relies on a semi-persistent running
        configuration files, describing the requested configuration.
        This configuration is checked against the actual caps report.
        """

        running_config = kernelconfig.normalize(self.running_config)
        running_config = running_config.as_unicode()

        netinfo = _normalize_caps(self.netinfo)
        kernel_config = kernelconfig.KernelConfig(netinfo)
        _extend_with_bridge_opts(kernel_config, running_config)
        kernel_config = kernel_config.as_unicode()

        # Do not use KernelConfig.__eq__ to get a better exception if something
        # breaks.
        assert running_config['networks'] == kernel_config['networks']
        assert running_config['bonds'] == kernel_config['bonds']

    @contextmanager
    def reset_persistent_config(self):
        try:
            yield
        finally:
            self.vdsm_proxy.setSafeNetworkConfig()


def _extend_with_bridge_opts(kernel_config, running_config):
    for net, attrs in six.viewitems(running_config['networks']):
        if not attrs['bridged']:
            continue
        if net not in kernel_config.networks:
            continue
        running_opts_str = attrs.get('custom', {}).get('bridge_opts')
        if not running_opts_str:
            continue
        running_opts_dict = bridge_opts_str_to_dict(running_opts_str)
        kernel_opts_dict = {
            key: val for key, val in six.viewitems(bridges.bridge_options(net))
            if key in running_opts_dict}
        kernel_opts_str = bridge_opts_dict_to_sorted_str(kernel_opts_dict)
        kernel_config.networks[net].setdefault(
            'custom', {})['bridge_opts'] = kernel_opts_str


def _ipv4_is_unused(attrs):
    return 'ipaddr' not in attrs and attrs.get('bootproto') != 'dhcp'


def _ipv6_is_unused(attrs):
    return ('ipv6addr' not in attrs and 'ipv6autoconf' not in attrs and
            'dhcpv6' not in attrs and ipv6_supported())


class SetupNetworksError(Exception):
    def __init__(self, status, msg):
        super(SetupNetworksError, self).__init__(msg)
        self.status = status
        self.msg = msg


class SetupNetworks(object):

    def __init__(self, vdsm_proxy, post_setup_hook):
        self.vdsm_proxy = vdsm_proxy
        self.post_setup_hook = post_setup_hook

    def __call__(self, networks, bonds, options):
        self.setup_networks = networks
        self.setup_bonds = bonds

        status, msg = self.vdsm_proxy.setupNetworks(networks, bonds, options)
        if status != SUCCESS:
            raise SetupNetworksError(status, msg)

        try:
            self.post_setup_hook()
        except:
            # Ignore cleanup failure, make sure to re-raise original exception.
            self._cleanup()
            raise

        return self

    def __enter__(self):
        pass

    def __exit__(self, type, value, traceback):
        status, msg = self._cleanup()
        if type is None and status != SUCCESS:
            raise SetupNetworksError(status, msg)

    def _cleanup(self):
        networks_caps = self.vdsm_proxy.netinfo.networks
        bonds_caps = self.vdsm_proxy.netinfo.bondings
        NETSETUP = {net: {'remove': True}
                    for net in self.setup_networks if net in networks_caps}
        BONDSETUP = {bond: {'remove': True}
                     for bond in self.setup_bonds if bond in bonds_caps}
        status, msg = self.vdsm_proxy.setupNetworks(NETSETUP, BONDSETUP, NOCHK)

        nics_used = [attr['nic']
                     for attr in six.itervalues(self.setup_networks)
                     if 'nic' in attr]
        for attr in six.itervalues(self.setup_bonds):
            nics_used += attr.get('nics', [])
        for nic in nics_used:
            fileutils.rm_file(IFCFG_PREFIX + nic)

        return status, msg


@contextmanager
def monitor_stable_link_state(device, wait_for_linkup=True):
    """Raises an exception if it detects that the device link state changes."""
    if wait_for_linkup:
        with waitfor.waitfor_linkup(device):
            pass
    iface_properties = iface(device).properties()
    original_state = iface_properties['state']
    try:
        with monitor.Monitor(groups=('link',)) as mon:
            yield
    finally:
        state_changes = (e['state'] for e in mon if e['name'] == device)
        for state in state_changes:
            if state != original_state:
                raise pytest.fail(
                    '{} link state changed: {} -> {}'.format(
                        device, original_state, state))


def _normalize_caps(netinfo_from_caps):
    """
    Normalize network caps to allow kernel vs running config comparison.

    The netinfo object used by the tests is created from the network caps data.
    To allow the kernel vs running comparison, it is required to revert the
    caps data compatibility conversions (required by the oVirt Engine).
    """
    netinfo = deepcopy(netinfo_from_caps)
    # TODO: When production code drops compatibility normalization, remove it.
    for dev in six.itervalues(netinfo.networks):
        dev['mtu'] = int(dev['mtu'])

    return netinfo


def _normalize_qos_config(qos):
    for value in six.viewvalues(qos):
        for attrs in six.viewvalues(value):
            if attrs.get('m1') == 0:
                del attrs['m1']
            if attrs.get('d') == 0:
                del attrs['d']
    return qos


def _normalize_bond_opts(opts):
    return [opt + '=' + val for (opt, val) in six.iteritems(opts)]


def _split_bond_options(opts):
    return _numerize_bond_options(opts) if opts else opts


def _numerize_bond_options(opts):
    optmap = dict((pair.split('=', 1) for pair in opts.split()))

    mode = optmap.get('mode')
    if not mode:
        return opts

    optmap['mode'] = numeric_mode = bond_options.numerize_bond_mode(mode)
    for opname, opval in six.viewitems(optmap):
        numeric_val = bond_opts_mapper.get_bonding_option_numeric_val(
            numeric_mode, opname, opval)
        if numeric_val is not None:
            optmap[opname] = numeric_val

    return _normalize_bond_opts(optmap)


def _gather_expected_legacy_links(net, attrs, netinfo):
    bridged = attrs.get('bridged', True)
    vlan = attrs.get('vlan')
    bond = attrs.get('bonding')
    nic = attrs.get('nic')

    devs = set()
    if bridged:
        devs.add(net)
    if vlan is not None:
        vlan_name = '{}.{}'.format(bond or nic, vlan)
        devs.add(vlan_name)
    if bond:
        devs.add(bond)
        slaves = netinfo.bondings[bond]['slaves']
        devs.update(slaves)
    elif nic:
        devs.add(nic)

    return devs


def _gather_expected_ovs_links(net, attrs, netinfo):
    bond = attrs.get('bonding')
    nic = attrs.get('nic')

    devs = {net}
    if bond:
        devs.add(bond)
        slaves = netinfo.bondings[bond]['slaves']
        devs.update(slaves)
    elif nic:
        devs.add(nic)

    return devs


class DeviceNotInCapsError(Exception):
    pass
