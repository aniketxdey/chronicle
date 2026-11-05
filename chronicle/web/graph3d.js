/* The knowledge graph: glowing suns on black, clustered by work, in three.js with a d3-force-3d layout.
   One renderer lives for the whole page and moves between graph hosts, so repainting a view never restarts it. */
(() => {
  const HOME_DISTANCE = 46;
  const ANCHORS = 6; // The biggest few keep their labels; the rest take turns.
  const CYCLE_MS = 1600;
  const AREA_PER_LABEL = 16000;
  const FACE = new Set(["person", "app", "site", "ai_tool", "meeting", "project", "file", "document", "event"]);
  const NUCLEUS = "#fff4c2";

  const VERTEX = `
    attribute vec3 aPos;
    attribute vec3 aColor;
    attribute float aSize;
    attribute float aAlpha;
    attribute float aSeed;
    varying vec2 vUv;
    varying vec3 vColor;
    varying float vAlpha;
    varying float vSeed;
    void main() {
      vUv = uv;
      vColor = aColor;
      vAlpha = aAlpha;
      vSeed = aSeed;
      vec4 view = modelViewMatrix * vec4(aPos, 1.0);
      view.xy += position.xy * aSize;
      gl_Position = projectionMatrix * view;
    }`;

  /* White-hot core, the category's color through the body, a darker limb, and a corona that flickers slowly. */
  const FRAGMENT = `
    uniform float uTime;
    varying vec2 vUv;
    varying vec3 vColor;
    varying float vAlpha;
    varying float vSeed;
    void main() {
      vec2 p = (vUv - 0.5) * 2.0;
      float d = length(p);
      if (d > 1.0) discard;
      float angle = atan(p.y, p.x);
      float flare = 0.5 + 0.25 * sin(angle * 5.0 + uTime * 0.7 + vSeed) + 0.25 * sin(angle * 11.0 - uTime * 0.45 + vSeed * 2.0);
      float disc = smoothstep(0.3, 0.22, d);
      float hot = smoothstep(0.13, 0.0, d);
      float corona = exp(-d * d * 16.0) * (0.75 + 0.25 * flare) * (1.0 - smoothstep(0.6, 0.95, d));
      vec3 color = mix(vColor, vec3(1.0, 0.97, 0.82), hot * 0.75);
      float alpha = (disc * 0.92 + corona * 0.32) * vAlpha;
      if (alpha < 0.01) discard;
      gl_FragColor = vec4(color, alpha);
    }`;

  const clusterCenters = (ids, radius) => {
    const out = {};
    const n = Math.max(ids.length, 1);
    const golden = Math.PI * (3 - Math.sqrt(5));
    ids.forEach((id, index) => {
      const y = n === 1 ? 0 : 1 - (index / (n - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * index;
      out[id] = { x: Math.cos(theta) * r * radius, y: y * radius * 0.85, z: Math.sin(theta) * r * radius, radius: 2.4 + Math.min(4, Math.sqrt(n)) };
    });
    return out;
  };

  /* Work clusters: weighted label propagation over the edges, so what was used together sits together. */
  const findClusters = (nodes, edges) => {
    const label = new Map(nodes.map((node) => [node.id, node.id]));
    const links = new Map(nodes.map((node) => [node.id, []]));
    for (const edge of edges) {
      if (!links.has(edge.src) || !links.has(edge.dst)) continue;
      const weight = Math.log1p(edge.weight || 1);
      links.get(edge.src).push([edge.dst, weight]);
      links.get(edge.dst).push([edge.src, weight]);
    }
    const order = [...nodes].sort((a, b) => a.id.localeCompare(b.id));
    for (let pass = 0; pass < 8; pass++) {
      let moved = false;
      for (const node of order) {
        const votes = new Map();
        for (const [other, weight] of links.get(node.id)) votes.set(label.get(other), (votes.get(label.get(other)) || 0) + weight);
        let best = label.get(node.id);
        let top = votes.get(best) || 0;
        for (const [candidate, score] of [...votes].sort((a, b) => String(a[0]).localeCompare(String(b[0])))) {
          if (score > top) {
            best = candidate;
            top = score;
          }
        }
        if (best !== label.get(node.id)) {
          label.set(node.id, best);
          moved = true;
        }
      }
      if (!moved) break;
    }
    return label;
  };

  const clusterForce = (centers, strength) => {
    let simNodes = [];
    const force = (alpha) => {
      const k = strength * Math.max(alpha, 0.04);
      for (const node of simNodes) {
        const home = centers[node.cluster];
        if (!home) continue;
        node.vx += (home.x - node.x) * k;
        node.vy += (home.y - node.y) * k;
        node.vz += (home.z - node.z) * k;
      }
    };
    force.initialize = (input) => {
      simNodes = input;
    };
    return force;
  };

  const layout = (input, edges, previous, groups) => {
    const { forceCollide, forceLink, forceManyBody, forceSimulation } = window.AtomKit;
    // Named groups come from the server. Anything it doesn't know, such as a node only in one file's graph, is worked out here.
    const local = findClusters(input, edges);
    const clusters = new Map(input.map((node) => [node.id, groups?.[node.id] || `local:${local.get(node.id)}`]));
    const ids = [...new Set(input.map((node) => clusters.get(node.id)))].sort();
    const centers = clusterCenters(ids, 9 + Math.min(8, ids.length * 0.35));
    const warm = Boolean(previous);
    const simNodes = input.map((node, index) => {
      const cluster = clusters.get(node.id);
      const home = centers[cluster];
      const at = previous?.index.get(node.id);
      if (at !== undefined) return { id: node.id, cluster, size: node.size, x: previous.target[at * 3], y: previous.target[at * 3 + 1], z: previous.target[at * 3 + 2] };
      const angle = index * 2.399963;
      const spread = home.radius * (0.35 + (index % 7) / 14);
      return { id: node.id, cluster, size: node.size, x: home.x + Math.cos(angle) * spread, y: home.y + Math.sin(angle * 0.7) * spread * 0.6, z: home.z + Math.sin(angle) * spread };
    });
    const known = new Set(simNodes.map((node) => node.id));
    const links = edges.filter((edge) => known.has(edge.src) && known.has(edge.dst)).map((edge) => ({ source: edge.src, target: edge.dst, weight: edge.weight || 1 }));
    const maxWeight = Math.max(1, ...links.map((link) => link.weight));
    const n = simNodes.length;
    const simulation = forceSimulation(simNodes, 3)
      .alpha(warm ? 0.28 : 1)
      .alphaDecay(warm ? 0.06 : 0.028)
      .velocityDecay(0.42)
      .force("link", forceLink(links).id((node) => node.id)
        .distance((link) => (link.source.cluster === link.target.cluster ? 1.6 : 6.5))
        .strength((link) => (link.source.cluster === link.target.cluster ? 0.28 : 0.02) * Math.sqrt(link.weight / maxWeight)))
      .force("charge", forceManyBody().strength(n > 400 ? -0.4 : -0.9).distanceMax(n > 400 ? 6 : 8))
      .force("collide", forceCollide((node) => 0.18 + node.size * 0.24))
      .force("cluster", clusterForce(centers, n > 400 ? 0.72 : 0.55))
      .stop();
    const ticks = warm ? 80 : Math.min(n > 400 ? 220 : 360, 100 + Math.floor(n * 0.28));
    for (let i = 0; i < ticks; i++) simulation.tick();
    const positions = new Float32Array(n * 3);
    simNodes.forEach((node, index) => positions.set([node.x, node.y, node.z], index * 3));
    return positions;
  };

  const sizes = (nodes) => {
    const value = (node) => Math.max(node.seconds || 0, (node.weight || 0) * 5);
    const top = Math.max(1, ...nodes.map(value));
    const newest = Math.max(0, ...nodes.map((node) => Date.parse(node.last_seen || "") || 0));
    return nodes.map((node) => {
      const seen = Date.parse(node.last_seen || "") || newest;
      const days = Math.max(0, (newest - seen) / 86400000);
      return { ...node, size: 0.25 + 0.75 * (Math.log1p(value(node)) / Math.log1p(top)), recency: Math.exp(-days / 14) };
    });
  };

  let scene = null;

  const build = () => {
    const K = window.AtomKit;
    if (!K) return null;
    let renderer;
    try {
      renderer = new K.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    } catch {
      return null;
    }
    renderer.setClearColor(0x000000, 1);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const camera = new K.PerspectiveCamera(45, 1, 0.1, 400);
    camera.position.set(0, 6, HOME_DISTANCE);
    const world = new K.Scene();
    const controls = new K.OrbitControls(camera, renderer.domElement);
    Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, minDistance: 4, maxDistance: 90, rotateSpeed: 0.6, zoomSpeed: 0.8 });
    const composer = new K.EffectComposer(renderer);
    composer.addPass(new K.RenderPass(world, camera));
    const bloom = new K.UnrealBloomPass(new K.Vector2(1, 1), 0.22, 0.25, 0.72);
    composer.addPass(bloom);
    composer.addPass(new K.OutputPass());

    const material = new K.ShaderMaterial({
      vertexShader: VERTEX, fragmentShader: FRAGMENT, uniforms: { uTime: { value: 0 } },
      transparent: true, depthWrite: false, blending: K.AdditiveBlending,
    });
    const edgeMaterial = new K.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false, blending: K.AdditiveBlending });
    const labels = document.createElement("div");
    labels.className = "atom-labels";
    const element = document.createElement("div");
    element.className = "atom-stage";
    element.append(renderer.domElement, labels);

    const state = {
      K, renderer, camera, world, controls, composer, material, edgeMaterial, labels, element, host: null,
      geometry: null, edgeGeometry: null, mesh: null, lines: null, live: null, data: null, key: "", time: 0, last: 0, raf: 0, flight: null,
      labelOffset: 0, nextCycle: 0, drawn: false, groupChips: new Map(), drag: null, pendingClick: null, lastDown: null, hovered: null, selected: null, focus: null, category: null, onSelect: null, chips: new Map(), expanded: false,
      spin: new K.Quaternion(), axis: new K.Vector3(0.18, 1, 0.12).normalize(), scratch: new K.Vector3(),
      reduced: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    };
    new ResizeObserver(() => resize(state)).observe(element);

    const nearest = (event) => {
      const live = state.live;
      if (!live) return null;
      const rect = renderer.domElement.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      let best = null;
      let bestDistance = 256;
      for (let i = 0; i < live.ids.length; i++) {
        if (!live.screen[i * 3 + 2]) continue;
        const dx = live.screen[i * 3] - x;
        const dy = live.screen[i * 3 + 1] - y;
        const distance = dx * dx + dy * dy;
        if (distance < bestDistance) {
          bestDistance = distance;
          best = live.ids[i];
        }
      }
      return best;
    };
    /* A click waits a moment in case it's the first half of a double-click, which picks a node up. */
    const CLICK_WAIT = 240;
    let down = null;
    const canvas = renderer.domElement;
    const pointerWorld = (event, depth) => {
      const rect = canvas.getBoundingClientRect();
      const point = new K.Vector3(((event.clientX - rect.left) / rect.width) * 2 - 1, 1 - ((event.clientY - rect.top) / rect.height) * 2, depth);
      return point.unproject(state.camera).applyQuaternion(state.spin.clone().invert());
    };
    const groupUnder = (event) => {
      const live = state.live;
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      let best = null;
      let bestDistance = 70 * 70;
      for (const [id, members] of live.groupMembers || []) {
        for (const i of members) {
          if (live.ids[i] === state.drag.id || !live.screen[i * 3 + 2]) continue;
          const dx = live.screen[i * 3] - x;
          const dy = live.screen[i * 3 + 1] - y;
          const distance = dx * dx + dy * dy;
          if (distance < bestDistance) {
            bestDistance = distance;
            best = id;
          }
        }
      }
      return best;
    };
    canvas.addEventListener("pointerdown", (event) => {
      const id = nearest(event);
      const now = performance.now();
      const again = state.lastDown && id && state.lastDown.id === id && now - state.lastDown.at < CLICK_WAIT + 160;
      if (!again) {
        state.lastDown = { id, at: now };
        return;
      }
      // Second press on the same node: pick it up.
      clearTimeout(state.pendingClick);
      state.pendingClick = null;
      state.lastDown = null;
      const live = state.live;
      const at = live.index.get(id);
      scratchDepth.set(live.world[at * 3], live.world[at * 3 + 1], live.world[at * 3 + 2]).project(state.camera);
      state.drag = { id, at, depth: scratchDepth.z, home: state.data.groups?.[id] || null, start: Array.from(live.target.subarray(at * 3, at * 3 + 3)), target: null };
      state.controls.enabled = false;
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = "grabbing";
      state.hovered = id;
      syncLabels(state);
      event.stopImmediatePropagation();
    }, { capture: true });
    const scratchDepth = new K.Vector3();
    canvas.addEventListener("pointermove", (event) => {
      if (state.drag) {
        const { at, depth, home } = state.drag;
        const point = pointerWorld(event, depth);
        state.live.target.set([point.x, point.y, point.z], at * 3);
        state.live.current.set([point.x, point.y, point.z], at * 3);
        const under = groupUnder(event);
        state.drag.target = under && under !== home ? under : null;
        return;
      }
      if (event.buttons) return;
      const id = nearest(event);
      if (id !== state.hovered) {
        state.hovered = id;
        syncLabels(state);
      }
      canvas.style.cursor = id ? "pointer" : "grab";
    });
    canvas.addEventListener("pointerleave", () => {
      if (state.drag) return;
      state.hovered = null;
      syncLabels(state);
    });
    canvas.addEventListener("pointerdown", (event) => {
      down = { x: event.clientX, y: event.clientY };
    });
    canvas.addEventListener("pointerup", (event) => {
      if (state.drag) {
        const { id, at, start, target } = state.drag;
        state.drag = null;
        state.controls.enabled = true;
        down = null;
        state.hovered = null;
        placeGroups(state);
        if (target) state.onMove?.(id, target);
        else state.live.target.set(start, at * 3);
        return;
      }
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) return;
      const id = nearest(event);
      clearTimeout(state.pendingClick);
      state.pendingClick = setTimeout(() => {
        state.pendingClick = null;
        state.onSelect?.(id);
      }, id ? CLICK_WAIT : 0);
    });
    canvas.addEventListener("dblclick", () => {
      clearTimeout(state.pendingClick);
      state.pendingClick = null;
      if (!state.drag) state.onSelect?.(null);
    });
    // On a page, a plain wheel keeps scrolling the page. Pinch, and any wheel in the expanded view, zooms.
    canvas.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !state.expanded) event.stopImmediatePropagation();
    }, { capture: true, passive: true });
    return state;
  };

  const resize = (state) => {
    const { clientWidth: width, clientHeight: height } = state.element;
    if (!width || !height) return;
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
    state.renderer.setSize(width, height);
    state.composer.setSize(width, height);
  };

  const setData = (state, data) => {
    const { K } = state;
    const nodes = sizes(data.nodes);
    const previous = state.live;
    const positions = layout(nodes, data.edges, previous, data.groups);
    const count = nodes.length + 1;
    const dropped = [state.mesh, state.lines].filter(Boolean);
    dropped.forEach((item) => {
      state.world.remove(item);
      item.geometry.dispose();
    });
    const base = new K.PlaneGeometry(1, 1);
    const geometry = new K.InstancedBufferGeometry();
    geometry.index = base.index;
    geometry.setAttribute("position", base.getAttribute("position"));
    geometry.setAttribute("uv", base.getAttribute("uv"));
    const attribute = (name, width) => geometry.setAttribute(name, new K.InstancedBufferAttribute(new Float32Array(count * width), width).setUsage(K.DynamicDrawUsage));
    attribute("aPos", 3);
    attribute("aColor", 3);
    attribute("aSize", 1);
    attribute("aAlpha", 1);
    attribute("aSeed", 1);
    geometry.instanceCount = count;
    const edgeGeometry = new K.BufferGeometry();
    edgeGeometry.setAttribute("position", new K.BufferAttribute(new Float32Array(Math.max(data.edges.length, 1) * 6), 3).setUsage(K.DynamicDrawUsage));
    edgeGeometry.setAttribute("color", new K.BufferAttribute(new Float32Array(Math.max(data.edges.length, 1) * 8), 4).setUsage(K.DynamicDrawUsage));
    state.geometry = geometry;
    state.edgeGeometry = edgeGeometry;
    state.lines = new K.LineSegments(edgeGeometry, state.edgeMaterial);
    state.mesh = new K.Mesh(geometry, state.material);
    state.lines.frustumCulled = false;
    state.mesh.frustumCulled = false;
    state.world.add(state.lines, state.mesh);

    const ids = nodes.map((node) => node.id);
    const index = new Map(ids.map((id, i) => [id, i]));
    const current = new Float32Array(positions.length);
    const color = new K.Color();
    const colors = geometry.getAttribute("aColor");
    const seeds = geometry.getAttribute("aSeed");
    const scratch = new K.Vector3();
    nodes.forEach((node, i) => {
      const before = previous?.index.get(node.id);
      if (previous && before !== undefined) current.set(previous.current.subarray(before * 3, before * 3 + 3), i * 3);
      else {
        scratch.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]).normalize();
        const start = previous ? 46 : 0.5;
        current.set([scratch.x * start, scratch.y * start, scratch.z * start], i * 3);
      }
      color.set(node.color || "#ffa51f");
      colors.setXYZ(i, color.r, color.g, color.b);
      seeds.setX(i, (i * 2.399963) % 6.283);
    });
    color.set(NUCLEUS);
    colors.setXYZ(nodes.length, color.r, color.g, color.b);
    seeds.setX(nodes.length, 0);
    colors.needsUpdate = seeds.needsUpdate = true;
    const neighbors = new Map();
    for (const edge of data.edges) {
      if (!neighbors.has(edge.src)) neighbors.set(edge.src, new Set());
      if (!neighbors.has(edge.dst)) neighbors.set(edge.dst, new Set());
      neighbors.get(edge.src).add(edge.dst);
      neighbors.get(edge.dst).add(edge.src);
    }
    let radius = 1;
    for (let i = 0; i < positions.length; i += 3) radius = Math.max(radius, Math.hypot(positions[i], positions[i + 1], positions[i + 2]));
    state.home = Math.min(70, Math.max(18, radius * 2.7));
    if (!previous) {
      state.camera.position.set(0, state.home * 0.13, state.home);
      state.controls.target.set(0, 0, 0);
    }
    state.live = { ids, index, nodes, target: positions, current, world: new Float32Array(positions.length), screen: new Float32Array(ids.length * 3 + 3), depth: new Float32Array(ids.length), neighbors };
    state.drawn = false;
    state.nextCycle = 0;
    syncGroups(state);
    syncLabels(state);
  };

  /* Group names are always on screen, above their members. Pressing one renames it. */
  const syncGroups = (state) => {
    const live = state.live;
    if (!live) return;
    const names = state.data?.groupNames || {};
    const members = new Map();
    live.ids.forEach((id, i) => {
      const group = state.data?.groups?.[id];
      if (!group || !names[group]) return;
      if (!members.has(group)) members.set(group, []);
      members.get(group).push(i);
    });
    live.groupMembers = members;
    for (const [id, chip] of state.groupChips) {
      if (!members.has(id)) {
        chip.remove();
        state.groupChips.delete(id);
      }
    }
    for (const id of members.keys()) {
      let chip = state.groupChips.get(id);
      if (!chip) {
        chip = document.createElement("button");
        chip.type = "button";
        chip.className = "atom-group";
        chip.title = "Rename this group";
        chip.addEventListener("click", () => editGroup(state, id, chip));
        state.labels.appendChild(chip);
        state.groupChips.set(id, chip);
      }
      if (!chip.querySelector("input")) chip.textContent = names[id];
      chip.setAttribute("aria-label", `Rename group ${names[id]}`);
    }
  };

  const editGroup = (state, id, chip) => {
    if (chip.querySelector("input")) return;
    const input = document.createElement("input");
    input.value = state.data.groupNames[id];
    input.setAttribute("aria-label", "Group name");
    chip.textContent = "";
    chip.appendChild(input);
    chip.classList.add("is-editing");
    input.focus();
    input.select();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      chip.classList.remove("is-editing");
      const name = input.value.trim();
      chip.textContent = save && name ? name : state.data.groupNames[id];
      if (save && name && name !== state.data.groupNames[id]) state.onRename?.(id, name);
    };
    input.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") finish(true);
      if (event.key === "Escape") finish(false);
    });
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("blur", () => finish(true));
  };

  const placeGroups = (state) => {
    const live = state.live;
    // The legend sits over the stage, so names steer around it as well as each other.
    const stage = state.element.getBoundingClientRect();
    const legendBox = state.element.closest(".graph-frame")?.querySelector(".graph-legend")?.getBoundingClientRect();
    const boxes = legendBox && legendBox.height ? [[legendBox.left - stage.left, legendBox.top - stage.top, legendBox.right - stage.left, legendBox.bottom - stage.top]] : [];
    for (const [id, chip] of state.groupChips) {
      const members = live.groupMembers?.get(id) || [];
      let x = 0;
      let top = Infinity;
      let shown = 0;
      for (const i of members) {
        if (!live.screen[i * 3 + 2]) continue;
        x += live.screen[i * 3];
        top = Math.min(top, live.screen[i * 3 + 1]);
        shown++;
      }
      if (!shown) {
        chip.style.opacity = "0";
        continue;
      }
      const half = (chip.offsetWidth || 90) / 2;
      x = Math.max(half + 4, Math.min(state.element.clientWidth - half - 4, x / shown));
      // Names never sit on each other: one that would moves to the nearest clear row, preferring up, or hides if none fits.
      const rise = (chip.offsetHeight || 28) / 2 + 1;
      const floor = state.element.clientHeight - rise;
      const want = Math.max(rise, Math.min(floor, top - rise - 12));
      const clear = (at) => at >= rise && at <= floor && !boxes.some((box) => x - half - 4 < box[2] && x + half + 4 > box[0] && at - rise < box[3] && at + rise > box[1]);
      let y = null;
      for (let step = 0; step * 6 <= state.element.clientHeight && y === null; step++) {
        if (clear(want - step * 6)) y = want - step * 6;
        else if (clear(want + step * 6)) y = want + step * 6;
      }
      if (y === null) {
        chip.style.opacity = "0";
        continue;
      }
      chip.style.opacity = "1";
      chip.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      chip.classList.toggle("is-target", state.drag?.target === id);
      boxes.push([x - half - 4, y - rise, x + half + 4, y + rise]);
    }
    live.groupBoxes = boxes;
  };

  /* Show as many labels as fit. Each cycle moves on through the rest, nearest the camera first, and skips any that would overlap. */
  const labelSlots = (state) => {
    const area = state.element.clientWidth * state.element.clientHeight;
    return Math.max(10, Math.min(48, Math.floor(area / AREA_PER_LABEL)));
  };

  const labelBox = (state, at, node) => {
    const live = state.live;
    const x = live.screen[at * 3];
    const y = live.screen[at * 3 + 1];
    const half = Math.min(node.label.length, 28) * 3.4 + 12;
    return [x - half, y + 6, x + half, y + 34];
  };

  const syncLabels = (state) => {
    const live = state.live;
    if (!live) return;
    const wanted = new Set();
    if (state.selected) wanted.add(state.selected);
    if (state.hovered) wanted.add(state.hovered);
    (state.focus || []).forEach((id) => wanted.add(id));
    if (state.selected) {
      [...(live.neighbors.get(state.selected) || [])].slice(0, 10).forEach((id) => wanted.add(id));
    }
    const visible = state.category ? live.nodes.filter((node) => node.category === state.category) : live.nodes;
    const ranked = [...visible].sort((a, b) => Number(FACE.has(b.kind)) - Number(FACE.has(a.kind)) || b.size - a.size);
    ranked.slice(0, ANCHORS).forEach((node) => wanted.add(node.id));
    const slots = labelSlots(state);
    if (!state.drawn) {
      ranked.slice(0, slots).forEach((node) => wanted.add(node.id));
    } else {
      const placed = [...(live.groupBoxes || []), ...[...wanted].flatMap((id) => {
        const at = live.index.get(id);
        return at === undefined ? [] : [labelBox(state, at, live.nodes[at])];
      })];
      const rest = ranked.slice(ANCHORS);
      const start = rest.length ? state.labelOffset % rest.length : 0;
      const turn = [...rest.slice(start), ...rest.slice(0, start)];
      // Within this turn's order, orbs facing the camera go first.
      const front = turn.slice(0, Math.max(slots * 2, 24)).sort((a, b) => live.depth[live.index.get(a.id)] - live.depth[live.index.get(b.id)]);
      for (const node of [...front, ...turn.slice(front.length)]) {
        if (wanted.size >= slots) break;
        const at = live.index.get(node.id);
        if (!live.screen[at * 3 + 2]) continue;
        const box = labelBox(state, at, node);
        if (box[0] < 0 || box[2] > state.element.clientWidth || box[3] > state.element.clientHeight) continue;
        if (placed.some((other) => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1])) continue;
        placed.push(box);
        wanted.add(node.id);
      }
    }
    for (const [id, chip] of state.chips) {
      if (!wanted.has(id) || !live.index.has(id)) {
        chip.style.opacity = "0";
        state.chips.delete(id);
        setTimeout(() => chip.remove(), 320);
      }
    }
    for (const id of wanted) {
      const at = live.index.get(id);
      if (at === undefined) continue;
      let chip = state.chips.get(id);
      if (!chip) {
        chip = document.createElement("div");
        chip.className = "atom-label";
        chip.innerHTML = "<span></span>";
        state.chips.set(id, chip);
      }
      const label = live.nodes[at].label;
      chip.firstChild.textContent = label.length > 28 ? `${label.slice(0, 27)}…` : label;
      chip.firstChild.style.setProperty("--ring", live.nodes[at].color || "#ffa51f");
      chip.classList.toggle("is-strong", id === state.selected || id === state.hovered || Boolean(state.focus?.has(id)));
      state.labels.appendChild(chip);
    }
  };

  const fly = (state) => {
    const live = state.live;
    const { K } = state;
    const at = state.selected ? live?.index.get(state.selected) : undefined;
    if (state.selected && at !== undefined) {
      state.flight = { target: new K.Vector3(live.world[at * 3], live.world[at * 3 + 1], live.world[at * 3 + 2]), distance: 11, t: 0 };
    } else if (!state.selected) {
      state.flight = { target: new K.Vector3(0, 0, 0), distance: state.home || HOME_DISTANCE, t: 0 };
    }
  };

  const frame = (now) => {
    const state = scene;
    state.raf = 0;
    if (!state.host?.isConnected) return;
    state.raf = requestAnimationFrame(frame);
    const delta = Math.min((now - (state.last || now)) / 1000, 0.1);
    state.last = now;
    const { K, live, camera, controls, scratch, spin } = state;
    const frozen = Boolean(state.selected || state.hovered) || state.reduced;
    if (!frozen) state.time += Math.min(delta, 0.05);
    if (!state.reduced) state.material.uniforms.uTime.value += delta;
    spin.setFromAxisAngle(state.axis, state.time * 0.045);
    if (state.flight) {
      const goal = state.flight;
      goal.t = Math.min(1, goal.t + delta * 1.4);
      const ease = 1 - Math.pow(1 - goal.t, 3);
      controls.target.lerp(goal.target, ease * 0.25 + 0.02);
      const direction = camera.position.clone().sub(controls.target).normalize();
      const wanted = controls.target.clone().addScaledVector(direction, goal.distance);
      camera.position.lerp(wanted, ease * 0.2 + 0.02);
      if (goal.t >= 1 && camera.position.distanceTo(wanted) < 0.05) state.flight = null;
    }
    controls.update();
    if (live) {
      drawNodes(state, delta, scratch, spin, K);
      state.drawn = true;
      if (now >= state.nextCycle && !state.reduced && !state.selected && !state.hovered) {
        state.nextCycle = now + CYCLE_MS;
        state.labelOffset += Math.max(3, Math.ceil(labelSlots(state) / 4));
        syncLabels(state);
      }
    }
    state.composer.render();
  };

  const drawNodes = (state, delta, scratch, spin, K) => {
    const { live, geometry, edgeGeometry, camera } = state;
    const count = live.ids.length;
    const pos = geometry.getAttribute("aPos");
    const sized = geometry.getAttribute("aSize");
    const alphas = geometry.getAttribute("aAlpha");
    const glide = state.reduced ? 1 : 1 - Math.exp(-delta * 2.6);
    const focusSet = state.selected ? live.neighbors.get(state.selected) : state.hovered ? live.neighbors.get(state.hovered) : null;
    const halfW = state.element.clientWidth / 2;
    const halfH = state.element.clientHeight / 2;
    const highlight = state.focus?.size ? state.focus : null;
    for (let i = 0; i < count; i++) {
      const node = live.nodes[i];
      for (let k = 0; k < 3; k++) live.current[i * 3 + k] += (live.target[i * 3 + k] - live.current[i * 3 + k]) * glide;
      scratch.set(live.current[i * 3], live.current[i * 3 + 1], live.current[i * 3 + 2]).applyQuaternion(spin);
      live.world.set([scratch.x, scratch.y, scratch.z], i * 3);
      pos.setXYZ(i, scratch.x, scratch.y, scratch.z);
      const active = node.id === state.selected || node.id === state.hovered || Boolean(highlight?.has(node.id));
      let alpha = 0.55 + node.recency * 0.45;
      if (state.category && node.category !== state.category) alpha *= 0.12;
      if ((focusSet || highlight) && !active && !focusSet?.has(node.id)) alpha *= 0.22;
      alphas.setX(i, alpha);
      sized.setX(i, (0.7 + node.size * 1.6) * (active ? 1.7 : 1));
      scratch.project(camera);
      live.screen[i * 3] = (scratch.x + 1) * halfW;
      live.screen[i * 3 + 1] = (1 - scratch.y) * halfH;
      live.screen[i * 3 + 2] = scratch.z < 1 ? alpha : 0;
      live.depth[i] = scratch.z;
    }
    const breathe = state.reduced ? 1 : 1 + Math.sin(state.time * 1.6) * 0.05;
    pos.setXYZ(count, 0, 0, 0);
    sized.setX(count, 2.8 * breathe);
    alphas.setX(count, 1);
    pos.needsUpdate = sized.needsUpdate = alphas.needsUpdate = true;

    const edgePos = edgeGeometry.getAttribute("position");
    const edgeColor = edgeGeometry.getAttribute("color");
    const colors = geometry.getAttribute("aColor");
    let drawn = 0;
    for (const edge of state.data.edges) {
      const a = live.index.get(edge.src);
      const b = live.index.get(edge.dst);
      if (a === undefined || b === undefined) continue;
      const touches = [edge.src, edge.dst].some((id) => id === state.selected || id === state.hovered);
      const base = Math.min(0.2, 0.02 + Math.log1p(edge.weight || 1) * 0.022);
      const alpha = touches ? 0.6 : base * Math.min(alphas.getX(a), alphas.getX(b));
      edgePos.setXYZ(drawn * 2, live.world[a * 3], live.world[a * 3 + 1], live.world[a * 3 + 2]);
      edgePos.setXYZ(drawn * 2 + 1, live.world[b * 3], live.world[b * 3 + 1], live.world[b * 3 + 2]);
      edgeColor.setXYZW(drawn * 2, colors.getX(a), colors.getY(a), colors.getZ(a), alpha);
      edgeColor.setXYZW(drawn * 2 + 1, colors.getX(b), colors.getY(b), colors.getZ(b), alpha);
      drawn++;
    }
    edgeGeometry.setDrawRange(0, drawn * 2);
    edgePos.needsUpdate = edgeColor.needsUpdate = true;

    // Biggest first; a label that would overlap one already placed hides, unless it's the one you're pointing at.
    placeGroups(state);
    const placed = [...(live.groupBoxes || [])];
    for (const [id, chip] of state.chips) {
      const at = live.index.get(id);
      if (at === undefined) continue;
      const x = live.screen[at * 3];
      const y = live.screen[at * 3 + 1];
      chip.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      const width = chip.firstChild.offsetWidth || 80;
      const box = [x - width / 2 - 4, y + 8, x + width / 2 + 4, y + 36];
      const strong = id === state.selected || id === state.hovered;
      const clash = !strong && placed.some((other) => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1]);
      const opacity = clash ? 0 : Math.min(1, live.screen[at * 3 + 2] * 1.6);
      chip.style.opacity = String(opacity);
      if (opacity > 0.05) placed.push(box);
    }
  };

  window.Atom = {
    /* Put the renderer in `host` and show `data`. Returns false when WebGL isn't available. */
    show(host, data, options) {
      scene ||= build();
      if (!scene) return false;
      const state = scene;
      const moved = state.host !== host;
      state.host = host;
      if (moved) host.appendChild(state.element);
      state.onSelect = options.onSelect;
      state.onRename = options.onRename;
      state.onMove = options.onMove;
      const expandedChanged = state.expanded !== Boolean(options.expanded);
      state.expanded = Boolean(options.expanded);
      state.data = data;
      const groupKey = data.nodes.map((node) => data.groups?.[node.id] || "").join("|");
      const key = `${options.key}:${data.nodes.map((node) => node.id).join("|")}:${data.edges.length}:${groupKey}`;
      if (key !== state.key) {
        // A different graph starts fresh; a changed one keeps its positions and glides.
        if (options.key !== state.scopeKey) {
          state.live = null;
          for (const chip of state.groupChips.values()) chip.remove();
          state.groupChips.clear();
        }
        state.scopeKey = options.key;
        state.key = key;
        setData(state, data);
      } else if (state.live) {
        const colors = state.geometry.getAttribute("aColor");
        const color = new state.K.Color();
        const fresh = new Map(data.nodes.map((node) => [node.id, node]));
        state.live.nodes = state.live.nodes.map((node, i) => {
          const update = fresh.get(node.id);
          color.set(update?.color || node.color || "#ffa51f");
          colors.setXYZ(i, color.r, color.g, color.b);
          return update ? { ...node, label: update.label, color: update.color, category: update.category } : node;
        });
        colors.needsUpdate = true;
      }
      const selectedChanged = state.selected !== (options.selected || null);
      state.selected = options.selected || null;
      state.focus = options.focus || null;
      state.category = options.category || null;
      syncGroups(state);
      syncLabels(state);
      if (selectedChanged || (expandedChanged && !state.selected)) fly(state);
      resize(state);
      if (!state.raf) state.raf = requestAnimationFrame(frame);
      return true;
    },
    resize() {
      if (scene) resize(scene);
    },
    setExpanded(on) {
      if (scene) scene.expanded = on;
    },
  };
})();
