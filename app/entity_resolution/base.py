"""Abstract base class for candidate generators.

A candidate generator answers:

    "Given an unresolved mention and a registry of canonical entities,
     which entities are plausible candidates worth evaluating later?"

This is NOT final entity resolution.  Generators produce a shortlist;
a future scoring stage decides which (if any) candidate is correct.

Design mirrors app/extraction/base.py:
  - The interface is declared here.
  - Concrete implementations live in sibling modules.
  - The service layer depends only on AbstractCandidateGenerator — never
    on a specific implementation — so generators are swappable without
    touching business logic.

To add a new generator:
  1. Create a new module in app/entity_resolution/
     (e.g., embedding_candidate_generator.py).
  2. Subclass AbstractCandidateGenerator and implement generate().
  3. Wire it into CandidateService via the constructor or DI factory.
"""

from abc import ABC, abstractmethod

from app.models.entity import CanonicalEntity, EntityCandidate, EntityMention


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractCandidateGenerator(ABC):
    """Interface for candidate generation strategies.

    Implementors receive a mention and the full list of candidate-eligible
    entities (already filtered to the same entity_type by the service layer)
    and must return an ordered list of EntityCandidate objects.

    Contract
    --------
    - MUST return candidates in a deterministic, stable order.
    - MUST NOT modify the mention's resolution_status or entity_id.
    - MUST NOT create or persist canonical entities.
    - MUST NOT perform final resolution (assigning entity_id to a mention).
    - MAY return an empty list when no entity is a plausible candidate.
    - The entities list is always pre-filtered to the mention's entity_type;
      implementors do not need to re-filter by type.
    """

    @abstractmethod
    def generate(
        self,
        mention: EntityMention,
        entities: list[CanonicalEntity],
    ) -> list[EntityCandidate]:
        """Generate an ordered list of candidate entities for *mention*.

        Parameters
        ----------
        mention:
            The unresolved mention to find candidates for.
        entities:
            All canonical entities of the same entity_type as *mention*.
            The caller guarantees this list is already type-filtered.

        Returns
        -------
        list[EntityCandidate]
            An ordered, deterministic list of candidate entities.
            Empty when no entity is a plausible match.
        """
        ...
