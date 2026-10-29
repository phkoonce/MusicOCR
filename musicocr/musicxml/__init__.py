"""Shared MusicXML helpers: writing files and measuring measures.

Every module that repairs or inspects raw MusicXML (``timesig``, ``tuplets``, ``barrests``,
``copisteria_fixes``, ``combine``, ...) takes these primitives from here, so "how long is this
bar" and "how is a file written" have one definition.
"""
from musicocr.musicxml.io import write_tree
from musicocr.musicxml.measure import (MULTIREST_TAG, check_direction, extent_length,
                                       measures_with_capacity, multirest_count,
                                       note_duration, sounding_length)

__all__ = ["MULTIREST_TAG", "check_direction", "extent_length", "measures_with_capacity",
           "multirest_count", "note_duration", "sounding_length", "write_tree"]
