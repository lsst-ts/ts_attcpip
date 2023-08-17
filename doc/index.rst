.. py:currentmodule:: lsst.ts.attcpip

.. _lsst.ts.attcpip:

###############
lsst.ts.attcpip
###############

This package contains code common to Python projects that communicate with the AuxTel LabVIEW TCP/IP servers.
Currently the only projects that use this common code are ATMCS and ATPneumatics.

The code is split up into two parts.
The first part is common CSC code, which sets up the TCP/IP clients and provides methods for sending commands and for receiving command replies, events and telemetry.
The second part is a simulator infrastructure for testing purposes that provides methods for recieving commands and for sending command replies, events and telemetry.
Interlaced with all this are common enums and a handler class for tracking issued commands.

.. _lsst.ts.attcpip-contributing:

Contributing
============

``lsst.ts.attcpip`` is developed at https://github.com/lsst-ts/ts_attcpip.
You can find Jira issues for this module under the `ts_attcpip <https://jira.lsstcorp.org/issues/?jql=project%20%3D%20DM%20AND%20component%20%3D%20ts_attcpip>`_ component.

Python API reference
====================

.. automodapi:: lsst.ts.attcpip
   :no-main-docstr:

Version History
===============

.. toctree::
    version_history
    :maxdepth: 2
