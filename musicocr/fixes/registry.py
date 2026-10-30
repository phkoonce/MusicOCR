"""Every automatic repair, declared once.

Three lists, each in the order its fixes run:

* ``PAGE_FIXES``       -- engine-agnostic, on the per-page MusicXML right after OMR (the ``extract`` stage);
* ``SCORE_FIXES``      -- engine-agnostic, on the merged score (the ``normalize`` stage);
* ``HOMR_FIXES`` / ``COPISTERIA_FIXES`` -- engine-specific, on each page straight after that engine wrote it.

``musicocr fixes`` prints them with their on/off state. To add a repair: write the function that
does it, add a :class:`~musicocr.fixes.base.Fix` here, and (if an older config key already
controlled it) point ``legacy`` at that key. Nothing else needs to know about it.
"""
from __future__ import annotations

from musicocr.fixes.base import Fix, FixContext, check_order

# ---- engine-agnostic: per page, in `extract` ----------------------------------------------------


def _time_signatures(ctx: FixContext) -> int:
    from musicocr.fixes.timesig import fix_time_signatures_files
    return fix_time_signatures_files(list(ctx.paths), log=ctx.log)


def _whole_bar_rests(ctx: FixContext) -> int:
    from musicocr.fixes.barrests import fix_file
    return fix_file(ctx.path, log=ctx.log)


def _tuplets(ctx: FixContext) -> int:
    from musicocr.fixes.tuplets import fix_tuplets_file
    return fix_tuplets_file(ctx.path, flag=ctx.config.stages.tuplets_flag, log=ctx.log)


def _slur_ties(ctx: FixContext) -> int:
    from musicocr.fixes.slurs import fix_file
    return fix_file(ctx.path, log=ctx.log)


PAGE_FIXES = (
    Fix("time_signatures", _time_signatures, scope="all",
        summary="recompute time-signature numerators from the bars they govern (homr guesses them from page medians)"),
    Fix("whole_bar_rests", _whole_bar_rests, after=("time_signatures",),
        summary="cut a whole-bar rest that is longer than the bar (cue notes read as graces)",
        message="{n} overlong whole-bar rest(s) cut to the bar length"),
    Fix("tuplets", _tuplets, after=("time_signatures",), legacy=lambda c: c.stages.tuplets_enabled,
        summary="rebuild inconsistent tuplet runs in bars that do not add up (flagged purple)",
        message="rebuilt {n} bar(s) with inconsistent tuplets"),
    Fix("slur_ties", _slur_ties,
        summary="a slur from one note to the same pitch right after it is a tie (OMR reads ties as slurs)",
        message="{n} same-pitch slur(s) turned into ties"),
)

# ---- engine-agnostic: merged score, in `normalize` ----------------------------------------------


def _measure_lengths(ctx: FixContext) -> int:
    from musicocr.fixes.measure_normalize import normalize_file
    return normalize_file(ctx.path, log=ctx.log)


SCORE_FIXES = (
    Fix("measure_lengths", _measure_lengths, legacy=lambda c: c.stages.normalize_enabled,
        summary="truncate/pad every bar to its time signature (lossy; gives a uniform grid to hand-correct)"),
)

# ---- homr ---------------------------------------------------------------------------------------


def _homr_multirest(ctx: FixContext) -> int:
    from musicocr.engines.homr.fixes import fix_multirest_file
    return fix_multirest_file(ctx.path, log=ctx.log)


HOMR_FIXES = (
    Fix("homr_multirest", _homr_multirest, engines=frozenset({"homr"}),
        legacy=lambda c: c.omr.homr.fix_multirest,
        summary="give homr's empty multi-rest bars their rest (upstream generator bug liebharc/homr#140)",
        message="fixed {n} empty multi-rest measure(s)"),
)

# ---- Copisteria ---------------------------------------------------------------------------------


