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

__all__ = ["CommandIssued"]

import asyncio

from lsst.ts import utils


class CommandIssued:
    """Track the progress of a low-level controller command.

    Parameters
    ----------
    timestamp : `float`
        The TAI unix timestamp [sec] at which the command was issued.
    name : `str`
        The name of the command.

    Attributes
    ----------
    ack_timestamp : `float`
        The TAI unix timestamp [sec] at which an ACK or NOACK was received.
    done_timestamp : `float`
        The TAI unix timestamp [sec] at which a SUCCESS or FAIL was received.
    done : `asyncio.Future`
        Future which ends as follows:

        * result = None when the command has ended successfully.
        * salobj.ExpectedError if the command has ended in an error.
    """

    def __init__(self, name: str) -> None:
        self.timestamp = utils.current_tai()
        self.name = name
        self.ack_timestamp: float | None = None
        self.done_timestamp: float | None = None
        self.done: asyncio.Future = asyncio.Future()

    def set_ack(self) -> None:
        """Report a command as acknowledged, meaning accepted to be
        executed."""
        self.ack_timestamp = utils.current_tai()

    def set_noack(self) -> None:
        """Report a command as not acknowledged.

        This means there was an issue with the command format (e.g. the command
        name or one of more of its parameters were wrong).
        """
        self.ack_timestamp = utils.current_tai()
        if not self.done.done():
            self.done.set_exception(
                RuntimeError(f"Command {self.name} was not acknowledged.")
            )

    def set_success(self) -> None:
        """Report a command as successfully executed."""
        self.done_timestamp = utils.current_tai()
        if not self.done.done():
            self.done.set_result(None)

    def set_fail(self, reason: str) -> None:
        """Report a command as failed during execution.

        Parameters
        ----------
        reason : `str`
            The reason why the command failed.
        """
        self.done_timestamp = utils.current_tai()
        if not self.done.done():
            self.done.set_exception(
                RuntimeError(f"Command {self.name} failed with {reason=!r}.")
            )
