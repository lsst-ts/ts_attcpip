.. py:currentmodule:: lsst.ts.attcpip

.. _lsst.ts.attcpip.version_history:

###############
Version History
###############

v0.1.2
======

* Add support for start, disable, enable and standby commands.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.1
======

* Add host and ports parameters to AtSimulator.
* Make AtTcpipCsc a Configurable CSC.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.0
======

First release of the AT TCP/IP common code package.

* A CSC infrastructure class.
* A simulator infrastructure.
* Common enums.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils
