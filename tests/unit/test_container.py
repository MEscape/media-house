import pytest

from media_house.core.modules import ApplicationModule, Container, DependencyError, install_modules


class Repo: ...


class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo


class A:
    def __init__(self, b: "B") -> None:
        self.b = b


class B:
    def __init__(self, a: A) -> None:
        self.a = a


def test_factories_are_lazy_singletons() -> None:
    container, calls = Container(), []

    def build(_: Container) -> Repo:
        calls.append(1)
        return Repo()

    container.register_factory(Repo, build)
    assert calls == []
    assert container.resolve(Repo) is container.resolve(Repo)
    assert calls == [1]


def test_factories_can_resolve_other_services() -> None:
    container = Container()
    container.register_factory(Repo, lambda _: Repo())
    container.register_factory(Service, lambda c: Service(c.resolve(Repo)))
    assert container.resolve(Service).repo is container.resolve(Repo)


def test_instances_and_duplicate_registration() -> None:
    container, repo = Container(), Repo()
    container.register_instance(Repo, repo)
    assert container.resolve(Repo) is repo
    with pytest.raises(DependencyError, match="already registered"):
        container.register_factory(Repo, lambda _: Repo())


def test_missing_service_has_clear_error() -> None:
    with pytest.raises(DependencyError, match="No service registered for Repo"):
        Container().resolve(Repo)


def test_cycles_are_detected_with_the_chain() -> None:
    container = Container()
    container.register_factory(A, lambda c: A(c.resolve(B)))
    container.register_factory(B, lambda c: B(c.resolve(A)))
    with pytest.raises(DependencyError, match=r"A -> B -> A"):
        container.resolve(A)


def test_resolution_state_is_clean_after_a_failed_resolve() -> None:
    container = Container()
    container.register_factory(Service, lambda c: Service(c.resolve(Repo)))
    with pytest.raises(DependencyError):
        container.resolve(Service)
    container.register_instance(Repo, Repo())
    assert isinstance(container.resolve(Service), Service)


def test_collections_gather_contributions_from_many_modules() -> None:
    container = Container()
    container.add_to_collection(Repo, lambda _: Repo())
    container.add_to_collection(Repo, lambda _: Repo())
    items = container.resolve_all(Repo)
    assert len(items) == 2
    assert container.resolve_all(Repo)[0] is items[0]
    with pytest.raises(DependencyError, match="already resolved"):
        container.add_to_collection(Repo, lambda _: Repo())
    assert container.resolve_all(Service) == []


def test_installing_the_same_module_twice_is_an_error() -> None:
    class Mod:
        name = "dup"

        def register(self, container: Container) -> None: ...

    module: ApplicationModule = Mod()
    with pytest.raises(DependencyError, match="twice"):
        install_modules([module, Mod()], Container())
