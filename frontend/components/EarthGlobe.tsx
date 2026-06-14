import { useState, useEffect, useRef } from 'react';

export default function EarthGlobe() {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [blobUrl, setBlobUrl] = useState<string>('');

  useEffect(() => {
    const html = `<!DOCTYPE html><html><head><style>*{margin:0;padding:0;}body{background:#000510;overflow:hidden;}</style></head><body>
<script src="https://unpkg.com/three@0.128.0/build/three.min.js"><\/script>
<script src="https://unpkg.com/three@0.128.0/examples/js/controls/OrbitControls.js"><\/script>
<script>
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(45,window.innerWidth/window.innerHeight,0.1,1000);
camera.position.set(2,0.5,2);
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setSize(window.innerWidth,window.innerHeight);
document.body.appendChild(renderer.domElement);

const sg=new THREE.BufferGeometry();
const sv=[];
for(let i=0;i<6000;i++){sv.push((Math.random()-.5)*200,(Math.random()-.5)*200,(Math.random()-.5)*200);}
sg.setAttribute('position',new THREE.Float32BufferAttribute(sv,3));
scene.add(new THREE.Points(sg,new THREE.PointsMaterial({color:0xffffff,size:0.12})));

const sun=new THREE.DirectionalLight(0xFFF5E0,1.8);
sun.position.set(5,3,5);
scene.add(sun);

const ambientLight = new THREE.AmbientLight(0x223344, 0.35);
scene.add(ambientLight);

const fillLight = new THREE.DirectionalLight(0x4488BB, 0.3);
fillLight.position.set(-5, -1, -3);
scene.add(fillLight);

const hemi = new THREE.HemisphereLight(0x0033AA, 0x001100, 0.2);
scene.add(hemi);

const loader=new THREE.TextureLoader();
loader.crossOrigin='anonymous';

const earth=new THREE.Mesh(
  new THREE.SphereGeometry(1,64,64),
  new THREE.MeshPhongMaterial({
    map:loader.load('https://raw.githubusercontent.com/turban/webgl-earth/master/images/2_no_clouds_4k.jpg'),
    specularMap:loader.load('https://raw.githubusercontent.com/turban/webgl-earth/master/images/water_4k.png'),
    specular:new THREE.Color(0x333333),
    shininess:25
  })
);
scene.add(earth);

const clouds=new THREE.Mesh(
  new THREE.SphereGeometry(1.01,64,64),
  new THREE.MeshPhongMaterial({
    map:loader.load('https://raw.githubusercontent.com/turban/webgl-earth/master/images/fair_clouds_4k.png'),
    transparent:true,opacity:0.4,depthWrite:false
  })
);
scene.add(clouds);

const atm=new THREE.Mesh(
  new THREE.SphereGeometry(1.05,64,64),
  new THREE.ShaderMaterial({
    vertexShader:'varying vec3 vN;void main(){vN=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader:'varying vec3 vN;void main(){float i=pow(0.65-dot(vN,vec3(0,0,1)),3.0);gl_FragColor=vec4(0.2,0.6,1.0,1.0)*i;}',
    blending:THREE.AdditiveBlending,side:THREE.BackSide,transparent:true
  })
);
scene.add(atm);

const pts=[];
for(let i=0;i<=128;i++){const a=i/128*Math.PI*2;pts.push(new THREE.Vector3(Math.cos(a)*1.35,Math.sin(a)*0.4,Math.sin(a)*1.25));}
scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:0x00FF8C,transparent:true,opacity:0.25})));

const sat=new THREE.Mesh(new THREE.SphereGeometry(0.025,16,16),new THREE.MeshBasicMaterial({color:0x00FF8C}));
scene.add(sat);

const ahmedabadDot = new THREE.Mesh(
  new THREE.SphereGeometry(0.015, 8, 8),
  new THREE.MeshBasicMaterial({ color: 0x00FF8C })
);
scene.add(ahmedabadDot);

const ahmedabadDome = new THREE.Mesh(
  new THREE.SphereGeometry(0.12, 16, 16, 0, Math.PI * 2, 0, Math.PI / 3),
  new THREE.MeshBasicMaterial({
    color: 0x00ffb2,
    wireframe: true,
    transparent: true,
    opacity: 0.1,
  })
);
scene.add(ahmedabadDome);

const linkGeom = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), new THREE.Vector3(0,0,0)]);
const linkMat = new THREE.LineBasicMaterial({
  color: 0x00FF8C,
  transparent: true,
  opacity: 0.75
});
const linkLine = new THREE.Line(linkGeom, linkMat);
scene.add(linkLine);

const getCoordinateOnSphere = (lat, lng, r, rotationY) => {
  const phi = (90 - lat) * Math.PI / 180;
  const theta = (lng + 180) * Math.PI / 180 + rotationY;
  const x = -(r * Math.sin(phi) * Math.sin(theta));
  const y = r * Math.cos(phi);
  const z = r * Math.sin(phi) * Math.cos(theta);
  return new THREE.Vector3(x, y, z);
};

const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.dampingFactor=0.05;
controls.minDistance=1.5;controls.maxDistance=4.0;

let t=0;
function animate(){
  requestAnimationFrame(animate);
  earth.rotation.y+=0.0005;
  clouds.rotation.y+=0.0003;
  t+=0.002;
  
  const satX = Math.cos(t)*1.35;
  const satY = Math.sin(t)*0.4;
  const satZ = Math.sin(t)*1.25;
  sat.position.set(satX, satY, satZ);

  const currentEarthRotation = earth.rotation.y;
  const ahmedabadPos = getCoordinateOnSphere(23.0225, 72.5714, 1.0, currentEarthRotation);
  
  ahmedabadDot.position.copy(ahmedabadPos);
  ahmedabadDome.position.copy(ahmedabadPos);
  ahmedabadDome.lookAt(ahmedabadPos.clone().multiplyScalar(2));
  ahmedabadDome.rotateX(Math.PI / 2);

  const satPosVec = sat.position;
  const normAhmedabad = ahmedabadPos.clone().normalize();
  const normSatellite = satPosVec.clone().normalize();
  const dotProd = normAhmedabad.dot(normSatellite);
  
  const hasUplinkLineOfSight = dotProd > 0.82;

  if (hasUplinkLineOfSight) {
    linkLine.visible = true;
    linkLine.geometry.setFromPoints([ahmedabadPos, satPosVec]);
    linkLine.material.opacity = 0.5 + Math.sin(Date.now() * 0.007) * 0.15;
    ahmedabadDome.material.opacity = 0.2 + Math.sin(Date.now() * 0.007) * 0.05;
  } else {
    linkLine.visible = false;
    ahmedabadDome.material.opacity = 0.06;
  }

  const r_xy_len = Math.sqrt(satX * satX + satY * satY);
  const lat_deg = Math.atan2(satY, r_xy_len) * 180 / Math.PI;
  const lng_deg = (((Math.atan2(satX, satZ) * 180 / Math.PI - (currentEarthRotation * 180 / Math.PI)) % 360) + 360) % 360 - 180;

  if (window.parent) {
    window.parent.postMessage({
      type: 'SATELLITE_TELEMETRY',
      satLat: lat_deg,
      satLng: lng_deg,
      isInContact: hasUplinkLineOfSight
    }, '*');
  }

  controls.update();
  renderer.render(scene,camera);
}
animate();

window.addEventListener('resize',()=>{
  camera.aspect=window.innerWidth/window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth,window.innerHeight);
});
</script></body></html>`;

    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    setBlobUrl(url);

    return () => {
      URL.revokeObjectURL(url);
    };
  }, []);

  return (
    <>
      {blobUrl && (
        <iframe
          ref={iframeRef}
          src={blobUrl}
          className="w-full h-full border-none rounded-xs"
          title="Earth Globe"
          sandbox="allow-scripts"
        />
      )}
    </>
  );
}
