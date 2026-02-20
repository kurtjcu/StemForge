# Minimal stub for torchdiffeq used by Audiocraft

def odeint(func, y0, t, *args, **kwargs):
    # Dummy ODE solver: returns y0 repeated for each time step
    import torch
    return torch.stack([y0 for _ in t], dim=0)
