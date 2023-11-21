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
import pathlib
import types
import typing

from lsst.ts import salobj, tcpip, utils

from .at_server_simulator import AtServerSimulator
from .command_issued import CommandIssued
from .enums import Ack, CommonCommand, CommonCommandArgument

# List of all state commands.
STATE_COMMANDS = [cmd.value for cmd in CommonCommand]


class AtTcpipCsc(salobj.ConfigurableCsc):
    """Base Configurable CSC with common code.

    The base Configurable CSC is intended to be used by systems that connect to
    the AuxTel LabVIEW servers via TCP/IP, like ATMCS and ATPneumatics.

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
        index: int | None,
        config_schema: dict[str, typing.Any],
        config_dir: str | pathlib.Path | None = None,
        check_if_duplicate: bool = False,
        initial_state: salobj.State | int | None = None,
        override: str = "",
        simulation_mode: int = 0,
    ) -> None:
        super().__init__(
            name=name,
            index=index,
            config_schema=config_schema,
            config_dir=config_dir,
            check_if_duplicate=check_if_duplicate,
            initial_state=initial_state,
            override=override,
            simulation_mode=simulation_mode,
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

        self.config: types.SimpleNamespace | None = None

    async def configure(self, config: typing.Any) -> None:
        self.config = config

    @staticmethod
    def get_config_pkg() -> str:
        return "ts_config_attcs"

    @property
    def connected(self) -> bool:
        return self.cmd_evt_client.connected and self.telemetry_client.connected

    async def begin_disable(self, data: salobj.BaseMsgType) -> None:
        """Begin do_disable; called before state changes.

        Parameters
        ----------
        data : `DataType`
            Command data
        """
        command = CommonCommand.DISABLE
        if self.connected:
            command_issued = await self.write_command(command=command)
            await command_issued.done
        else:
            self.log.warning(f"Not connected so not sending the {command} command.")

    async def begin_enable(self, data: salobj.BaseMsgType) -> None:
        """Begin do_enable; called before state changes.

        Parameters
        ----------
        data : `DataType`
            Command data
        """
        command = CommonCommand.ENABLE
        if self.connected:
            command_issued = await self.write_command(command=command)
            await command_issued.done
        else:
            self.log.warning(f"Not connected so not sending the {command} command.")

    async def begin_standby(self, data: salobj.BaseMsgType) -> None:
        """Begin do_standby; called before the state changes.

        Parameters
        ----------
        data : `DataType`
            Command data
        """
        command = CommonCommand.STANDBY
        if self.connected and self.summary_state in [
            salobj.State.DISABLED,
            salobj.State.ENABLED,
        ]:
            command_issued = await self.write_command(command=command)
            await command_issued.done
        else:
            self.log.warning(
                f"{self.connected=} and {self.summary_state=} so not "
                f"sending the {command.value} command."
            )
        await self.stop_clients()

    async def end_start(self, data: salobj.BaseMsgType) -> None:
        """End do_start; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        data : `DataType`
            Command data
        """
        await self.start_clients()
        command = CommonCommand.START
        if self.connected:
            command_issued = await self.write_command(command=command)
            await command_issued.done
        else:
            self.log.warning(f"Not connected so not sending the {command} command.")

    async def start_clients(self) -> None:
        """Start the clients for the TCP/IPconnections as well as background
        tasks.

        If simulator_mode == 1 then the simulator gets initialized and started
        as well.
        """
        self.log.debug("start_clients")
        if self.connected:
            self.log.warning("Already connected. Ignoring.")
            return

        assert self.config is not None
        host = self.config.host
        cmd_evt_port = self.config.cmd_evt_port
        telemetry_port = self.config.telemetry_port

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
        """Stop all clients and background tasks.

        If simulator_mode == 1 then the simulator gets stopped as well.
        """
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
            self.log.debug(f"Received cmd_evt {data=}")
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
            self.log.info(f"Received telemetry {data=}")
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
        if len(params) == 0 and command not in STATE_COMMANDS:
            data[CommonCommandArgument.VALUE] = True
        command_issued = CommandIssued(name=command)
        self.commands_issued[sequence_id] = command_issued
        self.log.debug(f"Writing {data=}")
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
        attr = getattr(self, f"{name}", None)
        if attr is not None:
            self.log.debug(f"Sending {name=} with {kwargs=}")
            await attr.set_write(**kwargs)
        else:
            self.log.error(f"{name=} not found. Ignoring.")
