Version 24 Feature Releases
===========================

We release new features in these releases of HTCondor. The details of each
version are described below.

Version 24.1.0
--------------

Release Notes:

.. HTCondor version 24.1.0 released on Month Date, 2024.

- HTCondor version 24.1.0 not yet released.

- This version includes all the updates from :ref:`lts-version-history-2401`.

New Features:

- Added ``get`` to the ``htcondor credential`` noun, which prints the contents
  of a stored OAuth2 credential.
  :jira:`2626`

- Added :meth:`htcondor2.set_ready_state` for those brave few writing daemons
  in the Python bindings.
  :jira:`2615`

Bugs Fixed:

- If HTCondor detects that an invalid checkpoint has been downloaded for a
  self-checkpoint jobs using third-party storage, that checkpoint is now
  marked for deletion and the job rescheduled.
  :jira:`1258`

