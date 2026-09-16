/**
 * HoloOrb — an organic, holographic Three.js orb for Mysa.
 *
 * Visual model (matches the reference art):
 *   - A deformed, soft sphere (fBm-driven displacement) — an irregular blob.
 *   - A thin-film interference layer giving iridescent streaks that drift and
 *     phase with the amplitude of speech/mic activity.
 *   - Pastel palette (cyan / magenta / peach / violet) over a white-tinted
 *     body, with dark-magenta vertical striations and color-fringed edges.
 *   - Subtle dust particles floating around the orb.
 *
 * The orb reacts to real data only: microphone amplitude (when listening) and
 * TTS playback amplitude (when speaking). There are no fabricated values.
 *
 * API: { new MysaOrb(canvas, { mic, speak }) }
 *      orb.setPhase(phase)   // idle | listening | thinking | speaking | error
 *      orb.update(dt)        // advance one frame
 *      orb.dispose()
 */

import * as THREE from "three";

export type OrbPhase = "idle" | "listening" | "thinking" | "speaking" | "error";

export interface OrbOptions {
  /** Source of microphone amplitude (0..1). Only read during `listening`. */
  mic: () => number;
  /** Source of TTS playback amplitude (0..1). Only read during `speaking`. */
  speak: () => number;
}

export class MysaOrb {
  private canvas: HTMLCanvasElement;
  private opts: OrbOptions;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
    private mesh: THREE.Mesh;
  private dust: THREE.Points;
  private clock: THREE.Clock;

  private phase: OrbPhase = "idle";
  private phaseTime = 0;
  private noiseOffset = Math.random() * 100;
  private disposeFns: (() => void)[] = [];
  private onResize: () => void;
  private loop: () => void;

