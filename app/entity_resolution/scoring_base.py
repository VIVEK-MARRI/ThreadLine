"""Abstract base class for candidate scorers.

A candidate scorer answers:

    "Given an unresolved mention and a shortlist of candidate entities,
     how strong is the evidence for each candidate?"

This is NOT a resolution decision.  Scorers evaluate and rank; a future
Resolution Decision stage decides what action (if any) to take.

Design mirrors app/entity_resolution/base.py (AbstractCandidateGenerator):
  - The interface is declared here.
  - Concrete implementations live in sibling modules.
  - The service layer depends only on AbstractCandidateScorer — never
    on a specific implementation — so scorers are swappable without
    touching business logic.

To add a new scorer:
  1. Create a new module in app/entity_resolution/
     (e.g., embedding_candidate_scorer.py).
  2. Subclass AbstractCandidateScorer and implement score().
  3. Wire it into CandidateScoringService via the constructor or DI factory.

Separation of concerns
-----------------------
Stage 1 (Candidate Generation): "Who could this be?"
Stage 2 (Candidate Scoring):    "How strong is the evidence for each?"
Stage 3 (Resolution Decision):  "What action should the system take?"

Scorers MUST NOT make resolution decisions (assign entity_id, change
resolution_status, create or mutate entities).
"""

from abc import ABC, abstractmethod

from app.models.entity import (
    CanonicalEntity,
    EntityCandidate,
    EntityMention,
    ScoredEntityCandidate,
)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class AbstractCandidateScorer(ABC):
    """Interface for candidate scoring strategies.

    Implementors receive a mention, the candidate shortlist produced by a
    generator, and the full entity data for those candidates, and must
    return a scored, ordered list of :class:`ScoredEntityCandidate` objects.

    Contract
    --------
    - MUST return a deterministic, stable order.
    - MUST NOT modify the mention's resolution_status or entity_id.
    - MUST NOT create or persist canonical entities.
    - MUST NOT make a final resolution decision.
    - MUST produce scores in the range [0.0, 1.0].
    - MAY return an empty list when no candidate can be meaningfully scored.
    - The entities mapping contains exactly the canonical entities
      corresponding to the candidates; implementors do not need to filter.
    """

    @abstractmethod
    def score(
        self,
        mention: EntityMention,
        candidates: list[EntityCandidate],
        entities: list[CanonicalEntity],
    ) -> list[ScoredEntityCandidate]:
        """Score and rank *candidates* for *mention*.

        Parameters
        ----------
        mention:
            The unresolved mention to score candidates for.
        candidates:
            The shortlist produced by a candidate generator.
        entities:
            The canonical entity objects corresponding to *candidates*.
            The caller guarantees that every candidate's entity_id maps
            to an entry in this list.

        Returns
        -------
        list[ScoredEntityCandidate]
            A deterministically ordered list of scored candidates.
            Empty when *candidates* is empty or no candidate can be scored.
        """
        ...
