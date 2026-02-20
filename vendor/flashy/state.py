# Minimal stub for flashy.state used by Audiocraft

class StateDictSource:
    """
    Dummy base class to satisfy Audiocraft's expectations.
    """
    pass


class StateDict(dict):
    """
    Dummy StateDict class. Real Flashy uses a dict-like structure
    for checkpoint/state-dict management. Audiocraft only needs
    the class to exist.
    """
    pass
