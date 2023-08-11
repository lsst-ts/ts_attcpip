##########
ts_attcpip
##########

``ts_attcpip`` is an LSST Telescope and Site package that provides common TCP/IP code for interaction with the AT LabVIEW servers.

This code uses ``pre-commit`` to maintain ``black`` formatting and ``flake8`` compliance.
To enable this, run the following commands once (the first removes the previous pre-commit hook)::

    git config --unset-all core.hooksPath
    pre-commit install
