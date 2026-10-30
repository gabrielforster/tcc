import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from collection.db.models.tenant import InvalidTransition, Tenant, TenantState, advance


def _tenant(state: TenantState = TenantState.PROVISIONED) -> Tenant:
    tenant = Tenant(name="Acme", slug="acme")
    tenant.state = state
    return tenant


def test_a_new_tenant_is_provisioned_and_not_servable():
    tenant = Tenant(name="Acme", slug="acme")

    assert tenant.state is TenantState.PROVISIONED
    assert tenant.servable is False
    assert tenant.activated_at is None
    assert tenant.tenant_key


def test_tenant_keys_are_not_shared():
    assert Tenant(name="A", slug="a").tenant_key != Tenant(name="B", slug="b").tenant_key


def test_the_ladder_advances_in_order():
    tenant = _tenant()
    for rung in (TenantState.INGESTING, TenantState.READY, TenantState.ACTIVE):
        advance(tenant, rung)
        assert tenant.state is rung


def test_only_active_is_servable():
    for state in TenantState:
        assert _tenant(state).servable is (state is TenantState.ACTIVE)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (TenantState.PROVISIONED, TenantState.ACTIVE),
        (TenantState.PROVISIONED, TenantState.READY),
        (TenantState.INGESTING, TenantState.ACTIVE),
        (TenantState.READY, TenantState.PROVISIONED),
        (TenantState.ACTIVE, TenantState.READY),
        (TenantState.READY, TenantState.READY),
        (TenantState.SUSPENDED, TenantState.ACTIVE),
        (TenantState.SUSPENDED, TenantState.SUSPENDED),
    ],
)
def test_skipping_a_rung_raises_invalid_transition(start, target):
    tenant = _tenant(start)

    with pytest.raises(InvalidTransition):
        advance(tenant, target)

    assert tenant.state is start


@pytest.mark.parametrize(
    "start", [TenantState.PROVISIONED, TenantState.INGESTING, TenantState.READY, TenantState.ACTIVE]
)
def test_any_live_state_may_be_suspended(start):
    tenant = _tenant(start)

    advance(tenant, TenantState.SUSPENDED)

    assert tenant.state is TenantState.SUSPENDED
    assert tenant.servable is False


def test_suspended_returns_to_ready_and_must_be_reactivated():
    tenant = _tenant(TenantState.ACTIVE)
    advance(tenant, TenantState.SUSPENDED)

    advance(tenant, TenantState.READY)

    assert tenant.state is TenantState.READY
    assert tenant.servable is False


def test_activating_sets_activated_at():
    tenant = _tenant(TenantState.READY)
    assert tenant.activated_at is None

    advance(tenant, TenantState.ACTIVE)

    assert tenant.activated_at is not None
    assert tenant.activated_at.tzinfo is not None


def test_a_failed_transition_does_not_stamp_activated_at():
    tenant = _tenant(TenantState.PROVISIONED)

    with pytest.raises(InvalidTransition):
        advance(tenant, TenantState.ACTIVE)

    assert tenant.activated_at is None


@pytest.mark.db
def test_a_tenant_persists_with_its_defaults_and_slug_and_key_are_unique(db):
    tenant = Tenant(name="Acme", slug="acme")
    db.add(tenant)
    db.flush()

    stored = db.scalars(select(Tenant).where(Tenant.slug == "acme")).one()
    assert stored.id is not None
    assert stored.state is TenantState.PROVISIONED
    assert stored.created_at is not None

    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(Tenant(name="Other", slug="acme"))
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(Tenant(name="Other", slug="other", tenant_key=stored.tenant_key))
        db.flush()
