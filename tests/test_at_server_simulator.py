# This file is part of ts_attcpip.
#
# Developed for the Vera Rubin Observatory Telescope and Site Systems.
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

import asyncio
import contextlib
import typing

from lsst.ts import attcpip, tcpip


class AtServerSimulatorTestCase(tcpip.BaseOneClientServerTestCase):
    server_class = attcpip.AtServerSimulator

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.data_event = asyncio.Event()
        self.data: typing.Any | None = None

    @contextlib.asynccontextmanager
    async def create_at_server_simulator(
        self,
    ) -> typing.AsyncGenerator[attcpip.AtServerSimulator, None]:
        async with self.create_server(
            name="AtServerSimulator",
            host=tcpip.DEFAULT_LOCALHOST,
            dispatch_callback=self.dispatch_callback,
        ) as server:
            yield server

    async def dispatch_callback(self, data: typing.Any) -> None:
        """Dispatch callback method for the AtServerSimulator to use.

        Parameters
        ----------
        data : `any`
            The data read by the AtServerSimulator.
        """
        self.data = data
        self.data_event.set()

    async def test_read_and_dispatch(self) -> None:
        async with self.create_at_server_simulator() as server, self.create_client(
            server
        ) as client:
            self.data_event.clear()
            data = "Test."
            await client.write_json(data=data)
            await self.data_event.wait()
            assert self.data == data
