"""Hand-written, parameterised FeatureScript templates.

The model selects a template and fills its parameters; it never authors
FeatureScript. That constraint comes from spike 3b and from the CAD-agent
literature (Embodied CAD, arXiv 2606.31252): a raw scripting space is too large
for reliable planning, and a small library of verified templates is not.

Template bodies are written once, verified against Onshape, and then left alone.
"""

from .base import Problem, Severity, Template, TemplateError
from .tube_hole_pattern import TubeHolePattern

#: Every template the skill can apply. Keep this small and deliberate.
TEMPLATES: dict[str, Template] = {t.name: t for t in (TubeHolePattern(),)}

__all__ = ["TEMPLATES", "Problem", "Severity", "Template", "TemplateError"]
