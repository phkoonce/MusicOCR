"""Automatic repairs of OMR output: the :class:`Fix` abstraction and the registry of all fixes."""
from musicocr.fixes.base import Fix, FixContext, fix_enabled, run_fixes
from musicocr.fixes.registry import (ALL_FIXES, COPISTERIA_FIXES, FIXES_BY_NAME, HOMR_FIXES,
                                     PAGE_FIXES, SCORE_FIXES)

__all__ = ["ALL_FIXES", "COPISTERIA_FIXES", "FIXES_BY_NAME", "Fix", "FixContext", "HOMR_FIXES",
           "PAGE_FIXES", "SCORE_FIXES", "fix_enabled", "run_fixes"]
