/**
 * Engram Landing Page - Animated Iridescent Ribbon
 * Creates a morphing 3D ribbon with holographic colors using Three.js
 */

class RibbonAnimation {
    constructor() {
        this.canvas = document.getElementById('ribbon-canvas');
        if (!this.canvas) return;

        this.init();
        this.createRibbon();
        this.animate();

        window.addEventListener('resize', () => this.onResize());
    }

    init() {
        // Scene setup
        this.scene = new THREE.Scene();

        // Camera
        this.camera = new THREE.PerspectiveCamera(
            45,
            window.innerWidth / window.innerHeight,
            0.1,
            1000
        );
        this.camera.position.z = 5;

        // Renderer
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: true,
            alpha: true
        });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        // Clock for animation
        this.clock = new THREE.Clock();

        // Mouse tracking for interactivity
        this.mouse = { x: 0, y: 0 };
        window.addEventListener('mousemove', (e) => {
            this.mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
            this.mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
        });
    }

    createRibbon() {
        // Ribbon geometry - parametric surface
        const ribbonWidth = 3;
        const ribbonLength = 8;
        const segmentsW = 100;
        const segmentsL = 200;

        const geometry = new THREE.PlaneGeometry(
            ribbonWidth,
            ribbonLength,
            segmentsW,
            segmentsL
        );

        // Custom shader material for iridescent effect
        const material = new THREE.ShaderMaterial({
            uniforms: {
                uTime: { value: 0 },
                uMouse: { value: new THREE.Vector2(0, 0) },
                uResolution: { value: new THREE.Vector2(window.innerWidth, window.innerHeight) }
            },
            vertexShader: `
                uniform float uTime;
                uniform vec2 uMouse;

                varying vec2 vUv;
                varying vec3 vPosition;
                varying vec3 vNormal;
                varying float vElevation;

                // Simplex noise function
                vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
                vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
                vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
                vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

                float snoise(vec3 v) {
                    const vec2 C = vec2(1.0/6.0, 1.0/3.0);
                    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

                    vec3 i  = floor(v + dot(v, C.yyy));
                    vec3 x0 = v - i + dot(i, C.xxx);

                    vec3 g = step(x0.yzx, x0.xyz);
                    vec3 l = 1.0 - g;
                    vec3 i1 = min(g.xyz, l.zxy);
                    vec3 i2 = max(g.xyz, l.zxy);

                    vec3 x1 = x0 - i1 + C.xxx;
                    vec3 x2 = x0 - i2 + C.yyy;
                    vec3 x3 = x0 - D.yyy;

                    i = mod289(i);
                    vec4 p = permute(permute(permute(
                        i.z + vec4(0.0, i1.z, i2.z, 1.0))
                        + i.y + vec4(0.0, i1.y, i2.y, 1.0))
                        + i.x + vec4(0.0, i1.x, i2.x, 1.0));

                    float n_ = 0.142857142857;
                    vec3 ns = n_ * D.wyz - D.xzx;

                    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

                    vec4 x_ = floor(j * ns.z);
                    vec4 y_ = floor(j - 7.0 * x_);

                    vec4 x = x_ *ns.x + ns.yyyy;
                    vec4 y = y_ *ns.x + ns.yyyy;
                    vec4 h = 1.0 - abs(x) - abs(y);

                    vec4 b0 = vec4(x.xy, y.xy);
                    vec4 b1 = vec4(x.zw, y.zw);

                    vec4 s0 = floor(b0)*2.0 + 1.0;
                    vec4 s1 = floor(b1)*2.0 + 1.0;
                    vec4 sh = -step(h, vec4(0.0));

                    vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
                    vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;

                    vec3 p0 = vec3(a0.xy, h.x);
                    vec3 p1 = vec3(a0.zw, h.y);
                    vec3 p2 = vec3(a1.xy, h.z);
                    vec3 p3 = vec3(a1.zw, h.w);

                    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
                    p0 *= norm.x;
                    p1 *= norm.y;
                    p2 *= norm.z;
                    p3 *= norm.w;

                    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
                    m = m * m;
                    return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
                }

                void main() {
                    vUv = uv;

                    vec3 pos = position;

                    // Create flowing ribbon shape
                    float time = uTime * 0.3;

                    // Primary wave - large flowing motion
                    float wave1 = sin(pos.y * 0.8 + time) * 0.8;
                    float wave2 = cos(pos.y * 0.5 + time * 0.7) * 0.5;

                    // Twist along the ribbon
                    float twist = pos.y * 0.3 + time * 0.5;
                    float twistAmount = sin(twist) * 0.6;

                    // Apply transformations
                    pos.x += wave1 + wave2 * pos.x * 0.5;
                    pos.z += sin(pos.y * 1.2 + time * 0.8) * 0.6;
                    pos.z += cos(pos.x * 2.0 + time) * 0.3;

                    // Noise-based displacement for organic feel
                    float noise = snoise(vec3(pos.xy * 0.5, time * 0.2));
                    pos.z += noise * 0.4;

                    // Mouse interaction - subtle attraction
                    pos.x += uMouse.x * 0.2 * (1.0 - abs(pos.y) * 0.1);
                    pos.y += uMouse.y * 0.1;

                    vElevation = pos.z;
                    vPosition = pos;

                    // Calculate normal for lighting
                    vNormal = normalize(normalMatrix * normal);

                    gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
                }
            `,
            fragmentShader: `
                uniform float uTime;
                uniform vec2 uResolution;

                varying vec2 vUv;
                varying vec3 vPosition;
                varying vec3 vNormal;
                varying float vElevation;

                // HSL to RGB conversion
                vec3 hsl2rgb(vec3 c) {
                    vec3 rgb = clamp(abs(mod(c.x*6.0+vec3(0.0,4.0,2.0),6.0)-3.0)-1.0, 0.0, 1.0);
                    return c.z + c.y * (rgb-0.5)*(1.0-abs(2.0*c.z-1.0));
                }

                void main() {
                    // Iridescent color based on position and view angle
                    float time = uTime * 0.2;

                    // Create color gradient along the ribbon
                    float hue = vUv.y * 0.6 + vUv.x * 0.3 + time * 0.1;
                    hue += vElevation * 0.15;
                    hue = mod(hue, 1.0);

                    // Color palette: purple -> blue -> pink -> orange -> teal
                    vec3 color1 = vec3(0.55, 0.2, 0.85);  // Purple
                    vec3 color2 = vec3(0.2, 0.4, 0.95);   // Blue
                    vec3 color3 = vec3(0.95, 0.4, 0.7);   // Pink
                    vec3 color4 = vec3(0.95, 0.5, 0.2);   // Orange
                    vec3 color5 = vec3(0.2, 0.8, 0.7);    // Teal

                    // Blend colors based on position
                    float blend = vUv.y + sin(vUv.x * 3.14159 + time) * 0.3;
                    blend = mod(blend + vElevation * 0.5, 1.0);

                    vec3 color;
                    if (blend < 0.2) {
                        color = mix(color1, color2, blend * 5.0);
                    } else if (blend < 0.4) {
                        color = mix(color2, color3, (blend - 0.2) * 5.0);
                    } else if (blend < 0.6) {
                        color = mix(color3, color4, (blend - 0.4) * 5.0);
                    } else if (blend < 0.8) {
                        color = mix(color4, color5, (blend - 0.6) * 5.0);
                    } else {
                        color = mix(color5, color1, (blend - 0.8) * 5.0);
                    }

                    // Add iridescent shimmer based on view angle
                    vec3 viewDir = normalize(vec3(0.0, 0.0, 1.0));
                    float fresnel = pow(1.0 - abs(dot(vNormal, viewDir)), 2.0);
                    color += fresnel * 0.3;

                    // Dotted/halftone texture effect
                    float dotPattern = sin(vUv.x * 100.0) * sin(vUv.y * 100.0);
                    dotPattern = smoothstep(0.3, 0.5, dotPattern);
                    color = mix(color, color * 0.85, dotPattern * 0.15);

                    // Soft edges
                    float edgeFade = smoothstep(0.0, 0.1, vUv.x) * smoothstep(1.0, 0.9, vUv.x);

                    // Opacity based on position for depth
                    float alpha = 0.9 * edgeFade;
                    alpha *= smoothstep(-0.5, 0.0, vElevation + 0.3);

                    gl_FragColor = vec4(color, alpha);
                }
            `,
            transparent: true,
            side: THREE.DoubleSide,
            depthWrite: false
        });

        this.ribbon = new THREE.Mesh(geometry, material);
        this.ribbon.rotation.x = -0.3;
        this.ribbon.position.set(1.5, 0.5, 0);
        this.scene.add(this.ribbon);

        // Add second ribbon layer for depth
        const ribbon2 = new THREE.Mesh(geometry.clone(), material.clone());
        ribbon2.rotation.x = -0.2;
        ribbon2.rotation.z = 0.5;
        ribbon2.position.set(1.2, 0.3, -0.5);
        ribbon2.scale.set(0.8, 0.9, 1);
        this.ribbon2 = ribbon2;
        this.scene.add(ribbon2);
    }

    animate() {
        requestAnimationFrame(() => this.animate());

        const elapsedTime = this.clock.getElapsedTime();

        // Update shader uniforms
        if (this.ribbon) {
            this.ribbon.material.uniforms.uTime.value = elapsedTime;
            this.ribbon.material.uniforms.uMouse.value.set(this.mouse.x, this.mouse.y);

            // Gentle rotation
            this.ribbon.rotation.z = Math.sin(elapsedTime * 0.1) * 0.1;
        }

        if (this.ribbon2) {
            this.ribbon2.material.uniforms.uTime.value = elapsedTime + 2;
            this.ribbon2.material.uniforms.uMouse.value.set(this.mouse.x * 0.5, this.mouse.y * 0.5);
            this.ribbon2.rotation.z = Math.sin(elapsedTime * 0.1 + 1) * 0.1;
        }

        this.renderer.render(this.scene, this.camera);
    }

    onResize() {
        this.camera.aspect = window.innerWidth / window.innerHeight;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(window.innerWidth, window.innerHeight);

        if (this.ribbon) {
            this.ribbon.material.uniforms.uResolution.value.set(window.innerWidth, window.innerHeight);
        }
    }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    new RibbonAnimation();
});
