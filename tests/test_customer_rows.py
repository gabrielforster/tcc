from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from collection.db.models import (
    Customer,
    InternalUser,
    Receivable,
    ReceivableStatus,
    Tenant,
    days_overdue,
)
from collection.db.repository import TenantScope


def _tenant(db: Session, slug: str) -> Tenant:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    return tenant


def _scope(db: Session, tenant: Tenant) -> TenantScope:
    """A scope on its own Session over the test transaction: a session serves one tenant."""
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


@pytest.mark.db
def test_two_tenants_may_hold_the_same_external_customer_id(db):
    one, two = _tenant(db, "one"), _tenant(db, "two")
    scopes = [_scope(db, one), _scope(db, two)]
    for scope in scopes:
        scope.add(Customer(external_id="C000123", pseudonym="p"))
        scope.session.flush()

    for scope in scopes:
        assert len(scope.all(Customer, Customer.external_id == "C000123")) == 1


@pytest.mark.db
def test_one_tenant_may_not_hold_an_external_customer_id_twice(db):
    scope = _scope(db, _tenant(db, "one"))
    scope.add(Customer(external_id="C000123", pseudonym="a"))
    scope.add(Customer(external_id="C000123", pseudonym="b"))

    with pytest.raises(IntegrityError):
        scope.session.flush()


@pytest.mark.db
def test_a_receivable_carries_its_own_tenant_id_not_only_its_customers(db):
    one, two = _tenant(db, "one"), _tenant(db, "two")
    scope = _scope(db, one)
    customer = Customer(external_id="C1", pseudonym="p")
    scope.add(customer)
    scope.session.flush()
    receivable = Receivable(
        customer_id=customer.id,
        amount=Decimal("1250.90"),
        due_date=date(2026, 2, 10),
        days_overdue=0,
        status=ReceivableStatus.OPEN,
    )
    scope.add(receivable)
    scope.session.flush()

    assert receivable.tenant_id == one.id

    # The column is the boundary, not the join: the other tenant sees nothing, with no
    # reference to the customer needed to hide it.
    assert _scope(db, two).all(Receivable) == []
    assert [r.id for r in _scope(db, one).all(Receivable)] == [receivable.id]


@pytest.mark.db
def test_a_receivable_cannot_point_at_another_tenants_customer(db):
    one, two = _tenant(db, "one"), _tenant(db, "two")
    theirs = _scope(db, two)
    customer = Customer(external_id="C1", pseudonym="p")
    theirs.add(customer)
    theirs.session.flush()

    ours = _scope(db, one)
    ours.add(
        Receivable(
            customer_id=customer.id,
            amount=Decimal("10.00"),
            due_date=date(2026, 2, 10),
            days_overdue=0,
            status=ReceivableStatus.OPEN,
        )
    )

    with pytest.raises(IntegrityError):
        ours.session.flush()


@pytest.mark.db
def test_days_overdue_matches_the_due_date_at_ingestion(db):
    scope = _scope(db, _tenant(db, "one"))
    customer = Customer(external_id="C1", pseudonym="p")
    scope.add(customer)
    scope.session.flush()
    ingested_on = date(2026, 3, 1)
    due = date(2026, 2, 10)
    receivable = Receivable(
        customer_id=customer.id,
        amount=Decimal("99.99"),
        due_date=due,
        days_overdue=days_overdue(due, ingested_on),
        status=ReceivableStatus.OPEN,
    )
    scope.add(receivable)
    scope.session.flush()
    scope.session.refresh(receivable)

    assert receivable.days_overdue == 19
    assert receivable.amount == Decimal("99.99")


def test_a_receivable_not_yet_due_is_zero_days_overdue():
    assert days_overdue(date(2026, 3, 10), date(2026, 3, 1)) == 0
    assert days_overdue(date(2026, 3, 1), date(2026, 3, 1)) == 0
    assert days_overdue(date(2026, 2, 28), date(2026, 3, 1)) == 1


@pytest.mark.db
def test_an_internal_user_email_is_unique_within_a_tenant_only(db):
    one, two = _tenant(db, "one"), _tenant(db, "two")
    for tenant in (one, two):
        scope = _scope(db, tenant)
        scope.add(InternalUser(email="a@x.com", password_hash="h", role="operator"))
        scope.session.flush()

    scope = _scope(db, one)
    scope.add(InternalUser(email="a@x.com", password_hash="h", role="operator"))
    with pytest.raises(IntegrityError):
        scope.session.flush()


@pytest.mark.db
def test_an_internal_user_is_active_by_default(db):
    scope = _scope(db, _tenant(db, "one"))
    user = InternalUser(email="a@x.com", password_hash="h", role="operator")
    scope.add(user)
    scope.session.flush()
    scope.session.refresh(user)

    assert user.active is True
