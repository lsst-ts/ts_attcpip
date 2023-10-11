# This file is part of ts_attcpip.
#
# Developed for the Vera C. Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import unittest

from lsst.ts import attcpip


class RegistryTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_registry(self) -> None:
        # Make sure that the expected commands are in the registry.
        for cmd in ["disable", "enable", "standby", "start"]:
            attcpip.registry.pop(f"command_{cmd}")

        # Make sure that no other commands are in the registry.
        assert len(attcpip.registry) == 0
