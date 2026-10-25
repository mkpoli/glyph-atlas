"""A contrastive shape encoder trained on one family's restored ink.

Two randomly altered views of a crop must match each other and no other crop in the batch (SimCLR, Chen et
al. 2020, arXiv:2002.05709). The alterations vary everything that is not stroke shape: position, size,
proportions, slant, ink weight and density, blur, broken strokes, specks and a stray fragment at a border,
so the encoder learns to disregard them. `heavy` widens the ink-weight changes; the two encoders' features
are joined.
"""
from __future__ import annotations

import numpy as np

#: Side of the encoder input.
SIDE = 64


def train(ink: np.ndarray, *, epochs: int = 100, seed: int = 0, heavy: bool = False, device: str = "cuda") -> np.ndarray:
    """512-d unit features of every image in `ink` (N × 96 × 96, ink high)."""
    import timm
    import torch
    import torch.nn.functional as F
    from torch import nn

    torch.manual_seed(seed)
    np.random.seed(seed)
    dev = device
    X = torch.from_numpy(ink).float().div(255).unsqueeze(1).to(dev)
    n, frame = len(X), X.shape[-1]

    def blur(x, s):
        k = torch.arange(-3, 4, device=dev).float()
        w = torch.exp(-(k[None] ** 2) / (2 * s[:, None] ** 2 + 1e-6))
        w = w / w.sum(1, keepdim=True)
        b = x.shape[0]
        xs = x.view(1, b, *x.shape[2:])
        xs = F.conv2d(F.pad(xs, (3, 3, 0, 0), mode="replicate"), w.view(b, 1, 1, 7), groups=b)
        xs = F.conv2d(F.pad(xs, (0, 0, 3, 3), mode="replicate"), w.view(b, 1, 7, 1), groups=b)
        return xs.view_as(x)

    def view(x):
        b = x.shape[0]

        def u(a, c):
            return torch.empty(b, device=dev).uniform_(a, c)

        def per(v):
            return v[:, None, None, None]

        # ink weight and density
        r = torch.rand(b, device=dev)
        if heavy:
            x = torch.where(per(r < 0.2), F.max_pool2d(x, 3, 1, 1), x)
            x = torch.where(per((r >= 0.2) & (r < 0.35)), F.max_pool2d(x, 7, 1, 3), x)
            x = torch.where(per(r > 0.65), -F.max_pool2d(-x, 3, 1, 1), x)
            x = torch.where(per(r > 0.85), -F.max_pool2d(-x, 5, 1, 2), x)
        else:
            x = torch.where(per(r < 0.25), F.max_pool2d(x, 3, 1, 1), x)
            x = torch.where(per(r > 0.75), -F.max_pool2d(-x, 3, 1, 1), x)
        x = torch.where(per(torch.rand(b, device=dev) < 0.3), (x > per(u(0.2, 0.6))).float(), x)
        x = x.clamp(0, 1) ** per(u(0.5, 2.0))
        # dry brush and broken strokes
        keep = (torch.rand_like(x) > per(u(0, 0.35))).float()
        x = x * F.max_pool2d(keep, 3, 1, 1) * torch.where(per(torch.rand(b, device=dev) < 0.5), keep, torch.ones_like(keep))
        yy, xx = torch.arange(frame, device=dev)[None, :, None], torch.arange(frame, device=dev)[None, None, :]
        for _ in range(2):
            cy, cx = torch.randint(0, frame, (2, b), device=dev)
            s = torch.randint(4, 12, (b,), device=dev)
            hole = ((yy - cy[:, None, None]).abs() < s[:, None, None]) & ((xx - cx[:, None, None]).abs() < s[:, None, None])
            x = x * (1 - (hole & (torch.rand(b, device=dev) < 0.4)[:, None, None]).float()[:, None])
        # a stray fragment of another crop at a border
        other = X[torch.randint(0, n, (b,), device=dev)]
        off = u(1.45, 1.75) * (torch.randint(0, 2, (b,), device=dev) * 2 - 1)
        ft = torch.zeros(b, 2, 3, device=dev)
        ft[:, 0, 0] = 1
        ft[:, 1, 1] = 1
        side = torch.rand(b, device=dev) < 0.5
        ft[:, 0, 2] = torch.where(side, off, u(-0.3, 0.3))
        ft[:, 1, 2] = torch.where(side, u(-0.3, 0.3), off)
        frag = F.grid_sample(other, F.affine_grid(ft, other.shape, align_corners=False), align_corners=False)
        x = torch.maximum(x, frag * per((torch.rand(b, device=dev) < 0.25).float()) * per(u(0.3, 1)))
        # rotation, slant, aspect, scale, shift
        a, shear, sc, asp = u(-0.2, 0.2), u(-0.25, 0.25), u(0.75, 1.1), torch.exp(u(-0.2, 0.2))
        sx, sy = sc * asp, sc / asp
        th = torch.zeros(b, 2, 3, device=dev)
        th[:, 0, 0] = torch.cos(a) / sx
        th[:, 0, 1] = (-torch.sin(a) + shear) / sx
        th[:, 1, 0] = torch.sin(a) / sy
        th[:, 1, 1] = torch.cos(a) / sy
        th[:, 0, 2] = u(-0.12, 0.12)
        th[:, 1, 2] = u(-0.12, 0.12)
        x = F.grid_sample(x, F.affine_grid(th, (b, 1, SIDE, SIDE), align_corners=False), align_corners=False)
        x = blur(x, u(0.01, 1.2))
        return x * per(u(0.5, 1)) + torch.randn_like(x) * per(u(0, 0.08))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.f = timm.create_model("resnet18", pretrained=False, num_classes=0, in_chans=1)
            self.f.conv1 = nn.Conv2d(1, 64, 3, 2, 1, bias=False)
            self.f.maxpool = nn.Identity()
            self.g = nn.Sequential(nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(), nn.Linear(512, 128))

        def forward(self, x):
            h = self.f(x)
            return h, self.g(h)

    net = Net().to(dev).to(memory_format=torch.channels_last)
    batch = min(512, n // 2 * 2)
    steps = epochs * (n // batch)
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=steps, pct_start=0.05)
    target = torch.cat([torch.arange(batch, 2 * batch), torch.arange(0, batch)]).to(dev)
    for _ in range(epochs):
        perm = torch.randperm(n, device=dev)
        for k in range(n // batch):
            x = X[perm[k * batch:(k + 1) * batch]]
            v = torch.cat([view(x), view(x)])
            with torch.autocast("cuda", torch.bfloat16):
                _, z = net(v.contiguous(memory_format=torch.channels_last))
            z = F.normalize(z.float(), dim=1)
            sim = z @ z.T / 0.1
            sim.fill_diagonal_(-1e9)
            loss = F.cross_entropy(sim, target)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
    net.eval()
    out = []
    with torch.no_grad():
        for i in range(0, n, 1024):
            h, _ = net(F.interpolate(X[i:i + 1024], size=SIDE, mode="bilinear", antialias=True))
            out.append(F.normalize(h.float(), dim=1).cpu().numpy())
    return np.concatenate(out)