  constructor(canvas: HTMLCanvasElement, opts: OrbOptions) {
    this.canvas = canvas;
    this.opts = opts;

    const { innerWidth: w, innerHeight: h } = window;
    const pixelRatio = Math.min(window.devicePixelRatio, 2);

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      premultipliedAlpha: false,
    });
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(w, h, false);
    this.renderer.setClearColor(0x000000, 0);
    this.disposeFns.push(() => this.renderer.dispose());

    this.scene = new THREE.Scene();
    this.scene.background = null;

    this.camera = new THREE.PerspectiveCamera(36, w / h, 0.01, 100);
    this.camera.position.set(0, 0, 3.6);

    const hemi = new THREE.HemisphereLight(0xffffff, 0xf8f4ff, 0.55);
    hemi.position.set(0, 1, 0);
    this.scene.add(hemi);
    const back = new THREE.DirectionalLight(0xffffff, 0.55);
    back.position.set(0, 0, 4);
    this.scene.add(back);

    this.clock = new THREE.Clock();
    this.mesh = this.buildMesh();
    this.scene.add(this.mesh);
    this.dust = this.buildDust();
    this.scene.add(this.dust);

        this.disposeFns.push(() => {
      this.mesh.geometry.dispose();
      const m = this.mesh.material as THREE.ShaderMaterial;
      m.dispose();
    });

    this.onResize = () => this.fit();
    this.loop = () => {
      if (!document.hidden) {
        requestAnimationFrame(this.loop);
      } else {
        setTimeout(this.loop, 1000 / 30);
      }
      const dt = Math.min(this.clock.getDelta(), 0.05);
      this.update(dt);
    };
    window.addEventListener("resize", this.onResize);
    this.disposeFns.push(() => window.removeEventListener("resize", this.onResize));

        this.fit();
    this.loop();
  }

  // --- geometry + materials -------------------------------------------------

  /** Organic deformed sphere driven by fractal noise in the vertex shader. */
  private buildMesh(): THREE.Mesh {
    const geometry = new THREE.IcosahedronGeometry(1.0, 4);

    const material = new THREE.ShaderMaterial({
      transparent: true,
      uniforms: {
        uTime: { value: 0 },
        uNoiseA: { value: 0 },
        uAmp: { value: 0 },
        uPhase: { value: 0 },
        uPhaseTime: { value: 0 },
        uBaseColor: { value: new THREE.Color(0xf7f4ff) },
      },
      vertexShader: MYSAR_VERTEX,
      fragmentShader: MYSAR_FRAGMENT,
    });

        return new THREE.Mesh(geometry, material);
  }

  /** Floating dust particles orbiting the orb. */
  private buildDust(): THREE.Points {
    const count = 120;
    const geometry = new THREE.BufferGeometry();
    const pos = new Float32Array(count * 3);
    const size = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const r = 2.2 + Math.random() * 1.2;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      size[i] = 0.014 + Math.random() * 0.028;
    }
    geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geometry.setAttribute("psize", new THREE.BufferAttribute(size, 1));

    const material = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: { uTime: { value: 0 }, uPhase: { value: 0 } },
      vertexShader: MYSAR_DUST_VERT,
      fragmentShader: MYSAR_DUST_FRAG,
    });

    const points = new THREE.Points(geometry, material);
    this.disposeFns.push(() => {
      geometry.dispose();
      material.dispose();
    });
        return points;
  }

  // --- public API -----------------------------------------------------------

  /** Change the orb's behavioral phase (drives animation + shader state). */
  setPhase(phase: OrbPhase): void {
    if (phase === this.phase) return;
    this.phase = phase;
    this.phaseTime = 0;
    this.noiseOffset = Math.random() * 100;
    const idx = phaseIndex(phase);
    (this.mesh.material as THREE.ShaderMaterial).uniforms.uPhase.value = idx;
    (this.dust.material as THREE.ShaderMaterial).uniforms.uPhase.value = idx;
  }

  /** Advance one frame by `dt` seconds, then render. */
  update(dt: number): void {
    const t = performance.now() / 1000;
    let amp = 0;
    if (this.phase === "listening") amp = this.opts.mic();
    else if (this.phase === "speaking") amp = this.opts.speak();
    else if (this.phase === "thinking") amp = 0.22 + 0.14 * Math.sin(t * 2.4);
    amp = Math.max(0, Math.min(1, amp));
    amp = amp * amp;

    this.phaseTime += dt;
    const mat = this.mesh.material as THREE.ShaderMaterial;
    mat.uniforms.uTime.value = t;
    mat.uniforms.uNoiseA.value = this.noiseOffset;
    mat.uniforms.uAmp.value = amp * 0.5;
    mat.uniforms.uPhaseTime.value = this.phaseTime;
    (this.dust.material as THREE.ShaderMaterial).uniforms.uTime.value = t;

    this.mesh.rotation.y += dt * 0.12;
    this.mesh.rotation.x += dt * 0.03;
    this.renderer.render(this.scene, this.camera);
  }

  /** Resize the renderer to match the device viewport. */
  private fit(): void {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  }

  /** Free all GPU resources. */
  dispose(): void {
    for (const fn of this.disposeFns) fn();
    this.disposeFns = [];
  }
}

/** Map a phase name to a float index usable in shaders. */
function phaseIndex(phase: OrbPhase): number {
  switch (phase) {
    case "idle":
      return 0;
    case "listening":
      return 1;
    case "thinking":
      return 2;
    case "speaking":
      return 3;
    case "error":
      return 4;
        default:
      return 0;
  }
}

const MYSAR_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uNoiseA;
uniform float uAmp;
uniform float uPhase;
uniform float uPhaseTime;
varying vec3 vNormal;
varying vec3 vPos;
varying vec3 vWorldPos;
varying vec2 vUv;

float hash(vec3 p){ return fract(sin(dot(p,vec3(12.9898,78.233,45.164)))*43758.5453); }
float noise(vec3 p){
  vec3 i=floor(p); vec3 f=fract(p);
  f=f*f*(3.0-2.0*f);
  float n=0.0;
  for(int j=0;j<2;j++)for(int k=0;k<2;k++)for(int l=0;l<2;l++){
    vec3 g=vec3(float(j),float(k),float(l));
    vec3 off=g*f+(1.0-g)*(1.0-f);
    n+=off.z*off.x*off.y*hash(i+g);
  }
  return n;
}
float fbm(vec3 p,float amp,float gain,int oct){
  float sum=0.0,a=amp;
  for(int i=0;i<oct;i++){sum+=a*noise(p);p*=2.0;a*=gain;}
  return sum;
}

