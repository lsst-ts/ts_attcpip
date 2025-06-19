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

import contextlib
import os
import pathlib
import typing
import unittest
from unittest import mock

import yaml
from lsst.ts import attcpip, salobj, tcpip
from lsst.ts.xml import sal_enums

CONFIG_DIR = pathlib.Path(__file__).parent / "data" / "config"
CONFIG_SCHEMA = yaml.safe_load(
    """
    $schema: http://json-schema.org/draft-07/schema#
    $id: https://github.com/lsst-ts/ts_atmcssimulator/blob/main/python/lsst/ts/atmcssimulator/config_schema.py
    title: MTDome v1
    description: Schema for ATMCS CSC configuration files.
    type: object
    properties:
      host:
        description: IP address of the TCP/IP interface.
        type: string
        format: hostname
      cmd_evt_port:
        description: Port number of the command and event TCP/IP interface.
        type: integer
      telemetry_port:
        description: Port number of the telemetry TCP/IP interface.
        type: integer
    required:
      - host
      - cmd_evt_port
      - telemetry_port
    additionalProperties: false
    """
)

# Timeout [s].
TIMEOUT = 1.0

# The ports for the simulator.
CMD_EVT_PORT = 5000
TELEMETRY_PORT = 6000


class CscTestCase(unittest.IsolatedAsyncioTestCase):
    @contextlib.asynccontextmanager
    async def create_at_simulator(
        self,
        go_to_fault_state: bool,
        simulator_state: sal_enums.State = sal_enums.State.STANDBY,
        send_state_event: bool = True,
    ) -> typing.AsyncGenerator[None, None]:
        os.environ["LSST_TOPIC_SUBNAME"] = "test_attcpip"
        os.environ["LSST_SITE"] = "test"
        os.environ["LSST_DDS_PARTITION_PREFIX"] = "test"
        attcpip.AtTcpipCsc.version = "UnitTest"

        with (
            mock.patch.object(salobj.Controller, "_assert_do_methods_present"),
            mock.patch.object(attcpip.AtSimulator, "cmd_evt_connect_callback"),
        ):
            async with attcpip.AtSimulator(
                host=tcpip.LOCALHOST_IPV4,
                cmd_evt_port=CMD_EVT_PORT,
                telemetry_port=TELEMETRY_PORT,
                simulator_state=simulator_state,
                send_state_event=send_state_event,
            ) as self.simulator:
                self.simulator.go_to_fault_state = go_to_fault_state
                await self.simulator.cmd_evt_server.start_task
                await self.simulator.telemetry_server.start_task
                yield

    async def test_csc_without_fault_state(self) -> None:
        """Test without the simulator going to FAULT state."""
        async with (
            self.create_at_simulator(go_to_fault_state=False),
            attcpip.AtTcpipCsc(
                name="Test",
                index=0,
                config_schema=CONFIG_SCHEMA,
                config_dir=CONFIG_DIR,
                initial_state=salobj.State.STANDBY,
                simulation_mode=1,
            ) as csc,
            salobj.Remote(
                domain=csc.domain,
                name=csc.salinfo.name,
                index=csc.salinfo.index,
            ) as remote,
        ):
            data = await self.set_csc_simulator(csc, remote)

            # Repeat to make sure that cmd_evt_client still is connected to the
            # simulator after going to STANDBY.
            for _ in range(2):
                await self.perform_state_transitions(
                    csc, remote, data, sal_enums.State.DISABLED
                )

                await csc.do_standby(data)
                assert self.simulator.simulator_state == sal_enums.State.STANDBY
                await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
                assert csc.cmd_evt_client.connected
                assert not csc.telemetry_client.connected

    async def test_csc_with_fault_state(self) -> None:
        """Test with the simulator going to FAULT state."""
        async with (
            self.create_at_simulator(go_to_fault_state=True),
            attcpip.AtTcpipCsc(
                name="Test",
                index=0,
                config_schema=CONFIG_SCHEMA,
                config_dir=CONFIG_DIR,
                initial_state=salobj.State.STANDBY,
                simulation_mode=1,
            ) as csc,
            salobj.Remote(
                domain=csc.domain,
                name=csc.salinfo.name,
                index=csc.salinfo.index,
            ) as remote,
        ):
            csc.simulator = self.simulator
            data = salobj.BaseMsgType()
            data.configurationOverride = ""
            assert self.simulator.simulator_state == sal_enums.State.STANDBY
            await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)

            # Repeat to make sure that both cmd_evt_client and telemetry_client
            # remain connected to the simulator all the time.
            for _ in range(2):
                await csc.do_start(data)
                assert self.simulator.simulator_state == sal_enums.State.FAULT
                await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
                await remote.evt_errorCode.next(flush=False, timeout=TIMEOUT)
                assert csc.cmd_evt_client.connected
                assert csc.telemetry_client.connected

                await csc.do_standby(data)
                assert self.simulator.simulator_state == sal_enums.State.STANDBY
                await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
                assert csc.cmd_evt_client.connected
                assert csc.telemetry_client.connected

    async def test_complete_state_cycle(self) -> None:
        async with (
            self.create_at_simulator(go_to_fault_state=False),
            attcpip.AtTcpipCsc(
                name="Test",
                index=0,
                config_schema=CONFIG_SCHEMA,
                config_dir=CONFIG_DIR,
                initial_state=salobj.State.STANDBY,
                simulation_mode=1,
            ) as csc,
            salobj.Remote(
                domain=csc.domain,
                name=csc.salinfo.name,
                index=csc.salinfo.index,
            ) as remote,
        ):
            data = await self.set_csc_simulator(csc, remote)

            await csc.do_start(data)
            await csc.do_enable(data)
            await csc.do_disable(data)
            await csc.do_standby(data)

    async def test_csc_with_at_in_unexpected_but_acceptable_state(self) -> None:
        for simulator_state in [
            sal_enums.State.DISABLED,
            sal_enums.State.ENABLED,
        ]:
            async with (
                self.create_at_simulator(
                    go_to_fault_state=False, simulator_state=simulator_state
                ),
                attcpip.AtTcpipCsc(
                    name="Test",
                    index=0,
                    config_schema=CONFIG_SCHEMA,
                    config_dir=CONFIG_DIR,
                    initial_state=salobj.State.STANDBY,
                    simulation_mode=1,
                ) as csc,
                salobj.Remote(
                    domain=csc.domain,
                    name=csc.salinfo.name,
                    index=csc.salinfo.index,
                ) as remote,
            ):
                data = await self.set_csc_simulator(csc, remote, simulator_state)

                await self.perform_state_transitions(csc, remote, data, simulator_state)

    async def test_csc_with_at_in_unexpected_and_unacceptable_state(self) -> None:
        async with (
            self.create_at_simulator(go_to_fault_state=False),
            attcpip.AtTcpipCsc(
                name="Test",
                index=0,
                config_schema=CONFIG_SCHEMA,
                config_dir=CONFIG_DIR,
                initial_state=salobj.State.STANDBY,
                simulation_mode=1,
            ) as csc,
            salobj.Remote(
                domain=csc.domain,
                name=csc.salinfo.name,
                index=csc.salinfo.index,
            ) as remote,
        ):
            data = await self.set_csc_simulator(csc, remote)

            await csc.do_start(data)
            assert self.simulator.simulator_state == sal_enums.State.DISABLED
            data = await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
            assert data.summaryState == sal_enums.State.DISABLED
            assert csc.cmd_evt_client.connected
            assert csc.telemetry_client.connected

            await csc.do_enable(data)
            assert self.simulator.simulator_state == sal_enums.State.ENABLED
            data = await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
            assert data.summaryState == sal_enums.State.ENABLED

            await self.simulator.cmd_evt_dispatch_callback(
                data={"id": "cmd_disable", "sequence_id": -1}
            )
            data = await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
            assert data.summaryState == sal_enums.State.FAULT

    async def test_not_receiving_summary_state_event(self) -> None:
        simulator_state = sal_enums.State.STANDBY
        async with (
            self.create_at_simulator(
                go_to_fault_state=False,
                simulator_state=simulator_state,
                send_state_event=False,
            ),
            attcpip.AtTcpipCsc(
                name="Test",
                index=0,
                config_schema=CONFIG_SCHEMA,
                config_dir=CONFIG_DIR,
                initial_state=salobj.State.STANDBY,
                simulation_mode=1,
            ) as csc,
            salobj.Remote(
                domain=csc.domain,
                name=csc.salinfo.name,
                index=csc.salinfo.index,
            ) as remote,
        ):
            data = await self.set_csc_simulator(csc, remote, simulator_state)

            await csc.do_start(data)
            data = await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
            assert data.summaryState == sal_enums.State.FAULT
            assert csc.cmd_evt_client.connected
            assert csc.telemetry_client.connected

    async def set_csc_simulator(
        self,
        csc: attcpip.AtTcpipCsc,
        remote: salobj.Remote,
        expected_simulator_state: sal_enums.State = sal_enums.State.STANDBY,
    ) -> salobj.BaseMsgType:
        csc.simulator = self.simulator
        data = salobj.BaseMsgType()
        data.configurationOverride = ""
        assert self.simulator.simulator_state == expected_simulator_state
        await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
        assert not csc.cmd_evt_client.connected
        assert not csc.telemetry_client.connected
        return data

    async def perform_state_transitions(
        self,
        csc: attcpip.AtTcpipCsc,
        remote: salobj.Remote,
        data: salobj.BaseMsgType,
        expected_simulator_state: sal_enums.State,
    ) -> None:
        await csc.do_start(data)
        assert self.simulator.simulator_state == expected_simulator_state
        await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
        assert csc.cmd_evt_client.connected
        assert csc.telemetry_client.connected

        await csc.do_enable(data)
        assert self.simulator.simulator_state == sal_enums.State.ENABLED
        await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
        assert csc.cmd_evt_client.connected
        assert csc.telemetry_client.connected

        await csc.do_disable(data)
        assert self.simulator.simulator_state == sal_enums.State.DISABLED
        await remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
        assert csc.cmd_evt_client.connected
        assert csc.telemetry_client.connected
