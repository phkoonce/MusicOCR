"""Collections: books of many short pieces, each piece in several part-books.

The workflow, one module per step (``musicocr collection <split|batch|combine> SPEC.toml``):

* ``split``   -- cut each part-book into one PDF per piece, using the book's table of contents;
* ``batch``   -- write a batch spec with one part per piece and part-book, ready for ``scripts/batch_run.py``;
* ``combine`` -- combine every piece's parts into one score and tabulate how each one went.

``spec`` reads the collection spec the three share (the books, the table-of-contents pages, per-piece
corrections).
"""
