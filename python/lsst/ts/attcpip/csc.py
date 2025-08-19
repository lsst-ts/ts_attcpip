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
from lsst.ts.xml import sal_enums

from .at_server_simulator import AtServerSimulator
from .command_issued import CommandIssued
from .enums import (
    Ack,
    CommonCommand,
    CommonCommandArgument,
    CommonEvent,
    CommonEventArgument,
)

# List of all state commands.
STATE_COMMANDS = [cmd.value for cmd in CommonCommand]

# Timeout [s] for wait operations.
WAIT_TIMEOUT = 1.0

# Timeout [s] for receivng an AT state event.
AT_STATE_EVENT_WAIT_TIMEOUT = 5.0

# Timeout [s] for commands to report they're done.
CMD_DONE_TIMEOUT = 60.0


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
        Typically, `State.STANDBY` (or `State.OFFLINE` for an
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

    valid_simulation_modes = [0, 1]

    def __init__(
        self,
        name: str,
        index: int | None,
        config_schema: dict[str, typing.Any],
        config_dir: str | pathlib.Path | None = None,
        check_if_duplicate: bool = False,
        initial_state: sal_enums.State = sal_enums.State.STANDBY,
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
        self.cmd_done_timeout = CMD_DONE_TIMEOUT

        # AT state event to wait when starting.
        self.at_state_event = asyncio.Event()

        # Event to indicate going to FAULT.
        self.fault_event = asyncio.Event()

        # Keep track of the AT state for state transition commands.
        self.at_state = sal_enums.State.OFFLINE
        self.at_connect_state = sal_enums.State.OFFLINE

        # Is a CSC state transition ongoing or not.
        self.state_transition_ongoing = False

        # At the start expect a state event from AT or not. This only should
        # be true when the cmd_evt client connects.
        self.expect_at_start_state_event = False

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
        return self.cmd_evt_client.connected

    async def wait_cmd_done(self, command: CommonCommand) -> None:
        command_issued = await self.write_command(command=command)
        fault_event_task = asyncio.create_task(self.fault_event.wait())
        try:
            async with asyncio.timeout(self.cmd_done_timeout):
                await asyncio.wait(
                    [command_issued.done, fault_event_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
        except TimeoutError:
            self.log.warning(f"Timeout waiting for {command=}. Ignoring.")
        if not fault_event_task.done():
            fault_event_task.cancel()

    async def perform_common_part_of_state_transition(
        self,
        command: CommonCommand,
        state: sal_enums.State,
        expected_states: list[sal_enums.State],
    ) -> None:
        self.log.debug("perform_common_part_of_state_transition")
        if self.at_state in expected_states:
            await self.wait_cmd_done(command)
        else:
            self.log.error(f"Unexpectedly {self.at_state=}. Going FAULT.")
            await self.fault(
                code=None, report=f"Server in unexpected state {self.at_state}."
            )

    async def begin_disable(self, data: salobj.BaseMsgType) -> None:
        """Begin do_disable; called before state changes.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = True
        self.log.debug(f"begin_disable {self.summary_state=}, {self.at_state=}")
        await self.cmd_disable.ack_in_progress(data, self.cmd_done_timeout)
        command = CommonCommand.DISABLE
        if self.connected:
            await self.perform_common_part_of_state_transition(
                command=command,
                state=sal_enums.State.DISABLED,
                expected_states=[sal_enums.State.STANDBY, sal_enums.State.ENABLED],
            )
        else:
            await self.fault(
                code=None, report=f"Not connected so not sending the {command} command."
            )

    async def end_disable(self, data: salobj.BaseMsgType) -> None:
        """End do_disable; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = False
        if self.fault_event.is_set():
            self.fault_event.clear()
            await self.fault(
                code=None,
                report="The fault_event is set so going to FAULT.",
            )
        self.log.debug(f"end_disable and {self.state_transition_ongoing=}.")

    async def begin_enable(self, data: salobj.BaseMsgType) -> None:
        """Begin do_enable; called before state changes.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = True
        self.log.debug(f"begin_enable {self.summary_state=}, {self.at_state=}")
        await self.cmd_enable.ack_in_progress(data, self.cmd_done_timeout)

        while self.summary_state not in [
            sal_enums.State.ENABLED,
            sal_enums.State.FAULT,
        ]:
            match self.at_state:
                case sal_enums.State.FAULT:
                    command = CommonCommand.STANDBY
                    expected_states = [sal_enums.State.FAULT]
                case sal_enums.State.STANDBY:
                    command = CommonCommand.START
                    expected_states = [sal_enums.State.STANDBY]
                case sal_enums.State.DISABLED:
                    command = CommonCommand.ENABLE
                    expected_states = [sal_enums.State.DISABLED]
                case _:
                    # AT is ENABLED.
                    return
            if self.connected:
                await self.perform_common_part_of_state_transition(
                    command=command,
                    state=sal_enums.State.ENABLED,
                    expected_states=expected_states,
                )
            else:
                await self.fault(
                    code=None,
                    report=f"Not connected so not sending the {command} command.",
                )
        self.log.debug(f"end begin_enable {self.summary_state=}, {self.at_state=}")

    async def end_enable(self, data: salobj.BaseMsgType) -> None:
        """End do_enable; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = False
        if self.fault_event.is_set():
            self.fault_event.clear()
            await self.fault(
                code=None,
                report="The fault_event is set so going to FAULT.",
            )
        self.log.debug(
            f"end_enable and {self.state_transition_ongoing=},  {self.summary_state=}, {self.at_state=}"
        )

    async def begin_standby(self, data: salobj.BaseMsgType) -> None:
        """Begin do_standby; called before the state changes.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = True
        self.log.debug(f"begin_standby {self.summary_state=}, {self.at_state=}")
        await self.cmd_standby.ack_in_progress(data, self.cmd_done_timeout)
        command = CommonCommand.STANDBY
        if self.connected and self.summary_state != sal_enums.State.FAULT:
            if self.at_state == sal_enums.State.ENABLED:
                await self.fault(
                    code=None, report=f"Server in unexpected state {self.at_state}."
                )
                return

            await self.perform_common_part_of_state_transition(
                command=command,
                state=sal_enums.State.STANDBY,
                expected_states=[sal_enums.State.DISABLED],
            )
            self.log.debug("Disconnecting.")
            await self.stop_clients()
        else:
            await self.fault(
                code=None, report=f"Not connected so not sending the {command} command."
            )

    async def end_standby(self, data: salobj.BaseMsgType) -> None:
        """End do_standby; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = False
        self.log.debug(f"end_standby and {self.state_transition_ongoing=}.")

    async def end_start(self, data: salobj.BaseMsgType) -> None:
        """End do_start; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        data : `salobj.BaseMsgType`
            Command data
        """
        self.state_transition_ongoing = True
        self.log.debug(f"end_start {self.summary_state=}, {self.at_state=}")
        await self.cmd_start.ack_in_progress(data, self.cmd_done_timeout)
        await self.start_clients()
        command = CommonCommand.START

        if self.connected:
            try:
                async with asyncio.timeout(AT_STATE_EVENT_WAIT_TIMEOUT):
                    self.log.debug("Waiting for AT state event to be set.")
                    await self.at_state_event.wait()
                    self.log.debug("AT state event was set.")
            except TimeoutError:
                report = f"Not received AT state event after {AT_STATE_EVENT_WAIT_TIMEOUT} seconds."
                self.log.error(report)
                await self.fault(code=None, report=report)
                self.state_transition_ongoing = False
                return
        else:
            await self.fault(
                code=None, report=f"Not connected so not sending the {command} command."
            )
        self.state_transition_ongoing = False
        self.log.debug(f"end_start and {self.state_transition_ongoing=}.")

    async def fault(self, code: int | None, report: str, traceback: str = "") -> None:
        """Enter the fault state and output the ``errorCode`` event.

        Parameters
        ----------
        code : `int`
            Error code for the ``errorCode`` event.
            If `None` then ``errorCode`` is not output, and you should
            output it yourself. Specifying `None` is deprecated;
            please always specify an integer error code.
        report : `str`
            Description of the error.
        traceback : `str`, optional
            Description of the traceback, if any.
        """
        # Only disconnect the telemetry client but not the evt_cmd client.
        await self.stop_clients()
        self.log.debug(f"Going to FAULT with {report=}.")
        await super().fault(code, report, traceback)

    async def start_clients(self) -> None:
        """Start the clients for the TCP/IPconnections as well as background
        tasks.

        If simulator_mode == 1 then the simulator gets initialized and started
        as well.
        """
        self.log.debug("start_clients")

        assert self.config is not None
        host = self.config.host
        cmd_evt_port = self.config.cmd_evt_port
        telemetry_port = self.config.telemetry_port

        if self.simulation_mode == 1:
            self.log.debug("Starting simulator.")
            assert self.simulator is not None
            await self.simulator.cmd_evt_server.start_task
            await self.simulator.telemetry_server.start_task
            host = self.simulator.cmd_evt_server.host
            cmd_evt_port = self.simulator.cmd_evt_server.port
            telemetry_port = self.simulator.telemetry_server.port

        # Do not call `await self.stop_clients()` here since that will stop
        # the simulator as well.
        self.log.debug("Stop the clients in case the configuration has changed.")
        await self._stop_cmd_evt_task_and_client()
        await self._stop_telemetry_task_and_client()

        self.log.debug("Starting cmd_evt client.")
        self.expect_at_start_state_event = True
        self.cmd_evt_client = tcpip.Client(
            host=host, port=cmd_evt_port, log=self.log, name="CmdEvtClient"
        )
        await self.cmd_evt_client.start_task
        self._event_task = asyncio.create_task(self.cmd_evt_loop())

        self.log.debug("Starting telemetry client.")
        self.telemetry_client = tcpip.Client(
            host=host, port=telemetry_port, log=self.log, name="TelemetryClient"
        )
        await self.telemetry_client.start_task
        self._telemetry_task = asyncio.create_task(self.telemetry_loop())

    async def _stop_cmd_evt_task_and_client(self) -> None:
        if not self._event_task.done():
            self._event_task.cancel()
        try:
            await self.cmd_evt_client.close()
        except BaseException:
            self.log.exception("Failed to stop cmd_evt client. Ignoring.")

    async def _stop_telemetry_task_and_client(self) -> None:
        if not self._telemetry_task.done():
            self._telemetry_task.cancel()
        try:
            await self.telemetry_client.close()
        except BaseException:
            self.log.exception("Failed to stop telemetry client. Ignoring.")

    async def stop_clients(self) -> None:
        """Stop all clients and background tasks.

        If simulator_mode == 1 then the simulator gets stopped as well.
        """
        self.log.debug("Stopping clients.")
        await self._stop_telemetry_task_and_client()
        await self._stop_cmd_evt_task_and_client()

        if self.simulator is not None:
            self.log.debug("Closing simulator servers.")
            await self.simulator.telemetry_server.close()
            await self.simulator.cmd_evt_server.close()
            self.simulator = None

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
        while self.connected:
            data = await self.cmd_evt_client.read_json()
            self.log.debug(f"Received cmd_evt {data=}")
            data_id: str = data[CommonCommandArgument.ID]

            # If data_id starts with "evt_" then handle the event data.
            if data_id.startswith("evt_"):
                await self._handle_event(data, data_id)
            elif CommonCommandArgument.SEQUENCE_ID in data:
                await self._handle_command_response(data)
            else:
                report = f"Received incorrect event or command {data=}."
                self.log.error(report)
                await self.fault(code=None, report=report)

    async def _handle_event(self, data: typing.Any, data_id: str) -> None:
        # Handle summary state and detailed state events.
        state_evt: CommonEvent | None = None
        try:
            state_evt = CommonEvent(data_id)
        except ValueError:
            pass
        if state_evt == CommonEvent.SUMMARY_STATE:
            self.at_state_event.set()
            self.at_state = sal_enums.State(data[CommonEventArgument.SUMMARY_STATE])
            self.log.debug(
                f"Received AT state {self.at_state.name}, CSC state is {self.summary_state.name}, "
                f"{self.state_transition_ongoing=}, {self.expect_at_start_state_event=}."
            )
            if self.expect_at_start_state_event:
                self.at_connect_state = self.at_state

            if (
                not self.state_transition_ongoing
                and not self.expect_at_start_state_event
                and self.summary_state == sal_enums.State.ENABLED
                and self.at_state in [sal_enums.State.DISABLED, sal_enums.State.STANDBY]
            ):
                message = (
                    f"Unexpectedly received AT state event with id={data['id']} "
                    f"and state={sal_enums.State(data['summaryState']).name}."
                )
                self.log.error(message)
                await self.fault(code=None, report=message)
            if self.expect_at_start_state_event:
                self.expect_at_start_state_event = False

            if (
                self.at_state == sal_enums.State.FAULT
                and self.at_connect_state != sal_enums.State.FAULT
            ):
                self.log.debug(
                    f"Received FAULT state. Going to FAULT now from {self.summary_state.name}."
                )
                self.fault_event.set()
                await self.fault(code=None, report="AT in FAULT state.")
            elif hasattr(self, "evt_crioSummaryState"):
                kwargs = {key: value for key, value in data.items() if key != "id"}
                self.log.debug(f"Sending evt_crioSummaryState with data {kwargs}.")
                await self.evt_crioSummaryState.set_write(**kwargs)
        else:
            await self.call_set_write(data=data)

    async def _handle_command_response(self, data: typing.Any) -> None:
        # Handle command responses.
        sequence_id = data[CommonCommandArgument.SEQUENCE_ID]
        response = data[CommonCommandArgument.ID]
        if sequence_id in self.commands_issued:
            match response:
                case Ack.ACK:
                    self.commands_issued[sequence_id].set_ack()
                case Ack.NOACK:
                    self.commands_issued[sequence_id].set_noack()
                case Ack.SUCCESS:
                    self.commands_issued[sequence_id].set_success()
                    del self.commands_issued[sequence_id]
                case Ack.FAIL:
                    self.commands_issued[sequence_id].set_fail()
                    del self.commands_issued[sequence_id]
                case _:
                    raise RuntimeError(f"Received unexpected {response=}.")
        else:
            self.log.warning(
                f"Received command response for unknown {sequence_id=}. Ignoring."
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
                data_id = data[CommonCommandArgument.ID]
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
        async with asyncio.timeout(WAIT_TIMEOUT):
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
        send_failure = False
        if attr is not None:
            if name.startswith("evt_"):
                self.log.debug(f"Sending {name=} with {kwargs=}")
            try:
                await attr.set_write(**kwargs)
            except Exception:
                send_failure = True
                self.log.exception(f"Failed to send {name=} with {kwargs=}")
            if name.startswith("evt_") and not send_failure:
                self.log.debug(f"Done sending {name=} with {kwargs=}")
        else:
            self.log.error(f"{name=} not found. Ignoring.")
