from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReservationRecoverySummary:
    requeued: int = 0
    removed: int = 0
    malformed: int = 0
