# Imported experiments

`translucent add-experiment` and `translucent.experiments.register()` store each user
experiment in `<id>/` here: its manifest, copied recording and lab-record files,
and their hashes. `record.json` stores the capture mapping and file hashes;
`record.sha256` checks its integrity. These folders are local data and are gitignored. Keep your own
backup, including the evidence ledger used for the import.

An experiment ID identifies immutable records. Repeating an identical import is
safe; use a new ID if the video or its declared records change. Corrections to
records for unchanged video bytes also need a separate ledger, because each
ledger associates identical video bytes with one experiment. The ledger refers
to it as `user/<id>`.

See [Bring your own experiment](../../docs/BRING-YOUR-OWN.md) for the required
records, manifest format and command.
