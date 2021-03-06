#
# Copyright 2020 Red Hat, Inc.
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

import pytest

from network.compat import mock

from vdsm.network import nmstate
from vdsm.network.nmstate.ovs import network

from .testlib import (
    IFACE0,
    IFACE1,
    OVS_BRIDGE,
    VLAN0,
    VLAN101,
    VLAN102,
    TESTNET1,
    TESTNET2,
    create_ethernet_iface_state,
    create_network_config,
    create_ovs_bridge_state,
    create_ovs_northbound_state,
    create_ovs_port_state,
    parametrize_bridged,
    parametrize_vlanned,
    sort_by_name,
)


@pytest.fixture(autouse=True)
def bridge_name_mock():
    with mock.patch.object(network, 'random_interface_name') as rnd:
        rnd.side_effect = OVS_BRIDGE
        yield


@parametrize_bridged
@pytest.mark.parametrize(
    'vlan', [VLAN0, VLAN101, None], ids=['vlan0', 'vlan101', 'non-vlan']
)
def test_add_single_net_without_ip(bridged, vlan):
    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged, switch='ovs', vlan=vlan
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)

    bridge_ports = [
        create_ovs_port_state(IFACE0),
        create_ovs_port_state(TESTNET1, vlan=vlan),
    ]
    sort_by_name(bridge_ports)
    bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
    nb_state = create_ovs_northbound_state(TESTNET1)

    expected_state = {
        nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
    }
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
@parametrize_vlanned
def test_add_nets_without_ip(bridged, vlanned):
    vlan1 = VLAN101 if vlanned else None
    vlan2 = VLAN102 if vlanned else None
    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged, switch='ovs', vlan=vlan1
        ),
        TESTNET2: create_network_config(
            'nic', IFACE1, bridged, switch='ovs', vlan=vlan2
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
    eth1_state = create_ethernet_iface_state(IFACE1, mtu=None)

    bridge1_ports = [
        create_ovs_port_state(IFACE0),
        create_ovs_port_state(TESTNET1, vlan=vlan1),
    ]
    sort_by_name(bridge1_ports)
    bridge2_ports = [
        create_ovs_port_state(IFACE1),
        create_ovs_port_state(TESTNET2, vlan=vlan2),
    ]
    sort_by_name(bridge2_ports)
    bridge1_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge1_ports)
    bridge2_state = create_ovs_bridge_state(OVS_BRIDGE[1], bridge2_ports)
    nb1_state = create_ovs_northbound_state(TESTNET1)
    nb2_state = create_ovs_northbound_state(TESTNET2)

    expected_state = {
        nmstate.Interface.KEY: [
            eth0_state,
            bridge1_state,
            nb1_state,
            eth1_state,
            bridge2_state,
            nb2_state,
        ]
    }
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
@parametrize_vlanned
def test_add_nets_over_single_sb_without_ip(bridged, vlanned):
    vlan1 = VLAN101 if vlanned else None
    vlan2 = VLAN102 if vlanned else None
    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged, switch='ovs', vlan=vlan1
        ),
        TESTNET2: create_network_config(
            'nic', IFACE0, bridged, switch='ovs', vlan=vlan2
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)

    bridge_ports = [
        create_ovs_port_state(IFACE0),
        create_ovs_port_state(TESTNET1, vlan=vlan1),
        create_ovs_port_state(TESTNET2, vlan=vlan2),
    ]
    sort_by_name(bridge_ports)
    bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
    nb1_state = create_ovs_northbound_state(TESTNET1)
    nb2_state = create_ovs_northbound_state(TESTNET2)

    expected_state = {
        nmstate.Interface.KEY: [eth0_state, bridge_state, nb1_state, nb2_state]
    }
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
@parametrize_vlanned
def test_add_net_over_existing_bridge_without_ip(
    bridged, vlanned, rconfig_mock, current_state_mock
):
    vlan = VLAN101 if vlanned else None
    rconfig_mock.networks = {
        TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
    }
    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states.append(
        {
            'name': OVS_BRIDGE[5],
            nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
            'state': 'up',
            nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                    {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                    {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                ]
            },
        }
    )
    networks = {
        TESTNET2: create_network_config(
            'nic', IFACE0, bridged, switch='ovs', vlan=vlan
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    bridge_ports = [
        create_ovs_port_state(IFACE0),
        create_ovs_port_state(TESTNET1),
        create_ovs_port_state(TESTNET2, vlan=vlan),
    ]
    sort_by_name(bridge_ports)
    bridge_state = create_ovs_bridge_state(OVS_BRIDGE[5], bridge_ports)
    nb_state = create_ovs_northbound_state(TESTNET2)

    expected_state = {nmstate.Interface.KEY: [bridge_state, nb_state]}
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
@parametrize_vlanned
def test_move_net_to_different_sb_without_ip(
    bridged, vlanned, rconfig_mock, current_state_mock
):
    vlan = VLAN102 if vlanned else None
    rconfig_mock.networks = {
        TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
    }
    if vlanned:
        rconfig_mock.networks[TESTNET1]['vlan'] = VLAN101

    nb_port = {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1}
    if vlanned:
        access = nmstate.OvsBridgeSchema.Port.Vlan.Mode.ACCESS
        nb_port[nmstate.OvsBridgeSchema.Port.VLAN_SUBTREE] = {
            nmstate.OvsBridgeSchema.Port.Vlan.MODE: access,
            nmstate.OvsBridgeSchema.Port.Vlan.TAG: VLAN101,
        }

    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states.append(
        {
            'name': OVS_BRIDGE[5],
            nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
            'state': 'up',
            nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                    nb_port,
                    {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                ]
            },
        }
    )
    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE1, bridged, switch='ovs', vlan=vlan
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
    eth1_state = create_ethernet_iface_state(IFACE1, mtu=None)
    bridge_ports = [
        create_ovs_port_state(IFACE1),
        create_ovs_port_state(TESTNET1, vlan=vlan),
    ]
    sort_by_name(bridge_ports)
    new_bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
    old_bridge_state = create_ovs_bridge_state(OVS_BRIDGE[5], None, 'absent')
    nb_state = create_ovs_northbound_state(TESTNET1)

    expected_state = {
        nmstate.Interface.KEY: [
            eth0_state,
            eth1_state,
            new_bridge_state,
            old_bridge_state,
            nb_state,
        ]
    }
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_remove_last_net_without_ip(bridged, rconfig_mock, current_state_mock):
    rconfig_mock.networks = {
        TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
    }
    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states.append(
        {
            'name': OVS_BRIDGE[5],
            nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
            'state': 'up',
            nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                    {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                    {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                ]
            },
        }
    )
    networks = {TESTNET1: {'remove': True}}
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
    bridge_state = create_ovs_bridge_state(OVS_BRIDGE[5], None, 'absent')
    nb_state = {'name': TESTNET1, 'state': 'absent'}

    expected_state = {
        nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
    }
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_remove_net_without_ip(bridged, rconfig_mock, current_state_mock):
    rconfig_mock.networks = {
        TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'},
        TESTNET2: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'},
    }
    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states.append(
        {
            'name': OVS_BRIDGE[5],
            nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
            'state': 'up',
            nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                    {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                    {nmstate.OvsBridgeSchema.Port.NAME: TESTNET2},
                    {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                ]
            },
        }
    )
    networks = {TESTNET2: {'remove': True}}
    state = nmstate.generate_state(networks=networks, bondings={})

    nb_state = {'name': TESTNET2, 'state': 'absent'}

    expected_state = {nmstate.Interface.KEY: [nb_state]}
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state
