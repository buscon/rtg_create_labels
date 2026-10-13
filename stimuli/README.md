# Stimulus preparation

**Current clip set (13 October 2026): 8 clips from the International Soundscape Database (ISD).**
See `rgt-tool-design/13_stimuli.md`. The workflow for it:

```bash
python stimuli/isd_shortlist.py --mode paq          # listening pages with PAQ map (stimuli/work/isd/)
# choose: stimuli/isd_selection.csv (GroupID, label, use = clip | practice)
python stimuli/isd_finalize.py                     # -> public/audio/*.flac, elements.csv, config.ini, credits
python scripts/make_triad_set.py --clips 8 --out private/data/triad_set.csv   # only if the number of clips changes
```

Then upload `public/audio/*.flac` to the server (not in git) and deploy as usual.

The sections below describe the earlier route with Urban Soundscapes of the World and EigenScape
(ambisonic); its scripts are kept.

## Earlier route: USotW and EigenScape

Scripts to select and prepare the 10 clips (plus 3 practice clips) from two public datasets:

| Dataset | Use | Content | Format | Licence |
|---|---|---|---|---|
| [Urban Soundscapes of the World (USotW)](https://zenodo.org/records/10106181) | urban and mixed end | 133 recordings in 9 cities, parks to busy streets, 60 s each, with measured LAeq | first-order ambisonics (ACN/SN3D), 48 kHz | CC BY 4.0 |
| [EigenScape](https://zenodo.org/records/2628463) | natural end | 8 classes (Woodland, Beach, Park, ...), 8 × 10-min recordings each, North of England | first-order ambisonics from the "Lite" version (ACN/SN3D), FLAC, 48 kHz | CC BY 4.0 |

Both are ambisonic, so all clips can be rendered to binaural with the same decoder.

## Workflow

```bash
pip install -r stimuli/requirements.txt

# 1. download (only the chosen recordings, not the full archives)
python stimuli/01_download.py --metadata-only                 # look at the lists first
python stimuli/01_download.py --usotw-quietest 25 --eigenscape-classes Woodland Beach Park

# 2. cut into 30-s candidates, compute indicators, make previews
python stimuli/02_candidates.py --eigenscape-offset 108       # see "Calibration"
#    open stimuli/work/candidates.html, listen, sort by the indicators

# 3. choose: copy stimuli/work/selection_template.csv to stimuli/selection.csv and fill it in
python stimuli/03_export.py --eigenscape-offset 108
#    -> stimuli/work/export/foa/*.wav (AmbiX), stimuli/ATTRIBUTION.md

# 4. render the AmbiX files to binaural with your decoder (same gain for all files), then
python stimuli/04_finalize.py --rendered path/to/binaural/
#    -> public/audio/*.flac, private/data/elements.csv, practice_clips in config.ini, public/credits.html
```

Downloaded and generated audio (`stimuli/raw/`, `stimuli/work/`, `public/audio/`) is not in git.
Commit `stimuli/selection.csv` and `stimuli/ATTRIBUTION.md`: together they document exactly which
excerpts were used.

**Download behaviour.** Each file shows a status bar (percent, MB, speed, remaining time).
Interrupted downloads (dropped connection, server error, Ctrl-C) continue where they stopped when
you run the same command again; the bytes received so far are kept in `<file>.zpart`. Finished
files are checked against the checksum stored in the archive and skipped on later runs. The
archive index is cached in `stimuli/raw/<dataset>/.index_*.json`, so it is read from the server
only once. Zenodo is often slow (0.3–0.6 MB/s in our test) and answers with 504 errors at times;
the script waits and retries (`--retries`, default 8). Files that still fail are listed at the
end with the archive link and an `unzip` command for a manual download.

Sizes: a USotW recording is ~35 MB, an EigenScape recording ~200 MB (10 min). Three EigenScape
classes are about 4.8 GB; 25 USotW recordings about 0.9 GB.

## Indicators in candidates.csv

All computed on the omnidirectional channel (W) of each 30-s excerpt:

| Column | Meaning |
|---|---|
| `laeq_est` | estimated LAeq in dB (dBFS + calibration offset) |
| `laeq_dbfs` | A-weighted level relative to full scale |
| `l10_l90` | spread of short-term levels (125 ms): high = eventful, low = steady |
| `max_rise_db` | largest level jump within 1 s: sudden events (sirens, shouts, a dog) |
| `centroid_hz` | spectral centroid of the A-weighted signal: brightness |
| `ndsi` | normalised difference soundscape index (scikit-maad): > 0 more 1–10 kHz energy (birds), < 0 more below 1 kHz (traffic) |
| `bi`, `aci` | bioacoustic index and acoustic complexity index (scikit-maad) |
| `lf_dipole_excess` | directional channels louder than W below 200 Hz: low-frequency noise |

The indicators help to sort and to check the spread; the choice itself should be made by listening.

## Decisions to make

**Calibration.** USotW publishes a measured LAeq for each recording. From it, the scripts derive
one offset from file level (dBFS) to dB; in a first test it was 107.5–108.1 dB for two recordings,
so the dataset is consistently calibrated. EigenScape has no calibration information. To keep real
level differences between a forest and a street, EigenScape needs an assumed offset
(`--eigenscape-offset`). Options:

- assume the same offset as USotW (~108 dB). In the first test this gave about 40 dB(A) for
  Dalby Forest, which is plausible for a quiet forest, but it is an assumption;
- choose the offset so that the EigenScape woodland clips land at a level you consider realistic
  (e.g. from literature or your own measurements);
- or use `--equal-loudness` and give up real level differences.

Record the choice in your methods.

**Level differences.** By default `03_export.py` keeps the real level differences (one common
gain for all clips). Online, the absolute playback level is unknown, but the relative levels
between clips are kept.

**High-pass filter.** EigenScape's directional channels contain low-frequency noise (below
~200 Hz, up to 10 dB above the omnidirectional channel in the first test); USotW partly too.
`03_export.py` applies a 4th-order high-pass at 80 Hz by default (`--highpass`, 0 = off). Listen
to the binaural result for rumble and raise the cut-off if needed.

**USotW normalisation: check before rendering.** The USotW page states ACN/SN3D. In the first
test, some directional channels were louder than W (A-weighted), which fits N3D better than
SN3D. Wrong normalisation distorts the spatial image after decoding. Check with a recording with
a clear single source, or ask the dataset authors, and set your decoder accordingly.

**What to avoid in a clip:** understandable speech, music, single striking events that dominate
the excerpt (check `max_rise_db`), well-known landmarks, wind noise on the microphone.

## Note on the clip set in the database

The database registers the clip file names. As long as no session has been started, a new clip
set simply replaces the old names (`setup.php` / deploy). Once sessions exist, changing the clips
requires a new, empty database, so that all participants hear the same set.
