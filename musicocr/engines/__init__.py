"""OMR engines: the registry (``musicocr.engines.ENGINES``) and one adapter per engine.

* ``registry``  -- which engines exist and what each declares (tool, checks, entry module);
* ``raster``    -- the per-page loop shared by the image-reading engines;
* ``audiveris`` / ``homr/`` / ``copisteria/`` / ``legato2/`` -- each engine's ``run(ctx)`` stage
  adapter plus its own fixes and helper scripts.

The ``omr`` stage dispatches here; downstream stages never see which engine ran.
"""
from musicocr.engines.registry import ENGINES, VALID_ENGINES, Engine, has_check

__all__ = ["ENGINES", "VALID_ENGINES", "Engine", "has_check"]
