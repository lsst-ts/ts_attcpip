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

import asyncio
import contextlib
import logging
import typing
import unittest
from unittest import mock

from lsst.ts import attcpip, tcpip
from lsst.ts.xml import sal_enums

# Standard timeout in seconds.
TIMEOUT = 2


class SimulatorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.log = logging.getLogger(type(self).__name__)
        self.sequence_id = 0

    @contextlib.asynccontextmanager
    async def create_at_simulator(
        self, go_to_fault_state: bool
    ) -> typing.AsyncGenerator[None, None]:
        with mock.patch.object(attcpip.AtSimulator, "cmd_evt_connect_callback"):
            async with attcpip.AtSimulator(
                host=tcpip.LOCALHOST_IPV4, cmd_evt_port=5000, telemetry_port=6000
            ) as self.simulator:
                self.simulator.go_to_fault_state = go_to_fault_state
                await self.simulator.cmd_evt_server.start_task
                await self.simulator.telemetry_server.start_task
                yield

    @contextlib.asynccontextmanager
    async def create_cmd_evt_client(
        self, simulator: attcpip.AtSimulator
    ) -> typing.AsyncGenerator[None, None]:
        async with tcpip.Client(
            host=simulator.cmd_evt_server.host,
            port=simulator.cmd_evt_server.port,
            log=self.log,
            name="CmdEvtClient",
        ) as self.cmd_evt_client:
            await asyncio.wait_for(
                simulator.cmd_evt_server.connected_task, timeout=TIMEOUT
            )
            assert simulator.cmd_evt_server.connected
            assert self.cmd_evt_client.connected
            yield

    async def verify_command_response(self, ack: attcpip.Ack, sequence_id: int) -> None:
        data = await self.cmd_evt_client.read_json()
        assert attcpip.CommonCommandArgument.ID in data
        assert attcpip.CommonCommandArgument.SEQUENCE_ID in data
        assert data[attcpip.CommonCommandArgument.ID] == ack
        assert data[attcpip.CommonCommandArgument.SEQUENCE_ID] == sequence_id

    async def verify_event(self, state: sal_enums.State) -> None:
        data = await self.cmd_evt_client.read_json()
        assert attcpip.CommonCommandArgument.ID in data
        assert (
            data[attcpip.CommonCommandArgument.ID] == attcpip.CommonEvent.SUMMARY_STATE
        )
        assert attcpip.CommonEventArgument.SUMMARY_STATE in data
        assert data[attcpip.CommonEventArgument.SUMMARY_STATE] == state

        if state == sal_enums.State.FAULT:
            data = await self.cmd_evt_client.read_json()
            assert attcpip.CommonCommandArgument.ID in data
            assert (
                data[attcpip.CommonCommandArgument.ID] == attcpip.CommonEvent.ERROR_CODE
            )
            assert attcpip.CommonEventArgument.ERROR_CODE in data
            assert attcpip.CommonEventArgument.ERROR_REPORT in data
            assert attcpip.CommonEventArgument.TRACEBACK in data

    async def execute_command(
        self, command: attcpip.CommonCommand, expected_state: sal_enums.State
    ) -> None:
        self.sequence_id += 1
        await self.cmd_evt_client.write_json(
            data={
                attcpip.CommonCommandArgument.ID: command.value,
                attcpip.CommonCommandArgument.SEQUENCE_ID: self.sequence_id,
            }
        )
        await self.verify_command_response(
            ack=attcpip.Ack.ACK, sequence_id=self.sequence_id
        )
        await self.verify_command_response(
            ack=attcpip.Ack.SUCCESS, sequence_id=self.sequence_id
        )
        await self.verify_event(state=expected_state)
        assert self.simulator.simulator_state == expected_state

    async def test_stimulator_state_commands(self) -> None:
        async with self.create_at_simulator(
            go_to_fault_state=False
        ), self.create_cmd_evt_client(self.simulator):
            assert self.simulator.simulator_state == sal_enums.State.STANDBY

            commands_and_expected_states = {
                attcpip.CommonCommand.START: sal_enums.State.DISABLED,
                attcpip.CommonCommand.ENABLE: sal_enums.State.ENABLED,
                attcpip.CommonCommand.DISABLE: sal_enums.State.DISABLED,
                attcpip.CommonCommand.STANDBY: sal_enums.State.STANDBY,
            }

            for command in commands_and_expected_states:
                await self.execute_command(
                    command, commands_and_expected_states[command]
                )

    async def test_fault_state(self) -> None:
        async with self.create_at_simulator(
            go_to_fault_state=True
        ), self.create_cmd_evt_client(self.simulator):
            assert self.simulator.simulator_state == sal_enums.State.STANDBY

            command = attcpip.CommonCommand.START
            expected_state = sal_enums.State.FAULT
            await self.execute_command(command, expected_state)

            command = attcpip.CommonCommand.STANDBY
            expected_state = sal_enums.State.STANDBY
            await self.execute_command(command, expected_state)
