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

__all__ = ["Ack", "CommonCommand", "CommonCommandArgument", "SimulatorState"]

import enum


class Ack(str, enum.Enum):
    ACK = "ack"
    FAIL = "fail"
    NOACK = "noack"
    SUCCESS = "success"


class CommonCommand(str, enum.Enum):
    """Enum containing all common command names."""

    DISABLE = "cmd_disable"
    ENABLE = "cmd_enable"
    STANDBY = "cmd_standby"
    START = "cmd_start"


class CommonCommandArgument(str, enum.Enum):
    """Enum containing all common command arguments."""

    ID = "id"
    SEQUENCE_ID = "sequence_id"
    VALUE = "value"


class SimulatorState(enum.IntEnum):
    """Enum containing all simulatr states."""

    DISABLED = enum.auto()
    ENABLED = enum.auto()
    STANDBY = enum.auto()
