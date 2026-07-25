"""In-app notification bell."""

from __future__ import annotations

from datetime import datetime

from models import Notification, db


def create_notification(
    user_id: int,
    message: str,
    *,
    kind: str,
    href: str | None = None,
) -> Notification:
    note = Notification(
        user_id=user_id,
        kind=kind,
        message=message,
        href=href,
    )
    db.session.add(note)
    return note


def unread_count(user_id: int) -> int:
    return Notification.query.filter_by(user_id=user_id, read_at=None).count()


def recent_notifications(user_id: int, limit: int = 8) -> list[Notification]:
    return (
        Notification.query.filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )


def mark_all_read(user_id: int) -> int:
    now = datetime.utcnow()
    pending = Notification.query.filter_by(user_id=user_id, read_at=None).all()
    for note in pending:
        note.read_at = now
    return len(pending)


def mark_read(notification_id: int, user_id: int) -> bool:
    note = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if note is None:
        return False
    if note.read_at is None:
        note.read_at = datetime.utcnow()
    return True
