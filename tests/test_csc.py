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
    async def create_csc_and_remote(self) -> typing.AsyncGenerator[None, None]:
        os.environ["LSST_TOPIC_SUBNAME"] = "test_attcpip"
        os.environ["LSST_SITE"] = "test"
        os.environ["LSST_DDS_PARTITION_PREFIX"] = "test"
        attcpip.AtTcpipCsc.version = "UnitTest"

        with mock.patch.object(salobj.Controller, "_assert_do_methods_present"):
            async with (
                attcpip.AtTcpipCsc(
                    name="Test",
                    index=0,
                    config_schema=CONFIG_SCHEMA,
                    config_dir=CONFIG_DIR,
                    initial_state=sal_enums.State.STANDBY,
                    simulation_mode=1,
                ) as self.csc,
                salobj.Remote(
                    domain=self.csc.domain,
                    name=self.csc.salinfo.name,
                    index=self.csc.salinfo.index,
                ) as self.remote,
            ):
                yield

    @contextlib.asynccontextmanager
    async def create_at_simulator(
        self,
        go_to_fault_state: bool,
        simulator_state: sal_enums.State = sal_enums.State.STANDBY,
    ) -> typing.AsyncGenerator[None, None]:
        with mock.patch.object(attcpip.AtSimulator, "cmd_evt_connect_callback"):
            async with attcpip.AtSimulator(
                host=tcpip.LOCALHOST_IPV4,
                cmd_evt_port=CMD_EVT_PORT,
                telemetry_port=TELEMETRY_PORT,
                simulator_state=simulator_state,
            ) as self.simulator:
                self.simulator.go_to_fault_state = go_to_fault_state
                await self.simulator.cmd_evt_server.start_task
                await self.simulator.telemetry_server.start_task

                self.csc.simulator = self.simulator
                assert self.simulator.simulator_state == simulator_state
                assert not self.csc.cmd_evt_client.connected
                assert not self.csc.telemetry_client.connected
                yield

    async def test_complete_state_cycle(self) -> None:
        """Test a complete state cycle with the AT server in different allowed
        start states."""
        for at_state in [
            sal_enums.State.STANDBY,
            sal_enums.State.FAULT,
            sal_enums.State.DISABLED,
            sal_enums.State.ENABLED,
        ]:
            async with (
                self.create_csc_and_remote(),
                self.create_at_simulator(
                    go_to_fault_state=False, simulator_state=at_state
                ),
            ):
                await self._validate_summary_state(sal_enums.State.STANDBY)

                data = salobj.BaseMsgType()
                data.configurationOverride = ""

                # When the CSC starts, it will not progress the AT server
                # state. It will connect to the AT server and receive the at
                # server state so that can be verified.
                await self.csc.do_start(data)
                assert self.csc.summary_state == sal_enums.State.DISABLED
                assert self.csc.at_state == at_state
                await self._validate_summary_state(sal_enums.State.DISABLED)
                await self._validate_crio_summary_state(at_state)

                # When the CSC goes to ENABLED, it should state transit the
                # AT server until it is ENABLED. Depending on the start state
                # the AT server will pass through various states, hence the
                # ifs.
                await self.csc.do_enable(data)
                assert self.csc.summary_state == sal_enums.State.ENABLED
                assert self.csc.at_state == sal_enums.State.ENABLED
                await self._validate_summary_state(sal_enums.State.ENABLED)
                if at_state == sal_enums.State.FAULT:
                    await self._validate_crio_summary_state(sal_enums.State.STANDBY)
                if at_state in [sal_enums.State.FAULT, sal_enums.State.STANDBY]:
                    await self._validate_crio_summary_state(sal_enums.State.DISABLED)
                if at_state in [
                    sal_enums.State.FAULT,
                    sal_enums.State.STANDBY,
                    sal_enums.State.DISABLED,
                ]:
                    await self._validate_crio_summary_state(sal_enums.State.ENABLED)

                # From here on the AT server should follow the CSC states.
                await self.csc.do_disable(data)
                assert self.csc.summary_state == sal_enums.State.DISABLED
                assert self.csc.at_state == sal_enums.State.DISABLED
                await self._validate_summary_state(sal_enums.State.DISABLED)
                await self._validate_crio_summary_state(sal_enums.State.DISABLED)

                await self.csc.do_standby(data)
                assert self.csc.summary_state == sal_enums.State.STANDBY
                assert self.csc.at_state == sal_enums.State.STANDBY
                await self._validate_summary_state(sal_enums.State.STANDBY)
                await self._validate_crio_summary_state(sal_enums.State.STANDBY)

    async def test_complete_state_cycle_with_fault(self) -> None:
        """Test a complete state cycle with the AT server in different allowed
        start states but the AT server goes to FAULT."""
        for at_state in [
            sal_enums.State.STANDBY,
            # sal_enums.State.FAULT,
            # sal_enums.State.DISABLED,
            # sal_enums.State.ENABLED,
        ]:
            async with (
                self.create_csc_and_remote(),
                self.create_at_simulator(
                    go_to_fault_state=True, simulator_state=at_state
                ),
            ):
                await self._validate_summary_state(sal_enums.State.STANDBY)

                data = salobj.BaseMsgType()
                data.configurationOverride = ""

                # When the CSC starts, it will not progress the AT server
                # state. It will connect to the AT server and receive the at
                # server state so that can be verified.
                await self.csc.do_start(data)
                assert self.csc.summary_state == sal_enums.State.DISABLED
                assert self.csc.at_state == at_state
                await self._validate_summary_state(sal_enums.State.DISABLED)
                await self._validate_crio_summary_state(at_state)

                # Now the AT server reports it is in FAULT state so the CSC
                # should go to FAULT as well.
                await self.csc.do_enable(data)
                assert self.csc.summary_state == sal_enums.State.FAULT
                assert self.csc.at_state == sal_enums.State.FAULT
                await self._validate_summary_state(sal_enums.State.FAULT)
                await self._validate_crio_summary_state(sal_enums.State.FAULT)

    async def _validate_summary_state(self, summary_state: sal_enums.State) -> None:
        data = await self.remote.evt_summaryState.next(flush=False, timeout=TIMEOUT)
        assert sal_enums.State(data.summaryState) == summary_state

    async def _validate_crio_summary_state(
        self, summary_state: sal_enums.State
    ) -> None:
        if hasattr(self.csc, "evt_crioSummaryState"):
            data = await self.remote.evt_crioSummaryState.next(
                flush=False, timeout=TIMEOUT
            )
            assert sal_enums.State(data.summaryState) == summary_state
