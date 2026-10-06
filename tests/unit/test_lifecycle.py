from media_house.bootstrap.lifecycle import ShutdownStack


def test_shutdown_runs_lifo_and_continues_after_a_failure() -> None:
    order: list[str] = []
    stack = ShutdownStack()
    stack.push("first", lambda: order.append("first"))

    def broken() -> None:
        raise RuntimeError("cleanup bug")

    stack.push("broken", broken)
    stack.push("last", lambda: order.append("last"))
    stack.close()
    assert order == ["last", "first"]
    stack.close()  # nothing left to run
    assert order == ["last", "first"]
