"""Team module. US-004 -- manage store assignments.

Grants and revokes a Manager's (or Admin's, though who may act on an Admin's
own assignment is an undecided gap -- see
``tests/test_us004_manage_store_assignments.py::TestGapAdminAssignmentAuthorityIsUndefined``)
row in ``user_store_assignments``. Identity and session management
(sign-in, sessions, scope *resolution*) stay in ``src.access`` -- this module
only manages *who holds* an assignment, never how a session is authenticated
or how scope is computed from one.
"""
