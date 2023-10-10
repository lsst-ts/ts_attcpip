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


class CscTestCase(unittest.IsolatedAsyncioTestCase):
    @contextlib.asynccontextmanager
    @mock.patch.object(attcpip.AtSimulator, "cmd_evt_connect_callback")
    async def create_at_simulator(
        self, mock_sim: mock.AsyncMock
    ) -> typing.AsyncGenerator[None, None]:
        async with attcpip.AtSimulator(
            host=tcpip.LOCALHOST_IPV4, cmd_evt_port=5000, telemetry_port=6000
        ) as self.simulator:
            await self.simulator.cmd_evt_server.start_task
            await self.simulator.telemetry_server.start_task
            yield

    async def test_csc(self) -> None:
        os.environ["LSST_TOPIC_SUBNAME"] = "test_attcpip"
        os.environ["LSST_SITE"] = "test"
        os.environ["LSST_DDS_PARTITION_PREFIX"] = "test"
        attcpip.AtTcpipCsc.version = "UnitTest"

        with mock.patch.object(
            salobj.Controller, "_assert_do_methods_present"
        ), mock.patch.object(
            salobj.ConfigurableCsc, "read_config_dir"
        ), mock.patch.object(
            salobj.topics.WriteTopic, "write"
        ):
            async with self.create_at_simulator():  # type: ignore
                csc = attcpip.AtTcpipCsc(
                    name="Test",
                    index=0,
                    config_schema=CONFIG_SCHEMA,
                    config_dir=CONFIG_DIR,
                    initial_state=salobj.State.STANDBY,
                    simulation_mode=1,
                )
                assert csc is not None
                csc.simulator = self.simulator
                data = salobj.BaseMsgType()
                data.configurationOverride = ""
                assert self.simulator.simulator_state == attcpip.SimulatorState.STANDBY

                await csc.do_start(data)
                assert self.simulator.simulator_state == attcpip.SimulatorState.DISABLED

                await csc.do_enable(data)
                assert self.simulator.simulator_state == attcpip.SimulatorState.ENABLED

                await csc.do_disable(data)
                assert self.simulator.simulator_state == attcpip.SimulatorState.DISABLED

                await csc.do_standby(data)
                assert self.simulator.simulator_state == attcpip.SimulatorState.STANDBY
