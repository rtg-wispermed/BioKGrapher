# BioKGrapher

[![Affiliated with RTG WisPerMed](https://img.shields.io/badge/Affiliated-RTG%202535%20WisPerMed-blue)](https://wispermed.org/)

Automatic construction of biomedical knowledge graphs from PubMed: MedCAT (NER+NEL → UMLS
concepts), KL-divergence + frequency re-ranking, hierarchy + relation graphs, served by a
FastAPI + Plotly.js / Cytoscape.js web app.

![demo](demo.gif)

## Requirements

Python ≥ 3.10.

```bash
pip install -e ".[annotate,ui]"      # annotate = MedCAT (build host); ui = web app
```

## Download the required files

A **UMLS license** is required for both downloads: https://uts.nlm.nih.gov/uts/signup-login

**1. UMLS Metathesaurus — four RRF files (unzipped, plain text).**
Download the *UMLS Metathesaurus Full Subset* from
https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html and copy these
four `.RRF` files (found in the release's `META/` folder) — **unzipped** — into `data/umls/`:

```
data/umls/MRCONSO.RRF
data/umls/MRHIER.RRF
data/umls/MRREL.RRF
data/umls/MRDEF.RRF
```

Use a UMLS release that is the same as, or newer than, the model's source ontology.

**2. MedCAT model pack — SNOMED International (`.zip`).**
Sign in with your UMLS license and download the **SNOMED International** pack
`mc_modelpack_snomed_int_16_mar_2022_25be3857ba34bdd5.zip` from
https://uts.nlm.nih.gov/uts/login?service=https://medcat.rosalind.kcl.ac.uk/auth-callback .
Leave it **zipped** and place it exactly here:

```
models/mc_modelpack_snomed_int_16_mar_2022_25be3857ba34bdd5.zip
```

Then copy the config — its defaults already point at the files above and set `map_codes = "snomed"`
for this pack, so nothing else needs editing:

```bash
cp biokgrapher.toml.example biokgrapher.toml
```

This pack is MedCAT **v1**; v2 auto-converts it on every load (slow). Convert it once and set
`[annotate].model_pack` to the result for fast loads:
```bash
biokgrapher convert-model models/medcat_v2   # prints the path to set as model_pack
```

PubMed baseline + update files are downloaded automatically — you do not download them yourself.

> Using a UMLS-Full pack instead (one that emits CUIs directly)? Set `[annotate].map_codes = "none"`.

## Build the index and start the app

```bash
biokgrapher ingest-umls     # parse the RRF files into SQLite (once per UMLS release)
biokgrapher build           # download PubMed, annotate with MedCAT, build the index (resumable)
biokgrapher precompute      # cache the predefined preset graphs (optional)
biokgrapher serve           # http://0.0.0.0:6006
```

Also: `biokgrapher update` (pull new update files), `biokgrapher build --shard i/k` +
`biokgrapher merge <shard dbs>` (parallel build across workers), `biokgrapher status`.

## Citation

```bibtex
@article{schafer2024biokgrapher,
  title   = {BioKGrapher: Initial evaluation of automated knowledge graph construction from biomedical literature},
  author  = {Sch{\"a}fer, Henning and Idrissi-Yaghir, Ahmad and Arzideh, Kamyar and Damm, Hendrik and Pakuli, Tabea M. G. and Schmidt, Cynthia S. and Bahn, Mikel and Lodde, Georg and Livingstone, Elisabeth and Schadendorf, Dirk and Nensa, Felix and Horn, Peter A. and Friedrich, Christoph M.},
  journal = {Computational and Structural Biotechnology Journal},
  volume  = {24},
  pages   = {639--660},
  year    = {2024},
  doi     = {10.1016/j.csbj.2024.10.017}
}
```
