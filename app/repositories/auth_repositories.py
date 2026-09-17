"""Durable repositories for Stage 24 identity and audit state.

Backends mirror the rest of ThreadLine: ``SQLiteAuthRepository`` over the
shared ``SQLiteSourceStore`` (schema v5 tables) and ``InMemoryAuthRepository``
for offline/unit tests.  Both implement ``AbstractAuthRepository`` with
identical semantics:

- emails are normalized (strip + lowercase) before uniqueness checks;
- organisation slugs are globally unique; names are not;
- (organisation_id, user_id) membership is unique; removal is a REMOVED
  tombstone (never a silent delete) so access history survives;
- only token *hashes* are persisted — never bearer tokens;
- revocation timestamps come from the repository clock, never the caller.

No passwords, tokens, or password hashes are ever written to the audit log;
``record_event`` persists only the caller-supplied operational detail dict.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Optional

from app.auth.models import (
    AuthSession,
    Organisation,
    OrganisationMember,
    Role,
    SecurityEvent,
    User,
    normalize_email,
)
from app.persistence.sqlite_store import SQLiteSourceStore


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class DuplicateUserError(ValueError):
    """Raised when the normalized email is already registered."""


class DuplicateOrganisationError(ValueError):
    """Raised when the organisation slug is already taken."""


class AbstractAuthRepository(ABC):
    # -- users ---------------------------------------------------------
    @abstractmethod
    def create_user(self, user: User) -> User: ...
    @abstractmethod
    def get_user_by_id(self, user_id: str) -> Optional[User]: ...
    @abstractmethod
    def get_user_by_email(self, email: str) -> Optional[User]: ...
    @abstractmethod
    def update_user(self, user: User) -> User: ...
    @abstractmethod
    def delete_user(self, user_id: str) -> None: ...
    @abstractmethod
    def user_count(self) -> int: ...

    # -- organisations --------------------------------------------------
    @abstractmethod
    def create_organisation(self, organisation: Organisation) -> Organisation: ...
    @abstractmethod
    def get_organisation(self, organisation_id: str) -> Optional[Organisation]: ...
    @abstractmethod
    def get_organisation_by_slug(self, slug: str) -> Optional[Organisation]: ...
    @abstractmethod
    def update_organisation(self, organisation: Organisation) -> Organisation: ...
    @abstractmethod
    def delete_organisation(self, organisation_id: str) -> None: ...
    @abstractmethod
    def list_organisation_ids(self, *, active_only: bool = True) -> list[str]:
        """Return organisation IDs for proactive scheduler discovery (Stage 34).

        Sorted deterministically.  IDs only — the scheduler needs scope
        keys, never full rows.
        """
        ...

    # -- memberships ----------------------------------------------------
    @abstractmethod
    def add_member(self, member: OrganisationMember) -> OrganisationMember: ...
    @abstractmethod
    def get_membership(self, organisation_id: str, user_id: str) -> Optional[OrganisationMember]: ...
    @abstractmethod
    def list_memberships_by_user(self, user_id: str, *, active_only: bool = True) -> list[OrganisationMember]: ...
    @abstractmethod
    def list_memberships_by_org(self, organisation_id: str, *, active_only: bool = True) -> list[OrganisationMember]: ...
    @abstractmethod
    def set_member_role(self, organisation_id: str, user_id: str, role: Role) -> OrganisationMember: ...
    @abstractmethod
    def remove_member(self, organisation_id: str, user_id: str) -> OrganisationMember: ...

    # -- sessions --------------------------------------------------------
    @abstractmethod
    def create_session(self, session: AuthSession) -> AuthSession: ...
    @abstractmethod
    def get_session_by_token_hash(self, token_hash: str) -> Optional[AuthSession]: ...
    @abstractmethod
    def revoke_session(self, session_id: str) -> None: ...
    @abstractmethod
    def revoke_sessions_for_user(self, user_id: str) -> int: ...

    # -- login attempts (brute-force protection) --------------------------
    @abstractmethod
    def record_login_attempt(self, email: str, *, success: bool) -> None: ...
    @abstractmethod
    def count_recent_failures(self, email: str, since: datetime) -> int: ...

    # -- audit -------------------------------------------------------------
    @abstractmethod
    def record_event(self, event: SecurityEvent) -> SecurityEvent: ...
    @abstractmethod
    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[SecurityEvent]: ...


class InMemoryAuthRepository(AbstractAuthRepository):
    """Offline auth repository with the same rules as the SQLite backend."""

    def __init__(self, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._lock = RLock()
        self._clock = clock or _default_clock
        self._users: dict[str, User] = {}
        self._email_index: dict[str, str] = {}
        self._orgs: dict[str, Organisation] = {}
        self._slug_index: dict[str, str] = {}
        self._members: dict[tuple[str, str], OrganisationMember] = {}
        self._sessions: dict[str, AuthSession] = {}
        self._sessions_by_token: dict[str, str] = {}
        self._attempts: list[tuple[str, datetime, bool]] = []
        self._events: list[SecurityEvent] = []

    # -- users ---------------------------------------------------------
    def create_user(self, user: User) -> User:
        with self._lock:
            email = normalize_email(user.email)
            if email in self._email_index:
                raise DuplicateUserError(f"email '{email}' is already registered")
            stored = user.model_copy(update={"email": email})
            self._users[stored.user_id] = stored
            self._email_index[email] = stored.user_id
            return stored

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._lock:
            return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            user_id = self._email_index.get(normalize_email(email))
            return self._users.get(user_id) if user_id else None

    def update_user(self, user: User) -> User:
        with self._lock:
            if user.user_id not in self._users:
                raise KeyError(user.user_id)
            self._users[user.user_id] = user
            self._email_index[normalize_email(user.email)] = user.user_id
            return user

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            user = self._users.pop(user_id, None)
            if user is None:
                return
            self._email_index.pop(normalize_email(user.email), None)
            for key in [k for k in self._members if k[1] == user_id]:
                del self._members[key]
            for session_id in [s.session_id for s in self._sessions.values() if s.user_id == user_id]:
                session = self._sessions.pop(session_id)
                self._sessions_by_token.pop(session.token_hash, None)

    def user_count(self) -> int:
        with self._lock:
            return len(self._users)

    # -- organisations --------------------------------------------------
    def create_organisation(self, organisation: Organisation) -> Organisation:
        with self._lock:
            slug = organisation.slug.strip().lower()
            if slug in self._slug_index:
                raise DuplicateOrganisationError(f"slug '{slug}' is already taken")
            stored = organisation.model_copy(update={"slug": slug})
            self._orgs[stored.organisation_id] = stored
            self._slug_index[slug] = stored.organisation_id
            return stored

    def get_organisation(self, organisation_id: str) -> Optional[Organisation]:
        with self._lock:
            return self._orgs.get(organisation_id)

    def get_organisation_by_slug(self, slug: str) -> Optional[Organisation]:
        with self._lock:
            org_id = self._slug_index.get(slug.strip().lower())
            return self._orgs.get(org_id) if org_id else None

    def update_organisation(self, organisation: Organisation) -> Organisation:
        with self._lock:
            if organisation.organisation_id not in self._orgs:
                raise KeyError(organisation.organisation_id)
            self._orgs[organisation.organisation_id] = organisation
            self._slug_index[organisation.slug.strip().lower()] = organisation.organisation_id
            return organisation

    def delete_organisation(self, organisation_id: str) -> None:
        with self._lock:
            org = self._orgs.pop(organisation_id, None)
            if org is None:
                return
            self._slug_index.pop(org.slug.strip().lower(), None)
            for key in [k for k in self._members if k[0] == organisation_id]:
                del self._members[key]

    def list_organisation_ids(self, *, active_only: bool = True) -> list[str]:
        from app.auth.models import OrganisationStatus

        with self._lock:
            ids = [
                org_id
                for org_id, org in self._orgs.items()
                if not active_only or org.status == OrganisationStatus.ACTIVE
            ]
        return sorted(ids)

    # -- memberships ----------------------------------------------------
    def add_member(self, member: OrganisationMember) -> OrganisationMember:
        with self._lock:
            key = (member.organisation_id, member.user_id)
            existing = self._members.get(key)
            if existing is None:
                self._members[key] = member
                return member
            # Idempotent re-add: refresh the role; reactivate a REMOVED tombstone.
            reactivated = existing.model_copy(
                update={"role": member.role, "status": member.status,
                        "updated_at": member.updated_at}
            )
            self._members[key] = reactivated
            return reactivated

    def get_membership(self, organisation_id: str, user_id: str) -> Optional[OrganisationMember]:
        with self._lock:
            return self._members.get((organisation_id, user_id))

    def list_memberships_by_user(self, user_id: str, *, active_only: bool = True) -> list[OrganisationMember]:
        with self._lock:
            rows = [m for (o, u), m in self._members.items() if u == user_id]
            if active_only:
                rows = [m for m in rows if m.status.value == "ACTIVE"]
            return sorted(rows, key=lambda m: m.organisation_id)

    def list_memberships_by_org(self, organisation_id: str, *, active_only: bool = True) -> list[OrganisationMember]:
        with self._lock:
            rows = [m for (o, u), m in self._members.items() if o == organisation_id]
            if active_only:
                rows = [m for m in rows if m.status.value == "ACTIVE"]
            return sorted(rows, key=lambda m: m.user_id)

    def set_member_role(self, organisation_id: str, user_id: str, role: Role) -> OrganisationMember:
        with self._lock:
            key = (organisation_id, user_id)
            member = self._members.get(key)
            if member is None:
                raise KeyError(key)
            updated = member.model_copy(update={"role": role, "updated_at": self._clock()})
            self._members[key] = updated
            return updated

    def remove_member(self, organisation_id: str, user_id: str) -> OrganisationMember:
        with self._lock:
            from app.auth.models import MembershipStatus

            key = (organisation_id, user_id)
            member = self._members.get(key)
            if member is None:
                raise KeyError(key)
            updated = member.model_copy(
                update={"status": MembershipStatus.REMOVED, "updated_at": self._clock()}
            )
            self._members[key] = updated
            return updated

    # -- sessions --------------------------------------------------------
    def create_session(self, session: AuthSession) -> AuthSession:
        with self._lock:
            self._sessions[session.session_id] = session
            self._sessions_by_token[session.token_hash] = session.session_id
            return session

    def get_session_by_token_hash(self, token_hash: str) -> Optional[AuthSession]:
        with self._lock:
            session_id = self._sessions_by_token.get(token_hash)
            return self._sessions.get(session_id) if session_id else None

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session.revoked_at is None:
                self._sessions[session_id] = session.model_copy(
                    update={"revoked_at": self._clock()}
                )

    def revoke_sessions_for_user(self, user_id: str) -> int:
        with self._lock:
            now = self._clock()
            count = 0
            for session_id, session in list(self._sessions.items()):
                if session.user_id == user_id and session.revoked_at is None:
                    self._sessions[session_id] = session.model_copy(update={"revoked_at": now})
                    count += 1
            return count

    # -- login attempts ----------------------------------------------------
    def record_login_attempt(self, email: str, *, success: bool) -> None:
        with self._lock:
            self._attempts.append((normalize_email(email), self._clock(), success))

    def count_recent_failures(self, email: str, since: datetime) -> int:
        with self._lock:
            target = normalize_email(email)
            return sum(
                1 for (address, at, ok) in self._attempts
                if address == target and not ok and at >= since
            )

    # -- audit ---------------------------------------------------------------
    def record_event(self, event: SecurityEvent) -> SecurityEvent:
        with self._lock:
            self._events.append(event)
            return event

    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[SecurityEvent]:
        with self._lock:
            rows = list(self._events)
            if event_type is not None:
                rows = [e for e in rows if e.event_type.value == event_type]
            if user_id is not None:
                rows = [e for e in rows if e.user_id == user_id]
            if organisation_id is not None:
                rows = [e for e in rows if e.organisation_id == organisation_id]
            rows.sort(key=lambda e: e.occurred_at)
            return rows[-limit:]


class SQLiteAuthRepository(AbstractAuthRepository):
    """SQLite auth repository over the shared source store (schema v5)."""

    def __init__(self, store: SQLiteSourceStore, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._store = store
        self._clock = clock or _default_clock

    # -- row mapping ------------------------------------------------------
    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        from app.auth.models import UserStatus

        return User(
            user_id=row["user_id"], email=row["email"], password_hash=row["password_hash"],
            status=UserStatus(row["status"]),
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _org_from_row(row: sqlite3.Row) -> Organisation:
        from app.auth.models import OrganisationStatus

        return Organisation(
            organisation_id=row["organisation_id"], name=row["name"], slug=row["slug"],
            status=OrganisationStatus(row["status"]),
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _member_from_row(row: sqlite3.Row) -> OrganisationMember:
        from app.auth.models import MembershipStatus

        return OrganisationMember(
            member_id=row["member_id"], organisation_id=row["organisation_id"],
            user_id=row["user_id"], role=Role(row["role"]),
            status=MembershipStatus(row["status"]),
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> AuthSession:
        return AuthSession(
            session_id=row["session_id"], token_hash=row["token_hash"], user_id=row["user_id"],
            created_at=_parse(row["created_at"]), expires_at=_parse(row["expires_at"]),
            revoked_at=_parse(row["revoked_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> SecurityEvent:
        from app.auth.models import SecurityEventType

        return SecurityEvent(
            event_id=row["event_id"], occurred_at=_parse(row["occurred_at"]),
            event_type=SecurityEventType(row["event_type"]),
            user_id=row["user_id"], organisation_id=row["organisation_id"],
            detail=json.loads(row["detail"] or "{}"),
        )

    # -- users ---------------------------------------------------------
    def create_user(self, user: User) -> User:
        email = normalize_email(user.email)
        stored = user.model_copy(update={"email": email})
        try:
            with self._store.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(user_id, email, password_hash, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (stored.user_id, stored.email, stored.password_hash, stored.status.value,
                     _iso(stored.created_at), _iso(stored.updated_at)),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateUserError(f"email '{email}' is already registered") from exc
        return stored

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        row = self._store._connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        row = self._store._connection.execute(
            "SELECT * FROM users WHERE email = ?", (normalize_email(email),)
        ).fetchone()
        return self._user_from_row(row) if row else None

    def update_user(self, user: User) -> User:
        with self._store.transaction() as connection:
            updated = connection.execute(
                "UPDATE users SET email = ?, password_hash = ?, status = ?, updated_at = ?"
                " WHERE user_id = ?",
                (normalize_email(user.email), user.password_hash, user.status.value,
                 _iso(user.updated_at), user.user_id),
            )
            if updated.rowcount != 1:
                raise KeyError(user.user_id)
        return user

    def delete_user(self, user_id: str) -> None:
        with self._store.transaction() as connection:
            connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

    def user_count(self) -> int:
        row = self._store._connection.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0])

    # -- organisations --------------------------------------------------
    def create_organisation(self, organisation: Organisation) -> Organisation:
        stored = organisation.model_copy(update={"slug": organisation.slug.strip().lower()})
        try:
            with self._store.transaction() as connection:
                connection.execute(
                    "INSERT INTO organisations(organisation_id, name, slug, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (stored.organisation_id, stored.name, stored.slug, stored.status.value,
                     _iso(stored.created_at), _iso(stored.updated_at)),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateOrganisationError(f"slug '{stored.slug}' is already taken") from exc
        return stored

    def get_organisation(self, organisation_id: str) -> Optional[Organisation]:
        row = self._store._connection.execute(
            "SELECT * FROM organisations WHERE organisation_id = ?", (organisation_id,)
        ).fetchone()
        return self._org_from_row(row) if row else None

    def get_organisation_by_slug(self, slug: str) -> Optional[Organisation]:
        row = self._store._connection.execute(
            "SELECT * FROM organisations WHERE slug = ?", (slug.strip().lower(),)
        ).fetchone()
        return self._org_from_row(row) if row else None

    def update_organisation(self, organisation: Organisation) -> Organisation:
        with self._store.transaction() as connection:
            updated = connection.execute(
                "UPDATE organisations SET name = ?, slug = ?, status = ?, updated_at = ?"
                " WHERE organisation_id = ?",
                (organisation.name, organisation.slug.strip().lower(), organisation.status.value,
                 _iso(organisation.updated_at), organisation.organisation_id),
            )
            if updated.rowcount != 1:
                raise KeyError(organisation.organisation_id)
        return organisation

    def delete_organisation(self, organisation_id: str) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "DELETE FROM organisations WHERE organisation_id = ?", (organisation_id,)
            )

    def list_organisation_ids(self, *, active_only: bool = True) -> list[str]:
        from app.auth.models import OrganisationStatus

        if active_only:
            rows = self._store._connection.execute(
                "SELECT organisation_id FROM organisations WHERE status = ? ORDER BY organisation_id",
                (OrganisationStatus.ACTIVE.value,),
            ).fetchall()
        else:
            rows = self._store._connection.execute(
                "SELECT organisation_id FROM organisations ORDER BY organisation_id"
            ).fetchall()
        return [row[0] for row in rows]

    # -- memberships ----------------------------------------------------
    def add_member(self, member: OrganisationMember) -> OrganisationMember:
        from app.auth.models import MembershipStatus

        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM organisation_members WHERE organisation_id = ? AND user_id = ?",
                (member.organisation_id, member.user_id),
            ).fetchone()
            if existing is not None:
                current = self._member_from_row(existing)
                if current.status == MembershipStatus.REMOVED:
                    connection.execute(
                        "UPDATE organisation_members SET role = ?, status = ?, updated_at = ?"
                        " WHERE organisation_id = ? AND user_id = ?",
                        (member.role.value, member.status.value, _iso(member.updated_at),
                         member.organisation_id, member.user_id),
                    )
                    return member
                connection.execute(
                    "UPDATE organisation_members SET role = ?, updated_at = ?"
                    " WHERE organisation_id = ? AND user_id = ?",
                    (member.role.value, _iso(member.updated_at),
                     member.organisation_id, member.user_id),
                )
                return current.model_copy(update={"role": member.role, "updated_at": member.updated_at})
            try:
                connection.execute(
                    "INSERT INTO organisation_members(member_id, organisation_id, user_id, role, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (member.member_id, member.organisation_id, member.user_id, member.role.value,
                     member.status.value, _iso(member.created_at), _iso(member.updated_at)),
                )
            except sqlite3.IntegrityError as exc:
                raise KeyError((member.organisation_id, member.user_id)) from exc
            return member

    def get_membership(self, organisation_id: str, user_id: str) -> Optional[OrganisationMember]:
        row = self._store._connection.execute(
            "SELECT * FROM organisation_members WHERE organisation_id = ? AND user_id = ?",
            (organisation_id, user_id),
        ).fetchone()
        return self._member_from_row(row) if row else None

    def _list_memberships(self, clause: str, args: tuple, *, active_only: bool) -> list[OrganisationMember]:
        query = f"SELECT * FROM organisation_members WHERE {clause}"
        if active_only:
            query += " AND status = 'ACTIVE'"
        rows = self._store._connection.execute(query, args).fetchall()
        return [self._member_from_row(row) for row in rows]

    def list_memberships_by_user(self, user_id: str, *, active_only: bool = True) -> list[OrganisationMember]:
        rows = self._list_memberships("user_id = ?", (user_id,), active_only=active_only)
        return sorted(rows, key=lambda m: m.organisation_id)

    def list_memberships_by_org(self, organisation_id: str, *, active_only: bool = True) -> list[OrganisationMember]:
        rows = self._list_memberships("organisation_id = ?", (organisation_id,), active_only=active_only)
        return sorted(rows, key=lambda m: m.user_id)

    def set_member_role(self, organisation_id: str, user_id: str, role: Role) -> OrganisationMember:
        with self._store.transaction() as connection:
            updated = connection.execute(
                "UPDATE organisation_members SET role = ?, updated_at = ?"
                " WHERE organisation_id = ? AND user_id = ?",
                (role.value, _iso(self._clock()), organisation_id, user_id),
            )
            if updated.rowcount != 1:
                raise KeyError((organisation_id, user_id))
            row = connection.execute(
                "SELECT * FROM organisation_members WHERE organisation_id = ? AND user_id = ?",
                (organisation_id, user_id),
            ).fetchone()
            return self._member_from_row(row)

    def remove_member(self, organisation_id: str, user_id: str) -> OrganisationMember:
        from app.auth.models import MembershipStatus

        with self._store.transaction() as connection:
            updated = connection.execute(
                "UPDATE organisation_members SET status = ?, updated_at = ?"
                " WHERE organisation_id = ? AND user_id = ?",
                (MembershipStatus.REMOVED.value, _iso(self._clock()), organisation_id, user_id),
            )
            if updated.rowcount != 1:
                raise KeyError((organisation_id, user_id))
            row = connection.execute(
                "SELECT * FROM organisation_members WHERE organisation_id = ? AND user_id = ?",
                (organisation_id, user_id),
            ).fetchone()
            return self._member_from_row(row)

    # -- sessions --------------------------------------------------------
    def create_session(self, session: AuthSession) -> AuthSession:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_sessions(session_id, token_hash, user_id, created_at, expires_at, revoked_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session.session_id, session.token_hash, session.user_id,
                 _iso(session.created_at), _iso(session.expires_at), _iso(session.revoked_at)),
            )
        return session

    def get_session_by_token_hash(self, token_hash: str) -> Optional[AuthSession]:
        row = self._store._connection.execute(
            "SELECT * FROM auth_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return self._session_from_row(row) if row else None

    def revoke_session(self, session_id: str) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (_iso(self._clock()), session_id),
            )

    def revoke_sessions_for_user(self, user_id: str) -> int:
        with self._store.transaction() as connection:
            updated = connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_iso(self._clock()), user_id),
            )
            return updated.rowcount

    # -- login attempts ----------------------------------------------------
    def record_login_attempt(self, email: str, *, success: bool) -> None:
        import uuid

        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_login_attempts(attempt_id, email, attempted_at, success)"
                " VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), normalize_email(email), _iso(self._clock()), 1 if success else 0),
            )

    def count_recent_failures(self, email: str, since: datetime) -> int:
        row = self._store._connection.execute(
            "SELECT COUNT(*) FROM auth_login_attempts WHERE email = ? AND success = 0 AND attempted_at >= ?",
            (normalize_email(email), _iso(since)),
        ).fetchone()
        return int(row[0])

    # -- audit ---------------------------------------------------------------
    def record_event(self, event: SecurityEvent) -> SecurityEvent:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO security_events(event_id, occurred_at, event_type, user_id, organisation_id, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event.event_id, _iso(event.occurred_at), event.event_type.value,
                 event.user_id, event.organisation_id, json.dumps(event.detail, sort_keys=True)),
            )
        return event

    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[SecurityEvent]:
        clauses = []
        args: list = []
        if event_type is not None:
            clauses.append("event_type = ?")
            args.append(event_type)
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        if organisation_id is not None:
            clauses.append("organisation_id = ?")
            args.append(organisation_id)
        query = "SELECT * FROM security_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY occurred_at ASC LIMIT ?"
        args.append(limit)
        rows = self._store._connection.execute(query, tuple(args)).fetchall()
        return [self._event_from_row(row) for row in rows]
