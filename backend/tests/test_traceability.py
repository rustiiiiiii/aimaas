from datetime import timedelta, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth.models import User
from ..schemas.traceability import ChangeReviewSchema
from ..traceability.crud import decline_change_request, review_changes, get_pending_change_requests
from ..traceability.enum import ChangeType, ContentType, EditableObjectType, ChangeStatus, \
    ReviewResult
from ..traceability.models import ChangeRequest, Change, ChangeAttrType, ChangeValueStr


@pytest.mark.parametrize(['content_type', 'change_type'], [
    (ContentType.ENTITY, ChangeType.CREATE),
    (ContentType.ENTITY, ChangeType.UPDATE),
    (ContentType.ENTITY, ChangeType.DELETE),
    (ContentType.SCHEMA, ChangeType.CREATE),
    (ContentType.SCHEMA, ChangeType.UPDATE),
    (ContentType.SCHEMA, ChangeType.DELETE),
])
def test_decline_change_request(dbsession: Session, content_type, change_type):
    user = dbsession.execute(select(User)).scalar()
    change_request = ChangeRequest(
        created_at=datetime.now(),
        created_by=user,
        object_type=EditableObjectType[content_type.name],
        change_type=change_type
    )
    if change_type != ChangeType.CREATE:
        change_request.object_id = 1
    v = ChangeValueStr()
    dbsession.add_all([change_request, v])
    dbsession.flush()
    change = Change(
        change_request=change_request,
        value_id=v.id,
        change_type=change_type,
        content_type=content_type,
        field_name='test',
        data_type=ChangeAttrType.STR
    )
    if change_type != ChangeType.CREATE:
        change.object_id = 1
    dbsession.add(change)
    dbsession.commit()

    decline_change_request(
        db=dbsession, 
        change_request=change_request,
        reviewed_by=user,
        comment='test'
    )

    assert change_request.status == ChangeStatus.DECLINED
    assert change_request.comment == 'test'
    assert change_request.reviewed_at >= change_request.created_at
    assert change_request.reviewed_by == user


def make_change_for_review(db: Session) -> ChangeRequest:
    user = db.execute(select(User)).scalar()
    change_request = ChangeRequest(
        created_at=datetime.utcnow(), 
        created_by=user,
        object_type=EditableObjectType.ENTITY,
        object_id=1,
        change_type=ChangeType.UPDATE
    )
    v = ChangeValueStr(new_value='test')
    db.add_all([change_request, v])
    db.flush()
    change = Change(
        change_request=change_request,
        value_id=v.id,
        change_type=ChangeType.UPDATE,
        object_id=1,
        content_type=ContentType.ENTITY,
        field_name='name',
        data_type=ChangeAttrType.STR
    )
    db.add(change)
    db.commit()
    return change_request


def test_review_changes(dbsession: Session):
    user = dbsession.execute(select(User)).scalar()
    change_request = make_change_for_review(db=dbsession)
    
    review = ChangeReviewSchema(
        result=ReviewResult.APPROVE,
        comment='test'
    )
    # approve
    review_changes(
        db=dbsession, 
        change_request_id=change_request.id,
        review=review,
        reviewed_by=user
    )
    assert change_request.status == ChangeStatus.APPROVED
    assert change_request.comment == 'test'
    assert change_request.reviewed_at >= change_request.created_at
    assert change_request.reviewed_by == user

    change_request.status = ChangeStatus.PENDING
    dbsession.flush()
    # decline
    review.result = ReviewResult.DECLINE
    review.comment = 'test2'
    review_changes(
        db=dbsession, 
        change_request_id=change_request.id,
        review=review,
        reviewed_by=user
    )

    assert change_request.status == ChangeStatus.DECLINED
    assert change_request.comment == 'test2'
    assert change_request.reviewed_at >= change_request.created_at
    assert change_request.reviewed_by == user


def test_get_pending_change_requests(dbsession: Session):
    # 10 requests, 9 UPD, 1 CREATE
    # 10 requests, 9 UPD, 1 CREATE
    schema_requests = dbsession.query(ChangeRequest)\
        .filter(ChangeRequest.object_type == EditableObjectType.SCHEMA)
    entity_requests = dbsession.query(ChangeRequest)\
        .filter(ChangeRequest.object_type == EditableObjectType.ENTITY)

    # limit 10 offset 0
    requests = get_pending_change_requests(dbsession)
    assert len(requests) == 10
    assert {i.id for i in schema_requests} == {i.id for i in requests}

    # limit 10, offset 10
    requests = get_pending_change_requests(dbsession, offset=10)
    assert len(requests) == 10
    assert {i.id for i in entity_requests} == {i.id for i in requests}

    # limit 1, offset 0
    requests = get_pending_change_requests(dbsession, limit=1)
    assert requests[0] == schema_requests[-1]

    # limit 1, offset 19
    requests = get_pending_change_requests(dbsession, limit=1, offset=19)
    assert requests[0] == entity_requests[0]

    # limit 20, all types
    requests = get_pending_change_requests(dbsession, limit=20)
    assert requests == schema_requests[::-1] + entity_requests[::-1]

    # no limit, all types
    requests = get_pending_change_requests(dbsession, all=True)
    assert requests == schema_requests[::-1] + entity_requests[::-1]

    # limit 20, offset 20, all types
    requests = get_pending_change_requests(dbsession, limit=20, offset=20)
    assert requests == []

    # limit 20, only schemas
    requests = get_pending_change_requests(dbsession, obj_type=ContentType.SCHEMA, limit=20)
    assert requests == schema_requests[::-1]

    # limit 1, offset 1, only schemas
    requests = get_pending_change_requests(dbsession, obj_type=ContentType.SCHEMA, limit=1, offset=1)
    assert requests == [schema_requests[-2]]

    # limit 20, only entities
    requests = get_pending_change_requests(dbsession, obj_type=ContentType.ENTITY, limit=20)
    assert requests == entity_requests[::-1]

    # limit 1, offset 1, only entities
    requests = get_pending_change_requests(dbsession, obj_type=ContentType.ENTITY, limit=1, offset=1)
    assert requests == [entity_requests[-2]]

    dbsession.execute(update(ChangeRequest).values(status=ChangeStatus.APPROVED))
    dbsession.commit()

    # no limit, all types
    requests = get_pending_change_requests(dbsession, all=True)
    assert requests == []