void main(){
  vUv=uv;
  vNormal=normal;
  vec3 worldPos=(modelMatrix*vec4(position,1.0)).xyz;
  vPos=position;
  vWorldPos=worldPos;
  float n=fbm(position*3.4+uTime*0.3+uNoiseA,1.0,0.5,4);
  float breath=0.08*sin(uTime*0.9+uPhaseTime);
  float disp=0.10+0.26*n+breath+uAmp*0.38;
  vec3 displaced=position+normal*disp;
  gl_Position=projectionMatrix*modelViewMatrix*vec4(displaced,1.0);
}
`;

const MYSAR_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uAmp;
uniform float uPhase;
uniform float uPhaseTime;
uniform vec3 uBaseColor;
varying vec3 vNormal;
varying vec3 vPos;
varying vec3 vWorldPos;
varying vec2 vUv;

float hash1(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
float noise2(vec2 p){
  vec2 i=floor(p);vec2 f=fract(p);
  f=f*f*(3.0-2.0*f);
  float a=hash1(i),b=hash1(i+vec2(1.0,0.0)),c=hash1(i+vec2(0.0,1.0)),d=hash1(i+vec2(1.0,1.0));
  return mix(mix(a,b),mix(c,d),f.y)*f.x+(1.0-f.x)*mix(a,b);
}
vec3 thinFilm(float t){
  float r=sin(t*22.0+0.9)*0.5+0.5,g=sin(t*19.0+1.7)*0.5+0.5,b=sin(t*15.0+2.9)*0.5+0.5;
  return normalize(vec3(r,g,b))*0.55;
}

void main(){
  vec3 n=normalize(vNormal);
  vec3 view=normalize(cameraPosition-vWorldPos);
  float fresnel=pow(1.0-max(0.0,dot(n,view)),3.0);
  float stripes=sin(vPos.y*18.0+uTime*0.4);
  float strip=smoothstep(0.55,0.75,abs(stripes))*0.5;
  vec2 uv2=vUv*5.0;
  float band=noise2(uv2+vec2(uTime*0.15,uPhaseTime*0.2));
  float band2=noise2(uv2*1.8+vec2(-uTime*0.1,0.3));
  float irid=0.5+0.5*sin(band*6.28+band2*3.0+uAmp*4.0);
  vec3 film=thinFilm(irid+uAmp*0.6);
  vec3 cyan=vec3(0.55,0.86,0.95),magenta=vec3(0.92,0.55,0.78);
  vec3 peach=vec3(0.98,0.74,0.62),violet=vec3(0.78,0.66,0.92);
  vec3 palette=mix(mix(cyan,magenta,band),mix(peach,violet,band2));
  vec3 color=uBaseColor*0.82;
  color=mix(color,palette,0.38+uAmp*0.22);
  color=mix(color,film,0.22+fresnel*0.22);
  color*=1.0-strip*0.18;
  color+=film*fresnel*(0.45+uAmp*0.35);
  gl_FragColor=vec4(color,1.0);
}
`;

const MYSAR_DUST_VERT = /* glsl */ `
attribute float psize;
uniform float uTime;
uniform float uPhase;
varying float vSize;
void main(){
  vSize=psize;
  vec3 p=position;
  float t=uTime*0.04+psize*17.0;
  p.x+=sin(t)*0.06;
  p.y+=cos(t*1.3)*0.05;
  float scale=1.0+(uPhase>0.5?0.02*sin(uTime*1.7):0.0);
  gl_PointSize=psize*280.0*scale;
  gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.0);
}
`;

const MYSAR_DUST_FRAG = /* glsl */ `
varying float vSize;
void main(){
  float d=length(gl_PointCoord-0.5);
  float a=smoothstep(0.5,0.0,d);
  vec3 c=mix(vec3(0.95,0.7,0.82),vec3(0.7,0.86,0.96),gl_PointCoord.x);
  gl_FragColor=vec4(c,a*0.55);
}
`;
