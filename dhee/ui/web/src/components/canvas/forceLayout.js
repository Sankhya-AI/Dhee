// Force-directed layout for OrgGraph canvas. Pure JS, no library.
//
// Each node has {x, y, vx, vy, fx?, fy?}. fx/fy pin a node (drag, anchor).
// Forces per tick:
//   - Center attraction: pulls everyone toward (cx, cy) with k_center.
//   - Pairwise repulsion: 1/r^2 with floor on r to prevent blowup.
//   - Edge spring: Hooke's law toward rest length.
//   - Damping: v *= friction each tick (explicit Euler).
// Auto-quiesces when total kinetic energy stays under EPS for STILL_FRAMES.
export class ForceSim {
    constructor(nodes, edges, options) {
        Object.defineProperty(this, "opts", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "idx", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "adj", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "nodes", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "stillCount", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: 0
        });
        this.opts = {
            width: options.width,
            height: options.height,
            centerX: options.centerX ?? options.width / 2,
            centerY: options.centerY ?? options.height / 2,
            kCenter: options.kCenter ?? 0.005,
            kRepulse: options.kRepulse ?? 2200,
            kSpring: options.kSpring ?? 0.04,
            rest: options.rest ?? 140,
            minDist: options.minDist ?? 40,
            friction: options.friction ?? 0.86,
            stillFrames: options.stillFrames ?? 30,
            energyEps: options.energyEps ?? 0.05,
        };
        this.nodes = nodes;
        this.idx = new Map(nodes.map((n) => [n.id, n]));
        this.adj = new Map();
        for (const e of edges) {
            const rest = e.rest ?? this.opts.rest;
            const a = this.adj.get(e.source) ?? [];
            a.push({ other: e.target, rest });
            this.adj.set(e.source, a);
            const b = this.adj.get(e.target) ?? [];
            b.push({ other: e.source, rest });
            this.adj.set(e.target, b);
        }
    }
    /** Sync the underlying node array if topology changes externally. */
    setNodes(nodes) {
        this.nodes = nodes;
        this.idx = new Map(nodes.map((n) => [n.id, n]));
    }
    setEdges(edges) {
        this.adj = new Map();
        for (const e of edges) {
            const rest = e.rest ?? this.opts.rest;
            const a = this.adj.get(e.source) ?? [];
            a.push({ other: e.target, rest });
            this.adj.set(e.source, a);
            const b = this.adj.get(e.target) ?? [];
            b.push({ other: e.source, rest });
            this.adj.set(e.target, b);
        }
    }
    pin(id, fx, fy) {
        const n = this.idx.get(id);
        if (!n)
            return;
        n.fx = fx;
        n.fy = fy;
    }
    unpin(id) {
        const n = this.idx.get(id);
        if (!n)
            return;
        n.fx = null;
        n.fy = null;
    }
    /** One Verlet-like tick. Returns total kinetic energy. */
    tick() {
        const { kCenter, kRepulse, kSpring, minDist, friction, centerX, centerY } = this.opts;
        const nodes = this.nodes;
        const fx = new Float64Array(nodes.length);
        const fy = new Float64Array(nodes.length);
        // Center attraction
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            fx[i] += -kCenter * (n.x - centerX);
            fy[i] += -kCenter * (n.y - centerY);
        }
        // Pairwise repulsion (O(n^2); fine for org charts <500 nodes)
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const a = nodes[i];
                const b = nodes[j];
                let dx = a.x - b.x;
                let dy = a.y - b.y;
                let dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < minDist) {
                    // jitter to avoid singularities
                    dx += (Math.random() - 0.5) * 0.5;
                    dy += (Math.random() - 0.5) * 0.5;
                    dist = Math.max(minDist, Math.sqrt(dx * dx + dy * dy));
                }
                const force = kRepulse / (dist * dist);
                const ux = dx / dist;
                const uy = dy / dist;
                fx[i] += force * ux;
                fy[i] += force * uy;
                fx[j] -= force * ux;
                fy[j] -= force * uy;
            }
        }
        // Edge springs
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            const adj = this.adj.get(n.id);
            if (!adj)
                continue;
            for (const { other, rest } of adj) {
                const m = this.idx.get(other);
                if (!m)
                    continue;
                const dx = m.x - n.x;
                const dy = m.y - n.y;
                const dist = Math.max(0.0001, Math.sqrt(dx * dx + dy * dy));
                const f = kSpring * (dist - rest);
                fx[i] += f * (dx / dist);
                fy[i] += f * (dy / dist);
            }
        }
        // Integrate
        let energy = 0;
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            if (n.fx != null && n.fy != null) {
                n.x = n.fx;
                n.y = n.fy;
                n.vx = 0;
                n.vy = 0;
                continue;
            }
            n.vx = (n.vx + fx[i]) * friction;
            n.vy = (n.vy + fy[i]) * friction;
            n.x += n.vx;
            n.y += n.vy;
            energy += n.vx * n.vx + n.vy * n.vy;
        }
        if (energy < this.opts.energyEps)
            this.stillCount += 1;
        else
            this.stillCount = 0;
        return energy;
    }
    isQuiesced() {
        return this.stillCount >= this.opts.stillFrames;
    }
    resetQuiescence() {
        this.stillCount = 0;
    }
}
/** Seed positions in a circle; cheap and avoids identical (0,0) starts. */
export function seedCircle(ids, width, height, radius) {
    const cx = width / 2;
    const cy = height / 2;
    const r = radius ?? Math.min(width, height) * 0.35;
    return ids.map((id, i) => {
        const a = (i / Math.max(1, ids.length)) * Math.PI * 2;
        return {
            id,
            x: cx + Math.cos(a) * r * (0.6 + Math.random() * 0.4),
            y: cy + Math.sin(a) * r * (0.6 + Math.random() * 0.4),
            vx: 0,
            vy: 0,
        };
    });
}
