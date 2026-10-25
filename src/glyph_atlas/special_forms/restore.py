"""Restoration: from a grey crop as written to its ideal ink.

The ideal is solid strokes with smooth edges, no specks and no fragments of neighbouring characters. There
is no clean copy of any crop, so the clean state is taken from the crisp crops of the same family (high
contrast, sharp edges, few specks, few pieces, ink already near its smoothed form): their ink with gaps
closed and edges smoothed is the target, and copies of them made worse on purpose (faint, blurred, dry
brush, blots, stained or grainy paper, specks, a stray fragment at the border) are the input. A small U-Net
learns the way back and is then run on every crop.
"""
from __future__ import annotations

import numpy as np

#: Quality floor of a crop the restoration learns from: contrast, edge sharpness, share of ink in
#: specks, pieces of ink, and agreement of its mask with its smoothed form (IoU).
CRISP = {"contrast": 0.55, "sharp": 0.35, "specks": 0.02, "pieces": 4, "iou": 0.88}


def crisp(quality: np.ndarray, mask: np.ndarray) -> np.ndarray:
    q = quality
    return np.nonzero((q[:, 0] >= CRISP["contrast"]) & (q[:, 1] >= CRISP["sharp"]) & (q[:, 2] < CRISP["specks"])
                      & (q[:, 3] <= CRISP["pieces"]) & (mask.max(axis=(1, 2)) > 0))[0]


