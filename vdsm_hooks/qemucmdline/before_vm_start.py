#!/usr/bin/python2
#
# Copyright 2012 IBM Corporation
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import sys
import hooking
import traceback
import json

'''
qemu_cmdline usage
===================

libvirt provides support for passing QEMU cmdline options.
With the help of this, one can inject QEMU options bypassing
the libvirt's domain XML code. This can help immensely in
testing and developing new qemu features, which are not yet in
libvirt and/or injecting one-time or some-time kind of stuff :)

The os environ 'qemu_cmdline' points to a json list, containing
the qemu option followed by value as individual list entries.
For qemu option that does not take any value, skip the value entry.

Note that validation of the option and value list entries is NOT done,
its passed as-is to qemu and in the same order as present in the list

For eg: qemu_cmdline='["-cdrom","<path/to/iso>", ...]'
'''


def addQemuNs(domXML):
    domain = domXML.getElementsByTagName('domain')[0]
    domain.setAttribute('xmlns:qemu',
                        'http://libvirt.org/schemas/domain/qemu/1.0')


def injectQemuCmdLine(domXML, qc):
    domain = domXML.getElementsByTagName('domain')[0]
    qctag = domXML.createElement('qemu:commandline')

    for cmd in qc:
        qatag = domXML.createElement('qemu:arg')
        qatag.setAttribute('value', cmd)

        qctag.appendChild(qatag)

    domain.appendChild(qctag)


if 'qemu_cmdline' in os.environ:
    try:
        domxml = hooking.read_domxml()

        qemu_cmdline = json.loads(os.environ['qemu_cmdline'])
        addQemuNs(domxml)
        injectQemuCmdLine(domxml, qemu_cmdline)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('qemu_cmdline: [unexpected error]: %s\n'
                         % traceback.format_exc())
        sys.exit(2)
