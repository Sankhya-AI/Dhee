"use client";

import { Canvas } from "@react-three/fiber";
import { OrbitControls, Float } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import { Suspense, useMemo, useRef } from "react";
import * as THREE from "three";
import { useConstellation } from "@/lib/hooks/use-constellation";
import { NEURAL } from "@/lib/utils/neural-palette";
import { useFrame } from "@react-three/fiber";

function BrainParticles({ scrollProgress }: { scrollProgress: number }) {
  const { data } = useConstellation();
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const timeRef = useRef(0);

  const { count, positions, colors, scales } = useMemo(() => {
    const nodes = data?.nodes || [];
    // Use real data if available, otherwise generate placeholder particles
    const particleCount = nodes.length > 0 ? nodes.length : 200;
    const pos = new Float32Array(particleCount * 3);
    const col = new Float32Array(particleCount * 3);
    const scl = new Float32Array(particleCount);

    if (nodes.length > 0) {
      nodes.forEach((node, i) => {
        // Map to brain-like 3D positions
        const angle = (i / nodes.length) * Math.PI * 2;
        const radius = 2 + Math.random() * 2;
        const y = (Math.random() - 0.5) * 3;
        pos[i * 3] = Math.cos(angle) * radius + (Math.random() - 0.5) * 0.5;
        pos[i * 3 + 1] = y;
        pos[i * 3 + 2] = Math.sin(angle) * radius + (Math.random() - 0.5) * 0.5;

        // Color based on layer
        const color = new THREE.Color(node.layer === "sml" ? NEURAL.sml : NEURAL.lml);
        col[i * 3] = color.r;
        col[i * 3 + 1] = color.g;
        col[i * 3 + 2] = color.b;

        scl[i] = 0.03 + node.strength * 0.08;
      });
    } else {
      // Placeholder brain shape
      for (let i = 0; i < particleCount; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 2.5 + (Math.random() - 0.5) * 1.5;

        // Brain-like shape: wider at sides, narrower at top/bottom
        const xScale = 1.3;
        const yScale = 1.0;
        const zScale = 1.1;

        pos[i * 3] = r * Math.sin(phi) * Math.cos(theta) * xScale;
        pos[i * 3 + 1] = r * Math.cos(phi) * yScale;
        pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta) * zScale;

        const color = new THREE.Color(i % 3 === 0 ? NEURAL.episodic : i % 3 === 1 ? NEURAL.sml : NEURAL.semantic);
        col[i * 3] = color.r;
        col[i * 3 + 1] = color.g;
        col[i * 3 + 2] = color.b;

        scl[i] = 0.02 + Math.random() * 0.06;
      }
    }

    return { count: particleCount, positions: pos, colors: col, scales: scl };
  }, [data]);

  const dummy = useMemo(() => new THREE.Object3D(), []);

  useFrame((_, delta) => {
    if (!meshRef.current) return;
    timeRef.current += delta;

    // Hemisphere split based on scroll
    const splitAmount = Math.max(0, (scrollProgress - 0.15) / 0.2) * 1.5;
    // Rotation slowdown as you scroll
    const rotSpeed = 0.15 * (1 - scrollProgress * 0.8);

    for (let i = 0; i < count; i++) {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];
      const z = positions[i * 3 + 2];

      // Breathing effect
      const breathe = 1 + Math.sin(timeRef.current * 0.5 + i * 0.1) * 0.03;

      // Split hemispheres (left/right based on x position)
      const splitOffset = x > 0 ? splitAmount : -splitAmount;

      dummy.position.set(
        (x + splitOffset) * breathe,
        y * breathe,
        z * breathe
      );
      dummy.scale.setScalar(scales[i] * (1 + Math.sin(timeRef.current + i) * 0.2));
      dummy.rotation.y = timeRef.current * rotSpeed;
      dummy.updateMatrix();
      meshRef.current.setMatrixAt(i, dummy.matrix);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]}>
      <sphereGeometry args={[1, 8, 8]} />
      <meshBasicMaterial vertexColors toneMapped={false}>
        <instancedBufferAttribute
          attach="geometry-attributes-color"
          args={[colors, 3]}
        />
      </meshBasicMaterial>
    </instancedMesh>
  );
}

function BrainPathways({ scrollProgress }: { scrollProgress: number }) {
  const { data } = useConstellation();
  const linesRef = useRef<THREE.LineSegments>(null);

  const geometry = useMemo(() => {
    const edges = data?.edges || [];
    const nodes = data?.nodes || [];
    if (edges.length === 0 || nodes.length === 0) return null;

    const nodeMap = new Map(nodes.map((n, i) => [n.id, i]));
    const positions: number[] = [];
    const colors: number[] = [];

    for (const edge of edges.slice(0, 200)) {
      const si = nodeMap.get(edge.source);
      const ti = nodeMap.get(edge.target);
      if (si === undefined || ti === undefined) continue;

      // Generate rough 3D positions matching BrainParticles
      for (const idx of [si, ti]) {
        const angle = (idx / nodes.length) * Math.PI * 2;
        const radius = 2 + ((idx * 7919) % 100) / 100 * 2;
        const y = (((idx * 6271) % 100) / 100 - 0.5) * 3;
        positions.push(
          Math.cos(angle) * radius,
          y,
          Math.sin(angle) * radius
        );
      }

      const edgeColor = new THREE.Color(edge.type === "category" ? NEURAL.episodic : NEURAL.sml);
      colors.push(edgeColor.r, edgeColor.g, edgeColor.b);
      colors.push(edgeColor.r, edgeColor.g, edgeColor.b);
    }

    if (positions.length === 0) return null;

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    return geo;
  }, [data]);

  if (!geometry) return null;

  return (
    <lineSegments ref={linesRef} geometry={geometry}>
      <lineBasicMaterial
        vertexColors
        transparent
        opacity={0.08 * (1 - scrollProgress * 0.5)}
        toneMapped={false}
      />
    </lineSegments>
  );
}

export function BrainCanvas({ scrollProgress }: { scrollProgress: number }) {
  return (
    <Canvas
      camera={{ position: [0, 0, 8], fov: 50 }}
      gl={{ antialias: false, alpha: true, powerPreference: "high-performance" }}
      dpr={[1, 1.5]}
      style={{ background: NEURAL.void }}
      frameloop="always"
    >
      <Suspense fallback={null}>
        <ambientLight intensity={0.2} />
        <pointLight position={[5, 5, 5]} intensity={0.5} color={NEURAL.episodic} />
        <pointLight position={[-5, -3, 3]} intensity={0.3} color={NEURAL.sml} />

        <Float speed={0.5} rotationIntensity={0.2} floatIntensity={0.3}>
          <group>
            <BrainParticles scrollProgress={scrollProgress} />
            <BrainPathways scrollProgress={scrollProgress} />
          </group>
        </Float>

        <OrbitControls
          enableZoom={false}
          enablePan={false}
          autoRotate
          autoRotateSpeed={0.3}
          maxPolarAngle={Math.PI * 0.65}
          minPolarAngle={Math.PI * 0.35}
        />

        <EffectComposer multisampling={0}>
          <Bloom
            intensity={0.8}
            luminanceThreshold={0.3}
            luminanceSmoothing={0.5}
          />
        </EffectComposer>
      </Suspense>
    </Canvas>
  );
}
