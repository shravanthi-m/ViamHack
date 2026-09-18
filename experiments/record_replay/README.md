# Record and replay experiment

Earlier standalone tools, separated from the demo runtime. Run from repo root:

```sh
python -m experiments.record_replay.teach_path --name coconut_pour
python -m experiments.record_replay.replay_path import RECORDING.json --name my_pour
python -m experiments.record_replay.replay_path show experiments/record_replay/tracks/coconut_pour.json
python -m experiments.record_replay.replay_path run experiments/record_replay/tracks/coconut_pour.json
python -m experiments.record_replay.smooth_track experiments/record_replay/tracks/coconut_pour.json --name another_smooth --report
```

Teaching reads joints; use Viam controls for manual/free-drive mode. Paths default
to this folder's `paths/`; canonical and smoothed tracks default to `tracks/` here.
Local recordings are ignored by Git; canonical tracks are versioned. Tool CLI
flags and existing physical replay confirmation are preserved. Replay `run`
validates offline until explicitly given `--execute`; turn manual mode off first
and follow its start-position checks. Read `--help` for each tool.

No default demo primitive replays these tracks. If the team chooses to reuse one,
wrap it deliberately behind the agreed primitive contract and verify its station,
object, start-state, and return-state assumptions first.
