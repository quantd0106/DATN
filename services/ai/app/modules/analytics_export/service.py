from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

MIN_STUDENTS = 5


@dataclass(frozen=True)
class TopicParticipation:
    school_id: UUID
    class_id: UUID
    student_id: UUID
    topic_id: UUID


@dataclass(frozen=True)
class TopicCount:
    topic_id: UUID
    students: int


def summarize_topics(
    rows: Iterable[TopicParticipation], *, class_id: UUID, school_id: UUID
) -> tuple[TopicCount, ...]:
    """Aggregate structured topic IDs from a single class release."""
    topics_by_student: dict[UUID, set[UUID]] = defaultdict(set)
    for row in rows:
        if (row.class_id, row.school_id) != (class_id, school_id):
            raise ValueError("Participation outside the requested school/class")
        topics_by_student[row.student_id].add(row.topic_id)

    if len(topics_by_student) < MIN_STUDENTS:
        return ()
    cohort_sizes = Counter(frozenset(topics) for topics in topics_by_student.values())
    # Rare overlap cohorts can be exposed by comparing cells even if both cells are large.
    suppressed = set().union(
        *(topics for topics, size in cohort_sizes.items() if size < MIN_STUDENTS)
    )
    counts = Counter(topic for topics in topics_by_student.values() for topic in topics)
    return tuple(
        TopicCount(topic, count)
        for topic, count in sorted(counts.items())
        if count >= MIN_STUDENTS and topic not in suppressed
    )
