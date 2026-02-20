# Minimal stub for flashy.utils used by Audiocraft

def averager(*args, **kwargs):
    # Audiocraft imports this symbol but does not rely on real functionality
    class _Averager:
        def update(self, *a, **k):
            pass

        def reset(self):
            pass

        def value(self):
            return 0

    return _Averager()
