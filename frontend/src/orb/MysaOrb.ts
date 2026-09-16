import * as THREE from "three";

export type OrbPhase = "idle" | "listening" | "thinking" | "speaking" | "error";

interface OrbLevels {
  mic: () => number;
  speak: () => number;
}

const VERT_NOISE = /* glsl */ `
uniform float uTime;
uniform float uDeform;
uniform float uListen;
uniform float uSpeak;
varying float vDisp;

vec3 hash3(vec3 p) {
  p = vec3(dot(p, vec3(127.1, 311.7, 74.7)),
           dot(p, vec3(269.5, 183.3, 246.1)),
           dot(p, vec3(113.5, 271.9, 124.6)));
  return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
}
float noise(vec3 p) {
  vec3 i = floor(p); vec3 f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(dot(hash3(i), f),
                     dot(hash3(i + vec3(1,0,0)), f - vec3(1,0,0)), u.x),
                 mix(dot(hash3(i + vec3(0,1,0)), f - vec3(0,1,0)),
                     dot(hash3(i + vec3(1,1,0)), f - vec3(1,1,0)), u.x), u.y),
             mix(mix(dot(hash3(i + vec3(0,0,1)), f - vec3(0,0,1)),
                     dot(hash3(i + vec3(1,0,1)), f - vec3(1,0,1)), u.x),
                 mix(dot(hash3(i + vec3(0,1,1)), f - vec3(0,1,1)),
                     dot(hash3(i + vec3(1,1,1)), f - vec3(1,1,1)), u.x), u.y), u.z);
}
float orbNoise(vec3 p) {
  return noise(p * 1.6 + vec3(uTime * 0.35, uTime * 0.28, -uTime * 0.21)) * 0.5
       + noise(p * 3.4 - vec3(uTime * 0.25)) * 0.25;
}
`;

const VERT_BODY = /* glsl */ `
  float breathe = sin(uTime * 1.2) * 0.015;
  float listenWave = uListen * 0.09 * sin(uTime * 9.0 + position.y * 6.0);
  float speakWave = uSpeak * 0.11 * noise(position * 4.0 + vec3(uTime * 2.2));
  float disp = orbNoise(position) * uDeform + breathe + listenWave + speakWave;
  vDisp = disp;
  vec3 displaced = position + objectNormal * disp;
  vec3 transformed = displaced;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
`;

/**
 * The Mysa orb: a translucent, iridescent, softly deforming holographic body
 * floating in a calm pastel field, with a faint halo of drifting particles.
 */
export class MysaOrb {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private orb: THREE.Mesh;
  private orbMat: THREE.MeshPhysicalMaterial;
  private particles: THREE.Points;
  private uniforms = {
    uTime: { value: 0 },
    uDeform: { value: 0.12 },
    uListen: { value: 0 },
    uSpeak: { value: 0 },
  };
  private frame = 0;
  private clock = new THREE.Clock();
  private disposed = false;
  private resizeObs: ResizeObserver;
  private phase: OrbPhase = "idle";
  private smoothMic = 0;
  private smoothSpeak = 0;
  private listenMix = 0;
  private speakMix = 0;
  private errorPulse = 0;

  constructor(
    private canvas: HTMLCanvasElement,
    private levels: OrbLevels,
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.25;

    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 50);
    this.camera.position.set(0, 0, 5.4);

    this.scene.add(new THREE.AmbientLight(0xfff5fb, 1.4));
    const pink = new THREE.PointLight(0xffc7e0, 26, 20);
    pink.position.set(-2.6, 1.8, 2.4);
    const lavender = new THREE.PointLight(0xc9b8ff, 22, 20);
    lavender.position.set(2.8, -1.4, 2.0);
    const cyan = new THREE.PointLight(0xbfeaff, 10, 20);
    cyan.position.set(0.4, 2.6, -2.2);
    this.scene.add(pink, lavender, cyan);

