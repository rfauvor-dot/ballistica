# Source: ammolytics/projectiles

Pinned snapshot, not "whatever's currently on GitHub" -- per the caveat
already flagged in BACKLOG.md's licensing research when this was first
scoped (2026-08-23).

- **Repository:** https://github.com/ammolytics/projectiles
- **Commit pinned:** `5b51ab231c66f60de6fcb62a6b4c4795240948e5` (`develop` branch, default at time of pull)
- **Pulled:** 2026-08-30
- **License:** MIT (see `LICENSE` in this directory -- read directly from
  the repository, not assumed from GitHub's license badge). Standard,
  unmodified MIT text: explicitly grants "use, copy, modify, merge,
  publish, distribute, sublicense, and/or sell," which covers
  commercial use without qualification. The only obligation is
  retaining the copyright notice and license text (satisfied by this
  file existing alongside the data, plus `LICENSE` itself).
- **Files kept:** `README.md`, `LICENSE`, and every per-manufacturer
  `data/*.csv` file (Barnes, Berger, Hornady, Lapua, Sierra, Speer,
  plus the combined `projectiles.csv`). The project's own `.json`
  duplicates, `index.js`, `package.json`, `yarn.lock`, `.travis.yml`,
  and `.github/` were not needed and weren't copied.

See `ballistica/bullet_reference.py` and `scripts/build_bullet_reference.py`
for how this raw data is filtered and mapped into Ballistica's own
schema -- this directory is the untouched source, kept for
reproducibility and so a future re-pull can diff against what's here.
