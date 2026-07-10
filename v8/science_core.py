"""Science Core — Re-export facade.

This module re-exports all public symbols from the split submodules.
External code importing from science_core should see no change.
"""
from __future__ import annotations

try:
    from ._utils import *  # noqa: F401,F403
    from ._models import *  # noqa: F401,F403
    from ._project import *  # noqa: F401,F403
    from ._llm import *  # noqa: F401,F403
    from ._literature_search import *  # noqa: F401,F403
    from ._literature_scoring import *  # noqa: F401,F403
    from ._literature_graph import *  # noqa: F401,F403
    from ._literature_import import *  # noqa: F401,F403
    from ._gap_detection import *  # noqa: F401,F403
    from ._hypothesis import *  # noqa: F401,F403
    from ._socrates import *  # noqa: F401,F403
    from ._verification import *  # noqa: F401,F403
    from ._debate import *  # noqa: F401,F403
    from ._supplement import *  # noqa: F401,F403
    from ._pipeline import *  # noqa: F401,F403
except ImportError:
    from _utils import *  # noqa: F401,F403
    from _models import *  # noqa: F401,F403
    from _project import *  # noqa: F401,F403
    from _llm import *  # noqa: F401,F403
    from _literature_search import *  # noqa: F401,F403
    from _literature_scoring import *  # noqa: F401,F403
    from _literature_graph import *  # noqa: F401,F403
    from _literature_import import *  # noqa: F401,F403
    from _gap_detection import *  # noqa: F401,F403
    from _hypothesis import *  # noqa: F401,F403
    from _socrates import *  # noqa: F401,F403
    from _verification import *  # noqa: F401,F403
    from _debate import *  # noqa: F401,F403
    from _supplement import *  # noqa: F401,F403
    from _pipeline import *  # noqa: F401,F403

# Make submodule references available for internal use
try:
    from . import _utils
    from . import _models
    from . import _project
    from . import _llm
    from . import _literature_search
    from . import _literature_scoring
    from . import _literature_graph
    from . import _literature_import
    from . import _gap_detection
    from . import _hypothesis
    from . import _socrates
    from . import _verification
    from . import _debate
    from . import _supplement
    from . import _pipeline
except ImportError:
    import _utils
    import _models
    import _project
    import _llm
    import _literature_search
    import _literature_scoring
    import _literature_graph
    import _literature_import
    import _gap_detection
    import _hypothesis
    import _socrates
    import _verification
    import _debate
    import _supplement
    import _pipeline
