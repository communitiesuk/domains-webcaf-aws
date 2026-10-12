from pathlib import Path

from django.conf import settings
from weasyprint.urls import URLFetcher


class StaticFileURLFetcher(URLFetcher):
    """Serve every asset a PDF asks for from STATIC_ROOT, and nothing else.

    WeasyPrint cannot fetch the relative asset URLs Django emits, so each one is
    mapped to a file under STATIC_ROOT. Only file URLs are allowed and a path
    that resolves outside STATIC_ROOT is refused, so a crafted URL in the HTML
    cannot make the PDF render read other files or reach the network.
    """

    def __init__(self, **kwargs):
        super().__init__(allowed_protocols=("file",), **kwargs)

    def fetch(self, url, headers=None):
        root = Path(settings.STATIC_ROOT).resolve()
        path = (root / url.split("assets/")[-1]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Refusing to load a file outside STATIC_ROOT: {url}")
        return super().fetch(path.as_uri(), headers)
