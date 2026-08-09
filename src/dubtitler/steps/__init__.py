"""Pipeline steps, one module per stage, each runnable as `python -m`.

Every step reads files and writes files. None of them holds state between runs,
and none of them overwrites a file a human has edited without either carrying
the edits across or saving a backup first. That rule exists because breaking it
once silently reverted 23 hand-made corrections on the reference job.
"""
