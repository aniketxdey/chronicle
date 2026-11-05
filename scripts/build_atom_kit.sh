#!/bin/sh
# Rebuild chronicle/web/vendor/atom-kit.js, the three.js and d3-force-3d bundle the knowledge graph runs on.
set -e
out="$(cd "$(dirname "$0")/.." && pwd)/chronicle/web/vendor"
work="$(mktemp -d)"
cd "$work"
npm install --silent --no-audit --no-fund three@0.187.0 d3-force-3d@3.0.6 esbuild@0.24.2
cat > entry.js <<'ENTRY'
export { AdditiveBlending, BufferAttribute, BufferGeometry, Color, DynamicDrawUsage, InstancedBufferAttribute, InstancedBufferGeometry, LineBasicMaterial, LineSegments, Mesh, PerspectiveCamera, PlaneGeometry, Quaternion, Scene, ShaderMaterial, Vector2, Vector3, WebGLRenderer } from "three";
export { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
export { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
export { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
export { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
export { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
export { forceCollide, forceLink, forceManyBody, forceSimulation } from "d3-force-3d";
ENTRY
./node_modules/.bin/esbuild entry.js --bundle --minify --format=iife --global-name=AtomKit --target=es2020 --legal-comments=none --outfile="$out/atom-kit.js"
rm -rf "$work"
