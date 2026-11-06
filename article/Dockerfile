# TeX for the article, so `make article` builds the PDF with no local TeX Live.
#
# pandoc/extra ships a trimmed TeX Live: enough for the schedule, not for the SBC class,
# which pulls orcidlink, tabularray, fontawesome, academicons and three font families
# (TeX Gyre Termes, Roboto, Fira Sans) by name through fontspec.
FROM pandoc/extra:latest

# The image's tlmgr is older than the repository it points at and refuses to install
# anything until it updates itself.
RUN tlmgr update --self

# One package at a time, so a name the repository does not carry is reported and skipped
# instead of failing the whole install.
RUN for p in orcidlink tabularray ninecolors footmisc xurl fontawesome fontawesome5 \
      sectsty psnfss babel-portuges hyphen-portuguese lineno natbib \
      tcolorbox environ trimspaces etoolbox caption float academicons \
      collection-fontsrecommended pgf xcolor microtype ragged2e multirow \
      enumitem titlesec fancyhdr lastpage changepage hyperref url tex-gyre \
      roboto fira xstring abstract lm geometry amsmath amsfonts \
      l3packages l3kernel iftex fontaxes mweights kastrup babel; do \
      tlmgr install "$p" >/dev/null 2>&1 || echo "MISSING $p"; \
    done

# XeTeX finds fonts by name through fontconfig, which does not look inside the TeX tree.
RUN printf '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n<dir>/opt/texlive/texdir/texmf-dist/fonts/opentype</dir>\n<dir>/opt/texlive/texdir/texmf-dist/fonts/truetype</dir>\n<dir>/opt/texlive/texdir/texmf-dist/fonts/type1</dir>\n</fontconfig>\n' \
      > /etc/fonts/local.conf && fc-cache -f >/dev/null
