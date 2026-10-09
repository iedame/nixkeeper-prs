"""The tests never use the network: a request that no test answers itself
(mock.patch) fails at once, as a server being down would."""

import urllib.request


def _offline(*args, **kwargs):
    raise OSError("the tests don't use the network")


urllib.request.urlopen = _offline
