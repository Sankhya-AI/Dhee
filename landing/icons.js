/**
 * Engram Landing Page - Animated Feature Icons
 * 3D line-art style icons for each feature card
 */

class FeatureIcons {
    constructor() {
        this.initDecayIcon();
        this.initEchoIcon();
        this.initCategoryIcon();
    }

    // FadeMem - Stacked rings representing memory layers
    initDecayIcon() {
        const canvas = document.getElementById('icon-decay');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        canvas.width = 120 * dpr;
        canvas.height = 120 * dpr;
        ctx.scale(dpr, dpr);

        let time = 0;

        const draw = () => {
            ctx.clearRect(0, 0, 120, 120);
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 1.5;

            const cx = 60;
            const cy = 60;
            const layers = 5;

            for (let i = 0; i < layers; i++) {
                const yOffset = i * 8 - 16;
                const radiusX = 35 - i * 2;
                const radiusY = 12 - i * 1;
                const opacity = 1 - (i * 0.15);
                const phase = time * 0.02 + i * 0.3;

                ctx.save();
                ctx.globalAlpha = opacity;

                // Draw ellipse (ring)
                ctx.beginPath();
                ctx.ellipse(
                    cx + Math.sin(phase) * 2,
                    cy + yOffset + Math.cos(phase) * 1,
                    radiusX,
                    radiusY,
                    0,
                    0,
                    Math.PI * 2
                );
                ctx.stroke();

                // Draw decay particles fading off
                if (i < 3) {
                    const particleCount = 3 - i;
                    for (let p = 0; p < particleCount; p++) {
                        const angle = (time * 0.01 + p * (Math.PI * 2 / particleCount) + i);
                        const dist = radiusX + 5 + Math.sin(time * 0.03 + p) * 3;
                        const px = cx + Math.cos(angle) * dist;
                        const py = cy + yOffset + Math.sin(angle) * (radiusY * 0.5);
                        const size = 2 - i * 0.5;

                        ctx.globalAlpha = opacity * 0.5 * (1 - (dist - radiusX) / 15);
                        ctx.beginPath();
                        ctx.arc(px, py, size, 0, Math.PI * 2);
                        ctx.stroke();
                    }
                }

                ctx.restore();
            }

            time++;
            requestAnimationFrame(draw);
        };

        draw();
    }

    // EchoMem - Dotted sphere representing multi-modal encoding
    initEchoIcon() {
        const canvas = document.getElementById('icon-echo');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        canvas.width = 120 * dpr;
        canvas.height = 120 * dpr;
        ctx.scale(dpr, dpr);

        let time = 0;

        const draw = () => {
            ctx.clearRect(0, 0, 120, 120);
            ctx.fillStyle = '#333';

            const cx = 60;
            const cy = 60;
            const radius = 35;

            // Draw dots on sphere surface
            const latitudes = 12;
            const longitudes = 16;

            for (let lat = 0; lat < latitudes; lat++) {
                const theta = (lat / latitudes) * Math.PI;
                const y = Math.cos(theta) * radius;
                const ringRadius = Math.sin(theta) * radius;

                for (let lon = 0; lon < longitudes; lon++) {
                    const phi = (lon / longitudes) * Math.PI * 2 + time * 0.01;

                    const x = Math.cos(phi) * ringRadius;
                    const z = Math.sin(phi) * ringRadius;

                    // 3D to 2D projection (simple)
                    const scale = 1 + z * 0.01;
                    const px = cx + x * scale;
                    const py = cy + y * scale * 0.8; // Slight vertical compression

                    // Dot size based on z-depth
                    const dotSize = 1.5 + (z / radius) * 0.8;
                    const opacity = 0.3 + (z / radius + 1) * 0.35;

                    // Pulse effect
                    const pulse = Math.sin(time * 0.05 + lat * 0.5 + lon * 0.3) * 0.3 + 0.7;

                    ctx.globalAlpha = opacity * pulse;
                    ctx.beginPath();
                    ctx.arc(px, py, Math.max(0.5, dotSize), 0, Math.PI * 2);
                    ctx.fill();
                }
            }

            // Draw echo waves emanating
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 1;
            for (let w = 0; w < 3; w++) {
                const waveRadius = radius + 5 + ((time * 0.5 + w * 20) % 25);
                const opacity = 1 - ((time * 0.5 + w * 20) % 25) / 25;
                ctx.globalAlpha = opacity * 0.3;
                ctx.beginPath();
                ctx.arc(cx, cy, waveRadius, 0, Math.PI * 2);
                ctx.stroke();
            }

            time++;
            requestAnimationFrame(draw);
        };

        draw();
    }

    // CategoryMem - Connected nodes representing hierarchical organization
    initCategoryIcon() {
        const canvas = document.getElementById('icon-category');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        canvas.width = 120 * dpr;
        canvas.height = 120 * dpr;
        ctx.scale(dpr, dpr);

        let time = 0;

        // Node positions
        const nodes = [
            { x: 60, y: 30, size: 6, children: [1, 2, 3] },      // Root
            { x: 30, y: 55, size: 5, children: [4] },            // Child 1
            { x: 60, y: 60, size: 5, children: [5, 6] },         // Child 2
            { x: 90, y: 55, size: 5, children: [7] },            // Child 3
            { x: 25, y: 85, size: 4, children: [] },             // Leaf 1
            { x: 50, y: 88, size: 4, children: [] },             // Leaf 2
            { x: 70, y: 88, size: 4, children: [] },             // Leaf 3
            { x: 95, y: 85, size: 4, children: [] },             // Leaf 4
        ];

        const draw = () => {
            ctx.clearRect(0, 0, 120, 120);

            // Draw connections
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 1;

            nodes.forEach((node, i) => {
                node.children.forEach(childIdx => {
                    const child = nodes[childIdx];

                    // Animated flow along connection
                    const flowPos = (time * 0.03 + i * 0.2) % 1;

                    ctx.globalAlpha = 0.5;
                    ctx.beginPath();
                    ctx.moveTo(node.x, node.y);
                    ctx.lineTo(child.x, child.y);
                    ctx.stroke();

                    // Flow particle
                    const fx = node.x + (child.x - node.x) * flowPos;
                    const fy = node.y + (child.y - node.y) * flowPos;
                    ctx.globalAlpha = 0.8;
                    ctx.fillStyle = '#6366f1';
                    ctx.beginPath();
                    ctx.arc(fx, fy, 2, 0, Math.PI * 2);
                    ctx.fill();
                });
            });

            // Draw nodes
            ctx.fillStyle = '#333';
            nodes.forEach((node, i) => {
                const pulse = Math.sin(time * 0.04 + i * 0.5) * 0.15 + 1;
                const size = node.size * pulse;

                // Node ring
                ctx.globalAlpha = 0.3;
                ctx.strokeStyle = '#333';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(node.x, node.y, size + 3, 0, Math.PI * 2);
                ctx.stroke();

                // Node fill
                ctx.globalAlpha = 0.9;
                ctx.beginPath();
                ctx.arc(node.x, node.y, size, 0, Math.PI * 2);
                ctx.fill();
            });

            // Decay effect - some leaves fading
            ctx.globalAlpha = Math.abs(Math.sin(time * 0.02)) * 0.5;
            ctx.strokeStyle = '#999';
            ctx.setLineDash([2, 2]);
            ctx.beginPath();
            ctx.arc(nodes[4].x, nodes[4].y, 10, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);

            time++;
            requestAnimationFrame(draw);
        };

        draw();
    }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    new FeatureIcons();
});
