import {CubismFramework, Option, LogLevel} from './vendor/framework/src/live2dcubismframework';
import {CubismMoc} from './vendor/framework/src/model/cubismmoc';
import {CubismRenderer_WebGL} from './vendor/framework/src/rendering/cubismrenderer_webgl';
import {CubismMatrix44} from './vendor/framework/src/math/cubismmatrix44';
import {CubismMotion} from './vendor/framework/src/motion/cubismmotion';
import {CubismMotionManager} from './vendor/framework/src/motion/cubismmotionmanager';
import {CubismExpressionMotion} from './vendor/framework/src/motion/cubismexpressionmotion';

const status=document.querySelector('#status')!;
const canvas=document.querySelector('canvas')!;
const errors:string[]=[];
window.addEventListener('error',e=>errors.push(e.message));
const buffer=async (url:string)=>{const r=await fetch(url);if(!r.ok)throw Error(`HTTP ${r.status} ${url}`);return r.arrayBuffer()};
async function main(){
  const option=new Option();option.logFunction=console.log;option.loggingLevel=LogLevel.LogLevel_Warning;
  if(!CubismFramework.startUp(option))throw Error('Core startup failed');
  CubismFramework.initialize();
  const base='/model/';const config=await (await fetch(base+'model.model3.json')).json();
  const bytes=await buffer(base+config.FileReferences.Moc);
  const moc=CubismMoc.create(bytes,true);if(!moc)throw Error('MOC consistency/load failed');
  const model=moc.createModel();if(!model)throw Error('Model initialization failed');
  const gl=canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,preserveDrawingBuffer:true});if(!gl)throw Error('WebGL2 unavailable');
  const renderer=new CubismRenderer_WebGL();renderer.initialize(model);renderer.startUp(gl);
  for(let i=0;i<config.FileReferences.Textures.length;i++){
    const image=new Image();image.src=base+config.FileReferences.Textures[i];await image.decode();
    const texture=gl.createTexture()!;gl.bindTexture(gl.TEXTURE_2D,texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,1);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    renderer.bindTexture(i,texture);
  }
  renderer.setIsPremultipliedAlpha(true);
  const core=model.getModel();const ids=Array.from(core.parameters.ids);
  const defaults=Array.from(core.parameters.defaultValues);
  const geometry=()=>Array.from({length:model.getDrawableCount()},(_,i)=>Array.from(model.getDrawableVertexPositions(i)));
  const reset=()=>{for(let i=0;i<ids.length;i++)model.setParameterValueByIndex(i,defaults[i]);};
  const matrix=new CubismMatrix44();matrix.scale(1.8/model.getCanvasWidth(),1.8/model.getCanvasWidth());
  renderer.setMvpMatrix(matrix);
  function draw(){model.update();gl!.viewport(0,0,canvas.width,canvas.height);gl!.clearColor(0,0,0,0);gl!.clear(gl!.COLOR_BUFFER_BIT);renderer.setRenderState(null,[0,0,canvas.width,canvas.height]);renderer.drawModel('/vendor/core/shaders/');}
  async function play(name:string,time:number){
    reset();const path=config.FileReferences.Motions?.[name]?.[0]?.File;
    if(!path)throw Error('Unknown motion '+name);
    const b=await buffer(base+path);const motionData=JSON.parse(new TextDecoder().decode(b));
    for(const curve of motionData.Curves){if(curve.Target==='Parameter'&&!ids.includes(curve.Id))throw Error('Unknown motion parameter '+curve.Id);}
    const motion=CubismMotion.create(b,b.byteLength,undefined,undefined,true);
    const groupIds=(name:string)=>(config.Groups||[]).filter((g:any)=>g.Target==='Parameter'&&g.Name===name).flatMap((g:any)=>g.Ids).map((id:string)=>CubismFramework.getIdManager().getId(id));
    motion.setEffectIds(groupIds('EyeBlink'),groupIds('LipSync'));
    const manager=new CubismMotionManager();manager.startMotionPriority(motion,false,1);manager.updateMotion(model,0);manager.updateMotion(model,time);draw();
    return {values:Array.from(core.parameters.values),geometry:geometry(),pixels:pixelStats(),duration:motion.getDuration()};
  }
  function pixelStats(){const pixels=new Uint8Array(canvas.width*canvas.height*4);gl!.readPixels(0,0,canvas.width,canvas.height,gl!.RGBA,gl!.UNSIGNED_BYTE,pixels);let visible=0,hash=2166136261;for(let i=0;i<pixels.length;i+=4){if(pixels[i+3])visible++;hash=Math.imul(hash^pixels[i],16777619);hash=Math.imul(hash^pixels[i+1],16777619);hash=Math.imul(hash^pixels[i+2],16777619);hash=Math.imul(hash^pixels[i+3],16777619);}return {visible,hash:hash>>>0};}
  const report={coreLoaded:true,mocConsistency:true,webgl:true,parameters:ids.length,drawables:model.getDrawableCount(),coreVersion:(window as any).Live2DCubismCore.Version.csmGetVersion()};
  reset();draw();
  (window as any).acceptance={report,ids,geometry,play,reset:()=>{reset();draw();return {geometry:geometry(),pixels:pixelStats(),values:Array.from(core.parameters.values)}},set:(values:Record<string,number>)=>{reset();for(const [id,value] of Object.entries(values)){const i=ids.indexOf(id);if(i<0)throw Error('Unknown parameter '+id);if(!Number.isFinite(value)||value<core.parameters.minimumValues[i]||value>core.parameters.maximumValues[i])throw Error('Parameter outside model range '+id);model.setParameterValueById(CubismFramework.getIdManager().getId(id),value)}draw();return {geometry:geometry(),pixels:pixelStats(),values:Array.from(core.parameters.values)};},expression:async(name:string)=>{reset();const ref=config.FileReferences.Expressions.find((v:any)=>v.Name===name);if(!ref)throw Error('Unknown expression '+name);const b=await buffer(base+ref.File);const expression=CubismExpressionMotion.create(b,b.byteLength);const manager=new CubismMotionManager();manager.startMotionPriority(expression,false,1);manager.updateMotion(model,0);manager.updateMotion(model,.5);draw();return {geometry:geometry(),pixels:pixelStats(),values:Array.from(core.parameters.values)}},errors};
  status.textContent=JSON.stringify(report,null,2);
  for(const name of Object.keys(config.FileReferences.Motions||{})){const button=document.createElement('button');button.textContent=name;button.onclick=()=>play(name,.25);document.querySelector('#controls')!.append(button)}
}
main().catch(e=>{status.textContent=String(e.stack||e);errors.push(String(e));(window as any).acceptanceError=String(e.stack||e)});