def _key_cancel(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import fix_missing_key_cancel
    reading = ctx.path.with_name(f"{ctx.path.stem}.reading.json")
    return fix_missing_key_cancel(ctx.path, reading, log=ctx.log)


def _time_inherit(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import inherit_unprinted_time
    return inherit_unprinted_time(ctx.path, ctx.prev_path, log=ctx.log)


def _empty_bars(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import fill_empty_measures
    return fill_empty_measures(ctx.path, log=ctx.log)


def _ghost_bars(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import drop_ghost_bars
    return drop_ghost_bars(ctx.path, log=ctx.log)


def _pickup(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import fix_opening_pickup
    return fix_opening_pickup(ctx.path, log=ctx.log)


def _half_tuplets(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import fix_half_tuplets
    return fix_half_tuplets(ctx.path, log=ctx.log)


def _reveal_gaps(ctx: FixContext) -> int:
    from musicocr.engines.copisteria.fixes import reveal_gaps
    return reveal_gaps(ctx.path, log=lambda s: None)


def _multirest_form(ctx: FixContext) -> int:
    from musicocr.fixes.multirest import apply_multirest_form
    form = ctx.config.omr.copisteria.multirest_form
    n = apply_multirest_form(ctx.path, form, log=ctx.log)
    if n:
        ctx.log(f"  {ctx.label}: multi-rest {form}: {n} bar(s) {'removed' if form == 'collapse' else 'added'}")
    return n


_COPISTERIA = frozenset({"copisteria"})

COPISTERIA_FIXES = (
    # reading.json bar indices must still match the XML here: nothing that adds or drops bars runs before it
    Fix("key_cancel", _key_cancel, engines=_COPISTERIA, legacy=lambda c: c.omr.copisteria.fix_key_cancel,
        summary="a staff that prints no key signature resets to C major instead of inheriting the last key",
        message="key reset to C major at {n} staff start(s) that print no key signature"),
    Fix("time_inherit", _time_inherit, engines=_COPISTERIA, legacy=lambda c: c.omr.copisteria.fix_time_inherit,
        when=lambda c: c.prev_path is not None,
        summary="a page that opens without a printed meter continues the previous page's"),
    # empty bars get their rest first so the multi-rest fold sees real whole-measure rests
    Fix("empty_bars", _empty_bars, engines=_COPISTERIA, legacy=lambda c: c.omr.copisteria.fix_empty,
        summary="give bare-<forward> bars a whole-measure rest",
        message="gave {n} empty bar(s) a whole-measure rest"),
    Fix("ghost_bars", _ghost_bars, engines=_COPISTERIA, after=("key_cancel",),
        legacy=lambda c: c.omr.copisteria.fix_ghost_bars,
        summary="drop a misread 'bar' made of a cautionary key/time signature after a double bar",
        message="dropped {n} ghost bar(s)"),
    Fix("pickup", _pickup, engines=_COPISTERIA, legacy=lambda c: c.omr.copisteria.fix_pickup,
        when=lambda c: c.page == 1,
        summary="page 1: turn a padded opening bar into a real pickup and show the meter",
        message="opening bar made a pickup / time signature shown"),
    Fix("half_tuplets", _half_tuplets, engines=_COPISTERIA, legacy=lambda c: c.omr.copisteria.fix_half_tuplets,
        summary="finish a duplet/triplet the model marked on only some of its notes",
        message="completed {n} half-read tuplet note(s)"),
    Fix("reveal_gaps", _reveal_gaps, engines=_COPISTERIA, after=("half_tuplets",),
        legacy=lambda c: c.omr.copisteria.reveal_gaps,
        summary="show every remaining gap (a dropped symbol) as a visible orange rest",
        message="{n} gap(s) shown as coloured rests (likely dropped symbols)"),
    Fix("multirest_form", _multirest_form, engines=_COPISTERIA, after=("empty_bars",),
        legacy=lambda c: c.omr.copisteria.multirest_form != "keep",
        summary="fold Copisteria's expanded multi-rests into one bar ([omr.copisteria] multirest_form picks the form)"),
)

ALL_FIXES = PAGE_FIXES + SCORE_FIXES + HOMR_FIXES + COPISTERIA_FIXES
FIXES_BY_NAME = {f.name: f for f in ALL_FIXES}

for _group in (PAGE_FIXES, SCORE_FIXES, HOMR_FIXES, COPISTERIA_FIXES):
    check_order(_group)
check_order(ALL_FIXES)       # names are unique across the lists, so one switch means one fix
