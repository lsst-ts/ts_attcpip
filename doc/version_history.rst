.. py:currentmodule:: lsst.ts.attcpip

.. _lsst.ts.attcpip.version_history:

###############
Version History
###############

v0.1.12
=======

* Make simulator respond to unexpected state transition commands.
* Make CSC handle unexpected AT server states.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.11
=======

* Increase command done timeout.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.10
=======

* Handle an IncompleteReadError used in unit tests.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.9
======

* Add timeouts and error handling to waiting for commands to be done.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.8
======

* Fix the conda recipe.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.7
======

* Update the version of ts-conda-build to 0.4 in the conda recipe.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.6
======

* Add support for the errorCode event.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.5
======

* Improve handling of data messages with incorrect parameters.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.4
======

* Improve handling of FAULT state.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

v0.1.3
======

* Add sending a summaryState event for the STANDBY, DISABLED, ENABLED and FAULT states.
* Stop the telemetry client and task when going to STANDBY state.
* Make sure that events emitted by the server get emitted by the CSC.
* Support simulation mode 0.

Requires:

* ts_salobj
* ts_tcpip >= 2
* ts_utils

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
