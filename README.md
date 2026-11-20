# tv-guide

One combined TV guide (XMLTV) for my IPTV playlist, rebuilt about every 6 hours by GitHub Actions and published with GitHub Pages.

**Guide link:** https://lanternbox-star.github.io/tv-guide/guide.xml.gz
**Last build details:** https://lanternbox-star.github.io/tv-guide/status.json

- `channels.json` lists which guide source each channel's listings come from.
- `build_guide.py` downloads the sources, keeps only those channels and the next 8 days, and writes `site/guide.xml.gz`. Python standard library only.
- If a source is down, that source's channels keep their listings from the previous guide. If fewer than half the channels have listings, nothing is published and the current guide stays live.
- To rebuild now: Actions tab, **Build TV guide**, **Run workflow**.
