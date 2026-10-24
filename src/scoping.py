"""Store scoping.

``access-v1`` requires every query to be constrained to the stores the actor
may see. The Owner has every store; an Admin or Manager has only their
assignments.

The pattern established here is that **scope is a required argument, not an
optional filter**. A repository function takes a ``StoreScope`` and cannot be
called without one. Forgetting a scope becomes a type error at the call site
rather than a data leak discovered later.

US-002 AC-02 adds a second requirement that is easy to miss: an unauthorised
store must be *indistinguishable* from a non-existent one. Returning 403 for a
store that exists and 404 for one that does not confirms the existence of
stores the caller may not see, which is itself a disclosure. Everything out of
scope is simply not found.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

__all__ = ["StoreScope", "OutOfScope", "Forbidden"]


class OutOfScope(LookupError):
    """The requested store is not in the actor's scope.

    Deliberately a LookupError. Callers should translate this to "not found",
    never to "forbidden" -- see the module docstring.
    """


class Forbidden(Exception):
    """The actor's role does not permit this action at all.

    A different question from ``OutOfScope``, and the two must never be
    merged (US-003 AC-03): ``OutOfScope`` answers "is this store visible to
    you" (404, protecting against disclosure); ``Forbidden`` answers "does
    your role permit this action" (403, where the actor already knows the
    resource exists -- there is nothing left to protect by hiding it behind
    a 404). BR-11's capability matrix draws these as two separate lines per
    action (e.g. "create store" vs. "change store settings"); this exception
    exists so a role check can fail on its own, independent of whichever
    store the action targets.
    """


@dataclass(frozen=True)
class StoreScope:
    """The set of stores an actor may act within.

    ``all_stores`` is the Owner case. It is a separate flag rather than a list
    containing every id so that an empty assignment list can never be mistaken
    for unrestricted access -- a bug whose failure mode is total exposure.
    """

    actor_id: uuid.UUID | None
    store_ids: frozenset[uuid.UUID]
    all_stores: bool = False

    @classmethod
    def owner(cls, actor_id: uuid.UUID | None = None) -> "StoreScope":
        return cls(actor_id=actor_id, store_ids=frozenset(), all_stores=True)

    @classmethod
    def assigned(cls, actor_id: uuid.UUID | None, store_ids: list[uuid.UUID]) -> "StoreScope":
        return cls(actor_id=actor_id, store_ids=frozenset(store_ids), all_stores=False)

    @classmethod
    def none(cls) -> "StoreScope":
        """No access at all. The safe default for an unauthenticated request."""
        return cls(actor_id=None, store_ids=frozenset(), all_stores=False)

    def permits(self, store_id: uuid.UUID) -> bool:
        return self.all_stores or store_id in self.store_ids

    def require(self, store_id: uuid.UUID) -> uuid.UUID:
        if not self.permits(store_id):
            raise OutOfScope(str(store_id))
        return store_id

    def sql_filter(self, column: str = "store_id") -> tuple[str, dict[str, object]]:
        """A WHERE fragment and its parameters.

        The Owner case returns ``true`` rather than an empty string so that the
        fragment is always safely composable into a WHERE clause. A scope with
        no stores returns ``false``, which yields nothing -- never everything.
        """
        if self.all_stores:
            return "true", {}
        if not self.store_ids:
            return "false", {}
        return f"{column} = any(%(scope_store_ids)s)", {
            "scope_store_ids": list(self.store_ids)
        }
