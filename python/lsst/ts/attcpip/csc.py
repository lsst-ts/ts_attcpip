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

__all__ = ["AtTcpipCsc"]

import asyncio
import typing

from lsst.ts import salobj, tcpip, utils

from .at_server_simulator import AtServerSimulator
from .command_issued import CommandIssued
from .enums import Ack, CommonCommandArgument


class AtTcpipCsc(salobj.BaseCsc):
    """Base CSC with common code.

    The base CSC is intended to be used by systems that connect to the AuxTel
    LabVIEW servers via TCP/IP, like ATMCS and ATPneumatics.

    Parameters
    ----------
    name : `str`
        Name of SAL component.
    index : `int` or `None`
        SAL component index, or 0 or None if the component is not indexed.
    check_if_duplicate : `bool`, optional
        Check for heartbeat events from the same SAL name and index
        at startup (before starting the heartbeat loop)?
        Defaults to False in order to speed up unit tests,
        but `amain` sets it true.
    initial_state : `State`, `int` or `None`, optional
        Initial state for this CSC.
        If None use the class attribute ``default_initial_state``.
        Typically `State.STANDBY` (or `State.OFFLINE` for an
        externally commandable CSC) but can also be
        `State.DISABLED`, or `State.ENABLED`,
        in which case you may also want to specify
        ``override`` for a configurable CSC.
    override : `str`, optional
        Configuration override file to apply if ``initial_state`` is
        `State.DISABLED` or `State.ENABLED`. Ignored if the CSC
        is not configurable.
    simulation_mode : `int`, optional
        Simulation mode. The default is 0: do not simulate.
    allow_missing_callbacks : `bool`, optional
        Allow missing ``do_<name>`` callback methods? Missing method
        will be replaced with one that raises salobj.ExpectedError.
        This is intended for mock controllers, which may only support
        a subset of commands.
    """

    valid_simulation_modes = [1]

    def __init__(
        self,
        name: str,
        index: int | None = None,
        check_if_duplicate: bool = False,
        initial_state: salobj.State | int | None = None,
        override: str = "",
        simulation_mode: int = 0,
        allow_missing_callbacks: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            index=index,
            check_if_duplicate=check_if_duplicate,
            initial_state=initial_state,
            override=override,
            simulation_mode=simulation_mode,
            allow_missing_callbacks=allow_missing_callbacks,
        )

        # TCP/IP clients for commands/events and for telemetry.
        self.cmd_evt_client = tcpip.Client(host="", port=None, log=self.log)
        self.telemetry_client = tcpip.Client(host="", port=None, log=self.log)

        # Simulator for simulation_mode == 1.
        self.simulator: AtServerSimulator | None = None

        # Task for receiving event messages.
        self._event_task = utils.make_done_future()
        # Task for receiving telemetry messages.
        self._telemetry_task = utils.make_done_future()

        # Keep track of the issued commands.
        self.commands_issued: dict[int, CommandIssued] = dict()

        # Keep track of unrecognized telemetry topics.
        self.unrecognized_telemetry_topics: set[str] = set()

        # Iterator for command sequence_id.
        self._command_sequence_id_generator = utils.index_generator()

    @property
    def connected(self) -> bool:
        return self.cmd_evt_client.connected and self.telemetry_client.connected

    async def handle_summary_state(self) -> None:
        if self.summary_state in (salobj.State.DISABLED, salobj.State.ENABLED):
            await self.start_clients()
        else:
            await self.stop_clients()

    async def start_clients(self) -> None:
        if self.connected:
            self.log.warning("Already connected. Ignoring.")
            return

        # TODO DM-38912 Get this from the configuration.
        host = ""
        cmd_evt_port = 0
        telemetry_port = 0

        if self.simulation_mode == 1:
            assert self.simulator is not None
            await self.simulator.cmd_evt_server.start_task
            await self.simulator.telemetry_server.start_task
            host = self.simulator.cmd_evt_server.host
            cmd_evt_port = self.simulator.cmd_evt_server.port
            telemetry_port = self.simulator.telemetry_server.port

        self.cmd_evt_client = tcpip.Client(
            host=host, port=cmd_evt_port, log=self.log, name="CmdEvtClient"
        )
        self.telemetry_client = tcpip.Client(
            host=host, port=telemetry_port, log=self.log, name="TelemetryClient"
        )

        await self.cmd_evt_client.start_task
        await self.telemetry_client.start_task

        self._event_task = asyncio.create_task(self.cmd_evt_loop())
        self._telemetry_task = asyncio.create_task(self.telemetry_loop())

    async def stop_clients(self) -> None:
        if not self.connected:
            self.log.warning("Not connected. Ignoring.")
            return

        self._event_task.cancel()
        self._telemetry_task.cancel()

        await self.cmd_evt_client.close()
        await self.telemetry_client.close()

        if (self.simulation_mode == 1) and (self.simulator is not None):
            await self.simulator.telemetry_server.close()
            await self.simulator.cmd_evt_server.close()

    async def close_tasks(self) -> None:
        """Shut down pending tasks. Called by `close`.

        Perform all cleanup other than disabling logging to SAL
        and closing the dds domain.
        """
        await self.stop_clients()
        await super().close_tasks()

    async def cmd_evt_loop(self) -> None:
        """Execute the command and event loop.

        This loop waits for incoming command and event messages and processes
        them when they arrive.
        """
        while True:
            data = await self.cmd_evt_client.read_json()
            data_id: str = data["id"]

            # If data_id starts with "evt_" then handle the event data.
            if data_id.startswith("evt_"):
                await self.call_set_write(data=data)
            elif CommonCommandArgument.SEQUENCE_ID in data:
                sequence_id = data[CommonCommandArgument.SEQUENCE_ID]
                response = data[CommonCommandArgument.ID]
                match response:
                    case Ack.ACK:
                        self.commands_issued[sequence_id].set_ack()
                    case Ack.NOACK:
                        self.commands_issued[sequence_id].set_noack()
                    case Ack.SUCCESS:
                        self.commands_issued[sequence_id].set_success()
                    case Ack.FAIL:
                        self.commands_issued[sequence_id].set_fail()
                    case _:
                        raise RuntimeError(f"Received unexpected {response=}.")
            else:
                await self.fault(
                    code=None,
                    report=f"Received incorrect event or command {data=}.",
                )

    async def telemetry_loop(self) -> None:
        """Execute the telemetry loop.

        This loop waits for incoming telemetry messages and processes them when
        they arrive.
        """
        while True:
            data = await self.telemetry_client.read_json()
            data_id = ""
            try:
                data_id = data["id"]
            except Exception:
                self.log.warning(f"Unable to parse {data=}. Ignoring.")
            if data_id.startswith("tel_"):
                if data_id not in self.unrecognized_telemetry_topics:
                    try:
                        getattr(self, data_id)
                    except Exception:
                        self.log.warning(
                            f"Unknown telemetry topic {data_id}. Ignoring."
                        )
                        self.unrecognized_telemetry_topics.add(data_id)
                    else:
                        await self.call_set_write(data=data)
            else:
                await self.log.error(f"Received non-telemetry {data=}.")

    async def write_command(
        self, command: str, **params: dict[str, typing.Any]
    ) -> CommandIssued:
        """Write the command JSON string to the TCP/IP command/event server.

        Parameters
        ----------
        command : `str`
            The command to write.
        **params : `typing.Any`
            The parameters for the command. This may be empty.

        Returns
        -------
        command_issued : `CommandIssued`
            An instance of CommandIssued to monitor the future state of the
            command.

        Notes
        -----
        If no command parameters are passed on then a default parameter named
        ``value`` is added since the real ATMCS expects this. This will be
        removed in DM-39629.
        """
        sequence_id = next(self._command_sequence_id_generator)
        data: dict[str, typing.Any] = {
            CommonCommandArgument.ID.value: command,
            CommonCommandArgument.SEQUENCE_ID.value: sequence_id,
        }
        for param in params:
            data[param] = params[param]
        # TODO DM-39629 Remove this when it is not necessary anymore.
        if len(params) == 0:
            data[CommonCommandArgument.VALUE] = True
        command_issued = CommandIssued(name=command)
        self.commands_issued[sequence_id] = command_issued
        await self.cmd_evt_client.write_json(data)
        return command_issued

    async def call_set_write(self, data: dict[str, typing.Any]) -> None:
        """Call await ``set_write`` for an event or telemetry.

        ``data`` contains both the event or telemetry name and the attribute
        values. The method will first extract the name and the values and then
        write.

        Parameters
        ----------
        data : `dict`[`str`, `Any`]
            Data.
        """
        name: str = data["id"]
        kwargs = {key: value for key, value in data.items() if key != "id"}
        attr = getattr(self, f"{name}")
        await attr.set_write(**kwargs)
