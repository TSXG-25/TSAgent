import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { OUTER_ORBIT, PLANETS, daysSinceJ2000, orbitPath, scenePosition, type PlanetDef, type PlanetKey } from "./orbits";

const DEG = Math.PI / 180;

export interface SceneCallbacks {
  onHover: (key: PlanetKey | null) => void;
  onSelect: (key: PlanetKey) => void;
  onSun: () => void;
  onSunDbl: () => void;
  onEmpty: () => void;
  onTick: (simDays: number) => void;
}

interface Pose { target: THREE.Vector3; az: number; el: number; dist: number; }

const PLANET_VERTEX = /* glsl */ `
  varying vec3 vWorld;
  varying vec3 vNormalW;
  varying vec3 vNormalO;
  varying vec2 vUv;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vNormalO = normal;
    vUv = uv;
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`;

const NOISE = /* glsl */ `
  float hash31(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise3(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash31(i), hash31(i + vec3(1,0,0)), f.x), mix(hash31(i + vec3(0,1,0)), hash31(i + vec3(1,1,0)), f.x), f.y),
      mix(mix(hash31(i + vec3(0,0,1)), hash31(i + vec3(1,0,1)), f.x), mix(hash31(i + vec3(0,1,1)), hash31(i + vec3(1,1,1)), f.x), f.y),
      f.z);
  }
  float fbm(vec3 p) {
    float v = 0.0; float a = 0.5;
    for (int k = 0; k < 5; k++) { v += a * noise3(p); p *= 2.03; a *= 0.5; }
    return v;
  }
  float fbm3(vec3 p) {
    float v = 0.0; float a = 0.5;
    for (int k = 0; k < 3; k++) { v += a * noise3(p); p *= 2.1; a *= 0.5; }
    return v;
  }
`;

const PLANET_FRAGMENT = /* glsl */ `
  uniform vec3 uA;      // primary surface color
  uniform vec3 uB;      // secondary surface color
  uniform vec3 uC;      // detail color
  uniform vec3 uAtmos;  // rim atmosphere color (0 = none)
  uniform float uKind;  // 0 rocky, 1 earth, 2 gas, 3 ice
  uniform float uTime;  // sim days, drives cloud drift
  uniform float uSeed;
  uniform float uDim;   // 1 normal, ~0.4 when another planet is active
  varying vec3 vWorld;
  varying vec3 vNormalW;
  varying vec3 vNormalO;
  varying vec2 vUv;
  ${NOISE}
  void main() {
    vec3 n = normalize(vNormalO);
    vec3 p = n * (3.0 + uSeed * 0.35) + vec3(uSeed * 0.13);
    vec3 col;
    if (uKind < 0.5) {                       // rocky
      float base = fbm(p * 2.0);
      float craters = smoothstep(0.62, 0.9, fbm(p * 6.5));
      col = mix(uA, uB, base);
      col = mix(col, uC, craters * 0.55);
    } else if (uKind < 1.5) {                // earth-like
      float cont = fbm3(p * 1.5 + 3.7);
      float land = smoothstep(0.5, 0.56, cont);
      vec3 ocean = mix(uA, uA * 0.6, fbm3(p * 2.4) * 0.45);
      vec3 ground = mix(uB, uC, smoothstep(0.35, 0.8, fbm3(p * 2.6)));
      float ice = smoothstep(0.78, 0.9, abs(n.y));
      col = mix(ocean, ground, land);
      col = mix(col, vec3(0.92, 0.96, 1.0), ice);
      float cloud = smoothstep(0.58, 0.82, fbm(p * 2.2 + vec3(uTime * 0.02, 0.0, uTime * 0.015)));
      col = mix(col, vec3(1.0), cloud * 0.5);
    } else {                                 // gas / ice giant
      float lat = n.y;
      float turb = fbm(p * 2.2) - 0.5;
      float bands = sin((lat * 9.0 + turb * 5.0) * (uKind < 2.5 ? 1.4 : 0.7) + uSeed * 9.0);
      col = mix(uA, uB, smoothstep(-1.0, 1.0, bands));
      float streak = smoothstep(0.55, 0.95, fbm(p * 5.0 + vec3(0.0, uTime * 0.004, 0.0)));
      col = mix(col, uC, streak * (uKind < 2.5 ? 0.45 : 0.2));
      if (uKind < 2.5) {                     // great red spot style storm
        vec2 d = vec2(vUv.x - 0.68, (vUv.y - 0.36) * 2.0);
        col = mix(col, uC * 1.25, smoothstep(0.16, 0.05, length(d)) * 0.6);
      }
    }
    vec3 ldir = normalize(-vWorld);
    float lit = max(dot(vNormalW, ldir), 0.0);
    float ambient = 0.045;
    col *= ambient + lit * 1.15;
    float rim = pow(1.0 - max(dot(normalize(cameraPosition - vWorld), vNormalW), 0.0), 3.0);
    col += uAtmos * rim * (0.35 + lit * 0.9);
    gl_FragColor = vec4(col * uDim, 1.0);
  }
`;

const SUN_VERTEX = /* glsl */ `
  varying vec3 vNormalO;
  varying vec3 vWorld;
  varying vec3 vNormalW;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vNormalO = normal;
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`;

const SUN_FRAGMENT = /* glsl */ `
  uniform float uTime;
  varying vec3 vNormalO;
  varying vec3 vWorld;
  varying vec3 vNormalW;
  ${NOISE}
  void main() {
    vec3 n = normalize(vNormalO);
    float g = fbm(n * 4.5 + vec3(uTime * 0.05, uTime * 0.03, 0.0));
    float cells = fbm(n * 9.0 - uTime * 0.06);
    vec3 col = mix(vec3(1.0, 0.55, 0.12), vec3(1.0, 0.9, 0.55), smoothstep(0.3, 0.75, g));
    col = mix(col, vec3(1.0, 1.0, 0.92), smoothstep(0.6, 0.95, cells) * 0.6);
    float limb = pow(max(dot(normalize(cameraPosition - vWorld), vNormalW), 0.0), 0.45);
    col *= 0.55 + limb * 0.75;
    gl_FragColor = vec4(col * 0.78, 1.0);
  }
`;

const WAVE_DUR = 1.5;
/** Eased shockwave radius: fast burst, long decelerating settle. */
function waveRadiusAt(t: number) {
  const e = 1 - Math.pow(1 - Math.min(1, t), 1.7);
  return 4 + e * (OUTER_ORBIT + 18);
}

const RING_VERTEX = /* glsl */ `
  varying vec2 vPos;
  varying vec3 vWorld;
  varying vec3 vNormalW;
  void main() {
    vPos = position.xy;
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`;

