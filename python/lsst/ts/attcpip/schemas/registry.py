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

__all__ = ["load_schemas", "registry", "JSON_SCHEMA_EXTENSION"]

import json
import pathlib
import typing

JSON_SCHEMA_EXTENSION = ".json"

registry: dict[str, typing.Any] = dict()


def load_schemas(schema_dir: pathlib.Path | None) -> None:
    if schema_dir is None:
        raise RuntimeError(f"{schema_dir=} cannot be None.")
    for file in list(schema_dir.glob(f"*{JSON_SCHEMA_EXTENSION}")):
        schema_name = file.name.replace(JSON_SCHEMA_EXTENSION, "")
        with open(file, "r") as f:
            schema = json.load(f)
            registry[schema_name] = schema
