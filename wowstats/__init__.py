"""wow-stats: a WoW Forever leveling log (web UI, ingest, Battle.net exporter)."""

__version__ = "0.13.3"

# Where this program's source lives (GPL-3.0-only). Binary recipients (the
# images, the one-line shipper installers) are pointed at the tag that built
# what they got: source_tag_url(). pyproject's [project.urls] Source matches
# (tests/test_license.py).
SOURCE_URL = "https://github.com/sam-dumont/wow-mcp"


def source_tag_url(version: str = __version__) -> str:
    """The source of this exact release: the repository at tag v<version>."""
    return f"{SOURCE_URL}/tree/v{version}"
