# Maintenance scripts

One-off / occasional maintenance utilities for the tsg-lazer server.

Run them **inside the running container** so they pick up the same database
configuration as the app:

```bash
docker compose exec -T tsg-lazer python - < scripts/<script>.py
```

## recompute_stats.py

Recomputes every user's profile statistics (pp, accuracy, ranks, ranked/total
score, total hits, play count, play time, grade counts) from their stored
scores.

Use it when:

- you imported or migrated scores from another source,
- you changed the logic in `app/usecases/stats.py` and need to backfill existing
  rows (statistics are otherwise only recomputed when a user submits a new
  score),
- profile totals look out of sync with the actual scores.

The operation is idempotent — running it more than once is safe.

```bash
docker compose exec -T tsg-lazer python - < scripts/recompute_stats.py
```
