"""Storage-maintenance startup regression.

The maintenance thread is easy to miss in route/unit tests because it starts
after the HTTP server. A wrong module name here silently disables VACUUM.
"""

from __future__ import annotations

import unittest
from unittest import mock
import threading

import server


class StorageMaintenanceThreadTests(unittest.TestCase):
    def test_maintenance_uses_the_store_module_alias(self):
        class ImmediateThread:
            def __init__(self, target, name, daemon):
                self.target = target

            def start(self):
                self.target()

        with (
            mock.patch.object(server.time, "sleep", side_effect=[None, StopIteration]),
            mock.patch.object(server, "_run_for_active_tenants", return_value=0) as run,
            mock.patch.object(server.threading, "Thread", ImmediateThread),
        ):
            with self.assertRaises(StopIteration):
                server.start_storage_maintenance()
        run.assert_called_once_with(server.store.maintain_storage)


if __name__ == "__main__":
    unittest.main()
