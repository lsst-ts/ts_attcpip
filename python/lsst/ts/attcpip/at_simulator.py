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

from __future__ import annotations

__all__ = ["AtSimulator"]

import abc
import logging
import pathlib
import types
import typing

import jsonschema
import numpy as np
from lsst.ts import tcpip
from lsst.ts.xml import sal_enums

from .at_server_simulator import AtServerSimulator
from .enums import Ack, CommonCommand, CommonCommandArgument, CommonEvent
from .schemas import load_schemas, registry

CMD_ITEMS_TO_IGNORE = frozenset({CommonCommandArgument.ID, CommonCommandArgument.VALUE})


class AtSimulator:
    """Common code for simulating an AT system.

    Attributes
    ----------
    host : `str`
        The simulator host.
    cmd_evt_port : `int`
        The command and events port.
    telemetry_port : `int`
        The telemetry port.
    """

    def __init__(self, host: str, cmd_evt_port: int, telemetry_port: int) -> None:
        self.log = logging.getLogger(type(self).__name__)
        self.cmd_evt_server = AtServerSimulator(
            host=host,
            port=cmd_evt_port,
            log=self.log,
            dispatch_callback=self.cmd_evt_dispatch_callback,
            connect_callback=self.cmd_evt_connect_callback,
            name="CmdEvtServer",
        )
        self.telemetry_server = AtServerSimulator(
            host=tcpip.LOCALHOST_IPV4,
            port=telemetry_port,
            log=self.log,
            dispatch_callback=self.telemetry_dispatch_callback,
            connect_callback=self.telemetry_connect_callback,
            name="TelemetryServer",
        )

        # Keep track of the sequence_id as commands come dripping in. The
        # sequence ID should raise monotonally without gaps. If a gap is seen,
        # a NOACK should be returned.
        self.last_sequence_id: int = 0

        # The simulator state. The lifecycle of the simulator state is:
        # - start: STANDBY -> DISABLED
        # - enable: DISABLED -> ENABLED
        # - disable: ENABLED -> DISABLED
        # - standby: DISABLED -> STANDBY
        self.simulator_state = sal_enums.State.STANDBY

        # Dict of command: function.
        self.dispatch_dict: dict[str, typing.Callable] = {
            CommonCommand.DISABLE: self.disable,
            CommonCommand.ENABLE: self.enable,
            CommonCommand.STANDBY: self.standby,
            CommonCommand.START: self.disable,
        }

        self.load_schemas()

    def load_schemas(self) -> None:
        """Load the JSON schemas needed to validate the incoming and outgoing
        JSON messages.
        """
        schema_dir = pathlib.Path(__file__).parent / "schemas"
        load_schemas(schema_dir=schema_dir)

    @abc.abstractmethod
    async def cmd_evt_connect_callback(self, server: tcpip.OneClientServer) -> None:
        """Callback to call when a command/event client client connects or
        disconnects.

        Parameters
        ----------
        server : `tcpip.OneClientServer`
            The server to which the client connected.
        """
        raise NotImplementedError

    async def cmd_evt_dispatch_callback(self, data: typing.Any) -> None:
        """Asynchronous function to call when data is read and dispatched.

        The received data is validated and then dispatched to the
        corresponding function as listed in ``self.dispatch_dict``. If the data
        is invalid then a ``noack`` response is sent back to the client and
        the data is not dispatched.

        Parameters
        ----------
        data : `dict`[`str`, `typing.Any`]
            The data sent to the server.
        """
        data_ok = await self.verify_data(data=data)
        if not data_ok:
            self.log.warning(f"Received incorrect {data=}.")
            await self.write_noack_response(
                sequence_id=data[CommonCommandArgument.SEQUENCE_ID]
            )
            return

        await self.write_ack_response(
            sequence_id=data[CommonCommandArgument.SEQUENCE_ID]
        )

        cmd = data[CommonCommandArgument.ID]
        func = self.dispatch_dict[cmd]
        kwargs = {
            key: value for key, value in data.items() if key not in CMD_ITEMS_TO_IGNORE
        }
        await func(**kwargs)

    async def telemetry_connect_callback(self, server: tcpip.OneClientServer) -> None:
        """Callback function for when a telemetry client connects or
        disconnects.

        Parameters
        ----------
        server : `tcpip.OneClientServer`
            The server to which the client connected.
        """
        pass

    async def telemetry_dispatch_callback(self, data: typing.Any) -> None:
        """Asynchronous function to call when data are read and dispatched.

        The received data is ignored since the telemetry server is only
        supposed to send data and not to receive any.

        Parameters
        ----------
        data : `dict`[`str`, `typing.Any`]
            The data sent to the server.
        """
        pass

    async def verify_data(self, data: dict[str, typing.Any]) -> bool:
        """Verify the format and values of the data.

        The format of the data is described at
        https://github.com/lsst-ts/ts_labview_tcp_json
        as well as in the JSON schemas in the schemas directory.

        Parameters
        ----------
        data : `dict` of `any`
            The dict to be verified.

        Returns
        -------
        `bool`:
            Whether the data follows the correct format and has the correct
            contents or not.
        """
        if (
            CommonCommandArgument.ID not in data
            or CommonCommandArgument.SEQUENCE_ID not in data
        ):
            self.log.error(f"Received invalid {data=}. Ignoring.")
            return False
        payload_id = data[CommonCommandArgument.ID].replace("cmd_", "command_")
        if payload_id not in registry:
            self.log.error(f"Unknown command in {data=}.")
            return False

        sequence_id = data[CommonCommandArgument.SEQUENCE_ID]
        if sequence_id - self.last_sequence_id != 1:
            return False
        self.last_sequence_id = sequence_id

        json_schema = registry[payload_id]
        try:
            jsonschema.validate(data, json_schema)
        except jsonschema.ValidationError as e:
            self.log.exception("Validation failed.", e)
            return False
        return True

    async def disable(self, *, sequence_id: int) -> None:
        """Switch to sal_enums.State.DISABLED."""
        self.simulator_state = sal_enums.State.DISABLED
        await self.write_success_response(sequence_id=sequence_id)
        await self._write_evt(
            evt_id=CommonEvent.SUMMARY_STATE, summaryState=sal_enums.State.DISABLED
        )

    async def enable(self, *, sequence_id: int) -> None:
        """Switch to sal_enums.State.ENABLED."""
        self.simulator_state = sal_enums.State.ENABLED
        await self.write_success_response(sequence_id=sequence_id)
        await self._write_evt(
            evt_id=CommonEvent.SUMMARY_STATE, summaryState=sal_enums.State.ENABLED
        )

    async def standby(self, *, sequence_id: int) -> None:
        """Switch to sal_enums.State.STANDBY."""
        self.simulator_state = sal_enums.State.STANDBY
        await self.write_success_response(sequence_id=sequence_id)
        await self._write_evt(
            evt_id=CommonEvent.SUMMARY_STATE, summaryState=sal_enums.State.STANDBY
        )

    async def _write_command_response(self, response: str, sequence_id: int) -> None:
        """Generic method to write a command response.

        Parameters
        ----------
        response : `str`
            The response to write. Acceptable responses are defined in the
            ``Ack`` enum. The value of response is not checked.
        sequence_id : `int`
            The command sequence id.
        """
        data = {
            CommonCommandArgument.ID: response,
            CommonCommandArgument.SEQUENCE_ID: sequence_id,
        }
        await self.cmd_evt_server.write_json(data=data)

    async def write_ack_response(self, sequence_id: int) -> None:
        """Write an ``ACK`` response.

        Parameters
        ----------
        sequence_id : `int`
            The command sequence id.
        """
        await self._write_command_response(Ack.ACK, sequence_id)

    async def write_fail_response(self, sequence_id: int) -> None:
        """Write a ``FAIL`` response.

        Parameters
        ----------
        sequence_id : `int`
            The command sequence id.
        """
        await self._write_command_response(Ack.FAIL, sequence_id)

    async def write_noack_response(self, sequence_id: int) -> None:
        """Write a ``NOACK`` response.

        Parameters
        ----------
        sequence_id : `int`
            The command sequence id.
        """
        await self._write_command_response(Ack.NOACK, sequence_id)

    async def write_success_response(self, sequence_id: int) -> None:
        """Write a ``SUCCESS`` response.

        Parameters
        ----------
        sequence_id : `int`
            The command sequence id.
        """
        await self._write_command_response(Ack.SUCCESS, sequence_id)

    async def _write_evt(self, evt_id: str, **kwargs: typing.Any) -> None:
        """Write an event message.

        Parameters
        ----------
        evt_id : `str`
            The name of the event, for instance ``evt_atMountState``.
        kwargs : `typing.Any`
            The data to include in the event message.
        """
        data: dict[str, typing.Any] = {CommonCommandArgument.ID: evt_id, **kwargs}
        await self.cmd_evt_server.write_json(data=data)

    async def _write_telemetry(
        self, tel_id: str, data: typing.Any, timestamp: np.float64 | None = None
    ) -> None:
        """Write a telemetry message.

        Parameters
        ----------
        tel_id : `str`
            The name of the telemetry.
        data : `typing.Any`
            The data to include in the telemetry message.
        timestamp : `np.float64` | None
            The timestamp of the telemetry. Defaults to None.
        """
        if timestamp is not None:
            data.cRIO_timestamp = timestamp.item()
        data.id = tel_id
        data_dict = vars(data)
        await self.telemetry_server.write_json(data=data_dict)

    async def __aenter__(self) -> AtSimulator:
        return self

    async def __aexit__(
        self,
        type: typing.Type[BaseException],
        value: BaseException,
        traceback: types.TracebackType,
    ) -> None:
        await self.cmd_evt_server.close()
        await self.telemetry_server.close()
