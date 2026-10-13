# Navigation Complexity Index (ICN)

A local application that measures whether the submenus of a web portal really
belong to the menu that contains them. Everything runs on your machine: the
scraping, the embedding model and the computations.

## How to cite

This repository accompanies the article:

> Mitre-Hernández, H., Pacheco-González, E., Lara-Álvarez, C. (in press).
> A calibrated semantic index of navigation complexity in government webpages.
> *Computación y Sistemas*. Instituto Politécnico Nacional, Centro de
> Investigación en Computación. ISSN 1405-5546, eISSN 2007-9737.

Accepted in October 2026. The reference will be updated with volume, issue,
pages and DOI once it is published. `CITATION.cff` carries the same reference in
the format GitHub recognizes.

```
SemanticIndexMenu/
├── app.py                  local server (FastAPI)
├── iniciar.sh              one-step launcher
├── requirements.txt
├── core/
│   ├── scraper.py          static extraction of the navigation tree
│   ├── dinamico.py         interaction-based extraction (mega-menus)
│   ├── similitud.py        multilingual embeddings and cosine
│   ├── complejidad.py      calibration against chance, and the index
│   ├── amplitud_ref.py     n_ref = 95th percentile of the corpus breadth
│   ├── estadisticos.py     descriptives, correlation, weight sensitivity
│   └── catalogo.py         32 candidate Mexican state portals
├── web/
│   ├── index.html
│   ├── estilos.css
│   └── app.js
├── tests/                  synthetic portals for testing without network access
└── datos/                  generated automatically: portals, trees and results
```

## Installation

From the terminal:

```bash
git clone https://github.com/HugoMitre/SemanticIndexMenu.git
cd SemanticIndexMenu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional, only if you are going to analyze portals whose menu is drawn with
JavaScript:

```bash
playwright install chromium
```

## Usage

```bash
./iniciar.sh          # or:  python3 app.py
```

Open `http://127.0.0.1:8000`. To stop it, `Ctrl+C`.

The first time you run an analysis the embedding model is downloaded (about
450 MB). Later runs read it from the local cache.

### How to add portals

Three ways, all in the left-hand column:

- **One at a time.** Name and address. `aguascalientes.gob.mx` is enough;
  `https://` is prepended for you.
- **Paste a list.** One per line, as `Name, URL`, `Name⇥URL` or just the
  address.
- **Load a file**, plain text or CSV, with one address per line.

The «Load the 32 federal entities» button adds the Mexican state portals
following the `[entity].gob.mx` convention. **Those addresses are not verified
one by one**: portals change domain when the administration changes. Those that
do not respond are skipped during the analysis and recorded in the log with the
reason, so that you can replace them.

It also accepts **paths to saved HTML files**. This is not merely a convenience:
for portals whose `robots.txt` restricts crawling, the right thing to do is to
save the rendered DOM once and analyze it from there, which also makes the
experiment reproducible.

### Analysis options

| Option | When to use it |
|---|---|
| Render JavaScript | The menu is not in the initial HTML because the browser draws it |
| Interact with the menu | Mega-menus whose panels appear only on hover |
| Ignore robots.txt | Only with the agency's authorization, and declare it in the article |
| Demonstration mode | Tests the whole flow without downloading the model. The numbers are **not** semantic |
| Percentile | Defines `n_ref`. 95 by default |

If a portal returns menus but no submenus, it is almost always a mega-menu: turn
on rendering and, if that is not enough, interaction.

## What it measures

For each menu-submenu pair the application computes the cosine similarity
between the labels. That cosine **is not interpretable on its own**: its scale
depends on the model. It is calibrated against the null distribution of the site
itself — every false pairing between submenus and the menus that are not their
parent — which yields the **membership index π**:

| π | Meaning |
|---|---|
| above 0.70 | well above the portal's false-pairing mean |
| close to 0.50 | at the portal's false-pairing mean |
| below 0.30 | well below that mean |

The reference is a mean, not a probability. A π below 0.50 says that the
observed similarity falls below the average of the pairings that the portal
itself does not sanction; it does not by itself establish that a random
reassignment would be preferable, nor that users would fail to find the item.
Low label similarity and poor navigation design are distinct properties, and
only the first one is measured here.

The five components per menu, all in the interval 0 to 1, where higher means
more complex:

- **Misalignment (D)** — the children do not correspond to their parent.
- **Dispersion (V)** — the siblings do not form a category among themselves.
- **Migration (X)** — fraction of children that would fit better in another menu.
- **Breadth (A)** — decision load, after the Hick-Hyman law.
- **Depth (P)** — cost of traversing levels.

The index is the convex combination `ICN = 0.35·D + 0.20·V + 0.20·X + 0.15·A +
0.10·P`, which by construction lies between 0 and 1.

## The order of the analysis is not arbitrary

The application runs three phases in this order, and it cannot be any other:

1. **Extract** the navigation trees of every portal.
2. **Compute `n_ref`** as the 95th percentile of the pooled breadth of the whole
   corpus.
3. **Evaluate** the portals, all of them with the same `n_ref`.

The reason: `n_ref` is a statistic of the corpus, so it does not exist until
everything has been extracted. And evaluating each portal with its own `n_ref`
would make the indices mutually incomparable, which is precisely what the corpus
is meant to resolve.

`n_ref` is not set by hand because of an identifiability problem: as long as no
menu saturates the cap, the term enters the index as
`w_A / log₂(1+n_ref) · log₂(1+n_i)`, so that only the ratio is identifiable.
Leaving it free would introduce a spurious degree of freedom.