    const geo = new THREE.IcosahedronGeometry(1.45, 64);
    this.orbMat = new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      metalness: 0.0,
      roughness: 0.12,
      transmission: 0.92,
      thickness: 1.6,
      ior: 1.32,
      clearcoat: 1.0,
      clearcoatRoughness: 0.25,
      iridescence: 1.0,
      iridescenceIOR: 1.28,
      iridescenceThicknessRange: [120, 520],
      sheen: 1.0,
      sheenColor: new THREE.Color(0xffd7ef),
      attenuationColor: new THREE.Color(0xe8d9ff),
      attenuationDistance: 2.4,
      envMapIntensity: 0.7,
    });
    this.orbMat.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms);
      shader.vertexShader = VERT_NOISE + shader.vertexShader;
      shader.vertexShader = shader.vertexShader.replace(
        "#include <begin_vertex>",
        VERT_BODY,
      );
    };
    this.orb = new THREE.Mesh(geo, this.orbMat);
    this.scene.add(this.orb);

    // Soft neutral environment for reflections/refraction.
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const envScene = new THREE.Scene();
    const envLight = new THREE.Mesh(
      new THREE.SphereGeometry(10),
      new THREE.MeshBasicMaterial({ color: 0xffeaf5, side: THREE.BackSide }),
    );
    envScene.add(envLight);
    this.orbMat.envMap = pmrem.fromScene(envScene, 0.04).texture;
    pmrem.dispose();

    // Faint drifting halo particles (few, soft, cheap).
    const count = 260;
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = 1.9 + Math.random() * 1.6;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.7;
      positions[i * 3 + 2] = r * Math.cos(phi);
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const pMat = new THREE.PointsMaterial({
      size: 0.07,
      map: MysaOrb.makeSpriteTexture(),
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
      color: 0xf3d9ff,
      blending: THREE.AdditiveBlending,
    });
    this.particles = new THREE.Points(pGeo, pMat);
    this.scene.add(this.particles);

    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(canvas.parentElement ?? canvas);
    this.resize();
    this.tick();
  }

  private static makeSpriteTexture(): THREE.Texture {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d")!;
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.4, "rgba(255,225,245,0.5)");
    grad.addColorStop(1, "rgba(255,225,245,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  setPhase(phase: OrbPhase): void {
    this.phase = phase;
  }

  private resize(): void {
    const parent = this.canvas.parentElement;
    const w = parent?.clientWidth ?? window.innerWidth;
    const h = parent?.clientHeight ?? window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private tick = (): void => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.tick);
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const t = this.clock.elapsedTime;
    this.uniforms.uTime.value = t;

    // Real amplitude → smoothed uniforms.
    const mic = this.phase === "listening" ? this.levels.mic() : 0;
    const speak = this.phase === "speaking" ? this.levels.speak() : 0;
    this.smoothMic += (mic - this.smoothMic) * Math.min(1, dt * 12);
    this.smoothSpeak += (speak - this.smoothSpeak) * Math.min(1, dt * 10);
    const k = Math.min(1, dt * 4);
    this.listenMix += ((this.phase === "listening" ? 1 : 0) - this.listenMix) * k;
    this.speakMix += ((this.phase === "speaking" ? 1 : 0) - this.speakMix) * k;
    this.uniforms.uListen.value = this.smoothMic * this.listenMix;
    this.uniforms.uSpeak.value = this.smoothSpeak * this.speakMix;

    // Phase-driven deformation & tempo.
    let target = 0.1;
    if (this.phase === "listening") target = 0.16 + this.smoothMic * 0.12;
    else if (this.phase === "thinking") target = 0.07;
    else if (this.phase === "speaking") target = 0.18 + this.smoothSpeak * 0.1;
    this.uniforms.uDeform.value +=
      (target - this.uniforms.uDeform.value) * Math.min(1, dt * 3);

    // Idle float + slow rotation; slower while thinking.
    const tempo = this.phase === "thinking" ? 0.35 : 1;
    this.orb.position.y = Math.sin(t * 0.6 * tempo) * 0.12;
    this.orb.rotation.y += dt * 0.08 * tempo;
    this.orb.rotation.x = Math.sin(t * 0.23 * tempo) * 0.06;

    // Soft internal illumination; restrained rose pulse on error.
    if (this.phase === "error") {
      this.errorPulse += dt * 2.4;
      const p = (Math.sin(this.errorPulse) * 0.5 + 0.5) * 0.35;
      this.orbMat.emissive.setHex(0xff9db4).multiplyScalar(p);
    } else {
      this.orbMat.emissive.setHex(0xffd9ec).multiplyScalar(
        0.12 + this.speakMix * this.smoothSpeak * 0.5 + this.listenMix * this.smoothMic * 0.4,
      );
    }

    // Gentle particle drift.
    this.particles.rotation.y += dt * 0.03;
    this.particles.rotation.z = Math.sin(t * 0.11) * 0.05;

    this.renderer.render(this.scene, this.camera);
  };

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    this.resizeObs.disconnect();
    this.orb.geometry.dispose();
    this.orbMat.dispose();
    (this.particles.geometry as THREE.BufferGeometry).dispose();
    (this.particles.material as THREE.Material).dispose();
    this.renderer.dispose();
  }
}
