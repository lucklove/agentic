from __future__ import annotations

import logfire


def pytest_configure() -> None:
    logfire.configure(send_to_logfire=False)