## Precision: why some portals are flagged with △

μ₀ and σ₀ are estimated from a finite sample of false pairings. A portal with 2
submenus and 6 menus contributes only 10 null pairs, and with so few the scale of
π is poorly determined.

The application computes the 95% confidence interval of the mean membership by
resampling the null distribution. The difference is large:

| Null pairs | Width of the π interval |
|---:|---:|
| 10 | 0.275 |
| 33 | 0.155 |
| 88 | 0.096 |
| 301 | 0.052 |

A portal calibrated with 10 null pairs carries five times more uncertainty than
one with 300. Presenting them in the same table without distinguishing them is
not defensible.

A criterion based **only** on the width of the interval has a hole in it, and a
subtle one. When there are very few null pairs, σ₀ collapses towards zero, the
argument of the logistic function blows up and π saturates at 0 or 1 in every
resample. The interval comes out extremely narrow — not because the estimate is
precise, but because the function is saturated — and the portal would pass the
filter. A portal with a single submenu can report π = 0.998 with an interval
0.002 wide.

That is why four conditions are required at once:

| Condition | Threshold | What it prevents |
|---|---|---|
| Null pairs | ≥ 30 | σ₀ being inestimable |
| Parent-child edges | ≥ 8 | D, V and X being structural zeros |
| Width of the interval | ≤ 0.15 | imprecision of the scale |
| π not saturated | outside 0.03–0.97 | the logistic function hiding the imprecision |

The threshold of 30 null pairs comes from the simulation above: with 33 the
interval is 0.155 wide and with 60 it drops to 0.115; below 30 it stops being
usable.

Portals that fail any condition are flagged with △, state the specific reason
and **are left out of the corpus statistics** — quartiles, correlations, weight
sensitivity — although they remain visible in the table. It almost always means
that the extraction recovered few submenus, not that the portal is simple: retry
them with rendering or interaction before concluding anything.

## Components without variance

If a component takes the same value in every portal, the application points it
out and computes the consequence. This is not harmless: it consumes weight
without discriminating, adds a constant to every index and compresses the range
towards the center, so that real differences look smaller than they are. The
index ends up with fewer effective dimensions than it declares.

The warning includes what would happen if the component were removed and the
weights renormalized: the correlation between the two rankings and how the range
of the index changes. If the ordering is preserved, removing it simplifies the
method at no cost.

Before removing anything, rule out that the problem is one of measurement rather
than of the index. If every portal comes out with the same depth because the
extractor does not descend further, what is failing is the extraction.

## What to report in the article

The results panel is designed so that you can paste its figures directly. The
four blocks a reviewer will look for:

**Correlation between components.** If two of them exceed ρ = 0.90 they are
measuring the same thing and the index is counting it twice. The application
flags this explicitly.

**Weight sensitivity.** The weights are resampled from a uniform Dirichlet
distribution over the simplex and the change in the ranking is measured with
Kendall's τ. This is the empirical answer to the objection that the weights are
arbitrary. With a mean τ above 0.85 the ranking is robust; below 0.65 it should
not be presented without declaring the dependence.

**Empirical quartiles.** They replace any conventional interpretation band.
"This portal is in the upper quartile among the states evaluated" is verifiable;
"this portal is confusing" is not.

**Parameters of the measurement.** Report μ₀ and σ₀ — they make the scale of π
interpretable and they vary by site — together with `n_ref` and its saturation
rate.

Cronbach's α is shown because reviewers ask for it, with the caveat that the ICN
is a **formative** index: the components define the construct rather than
reflecting a common latent factor, so a low α is not a defect but what is to be
expected from complementary components.

## What the measure does not establish

The index describes the semantics of the labels. It incorporates neither human
category judgments nor navigation-task outcomes, so the percentages of items
with low membership are outputs of the method and not estimates of its accuracy:
they do not convert into a false-positive rate and they do not demonstrate that
a portal is hard to navigate. Nor has it been verified that a different
embedding model would order the pairs in the same way; calibration fixes the
scale within a portal, not the ordering. Organizational taxonomies — an agency
menu grouping proper names of institutions — receive a penalty that reflects a
limit of the instrument, not a defect of the portal.

## Exporting

The buttons in the results header download the CSV, with one row per portal, and
the full JSON, which includes the per-menu detail and the worst-placed submenus
of each portal.

The intermediate files are left in `datos/`: `portales.json` with your list,
`arboles/` with the navigation extracted from each portal, `n_ref.json` with the
log of the reference-breadth computation, and `resultados.json` with the
complete report.

## Testing without network access

The `tests/` folder ships synthetic portals. Add their paths and turn on
demonstration mode to check that the whole flow runs before downloading the
model:

```
tests/fixture_portal.html      classic menu with nested lists
tests/fixture_megamenu.html    mega-menu with detached panels
```

## Known problems

| Symptom | Cause | Fix |
|---|---|---|
| `command not found: python` | macOS has not shipped `python` since Monterey | use `python3`, or activate the virtual environment |
| A portal is skipped as "no submenus" | mega-menu drawn by JavaScript | turn on rendering and, if that is not enough, interaction |
| A portal is skipped because of `robots.txt` | the site restricts crawling | save the HTML and add the file path |
| The analysis takes several minutes | the first run downloads the model | later ones use the cache |
| Port 8000 is busy | another process is using it | change the port at the end of `app.py` |
