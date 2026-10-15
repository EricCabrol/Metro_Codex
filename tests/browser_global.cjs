// Optional check: use METRO_GLOBAL_REPORT, METRO_PYTHON, METRO_PLAYWRIGHT_DIR and METRO_BROWSER_EXE.
const {chromium}=require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn}=require('node:child_process');const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'metro-global-')),output=path.resolve(process.env.METRO_GLOBAL_REPORT || 'results_reviewed'),cwd=path.resolve(__dirname,'..');
 const env={...process.env};
 const server=spawn(process.env.METRO_PYTHON || 'python',['-m','metro_braking','--serve','--output',output,'--review-dir',temp,'--port','8779'],{cwd,env});
 let logs='',browser;server.stdout.on('data',x=>logs+=x);server.stderr.on('data',x=>logs+=x);
 try{
  for(let i=0;i<100&&!logs.includes('Review stops at');i++){if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8779/report.html');await page.locator('#review-save-status').filter({hasText:'Disk save connected'}).waitFor();
  const section=page.locator('#global-statistics');assert.equal(await section.count(),1);
  assert(await section.evaluate(el=>!!(el.compareDocumentPosition(document.getElementById('route-checks'))&Node.DOCUMENT_POSITION_FOLLOWING)));
  const image=section.locator('img');await image.evaluate(img=>img.decode());assert(await image.evaluate(img=>img.naturalWidth>500));
  const response=await page.request.get('http://127.0.0.1:8779/global_statistics.csv');assert(response.ok());assert((await response.text()).includes('median_mps2'));
  await page.locator('#report-contents').getByRole('link',{name:'Global Statistics',exact:true}).click();assert((await page.evaluate(()=>location.hash))==='#global-statistics');
  await page.getByRole('button',{name:'Export sections from accepted stops',exact:true}).click();await page.locator('#review-save-status').filter({hasText:'Exported'}).waitFor({timeout:60000});
  assert((await image.getAttribute('src')).startsWith('global_statistics.png?updated='));await image.evaluate(img=>img.decode());
  if(process.env.METRO_GLOBAL_SCREENSHOT)await section.screenshot({path:process.env.METRO_GLOBAL_SCREENSHOT});
  assert.deepEqual(errors,[]);console.log('PASS: Global Statistics at report start, TOC navigation, boxplot image, CSV download, refresh after export, no JavaScript errors');
 } finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
