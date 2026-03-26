"""Teaching primitives for Engram — concepts, student models, and teaching memory.

Domain objects stored as Engram memories with ``memory_type`` + metadata dict,
following the same pattern as ``engram.memory.tasks``.
"""

from dhee.teaching.config import TeachingConfig
from dhee.teaching.concepts import ConceptStore
from dhee.teaching.student_model import StudentModel
from dhee.teaching.teaching_memory import TeachingMemory

__all__ = [
    "TeachingConfig",
    "ConceptStore",
    "StudentModel",
    "TeachingMemory",
]