def train(grey: np.ndarray, mask: np.ndarray, quality: np.ndarray, *, steps: int = 6000, seed: int = 0,
          device: str = "cuda") -> tuple[np.ndarray, int]:
    """Restored ink (0–255, ink high) of every crop, and how many crisp crops it learnt from."""
    import torch
    import torch.nn.functional as F
    from torch import nn

    torch.manual_seed(seed)
    dev = device

    def gblur(x, s):
        k = torch.arange(-4, 5, device=x.device).float()
        w = torch.exp(-(k[None] ** 2) / (2 * s[:, None] ** 2 + 1e-6))
        w = w / w.sum(1, keepdim=True)
        b = x.shape[0]
        xs = x.reshape(1, b, *x.shape[2:])
        xs = F.conv2d(F.pad(xs, (4, 4, 0, 0), mode="replicate"), w.view(b, 1, 1, 9), groups=b)
        xs = F.conv2d(F.pad(xs, (0, 0, 4, 4), mode="replicate"), w.view(b, 1, 9, 1), groups=b)
        return xs.view_as(x)

    def ideal(m):
        # close dry-brush gaps, smooth the outline, fill pinholes
        m = -F.max_pool2d(-F.max_pool2d(m, 3, 1, 1), 3, 1, 1)
        return (gblur(m, torch.full((m.shape[0],), 1.2, device=m.device)) > 0.5).float()

    def stretch(x):
        lo = torch.quantile(x.flatten(1), 0.01, dim=1)[:, None, None, None]
        hi = torch.quantile(x.flatten(1), 0.99, dim=1)[:, None, None, None]
        return ((x - lo) / (hi - lo).clamp(min=0.05)).clamp(0, 1)

    grey_t = torch.from_numpy(grey).float().div(255)
    mask_t = torch.from_numpy(mask).float().div(255)
    pool = torch.from_numpy(crisp(quality, mask))
    with torch.no_grad():
        m = mask_t[pool].unsqueeze(1).to(dev)
        t = ideal(m)
        iou = ((m > 0.5) & (t > 0.5)).flatten(1).sum(1) / ((m > 0.5) | (t > 0.5)).flatten(1).sum(1).clamp(min=1)
        pool = pool[(iou > CRISP["iou"]).cpu()]
    G = grey_t[pool].unsqueeze(1).to(dev)
    T = ideal(mask_t[pool].unsqueeze(1).to(dev))

    def degrade(x, t):
        b = x.shape[0]

        def u(a, c):
            return torch.empty(b, 1, 1, 1, device=dev).uniform_(a, c)

        x = stretch(x)
        # geometry, shared with the target
        th = torch.zeros(b, 2, 3, device=dev)
        a, sc = u(-0.15, 0.15).view(b), u(0.85, 1.1).view(b)
        flip = torch.where(torch.rand(b, device=dev) < 0.5, -1.0, 1.0)
        th[:, 0, 0] = torch.cos(a) / sc * flip
        th[:, 0, 1] = -torch.sin(a) / sc
        th[:, 1, 0] = torch.sin(a) / sc * flip
        th[:, 1, 1] = torch.cos(a) / sc
        th[:, :, 2] = torch.empty(b, 2, device=dev).uniform_(-0.08, 0.08)
        grid = F.affine_grid(th, x.shape, align_corners=False)
        x = F.grid_sample(x, grid, align_corners=False, padding_mode="border")
        t = (F.grid_sample(t, grid, align_corners=False) > 0.5).float()
        ink = 1 - x
        # dry brush: streaky dropout
        n = F.interpolate(torch.rand(b, 1, 24, 24, device=dev), size=x.shape[-1], mode="bilinear")
        streak = gblur(torch.rand_like(ink), u(0.3, 1.0).view(b))
        dry = torch.rand(b, 1, 1, 1, device=dev) < 0.5
        ink = torch.where(dry, ink * (streak > u(0.3, 0.6)).float().maximum(n * 0.5), ink)
        # faint, blurred, blotted
        ink = gblur(ink * u(0.3, 1.0), u(0.01, 1.8).view(b))
        blot = gblur((torch.rand_like(ink) < 0.0015).float(), u(1.5, 3.5).view(b)) * 25
        ink = (ink + blot * t * (torch.rand(b, 1, 1, 1, device=dev) < 0.3).float()).clamp(0, 1)
        # a stray fragment of another crop at a border, not in the target
        o = 1 - stretch(G[torch.randint(0, len(G), (b,), device=dev)])
        ft = torch.zeros(b, 2, 3, device=dev)
        ft[:, 0, 0] = 1
        ft[:, 1, 1] = 1
        side = torch.rand(b, device=dev) < 0.5
        off = u(1.4, 1.75).view(b) * torch.where(torch.rand(b, device=dev) < 0.5, -1.0, 1.0)
        ft[:, 0, 2] = torch.where(side, off, 0 * off)
        ft[:, 1, 2] = torch.where(side, 0 * off, off)
        frag = F.grid_sample(o, F.affine_grid(ft, o.shape, align_corners=False), align_corners=False)
        ink = torch.maximum(ink, frag * (torch.rand(b, 1, 1, 1, device=dev) < 0.3).float() * u(0.2, 1))
        # paper: tone, a low-frequency stain, grain, specks
        paper = u(0.45, 1.0) - F.interpolate(torch.rand(b, 1, 4, 4, device=dev), size=x.shape[-1], mode="bicubic") * u(0, 0.12)
        y = paper * (1 - ink * u(0.5, 1.0)) + torch.randn_like(ink) * u(0, 0.07)
        y = torch.where(torch.rand_like(y) < u(0, 0.01), torch.rand_like(y) * 0.3, y)
        return stretch(y), t

    def block(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(True),
                             nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(True))

    class UNet(nn.Module):
        def __init__(self, c=(32, 64, 128, 256)):
            super().__init__()
            self.down = nn.ModuleList([block(1, c[0]), block(c[0], c[1]), block(c[1], c[2]), block(c[2], c[3])])
            self.up = nn.ModuleList([nn.ConvTranspose2d(c[i + 1], c[i], 2, 2) for i in range(3)])
            self.merge = nn.ModuleList([block(2 * c[i], c[i]) for i in range(3)])
            self.out = nn.Conv2d(c[0], 1, 1)

        def forward(self, x):
            skips = []
            for i, d in enumerate(self.down):
                x = d(x if i == 0 else F.max_pool2d(x, 2))
                skips.append(x)
            for i in (2, 1, 0):
                x = self.merge[i](torch.cat([self.up[i](x), skips[i]], 1))
            return self.out(x)

    net = UNet().to(dev)
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=steps, pct_start=0.05)
    for _ in range(steps):
        pick = torch.randint(0, len(G), (96,), device=dev)
        with torch.no_grad():
            x, t = degrade(G[pick], T[pick])
        with torch.autocast("cuda", torch.bfloat16):
            z = net(x)
        z = z.float()
        p = torch.sigmoid(z)
        dice = 1 - (2 * (p * t).sum((1, 2, 3)) + 1) / (p.sum((1, 2, 3)) + t.sum((1, 2, 3)) + 1)
        loss = F.binary_cross_entropy_with_logits(z, t) + dice.mean()
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
    net.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(grey_t), 512):
            x = stretch(grey_t[i:i + 512].unsqueeze(1).to(dev))
            out.append((torch.sigmoid(net(x)) * 255).byte().cpu().numpy()[:, 0])
    return np.concatenate(out), len(pool)
