import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const [root, modules] = process.argv.slice(2).map(p=>path.resolve(p));
const require = createRequire(path.join(modules, '__live2d_probe.cjs'));
let server, browser;
const report = {passed:false, errors:[], cases:[], computerUseCalls:0};
const stage = async name=>fs.writeFile(path.join(root,'progress.json'),JSON.stringify({stage:name,time:new Date().toISOString()}));
try {
  await stage('load_dependencies');
  const {createServer} = await import(pathToFileURL(require.resolve('vite')));
  await stage('vite_imported');
  const {chromium} = require('playwright');
  const options = JSON.parse(await fs.readFile(path.join(root,'options.json'),'utf8'));
  server = await createServer({configFile:false, root:path.join(root,'project'),
    server:{host:'127.0.0.1',port:0,strictPort:false},logLevel:'warn'});
  await server.listen();
  await stage('server_started');
  const port = server.httpServer.address().port;
  browser = await chromium.launch({headless:true,
    ...(options.browser_channel === 'chromium' ? {} : {channel:options.browser_channel})});
  const page = await browser.newPage({viewport:{width:1000,height:1000}});
  await stage('browser_started');
  await page.route('**/*', route=>route.request().url().startsWith('http://127.0.0.1:'+port+'/') ? route.continue() : route.abort());
  page.on('pageerror', e=>report.errors.push(e.message));
  await page.goto('http://127.0.0.1:'+port+'/');
  await stage('page_loaded');
  await page.waitForFunction(()=>window.acceptance || window.acceptanceError,undefined,{timeout:25000});
  const initial = await page.evaluate(()=>window.acceptanceError || window.acceptance.report);
  if(typeof initial==='string')throw Error(initial);
  Object.assign(report,initial);
  await stage('core_loaded');
  // SDK shader compilation is asynchronous after the first draw.
  await page.waitForFunction(()=>window.acceptance.reset().pixels.visible > 0,undefined,{timeout:15000});
  const baseline = await page.evaluate(()=>window.acceptance.reset());
  if(baseline.pixels.visible < 1)throw Error('Blank WebGL frame');
  report.baseline = baseline.pixels;
  const ids = await page.evaluate(()=>window.acceptance.ids);
  await stage('baseline_rendered');
  await page.screenshot({path:path.join(root,'baseline.png')});
  for(const [index,c] of options.cases.entries()) {
    await stage('case_'+index);
    const actual = await page.evaluate(async c=>{
      if(c.kind==='parameters')return window.acceptance.set(c.parameters);
      if(c.kind==='motion')return window.acceptance.play(c.resource_name,c.time);
      return window.acceptance.expression(c.resource_name);
    },c);
    const pixelChange = actual.pixels.hash !== baseline.pixels.hash;
    const geometryChange = JSON.stringify(actual.geometry)!==JSON.stringify(baseline.geometry);
    const parametersMatch = Object.entries(c.expected_parameters).every(([id,value])=>ids.includes(id) && Math.abs(actual.values[ids.indexOf(id)]-value)<0.0001);
    const passed = (!c.require_pixel_change || pixelChange) && (!c.require_geometry_change || geometryChange) && parametersMatch;
    report.cases.push({name:c.name,kind:c.kind,passed,pixelChange,geometryChange,parametersMatch,
      pixels:actual.pixels,values:actual.values});
    await page.screenshot({path:path.join(root,'case-'+index+'.png')});
    if(!passed)report.errors.push('No required visible/geometry change: '+c.name);
  }
  report.errors.push(...await page.evaluate(()=>window.acceptance.errors));
  report.passed = report.errors.length===0;
} catch(error) {
  report.errors.push(String(error.stack||error));
} finally {
  await stage('cleanup');
  if(browser)await browser.close();
  if(server)await server.close();
  await fs.writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
}
console.log(JSON.stringify(report));
process.exitCode = report.passed ? 0 : 1;
