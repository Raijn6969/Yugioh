"""
`requests` erst beim ersten Netzzugriff laden (der Import kostet ~100 ms Startzeit): `from lazy_requests import requests`
steht für das Modul und lädt das echte erst bei der ersten Benutzung (requests.get, requests.Session …).
"""


class _LazyRequests:
    def __getattr__(self, name):
        import requests
        return getattr(requests, name)


requests = _LazyRequests()
