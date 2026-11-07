# Delivery checklist — 03/12/2026

The final delivery (meeting 19): the article in its final version, a tagged repository, and the
defense presented and rehearsed. Work through the sections in order; each box is something to
check, not something that happens on its own.

## 1. Merge the open stack (by 26/11)

The remaining work is one stack of pull requests on `gabrielforster/tcc`. Merge it **bottom
up**: each PR's base is the branch below it, so merging out of order merges the lower PR's
commits through the upper one without review.

- [ ] `gh stack view` (or the stack on GitHub) lists the PRs in this order, bottom to top
      (the last four were numbered out of stack order, so go by this list, not by number):

      1. `#32` — test-database fix
      2. `#33` — critical-function coverage and flow tests
      3. `#34` — local LLM arm (Ollama)
      4. `#35` — experiment against the baselines
      5. `#36` — LiveKit voice call
      6. `#37` — article rewrite
      7. #42 `feat/spoken-replies` — replies shaped for the ear on the call
      8. #39 `docs/schedule-and-readme-catch-up` — schedule and README
      9. #40 `docs/article-within-the-page-limit` — article cut to the page limit
      10. #41 `docs/defense-deck` — defense deck, demo script and this checklist
- [ ] Merge each one only after the one below it is on `master`; then retarget the next to
      `master` (GitHub does this when the base branch is deleted on merge).
- [ ] After the last merge, on a clean checkout of `master`:

```sh
git switch master && git pull
make setup
make test                 # with TEST_DATABASE_URL set in .env and `make up` running
make lint
make coverage             # fails if any critical group is under 80%
```

GitHub Actions has been failing in 2 s without starting, because of a billing lock on the
account. Until that is fixed, a green check means nothing — the local run above is the
verification.

## 2. Rebuild the documents (after the merge)

```sh
make article              # article/main.pdf — XeLaTeX + BibTeX in Docker; first build ~15 min
make cronograma           # cronograma.pdf from cronograma.md
```

- [ ] `article/main.pdf` builds with no `!` errors and has no `\pendente` text left
      (`grep -c pendente article/main.tex` returns 0).
- [ ] Every number in the article matches its source in `docs/` (the PR descriptions list
      where each one comes from). In particular the spoken-reply duration of the voice call, which
      also appears in `pitch/defesa.html` (constant `DEFESA.vozFala`) and twice in
      `pitch/roteiro-defesa.md` (marked `VOZ_FALA`).
- [ ] The page count against the venue's limit: the proposal's `article/README.md` states a
      4–6 page body; confirm with the advisor whether that limit applies to the final article.
- [ ] `cronograma.pdf` is committed if `cronograma.md` changed (it is tracked; `main.pdf` is not).

## 3. Tag the delivery (on 03/12, after the final merge)

Tag the exact commit that is delivered, so the article's reproduction commands point at code that
does not move. The tag is annotated so it carries a date and a message.

```sh
git switch master && git pull
git tag -a v1.0-entrega -m "Entrega final do TCC — 03/12/2026"
git push origin v1.0-entrega
```

Then publish the PDFs with the tag, since `article/main.pdf` is ignored by git:

```sh
gh release create v1.0-entrega article/main.pdf cronograma.pdf \
  --title "Entrega final — 03/12/2026" \
  --notes "Artigo final, cronograma e o código no estado entregue."
```

- [ ] The tag points at the merged `master`, not at a stack branch.
- [ ] The release shows both PDFs.

## 4. The defense

- [ ] `pitch/defesa.html` opens offline on the presenting laptop (double-click, or
      `cd pitch && python3 -m http.server 8000`). Fonts and reveal.js are vendored.
- [ ] Speaker view (`S`) shows the notes; the timings in `pitch/roteiro-defesa.md` add up to
      about 15–16 minutes of talk plus 3 of demo.
- [ ] Export a PDF of the deck as a fallback for a projector without a browser:
      `cd pitch && ./export-pdf.sh defesa.html` — one 1280×720 page per slide, every fragment
      shown. Check it has 19 pages. (Headless `--print-to-pdf` on `?print-pdf` prints before
      reveal.js lays out the pages, so it is not used.)
- [ ] Rehearse the demo on the presenting machine the day before, following
      [`docs/demo-script.md`](./demo-script.md), and record the fallback video then.
- [ ] Rehearse the talk at least twice with a timer; read the likely questions at the end of the
      speaker script.

## 5. What to bring

- [ ] The presenting laptop, charged, with the repository at the tag, `make setup` and
      `make voices` already run, and the LiveKit image pulled (the demo runs with no network).
- [ ] Charger and an HDMI/USB-C adapter for the projector.
- [ ] A headset with a microphone, for the voice-call demo (an open laptop microphone picks up
      the room and keeps the energy-based end-of-turn detector from closing the turn).
- [ ] The deck as PDF and the fallback recording on a USB stick, in case the laptop fails.
- [ ] `article/main.pdf` printed if the panel expects a paper copy.

## 6. After the defense

- [ ] Apply the panel's corrections to `article/main.tex`, rebuild, and move the tag only if
      the program requires the corrected version under the same name — otherwise tag the
      corrected commit as `v1.1-entrega`.
- [ ] `docker compose down` and remove the agent worktrees under `.claude/worktrees/`.