const RING_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uInner;
  uniform float uOuter;
  varying vec2 vPos;
  varying vec3 vWorld;
  varying vec3 vNormalW;
  ${NOISE}
  void main() {
    float r = length(vPos);
    float t = (r - uInner) / (uOuter - uInner);
    float bands = fbm(vec3(t * 40.0, 0.0, 0.0));
    float alpha = (0.25 + 0.6 * bands) * smoothstep(0.0, 0.06, t) * (1.0 - smoothstep(0.85, 1.0, t));
    float lit = max(abs(dot(vNormalW, normalize(-vWorld))), 0.0);
    gl_FragColor = vec4(uColor * (0.35 + lit), alpha);
  }
`;

const STAR_VERTEX = /* glsl */ `
  attribute float aSize;
  attribute float aPhase;
  uniform float uTime;
  uniform float uPixelRatio;
  uniform float uDim;
  varying vec3 vColor;
  varying float vTw;
  void main() {
    vColor = color;
    vTw = 0.72 + 0.28 * sin(uTime * 1.7 + aPhase);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * vTw * uPixelRatio * (320.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const STAR_FRAGMENT = /* glsl */ `
  uniform float uDim;
  varying vec3 vColor;
  varying float vTw;
  void main() {
    vec2 d = gl_PointCoord - 0.5;
    float a = smoothstep(0.5, 0.05, length(d));
    gl_FragColor = vec4(vColor, a * vTw * uDim);
  }
`;

export class SolarScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private sys = new THREE.Group();
  private sysN = new THREE.Vector3();
  private sysUp = new THREE.Vector3();
  private camera: THREE.PerspectiveCamera;
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2(-2, -2);
  private planetGroups = new Map<PlanetKey, THREE.Group>();
  private planetMeshes = new Map<PlanetKey, THREE.Mesh>();
  private hitMeshes = new Map<PlanetKey, THREE.Mesh>();
  private orbitLines = new Map<PlanetKey, THREE.LineLoop>();
  private planetGlows = new Map<PlanetKey, THREE.Sprite>();
  private spinners: { mesh: THREE.Object3D; period: number; angle: number }[] = [];
  private moon?: THREE.Object3D;
  private sunUniforms = { uTime: { value: 0 } };
  private starUniforms = { uTime: { value: 0 }, uPixelRatio: { value: 1 }, uDim: { value: 1 }, uGlitch: { value: 0 } };
  private sunMesh?: THREE.Mesh;
  private sunGlows: THREE.Sprite[] = [];
  private timeUniforms: { value: number }[] = [];
  private simDays = daysSinceJ2000(new Date());
  private timeScale = 30;
  private frozen = false;
  private paused = false;
  private hovered: PlanetKey | null = null;
  private selected: PlanetKey | null = null;
  private raf = 0;
  private last = performance.now();
  private pose: Pose;
  private fpsAcc = 0;
  private fpsN = 0;
  private fpsT = 0;
  private degraded = false;
  private insertT = -1;
  private insertCb: ((p: { x: number; y: number }) => void) | null = null;
  private insertKey: PlanetKey | null = null;
  private drift = { x: 0, y: 0 };
  private accentColors: Record<string, number>;
  private callbacks: SceneCallbacks;
  private disposed = false;
  private tickAccum = 0;
  private readonly reduce: boolean;
  private w = 1;
  private h = 1;
  private tmpV = new THREE.Vector3();
  private fitDist = 200;
  private mapDist = 120;
  private navMode = false;
  private zoom = 1;
  private dragAz = 0;
  private dragEl = 0;
  private dragging = false;
  private dragMoved = 0;
  private downPos = { x: 0, y: 0 };
  private upV = new THREE.Vector3(0, 1, 0);
  private navPulse = 0;
  private simSpeed = 1;
  private fractureT = 0;
  private trails = new Map<PlanetKey, { line: THREE.Line; pts: THREE.Vector3[]; acc: number }>();
  private tacGrid?: THREE.LineSegments;
  private dimUniforms = new Map<PlanetKey, { value: number }>();
  private pulseRing?: THREE.Mesh;
  private pulseT = -1;
  private composer?: EffectComposer;
  private bloomPass?: UnrealBloomPass;
  private distortUniforms: { [uniform: string]: THREE.IUniform } = { tDiffuse: { value: null }, uWave: { value: -1 }, uIntensity: { value: 0 }, uCenter: { value: new THREE.Vector2(0.5, 0.5) }, uAspect: { value: 1 } };
  private transitions = 0;
  private nextPulse = 7;
  private travelers: { points: THREE.Points; def: PlanetDef; phase: number }[] = [];
  private simGrid?: THREE.LineSegments;
  private simGridUniforms: { [uniform: string]: THREE.IUniform } = { uNav: { value: 0 }, uWave: { value: -1 }, uColor: { value: new THREE.Color(0x6fd4ff) } };
  private futures = new Map<PlanetKey, THREE.Line>();
  private flows: { curve: THREE.CatmullRomCurve3; line: THREE.Line; pts: THREE.Points; t: number; speed: number; target: PlanetKey; fade: number }[] = [];
  private nextFlow = 2;

  constructor(private container: HTMLElement, callbacks: SceneCallbacks, accentColors: Record<string, number>) {
    this.callbacks = callbacks;
    this.accentColors = accentColors;
    this.reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    this.starUniforms.uPixelRatio.value = this.renderer.getPixelRatio();
    container.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
    this.pose = { target: new THREE.Vector3(), az: 25 * DEG, el: 24 * DEG, dist: 175 };

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.55, 0.85, 0.72);
    this.composer.addPass(this.bloomPass);
    const distort = new ShaderPass({
      uniforms: this.distortUniforms,
      vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position,1.0); }",
      fragmentShader: `uniform sampler2D tDiffuse; uniform float uWave; uniform float uIntensity; uniform vec2 uCenter; uniform float uAspect;
        varying vec2 vUv;
        void main(){
          vec2 d = vUv - uCenter; d.x *= uAspect;
          float r = length(d);
          vec2 dir = r > 0.0001 ? normalize(d) : vec2(0.0);
          float band = uWave > 0.0 ? 1.0 - smoothstep(0.0, 0.07, abs(r - uWave)) : 0.0;
          vec2 uv = vUv - (dir / vec2(uAspect, 1.0)) * band * 0.02 * uIntensity;
          float ab = (band * 0.009 + uIntensity * 0.0016);
          vec4 c;
          c.r = texture2D(tDiffuse, uv + dir / vec2(uAspect, 1.0) * ab).r;
          c.g = texture2D(tDiffuse, uv).g;
          c.b = texture2D(tDiffuse, uv - dir / vec2(uAspect, 1.0) * ab).b;
          c.a = 1.0;
          float vig = smoothstep(1.15, 0.4, length(vUv - 0.5) * 1.35);
          gl_FragColor = vec4(c.rgb * mix(0.8, 1.0, vig), 1.0);
        }`,
    });
    this.composer.addPass(distort);
    this.composer.addPass(new OutputPass());

    this.sys.rotation.set(-0.3, 0, 0.16);
    this.scene.add(this.sys);
    this.sysN.set(0, 1, 0).applyQuaternion(this.sys.quaternion);
    this.sysUp.set(0, 0, -1).applyQuaternion(this.sys.quaternion);
    this.buildLights();
    this.buildSun();
    this.buildStars();
    this.buildNebulae();
    this.buildPlanets();
    this.buildTacticalGrid();
    this.buildSimGrid();
    this.buildFutures();

    const el = this.renderer.domElement;
    el.addEventListener("pointermove", this.onPointerMove);
    el.addEventListener("pointerleave", this.onPointerLeave);
    el.addEventListener("pointerdown", this.onPointerDown);
    el.addEventListener("click", this.onClick);
    el.addEventListener("dblclick", this.onDblClick);
    el.addEventListener("wheel", this.onWheel, { passive: false });
    this.raf = requestAnimationFrame(this.loop);
  }

  // ---------- construction ----------

  private buildLights() {
    const sun = new THREE.PointLight(0xfff2dd, 1.4, 0, 1.6);
    this.scene.add(sun);
    this.scene.add(new THREE.AmbientLight(0x223044, 0.35));
  }

  private buildSun() {
    const geo = new THREE.SphereGeometry(3.4, 48, 48);
    const mat = new THREE.ShaderMaterial({
      uniforms: this.sunUniforms,
      vertexShader: SUN_VERTEX,
      fragmentShader: SUN_FRAGMENT,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.name = "sun";
    this.scene.add(mesh);
    this.sunMesh = mesh;
    const glow = this.makeGlowSprite(0xffc46a, 20);
    this.scene.add(glow);
    const halo = this.makeGlowSprite(0xff9a3c, 38);
    halo.material.opacity = 0.09;
    this.scene.add(halo);
    this.sunGlows = [glow, halo];
  }

  private makeGlowSprite(color: number, size: number) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 128;
    const ctx = canvas.getContext("2d")!;
    const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, "rgba(255,255,255,0.85)");
    g.addColorStop(0.25, "rgba(255,255,255,0.28)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({ map: tex, color, transparent: true, opacity: 0.24, blending: THREE.AdditiveBlending, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.setScalar(size);
    return sprite;
  }

  private nebulae: THREE.Sprite[] = [];
  private nearNebulae: { sp: THREE.Sprite; vx: number; vz: number; phase: number; baseY: number }[] = [];

  private buildNebulae() {
    const spots: [number, number, number, number, number][] = [
      [-260, 90, -320, 0x5a48c8, 340],
      [300, -60, -260, 0x1f6aa8, 300],
      [-180, -140, 260, 0x7a3f9e, 260],
      [240, 150, 280, 0x276a8e, 380],
    ];
    for (const [x, y, z, color, size] of spots) {
      const sp = this.makeGlowSprite(color, size);
      sp.material.opacity = 0.07;
      sp.position.set(x, y, z);
      this.scene.add(sp);
      this.nebulae.push(sp);
    }
    // near-field clouds: close enough to parallax against the system on camera moves
    const near: [number, number, number, number, number][] = [
      [-150, 40, -120, 0x4a3fb0, 130],
      [170, -30, 90, 0x2a6a9e, 110],
      [60, 70, 170, 0x6a4fc8, 100],
      [-110, -50, 150, 0x3a5a9e, 90],
      [200, 20, -160, 0x5a3f9e, 120],
    ];
    for (const [x, y, z, color, size] of near) {
      const sp = this.makeGlowSprite(color, size);
      sp.material.opacity = 0.055;
      sp.position.set(x, y, z);
      this.scene.add(sp);
      this.nearNebulae.push({ sp, vx: (Math.random() - 0.5) * 1.6, vz: (Math.random() - 0.5) * 1.6, phase: Math.random() * Math.PI * 2, baseY: y });
    }
  }

  private buildStars() {
    const palette = [0xffffff, 0xcfe0ff, 0xfff2cf, 0xffd9b8, 0xbfd4ff];
    const mk = (count: number, banded: boolean) => {
      const pos = new Float32Array(count * 3);
      const col = new Float32Array(count * 3);
      const size = new Float32Array(count);
      const phase = new Float32Array(count);
      const c = new THREE.Color();
      const band = new THREE.Matrix4().makeRotationZ(0.6).multiply(new THREE.Matrix4().makeRotationX(0.4));
      const v = new THREE.Vector3();
      for (let k = 0; k < count; k++) {
        if (banded) {
          v.set((Math.random() - 0.5) * 2.4, (Math.random() - 0.5) * 0.34 * (1 - Math.abs((Math.random() - 0.5)) ), (Math.random() - 0.5) * 2.4);
          v.applyMatrix4(band).normalize().multiplyScalar(950 + Math.random() * 500);
        } else {
          v.set(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize().multiplyScalar(900 + Math.random() * 700);
        }
        pos.set([v.x, v.y, v.z], k * 3);
        c.setHex(palette[(Math.random() * palette.length) | 0]).multiplyScalar(banded ? 0.45 : 0.75 + Math.random() * 0.25);
        col.set([c.r, c.g, c.b], k * 3);
        size[k] = banded ? 0.8 + Math.random() * 1.4 : 1.0 + Math.random() * 2.2;
        phase[k] = Math.random() * Math.PI * 2;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
      geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
      const mat = new THREE.ShaderMaterial({
        uniforms: this.starUniforms,
        vertexShader: STAR_VERTEX,
        fragmentShader: STAR_FRAGMENT,
        transparent: true,
        depthWrite: false,
        vertexColors: true,
        blending: THREE.AdditiveBlending,
      });
      this.scene.add(new THREE.Points(geo, mat));
    };
    mk(5200, false);
    mk(3800, true);
  }

  private buildTacticalGrid() {
    const verts: number[] = [];
    for (let ring = 1; ring <= 6; ring++) {
      const r = ring * 11;
      for (let i = 0; i < 96; i++) {
        const a0 = (i / 96) * Math.PI * 2;
        const a1 = ((i + 1) / 96) * Math.PI * 2;
        verts.push(Math.cos(a0) * r, 0.12, Math.sin(a0) * r, Math.cos(a1) * r, 0.12, Math.sin(a1) * r);
      }
    }
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      const c = Math.cos(a), sV = Math.sin(a);
      verts.push(c * 9, 0.12, sV * 9, c * 66, 0.12, sV * 66);
    }
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const c = Math.cos(a), sV = Math.sin(a);
      const len = i % 6 === 0 ? 3.4 : 1.6;
      verts.push(c * 66, 0.12, sV * 66, c * (66 + len), 0.12, sV * (66 + len));
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
    this.tacGrid = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x8fb8e8, transparent: true, opacity: 0, depthWrite: false }));
    this.sys.add(this.tacGrid);
  }

  /** The simulated-universe reveal: a huge lat/long sphere around the system. */
  private buildSimGrid() {
    const R = OUTER_ORBIT + 34;
    const verts: number[] = [];
    for (let lat = -3; lat <= 3; lat++) {
      const y = Math.sin(lat * 0.28) * R;
      const rr = Math.cos(lat * 0.28) * R;
      for (let i = 0; i < 96; i++) {
        const a0 = (i / 96) * Math.PI * 2, a1 = ((i + 1) / 96) * Math.PI * 2;
        verts.push(Math.cos(a0) * rr, y, Math.sin(a0) * rr, Math.cos(a1) * rr, y, Math.sin(a1) * rr);
      }
    }
    for (let lon = 0; lon < 18; lon++) {
      const a = (lon / 18) * Math.PI;
      const c = Math.cos(a), sV = Math.sin(a);
      for (let i = 0; i < 48; i++) {
        const t0 = (i / 48) * Math.PI, t1 = ((i + 1) / 48) * Math.PI;
        verts.push(Math.sin(t0) * c * R, Math.cos(t0) * R, Math.sin(t0) * sV * R, Math.sin(t1) * c * R, Math.cos(t1) * R, Math.sin(t1) * sV * R);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
    const mat = new THREE.ShaderMaterial({
      uniforms: this.simGridUniforms,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `varying vec3 vPos; void main(){ vPos = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `uniform float uNav; uniform float uWave; uniform vec3 uColor; varying vec3 vPos;
        void main(){
          float rr = length(vPos.xz);
          float front = uWave > 0.0 ? smoothstep(12.0, 0.0, abs(rr - uWave)) : 0.0;
          float tail = uWave > 0.0 && rr < uWave ? exp(-(uWave - rr) * 0.055) * 0.5 : 0.0;
          float g = max(front, tail);
          float a = uNav * 0.05 + g * 0.45;
          gl_FragColor = vec4(uColor * (0.7 + g * 1.8), a);
        }`,
    });
    this.simGrid = new THREE.LineSegments(geo, mat);
    this.sys.add(this.simGrid);
  }

  /** Future-path preview: a dashed violet arc ahead of each planet. */
  private buildFutures() {
    for (const def of PLANETS) {
      const fg = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(14 * 3), 3));
      const fl = new THREE.Line(fg, new THREE.LineDashedMaterial({ color: 0x9b7cff, transparent: true, opacity: 0, dashSize: 1.6, gapSize: 1.4 }));
      fl.computeLineDistances();
      this.sys.add(fl);
      this.futures.set(def.key, fl);
    }
  }

  /** Event-style energy packet: core → planet along an arced curve. */
  private spawnFlow(def: PlanetDef) {
    const target = this.planetGroups.get(def.key)!.position.clone();
    const mid = (k: number, lift: number) =>
      target.clone().multiplyScalar(k).add(new THREE.Vector3((Math.random() - 0.5) * 12, lift + Math.random() * 7, (Math.random() - 0.5) * 12));
    const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(0, 1.4, 0), mid(0.33, 5), mid(0.66, 3), target]);
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)),
      new THREE.LineBasicMaterial({ color: 0x66d9ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    const pts = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(10 * 3), 3)),
      new THREE.PointsMaterial({ color: this.accentColors[def.key] ?? 0x9fe8ff, size: 4.6, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    this.sys.add(line, pts);
    this.flows.push({ curve, line, pts, t: 0, speed: 1 / (2.4 + Math.random() * 1.4), target: def.key, fade: 1 });
  }

  private planetPalette(def: PlanetDef): { a: number; b: number; c: number; atmos: number } {
    switch (def.key) {
      case "mercury": return { a: 0x8a8378, b: 0x5e574c, c: 0x3d382f, atmos: 0x000000 };
      case "venus": return { a: 0xd9b98a, b: 0xc49a66, c: 0xe8d5ae, atmos: 0xffe0b0 };
      case "earth": return { a: 0x2f6bb3, b: 0x3e8a4f, c: 0xb08c4f, atmos: 0x5fb8ff };
      case "mars": return { a: 0xb3613a, b: 0x8c4526, c: 0xd9905f, atmos: 0xd97a4a };
      case "jupiter": return { a: 0xc8a06a, b: 0x8f6a45, c: 0xe8d3a8, atmos: 0xd9b98a };
      case "saturn": return { a: 0xd6c08a, b: 0xa8905e, c: 0xe8dcb0, atmos: 0xe8d9a8 };
      case "uranus": return { a: 0x9fd4d8, b: 0x6fb0bc, c: 0xc8ecf0, atmos: 0x8fd8e0 };
      case "neptune": return { a: 0x3f66d4, b: 0x2a47a8, c: 0x7fa8ff, atmos: 0x6090ff };
      case "pluto": return { a: 0xa08a6e, b: 0x7d6450, c: 0xc4b09a, atmos: 0xa89880 };
    }
  }

  private buildPlanets() {
    for (const def of PLANETS) {
      const group = new THREE.Group();
      const palette = this.planetPalette(def);
      const uTime = { value: 0 };
      this.timeUniforms.push(uTime);
      const kindMap = { rocky: 0, earth: 1, gas: 2, ice: 3 };
      const mat = new THREE.ShaderMaterial({
        uniforms: {
          uA: { value: new THREE.Color(palette.a) },
          uB: { value: new THREE.Color(palette.b) },
          uC: { value: new THREE.Color(palette.c) },
          uAtmos: { value: new THREE.Color(palette.atmos) },
          uKind: { value: kindMap[def.kind] },
          uDim: { value: 1 },
          uTime,
          uSeed: { value: PLANETS.indexOf(def) * 7.31 + 1.7 },
        },
        vertexShader: PLANET_VERTEX,
        fragmentShader: PLANET_FRAGMENT,
      });
      this.dimUniforms.set(def.key, mat.uniforms.uDim as { value: number });
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(def.radius, 48, 48), mat);
      const tilt = new THREE.Group();
      tilt.rotation.z = def.axialTilt * DEG;
      tilt.add(mesh);
      group.add(tilt);
      this.spinners.push({ mesh, period: def.rotation, angle: 0 });

      if (def.ringed) {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(def.radius * 1.35, def.radius * 2.25, 128, 1),
          new THREE.ShaderMaterial({
            uniforms: { uColor: { value: new THREE.Color(0xcbb98e) }, uInner: { value: def.radius * 1.35 }, uOuter: { value: def.radius * 2.25 } },
            vertexShader: RING_VERTEX,
            fragmentShader: RING_FRAGMENT,
            transparent: true,
            side: THREE.DoubleSide,
            depthWrite: false,
          }),
        );
        ring.rotation.x = Math.PI / 2;
        tilt.add(ring);
      }

      if (def.key === "earth") {
        const moonTime = { value: 0 };
        this.timeUniforms.push(moonTime);
        const moonMat = new THREE.ShaderMaterial({
          uniforms: {
            uA: { value: new THREE.Color(0x9a958c) },
            uB: { value: new THREE.Color(0x6e695f) },
            uC: { value: new THREE.Color(0x4a463f) },
            uAtmos: { value: new THREE.Color(0x000000) },
            uKind: { value: 0 },
            uDim: { value: 1 },
            uTime: moonTime,
            uSeed: { value: 99.7 },
              },
          vertexShader: PLANET_VERTEX,
          fragmentShader: PLANET_FRAGMENT,
        });
        const moon = new THREE.Mesh(new THREE.SphereGeometry(0.42, 24, 24), moonMat);
        const moonPivot = new THREE.Group();
        moon.position.x = 2.9;
        moonPivot.add(moon);
        group.add(moonPivot);
        this.moon = moonPivot;
      }

      const line = new THREE.LineLoop(
        new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(orbitPath(def), 3)),
        new THREE.LineBasicMaterial({ color: 0xa8ccec, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false }),
      );
      this.sys.add(line);
      this.orbitLines.set(def.key, line);

      const pGlow = this.makeGlowSprite(this.accentColors[def.key] ?? 0x42d9ff, def.radius * 3.4);
      pGlow.material.opacity = 0.2;
      group.add(pGlow);
      this.planetGlows.set(def.key, pGlow);

      const hit = new THREE.Mesh(new THREE.SphereGeometry(Math.max(def.radius * 1.9, 2.6), 12, 12), new THREE.MeshBasicMaterial({ visible: false }));
      hit.userData.key = def.key;
      group.add(hit);
      this.hitMeshes.set(def.key, hit);

      this.planetGroups.set(def.key, group);
      this.planetMeshes.set(def.key, mesh);
      this.sys.add(group);
    }
    const sunHit = new THREE.Mesh(new THREE.SphereGeometry(11, 12, 12), new THREE.MeshBasicMaterial({ visible: false }));
    sunHit.userData.key = "sun";
    this.sys.add(sunHit);
    this.hitMeshes.set("sun" as PlanetKey, sunHit);
  }

  // ---------- input ----------

  private onPointerMove = (event: PointerEvent) => {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.drift.x = (event.clientX / window.innerWidth) * 2 - 1;
    this.drift.y = (event.clientY / window.innerHeight) * 2 - 1;
  };

  private onPointerLeave = () => {
    this.pointer.set(-2, -2);
    this.drift.x = 0;
    this.drift.y = 0;
    this.dragging = false;
    this.callbacks.onHover(null);
  };

  private onPointerDown = (event: PointerEvent) => {
    this.dragging = true;
    this.dragMoved = 0;
    this.downPos = { x: event.clientX, y: event.clientY };
    const move = (e: PointerEvent) => {
      if (!this.dragging) return;
      const dx = e.clientX - this.downPos.x;
      const dy = e.clientY - this.downPos.y;
      this.dragMoved = Math.max(this.dragMoved, Math.abs(dx) + Math.abs(dy));
      this.downPos = { x: e.clientX, y: e.clientY };
      this.dragAz -= dx * 0.004;
      this.dragEl += dy * 0.003;
      this.dragAz = Math.min(Math.PI / 3, Math.max(-Math.PI / 3, this.dragAz));
      this.dragEl = Math.min(0.9, Math.max(-0.35, this.dragEl));
    };
    const up = () => {
      this.dragging = false;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  private onWheel = (event: WheelEvent) => {
    event.preventDefault();
    this.zoom = Math.min(2.4, Math.max(0.45, this.zoom * Math.exp(-event.deltaY * 0.0011)));
  };

  private onClick = () => {
    if (this.dragMoved > 7) return;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.hitMeshes.values()], false);
    // planets take precedence over the core's large hit sphere
    const key = (hits.find((h) => h.object.userData.key !== "sun") ?? hits[0])?.object.userData.key as PlanetKey | "sun" | undefined;
    if (key === "sun") this.callbacks.onSun();
    else if (key) this.callbacks.onSelect(key);
    else this.callbacks.onEmpty();
  };

  private onDblClick = () => {
    if (this.dragMoved > 7) return;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.hitMeshes.values()], false);
    if (hits.find((h) => h.object.userData.key !== "sun")) return;
    if (hits[0]?.object.userData.key === "sun") this.callbacks.onSunDbl();
  };

  // ---------- public API ----------

  setHovered(key: PlanetKey | null) { this.hovered = key; }
  setSelected(key: PlanetKey | null) {
    if (key === this.selected) return;
    this.selected = key;
    this.frozen = key !== null || this.navMode;
    this.dragAz = 0;
    this.dragEl = 0;
  }
  setNav(nav: boolean) {
    if (nav === this.navMode) return;
    this.navMode = nav;
    this.frozen = nav || this.selected !== null;
    this.dragAz = 0;
    this.dragEl = 0;
    this.navPulse = 1;
    this.pulseT = 0;
    if (nav) this.transitions += 1;
  }
  setTimeScale(daysPerSecond: number) { this.timeScale = daysPerSecond; }
  setPaused(paused: boolean) { this.paused = paused; }
  getSimDays() { return this.simDays; }

  resize(w: number, h: number) {
    this.w = w;
    this.h = h;
    this.renderer.setSize(w, h);
    this.composer?.setSize(w, h);
    this.camera.aspect = w / h;
    this.distortUniforms.uAspect.value = w / h;
    this.camera.updateProjectionMatrix();
    this.fitDist = (OUTER_ORBIT + 10) / Math.tan(20 * DEG) / Math.min(w / h, 1.8);
    this.mapDist = this.fitDist * 1.02;
  }

  /** T1 orbital insertion — camera pushes toward the planet, shockwave +
   *  bloom fire; cb receives the planet's screen coords for the reveal. */
  insertion(key: PlanetKey, cb: (p: { x: number; y: number }) => void) {
    this.setSelected(key);
    this.insertT = 0;
    this.insertKey = key;
    this.insertCb = cb;
  }

  get inLineup() { return this.selected !== null; }

  /** Screen-space position + pixel radius of a planet, for DOM labels. */
  project(key: PlanetKey): { x: number; y: number; r: number; visible: boolean } {
    const group = this.planetGroups.get(key);
    if (!group) return { x: 0, y: 0, r: 0, visible: false };
    const def = PLANETS.find((d) => d.key === key)!;
    this.tmpV.setFromMatrixPosition(group.matrixWorld);
    const cx = this.tmpV.clone().project(this.camera);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const edge = this.tmpV.addScaledVector(right, def.radius * group.scale.x).project(this.camera);
    return {
      x: (cx.x * 0.5 + 0.5) * this.w,
      y: (-cx.y * 0.5 + 0.5) * this.h,
      r: Math.hypot((edge.x - cx.x) * this.w, (edge.y - cx.y) * this.h) * 0.5,
      visible: cx.z < 1,
    };
  }

  /** Screen-space position of the sun (scene origin). */
  center(): { x: number; y: number } {
    this.tmpV.set(0, 0, 0).project(this.camera);
    return { x: (this.tmpV.x * 0.5 + 0.5) * this.w, y: (-this.tmpV.y * 0.5 + 0.5) * this.h };
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    const el = this.renderer.domElement;
    el.removeEventListener("pointermove", this.onPointerMove);
    el.removeEventListener("pointerleave", this.onPointerLeave);
    el.removeEventListener("pointerdown", this.onPointerDown);
    el.removeEventListener("wheel", this.onWheel);
    el.removeEventListener("click", this.onClick);
    el.removeEventListener("dblclick", this.onDblClick);
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.Points || obj instanceof THREE.LineLoop) {
        obj.geometry.dispose();
        const mat = obj.material as THREE.Material | THREE.Material[];
        (Array.isArray(mat) ? mat : [mat]).forEach((m) => m.dispose());
      }
    });
    this.composer?.dispose();
    this.renderer.dispose();
    el.remove();
  }

  private updateTrails(dt: number) {
    for (const [key, trail] of this.trails) {
      const group = this.planetGroups.get(key)!;
      trail.acc += dt;
      if (this.simSpeed > 0.3 && trail.acc > 0.045) {
        trail.acc = 0;
        trail.pts.push(group.position.clone());
        if (trail.pts.length > 24) trail.pts.shift();
        const attr = trail.line.geometry.getAttribute("position") as THREE.BufferAttribute;
        trail.pts.forEach((p, i) => attr.setXYZ(i, p.x, p.y, p.z));
        attr.needsUpdate = true;
        trail.line.geometry.setDrawRange(0, trail.pts.length);
      }
      const mat = trail.line.material as THREE.LineBasicMaterial;
      const goal = this.simSpeed > 0.3 && this.simSpeed < 0.98 ? 0.5 : 0;
      mat.opacity += (goal - mat.opacity) * Math.min(1, dt * 6);
      if (mat.opacity < 0.02 && trail.pts.length > 0) {
        trail.pts.length = 0;
        trail.line.geometry.setDrawRange(0, 0);
      }
    }
  }

  orbitRadiusOf(key: PlanetKey): number {
    const def = PLANETS.find((d) => d.key === key);
    return def ? Math.hypot(...(() => { const g = this.planetGroups.get(key)!.position; return [g.x, g.z]; })()) : 0;
  }

  /** Shockwave radius in scene units; Infinity once settled in lock, 0 in orbit mode. */
  waveRadius(): number {
    if (!this.navMode) return 0;
    if (this.pulseT >= 0) return waveRadiusAt(this.pulseT / WAVE_DUR);
    return Infinity;
  }

  private planetFlashes = new Map<PlanetKey, number>();

  // ---------- frame loop ----------

  private targetPose(): Pose {
    const out: Pose = { target: new THREE.Vector3(), az: this.pose.az, el: this.pose.el, dist: this.pose.dist };
    if (this.selected === ("sun" as PlanetKey)) {
      // core focus: push in toward the star itself
      out.target.set(0, 0, 0);
      out.az = this.pose.az + this.dragAz * 0.4;
      out.el = 42 * DEG + this.dragEl * 0.5;
      out.dist = 58 * this.zoom;
      return out;
    }
    if (this.selected) {
      const group = this.planetGroups.get(this.selected);
      const def = PLANETS.find((p) => p.key === this.selected);
      if (group && def) {
        out.target.setFromMatrixPosition(group.matrixWorld);
        out.az = Math.atan2(group.position.z, group.position.x) + Math.PI + this.dragAz * 0.4;
        out.el = 46 * DEG + this.dragEl * 0.5;
        const close = this.insertT >= 0 ? 3.2 : 14;
        out.dist = Math.max(26, def.radius * close) * this.zoom;
        return out;
      }
    }
    if (this.navMode) {
      // look straight down the tilted plane's normal → perfectly circular starmap
      out.el = Math.asin(this.sysN.y) + this.dragEl * 0.3;
      out.az = Math.atan2(this.sysN.z, this.sysN.x) + this.dragAz * 0.3;
      out.dist = this.mapDist * this.zoom;
    } else {
      out.el = (24 + this.drift.y * -1.2) * DEG + this.dragEl;
      out.az = 25 * DEG + this.drift.x * 1.5 * DEG + this.dragAz;
      out.dist = this.fitDist * this.zoom;
    }
    return out;
  }

  private loop = (now: number) => {
    if (this.disposed) return;
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    const realTime = now / 1000;

    const speedTarget = this.frozen ? 0 : this.paused || this.reduce ? 0 : 1;
    const prevSpeed = this.simSpeed;
    const ease = speedTarget < this.simSpeed ? dt / 0.12 : dt / 0.45;
    this.simSpeed += (speedTarget - this.simSpeed) * Math.min(1, ease);
    if (Math.abs(this.simSpeed - speedTarget) < 0.004) this.simSpeed = speedTarget;
    if (prevSpeed > 0.15 && this.simSpeed <= 0.15 && this.fractureT <= 0) this.fractureT = 0.14;
    this.simDays += this.timeScale * this.simSpeed * dt;
    for (const s of this.spinners) {
      s.angle += (360 / s.period) * this.timeScale * this.simSpeed * dt;
    }
    this.updateTrails(dt);

    this.sunUniforms.uTime.value = realTime;
    this.starUniforms.uTime.value = this.reduce ? 0 : realTime;
    for (const u of this.timeUniforms) u.value = this.simDays;

    for (const def of PLANETS) {
      const p = scenePosition(def, this.simDays);
      this.planetGroups.get(def.key)!.position.set(p.x, p.y, p.z);
    }
    // hover/selected → dim the others (uDim lerps per planet)
    const anyActive = this.selected ?? this.hovered;
    for (const def of PLANETS) {
      const u = this.dimUniforms.get(def.key);
      if (u) u.value += ((anyActive && def.key !== anyActive ? 0.38 : 1) - u.value) * Math.min(1, dt * 7);
    }
    if (this.navPulse > 0) this.navPulse = Math.max(0, this.navPulse - dt * 1.1);
    const waveR = this.waveRadius();
    for (const def of PLANETS) {
      const group = this.planetGroups.get(def.key)!;
      const r = Math.hypot(group.position.x, group.position.z);
      if (waveR >= r && (this.planetFlashes.get(def.key) ?? 0) <= 0) this.planetFlashes.set(def.key, 1);
      const f = this.planetFlashes.get(def.key) ?? 0;
      if (f > 0) this.planetFlashes.set(def.key, f - dt * 1.6);
      const glow = this.planetGlows.get(def.key);
      if (glow && f > 0) glow.material.opacity += f * 1.1;
    }
    if (this.fractureT > 0) this.fractureT = Math.max(0, this.fractureT - dt);
    this.starUniforms.uGlitch.value = this.fractureT > 0 ? this.fractureT / 0.14 : 0;
    if (this.tacGrid) {
      const m = this.tacGrid.material as THREE.LineBasicMaterial;
      m.opacity += (((this.navMode ? 0.15 : 0)) - m.opacity) * Math.min(1, dt * 3);
    }
    // sim grid: fade in on lock; grid band follows the shockwave radius
    this.simGridUniforms.uNav.value += (((this.navMode ? 1 : 0)) - this.simGridUniforms.uNav.value) * Math.min(1, dt * 2.2);
    this.simGridUniforms.uWave.value = this.navMode ? (this.pulseT >= 0 ? this.waveRadius() : -1) : -1;
    // future prediction dashed line
    for (const def of PLANETS) {
      const lag = def.period * 0.024;
      const fl = this.futures.get(def.key)!;
      const attr = fl.geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let i = 0; i < 14; i++) {
        const p = scenePosition(def, this.simDays + lag * (i / 14) * (60 / Math.max(6, this.timeScale)));
        attr.setXYZ(i, p.x, p.y, p.z);
      }
      attr.needsUpdate = true;
      fl.computeLineDistances();
      const fm = fl.material as THREE.LineDashedMaterial;
      fm.opacity += ((this.navMode ? 0.5 : 0) - fm.opacity) * Math.min(1, dt * 4);
    }
    // cosmic data flow: event-style packets; freeze mid-flight under temporal lock
    this.nextFlow -= dt;
    if (this.nextFlow <= 0) {
      this.nextFlow = 2.6 + Math.random() * 2.6;
      if (!this.navMode && !this.selected && !this.paused && this.flows.length < 3) {
        this.spawnFlow(PLANETS[(Math.random() * PLANETS.length) | 0]);
      }
    }
    for (let i = this.flows.length - 1; i >= 0; i--) {
      const f = this.flows[i];
      f.t += f.speed * this.simSpeed * dt;
      const arrived = f.t >= 1;
      if (arrived && f.fade === 1) this.planetFlashes.set(f.target, 0.8);
      if (arrived) f.fade = Math.max(0, f.fade - dt * 0.8);
      const attr = f.pts.geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let k = 0; k < 10; k++) {
        const tt = Math.min(1, Math.max(0, f.t - k * 0.022));
        const p = f.curve.getPoint(tt);
        attr.setXYZ(k, p.x, p.y, p.z);
      }
      attr.needsUpdate = true;
      const fadeIn = Math.min(1, f.t * 9);
      const pGoal = f.fade * fadeIn * (this.navMode ? 0.9 : 0.8);
      (f.pts.material as THREE.PointsMaterial).opacity += (pGoal - (f.pts.material as THREE.PointsMaterial).opacity) * Math.min(1, dt * 6);
      const lGoal = f.fade * (this.navMode ? 0.32 : 0.04);
      (f.line.material as THREE.LineBasicMaterial).opacity += (lGoal - (f.line.material as THREE.LineBasicMaterial).opacity) * Math.min(1, dt * 3);
      if (f.fade <= 0) {
        this.sys.remove(f.line, f.pts);
        f.line.geometry.dispose(); f.pts.geometry.dispose();
        (f.line.material as THREE.Material).dispose(); (f.pts.material as THREE.Material).dispose();
        this.flows.splice(i, 1);
      }
    }
    if (this.pulseT >= 0) {
      this.pulseT += dt;
      if (!this.pulseRing) {
        this.pulseRing = new THREE.Mesh(
          new THREE.RingGeometry(0.94, 1, 96),
          new THREE.MeshBasicMaterial({ color: 0xaee6ff, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }),
        );
        this.pulseRing.rotation.x = -Math.PI / 2;
        this.sys.add(this.pulseRing);
      }
      const t = Math.min(1, this.pulseT / WAVE_DUR);
      this.pulseRing.scale.setScalar(waveRadiusAt(t));
    }
    if (this.insertT >= 0 && this.insertKey) {
      this.insertT += dt;
      const t = Math.min(this.insertT / 0.42, 1);
      const pp = this.project(this.insertKey);
      this.distortUniforms.uCenter.value.set(pp.x / this.w, 1 - pp.y / this.h);
      this.distortUniforms.uWave.value = t * 1.15;
      this.distortUniforms.uIntensity.value = Math.sin(Math.min(t * 1.25, 1) * Math.PI) * 0.9;
      if (this.bloomPass) this.bloomPass.strength = 0.55 + Math.sin(t * Math.PI) * 0.85;
      if (t >= 1 && this.insertCb) {
        const cb = this.insertCb; this.insertCb = null; this.insertT = -1;
        cb({ x: pp.x, y: pp.y });
      }
    }
    if (this.pulseT >= 0 && this.pulseRing) {
      const t = Math.min(this.pulseT / WAVE_DUR, 1);
      (this.pulseRing.material as THREE.MeshBasicMaterial).opacity = 0.9 * Math.pow(1 - t, 1.6);
      if (t >= 1) { this.sys.remove(this.pulseRing); this.pulseRing.geometry.dispose(); this.pulseRing = undefined; this.pulseT = -1; }
    }
    this.sunGlows.forEach((sprite, i) => {
      const base = (i === 0 ? 0.15 : 0.05) + this.navPulse * (i === 0 ? 0.45 : 0.28);
      sprite.material.opacity += (base - sprite.material.opacity) * Math.min(1, dt * 8);
      const bs = (i === 0 ? 18 : 30) * (1 + this.navPulse * 0.9);
      sprite.scale.setScalar(sprite.scale.x + (bs - sprite.scale.x) * Math.min(1, dt * 8));
    });
    this.starUniforms.uDim.value += (((this.selected || this.navMode) ? 0.55 : 1) - this.starUniforms.uDim.value) * Math.min(1, dt * 4);
    for (const n of this.nebulae) n.material.rotation += dt * 0.004;
    for (const nn of this.nearNebulae) {
      nn.sp.position.x += nn.vx * dt;
      nn.sp.position.z += nn.vz * dt;
      nn.sp.position.y = nn.baseY + Math.sin(realTime * 0.05 + nn.phase) * 7;
      nn.sp.material.rotation -= dt * 0.007;
      if (Math.hypot(nn.sp.position.x, nn.sp.position.z) > 300) { nn.vx = -nn.vx; nn.vz = -nn.vz; }
    }
    for (const s of this.spinners) s.mesh.rotation.y = s.angle * DEG;
    if (this.moon) this.moon.rotation.y = (this.simDays / 27.32) * Math.PI * 2;

    // hover raycast (throttled to pointer position changes)
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.hitMeshes.values()], false);
    const hitKey = hits.find((h) => h.object.userData.key !== "sun")?.object.userData.key as PlanetKey | undefined;
    const nextHover = hitKey ?? null;
    if (nextHover !== this.hovered) this.callbacks.onHover(nextHover);

    // hover gravity lift
    for (const [key, group] of this.planetGroups) {
      const lift = key === this.selected ? 1.16 : key === this.hovered ? 1.1 : 1;
      const sc = group.scale.x + (lift - group.scale.x) * Math.min(1, dt * 9);
      group.scale.setScalar(sc);
    }

    // orbit line emphasis
    for (const [key, line] of this.orbitLines) {
      const mat = line.material as THREE.LineBasicMaterial;
      const active = key === this.selected || key === (this.hovered ?? nextHover);
      mat.opacity += (((active ? 0.85 : this.navMode ? 0.55 : 0.18)) - mat.opacity) * Math.min(1, dt * 8);
      mat.color.setHex(active ? (this.accentColors[key] ?? 0x42d9ff) : 0xa8ccec);
      const glow = this.planetGlows.get(key);
      if (glow) glow.material.opacity += ((key === this.selected ? 0.5 : active ? 0.38 : 0.2) - glow.material.opacity) * Math.min(1, dt * 8);
    }

    for (const t of this.travelers) {
      const path = (this.orbitLines.get(t.def.key)!.geometry.getAttribute("position") as THREE.BufferAttribute).array as Float32Array;
      const segs = path.length / 3 - 1;
      const f = ((realTime * 0.07 + t.phase) % 1) * segs;
      const k0 = Math.floor(f);
      const k1 = Math.min(segs, k0 + 1);
      const fr = f - k0;
      const arr = t.points.geometry.getAttribute("position") as THREE.BufferAttribute;
      arr.setXYZ(0,
        path[k0 * 3] + (path[k1 * 3] - path[k0 * 3]) * fr,
        path[k0 * 3 + 1] + (path[k1 * 3 + 1] - path[k0 * 3 + 1]) * fr,
        path[k0 * 3 + 2] + (path[k1 * 3 + 2] - path[k0 * 3 + 2]) * fr);
      arr.needsUpdate = true;
      const tGoal = this.navMode ? 0 : 0.4 + 0.35 * Math.sin(realTime * 2 + t.phase * 9);
      (t.points.material as THREE.PointsMaterial).opacity += (tGoal - (t.points.material as THREE.PointsMaterial).opacity) * Math.min(1, dt * 6);
    }

    // camera: exponential smoothing toward target pose (always interruptible)
    const goal = this.targetPose();
    const k = this.reduce ? 1 : 1 - Math.exp(-dt / (this.transitions <= 1 ? 0.32 : 0.2));
    this.pose.target.lerp(goal.target, k);
    this.pose.el += (goal.el - this.pose.el) * k;
    this.pose.az += (goal.az - this.pose.az) * k;
    this.pose.dist += (goal.dist - this.pose.dist) * k;
    const { target, az, el, dist } = this.pose;
    const ce = Math.cos(el);
    const upGoal = this.navMode ? this.tmpV.copy(this.sysUp) : this.tmpV.set(0, 1, 0);
    this.upV.lerp(upGoal, k);
    this.camera.up.copy(this.upV).normalize();
    this.camera.position.set(
      target.x + dist * ce * Math.cos(az),
      target.y + dist * Math.sin(el),
      target.z + dist * ce * Math.sin(az),
    );
    this.camera.lookAt(target);

    // periodic idle energy pulse (every 7-10s in orbit mode)
    this.nextPulse -= dt;
    if (this.nextPulse <= 0 && !this.navMode && !this.selected) {
      this.nextPulse = 7 + Math.random() * 3;
      if (this.pulseT < 0) this.pulseT = 0;
    }

    // wave → screen-space distortion uniforms; project the actual ring so the ripple hugs it
    const c = this.center();
    this.distortUniforms.uCenter.value.set(c.x / this.w, 1 - c.y / this.h);
    if (this.pulseT >= 0) {
      const t = Math.min(1, this.pulseT / WAVE_DUR);
      this.tmpV.set(waveRadiusAt(t), 0, 0).applyMatrix4(this.sys.matrixWorld).project(this.camera);
      const sx = this.tmpV.x * 0.5 + 0.5;
      const sy = -this.tmpV.y * 0.5 + 0.5;
      const ucx = this.distortUniforms.uCenter.value.x;
      const ucy = this.distortUniforms.uCenter.value.y;
      this.distortUniforms.uWave.value = Math.hypot((sx - ucx) * this.distortUniforms.uAspect.value, sy - ucy);
      this.distortUniforms.uIntensity.value = (this.navMode ? 1 : 0.55) * (1 - t * 0.55);
    } else {
      this.distortUniforms.uWave.value = -1;
      this.distortUniforms.uIntensity.value = this.fractureT > 0 ? this.fractureT / 0.14 : 0;
    }
    if (this.bloomPass) {
      const boost = this.navPulse * 0.9 + (this.fractureT > 0 ? 0.35 : 0);
      this.bloomPass.strength += ((0.55 + boost) - this.bloomPass.strength) * Math.min(1, dt * 8);
    }
    // idle core breathing (5s period)
    this.sunGlows[0].material.opacity += Math.sin(realTime * 1.257) * 0.002;

    this.tickAccum += dt;
    if (this.tickAccum > 0.1) {
      this.tickAccum = 0;
      this.callbacks.onTick(this.simDays);
    }

    // fps watchdog: drop pixel ratio / bloom on weak devices
    this.fpsAcc += dt; this.fpsN += 1;
    if (this.fpsAcc > 3 && !this.degraded) {
      const fps = this.fpsN / this.fpsAcc;
      if (fps < 42) {
        this.degraded = true;
        this.renderer.setPixelRatio(1);
        this.composer?.setPixelRatio?.(1);
        if (fps < 30 && this.bloomPass) this.bloomPass.strength = 0.3;
        void import("../../app/motionTier").then((m) => m.setMotionTier("standard"));
      }
      this.fpsAcc = 0; this.fpsN = 0;
    }

    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
    this.raf = requestAnimationFrame(this.loop);
  };
}

export const ACCENTS: Record<string, number> = {
  cyan: 0x42d9ff,
  violet: 0x9b7cff,
  ice: 0x7dd3fc,
  green: 0x34d399,
  amber: 0xffb454,
};
