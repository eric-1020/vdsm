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

from vdsm.storage import sanlock_direct
from vdsm.storage import constants as sc
from . import expose


@expose
def sanlock_direct_run_dump(
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    return sanlock_direct.run_dump(
        path=path,
        offset=offset,
        size=size,
        block_size=block_size,
        alignment=alignment)
