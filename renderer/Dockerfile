FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# Fonts used by the menu templates, from the Google Fonts repo (all SIL OFL).
# Pinned to an immutable commit (not "main", which moves) so builds are reproducible.
ARG GOOGLE_FONTS_SHA=baa2e5561af8a4873b058859dcfe158bdd033942
RUN mkdir -p /usr/share/fonts/truetype/khanshoof && cd /usr/share/fonts/truetype/khanshoof && \
    curl -fsSL -o Inter.ttf "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf" && \
    curl -fsSL -o JetBrainsMono.ttf "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf" && \
    for w in Regular Medium SemiBold Bold; do \
      curl -fsSL -o "IBMPlexSansArabic-$w.ttf" "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/ibmplexsansarabic/IBMPlexSansArabic-$w.ttf"; \
    done && fc-cache -f && \
    fc-list | grep -qi "IBM Plex Sans Arabic" && fc-list | grep -qi "Inter" && fc-list | grep -qi "JetBrains Mono"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py smoke.py ./
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
